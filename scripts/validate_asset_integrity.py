#!/usr/bin/env python3
"""Validate YAML syntax, asset IDs, references, and Required structures."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:
    print(
        "Asset integrity validation requires PyYAML. "
        "Install requirements-validation.txt before running this script."
    )
    raise SystemExit(2)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_RULES_PATH = (
    REPOSITORY_ROOT / "phase1_5/templates/asset_required_fields.yaml"
)
ASSET_ROOT = REPOSITORY_ROOT / "phase1_5/assets"

PLACEHOLDER_PATTERN = re.compile(r"^\$\{[A-Z0-9_]+_LOCAL_ONLY\}$")


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""


def construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)


@dataclass(frozen=True)
class Definition:
    kind: str
    asset_id: str
    file: str
    path: str


@dataclass(frozen=True)
class Reference:
    kind: str
    asset_id: str
    file: str
    path: str


def repository_yaml_files() -> list[str]:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "ls-files",
            "-co",
            "--exclude-standard",
            "*.yaml",
            "*.yml",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return sorted(set(result.stdout.splitlines()))


def load_yaml(path: Path) -> Any:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)


def is_reference_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    return bool(normalized) and normalized.upper() != "TBD" and not bool(
        PLACEHOLDER_PATTERN.fullmatch(normalized)
    )


def flatten_reference_values(value: Any) -> Iterable[str]:
    if is_reference_value(value):
        yield value.strip()
    elif isinstance(value, list):
        for item in value:
            if is_reference_value(item):
                yield item.strip()


def add_definition(
    definitions: list[Definition],
    definition_paths: set[tuple[str, str]],
    kind: str,
    value: Any,
    file: str,
    path: str,
) -> None:
    if is_reference_value(value):
        definitions.append(Definition(kind, value.strip(), file, path))
        definition_paths.add((file, path))


def collect_definitions(
    documents: dict[str, Any],
) -> tuple[list[Definition], set[tuple[str, str]]]:
    definitions: list[Definition] = []
    definition_paths: set[tuple[str, str]] = set()
    formal_contract_ids = {
        str(document.get("result_contract_id"))
        for document in documents.values()
        if isinstance(document, dict)
        and document.get("config_type") == "result_contract"
        and is_reference_value(document.get("result_contract_id"))
    }

    for file, document in documents.items():
        if not isinstance(document, dict) or not file.startswith("phase1_5/assets/"):
            continue

        if document.get("config_type") == "data_source_inventory":
            for index, item in enumerate(document.get("data_sources", [])):
                add_definition(
                    definitions,
                    definition_paths,
                    "source",
                    item.get("source_id"),
                    file,
                    f"data_sources[{index}].source_id",
                )

        if document.get("config_type") == "dataset_inventory":
            for index, item in enumerate(document.get("datasets", [])):
                add_definition(
                    definitions,
                    definition_paths,
                    "dataset",
                    item.get("dataset_id"),
                    file,
                    f"datasets[{index}].dataset_id",
                )
            for index, item in enumerate(document.get("query_assets", [])):
                add_definition(
                    definitions,
                    definition_paths,
                    "query_asset",
                    item.get("query_asset_id"),
                    file,
                    f"query_assets[{index}].query_asset_id",
                )

        if document.get("config_type") == "pipeline_registry":
            for index, item in enumerate(document.get("pipelines", [])):
                add_definition(
                    definitions,
                    definition_paths,
                    "pipeline",
                    item.get("pipeline_id"),
                    file,
                    f"pipelines[{index}].pipeline_id",
                )
                result_path = f"pipelines[{index}].outputs.result_contract_ids"
                for contract_id in item.get("outputs", {}).get(
                    "result_contract_ids", []
                ):
                    if contract_id not in formal_contract_ids:
                        add_definition(
                            definitions,
                            definition_paths,
                            "result_contract",
                            contract_id,
                            file,
                            result_path,
                        )
            scope = document.get("initialization_scope", {})
            for workflow_id in scope.get("included_workflows", []):
                add_definition(
                    definitions,
                    definition_paths,
                    "workflow",
                    workflow_id,
                    file,
                    "initialization_scope.included_workflows",
                )
            for index, item in enumerate(scope.get("deferred_workflows", [])):
                add_definition(
                    definitions,
                    definition_paths,
                    "workflow",
                    item.get("workflow_id"),
                    file,
                    f"initialization_scope.deferred_workflows[{index}].workflow_id",
                )

        if document.get("config_type") == "field_mapping_profile":
            add_definition(
                definitions,
                definition_paths,
                "mapping_profile",
                document.get("mapping_profile_id"),
                file,
                "mapping_profile_id",
            )

        if document.get("config_type") == "business_rule":
            add_definition(
                definitions,
                definition_paths,
                "business_rule",
                document.get("rule_id"),
                file,
                "rule_id",
            )

        if document.get("asset_type") == "Metric Library":
            for index, item in enumerate(document.get("metric_definitions", [])):
                add_definition(
                    definitions,
                    definition_paths,
                    "metric",
                    item.get("metric_id"),
                    file,
                    f"metric_definitions[{index}].metric_id",
                )
            for index, item in enumerate(document.get("metric_variants", [])):
                add_definition(
                    definitions,
                    definition_paths,
                    "metric_variant",
                    item.get("metric_variant_id"),
                    file,
                    f"metric_variants[{index}].metric_variant_id",
                )
            for index, item in enumerate(document.get("normalization_rules", [])):
                add_definition(
                    definitions,
                    definition_paths,
                    "normalization_rule",
                    item.get("rule_id"),
                    file,
                    f"normalization_rules[{index}].rule_id",
                )

        if document.get("config_type") == "output_mapping":
            add_definition(
                definitions,
                definition_paths,
                "output_mapping",
                document.get("output_mapping_id"),
                file,
                "output_mapping_id",
            )

        if file.startswith("phase1_5/assets/") and document.get(
            "config_type"
        ) == "result_contract":
            add_definition(
                definitions,
                definition_paths,
                "result_contract",
                document.get("result_contract_id"),
                file,
                "result_contract_id",
            )

        if document.get("asset_type") == "Metric Result Store":
            for store_index, store in enumerate(
                document.get("metric_result_stores", [])
            ):
                add_definition(
                    definitions,
                    definition_paths,
                    "metric_store",
                    store.get("store_id"),
                    file,
                    f"metric_result_stores[{store_index}].store_id",
                )
                for asset_index, asset in enumerate(store.get("store_assets", [])):
                    add_definition(
                        definitions,
                        definition_paths,
                        "metric_store_asset",
                        asset.get("store_asset_id"),
                        file,
                        (
                            f"metric_result_stores[{store_index}]."
                            f"store_assets[{asset_index}].store_asset_id"
                        ),
                    )

        if document.get("config_type") == "execution_policy":
            add_definition(
                definitions,
                definition_paths,
                "policy",
                document.get("policy_id"),
                file,
                "policy_id",
            )

        if document.get("config_type") == "configured_display_value_policy":
            add_definition(
                definitions,
                definition_paths,
                "policy",
                document.get("policy_id"),
                file,
                "policy_id",
            )

        if document.get("asset_type") == "Pipeline Policy":
            add_definition(
                definitions,
                definition_paths,
                "policy",
                document.get("policy_id"),
                file,
                "policy_id",
            )

        if document.get("config_type") == "external_asset_reference_registry":
            for index, item in enumerate(document.get("external_assets", [])):
                add_definition(
                    definitions,
                    definition_paths,
                    "external_asset",
                    item.get("asset_id"),
                    file,
                    f"external_assets[{index}].asset_id",
                )

        config_type = str(document.get("config_type", ""))
        if config_type.endswith("readiness_gate") or config_type in {
            "field_mapping_readiness_gate",
            "code_implementation_readiness_gate",
        }:
            add_definition(
                definitions,
                definition_paths,
                "gate",
                document.get("gate_id"),
                file,
                "gate_id",
            )

        if config_type == "dataset_readiness_matrix":
            add_definition(
                definitions,
                definition_paths,
                "gate",
                document.get("gate_id", "GATE_DATASET_READINESS_WF_WEEKLY_BUSINESS_REPORT_V1"),
                file,
                "gate_id" if document.get("gate_id") else "<implicit_dataset_readiness_gate>",
            )

        if config_type == "pipeline_registry":
            add_definition(
                definitions,
                definition_paths,
                "gate",
                document.get("gate_id", "GATE_PIPELINE_REGISTRY_WF_WEEKLY_BUSINESS_REPORT_V1"),
                file,
                "gate_id" if document.get("gate_id") else "<implicit_pipeline_registry_gate>",
            )

        if config_type == "metric_result_store_readiness_matrix":
            add_definition(
                definitions,
                definition_paths,
                "gate",
                document.get("gate_id", "GATE_METRIC_RESULT_STORE_RUNTIME_WF_WEEKLY_BUSINESS_REPORT_V1"),
                file,
                "gate_id" if document.get("gate_id") else "<implicit_metric_store_runtime_gate>",
            )

    return definitions, definition_paths


def reference_kind(key: str) -> str | None:
    exact = {
        "source_id": "source",
        "original_source_id": "source",
        "dataset_id": "dataset",
        "dataset_dependencies": "dataset",
        "output_dataset_id": "dataset",
        "fallback_dataset_id": "dataset",
        "primary_dataset_id": "dataset",
        "out_of_scope_dataset_id": "dataset",
        "affected_dataset_ids": "dataset",
        "query_asset_id": "query_asset",
        "mapping_profile_id": "mapping_profile",
        "mapping_profile_ids": "mapping_profile",
        "additional_mapping_profile_ids": "mapping_profile",
        "primary_profile_id": "mapping_profile",
        "fallback_profile_id": "mapping_profile",
        "pipeline_id": "pipeline",
        "applicable_pipeline_ids": "pipeline",
        "affected_pipeline_id": "pipeline",
        "affected_pipeline_ids": "pipeline",
        "producer_pipeline_id": "pipeline",
        "source_pipeline_id": "pipeline",
        "downstream_pipeline_id": "pipeline",
        "upstream_pipeline_id": "pipeline",
        "upstream_pipeline_ids": "pipeline",
        "parallel_peer_pipeline_ids": "pipeline",
        "trigger_consumer_pipeline_id": "pipeline",
        "metric_id": "metric",
        "applies_to_metric_ids": "metric",
        "metric_variant_id": "metric_variant",
        "source_metric_variant_id": "metric_variant",
        "metric_variant_ids": "metric_variant",
        "input_metric_variant_id": "metric_variant",
        "input_metric_variant_ids": "metric_variant",
        "numerator_metric_variant_id": "metric_variant",
        "denominator_metric_variant_id": "metric_variant",
        "upstream_metric_variant_id": "metric_variant",
        "upstream_metric_variant_ids": "metric_variant",
        "upstream_metric_variants": "metric_variant",
        "business_rule_dependencies": "business_rule",
        "ordered_rule_set_ids": "business_rule",
        "affected_business_rule_ids": "business_rule",
        "enables_business_rule_ids": "business_rule",
        "mode_selection_rule_id": "business_rule",
        "prior_year_source_rule_id": "business_rule",
        "relationship_rule_id": "business_rule",
        "rule_id": "business_rule",
        "trigger_policy_id": "policy",
        "business_rule_processing_policy_id": "policy",
        "join_or_relationship_rule_id": "business_rule",
        "normalization_rule_id": "normalization_rule",
        "output_mapping_id": "output_mapping",
        "output_mapping_ids": "output_mapping",
        "included_output_mapping_ids": "output_mapping",
        "deferred_output_mapping_ids": "output_mapping",
        "body_mapping_id": "output_mapping",
        "outlook_draft_mapping_id": "output_mapping",
        "outlook_draft_mapping_reference_id": "output_mapping",
        "output_mapping_reference_id": "output_mapping",
        "workflow_id": "workflow",
        "workflow_ids": "workflow",
        "applicable_workflow_ids": "workflow",
        "supported_workflow_ids": "workflow",
        "assessment_workflow_id": "workflow",
        "metric_result_store_id": "metric_store",
        "store_id": "metric_store",
        "metric_result_store_asset_id": "metric_store_asset",
        "store_asset_id": "metric_store_asset",
        "required_metric_variant_ids": "metric_variant",
        "policy_id": "policy",
        "source_policy_id": "policy",
        "refresh_policy_id": "policy",
        "reference_mapping_asset_id": "external_asset",
        "asset_id": "external_asset",
        "filter_id": "external_asset",
        "local_knowledge_pack_dependency": "external_asset",
        "template_id": "external_asset",
        "template_asset_id": "external_asset",
        "recipient_configuration_id": "external_asset",
        "product_routing_asset_id": "external_asset",
        "field_mapping_gate_id": "gate",
        "implementation_readiness_gate_id": "gate",
        "final_code_implementation_gate_id": "gate",
        "final_gate_id": "gate",
        "gate_id": "gate",
    }
    if key in exact:
        return exact[key]
    if "result_contract" in key and key.endswith(("_id", "_ids")):
        return "result_contract"
    return None


def collect_references(
    documents: dict[str, Any], definition_paths: set[tuple[str, str]]
) -> list[Reference]:
    references: list[Reference] = []

    def walk(file: str, value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                kind = reference_kind(str(key))
                if kind and (file, child_path) not in definition_paths:
                    for asset_id in flatten_reference_values(child):
                        references.append(
                            Reference(kind, asset_id, file, child_path)
                        )
                walk(file, child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(file, child, f"{path}[{index}]")

    for file, document in documents.items():
        if file.startswith("phase1_5/assets/"):
            walk(file, document)
    return references


def required_path_errors(document: Any, required_path: str) -> list[str]:
    segments = required_path.split(".")
    current: list[tuple[str, Any]] = [("", document)]
    errors: list[str] = []

    for segment in segments:
        is_list = segment.endswith("[]")
        key = segment[:-2] if is_list else segment
        next_values: list[tuple[str, Any]] = []
        for prefix, value in current:
            current_path = f"{prefix}.{key}" if prefix else key
            if not isinstance(value, dict):
                errors.append(f"{prefix or '<root>'}: expected mapping")
                continue
            if key not in value:
                errors.append(f"{current_path}: missing Required field")
                continue
            child = value[key]
            if is_list:
                if not isinstance(child, list):
                    errors.append(f"{current_path}: expected list")
                    continue
                if not child:
                    errors.append(f"{current_path}: Required list is empty")
                    continue
                for index, item in enumerate(child):
                    next_values.append((f"{current_path}[{index}]", item))
            else:
                next_values.append((current_path, child))
        current = next_values
        if not current and errors:
            break
    return errors


def validate_required_structures(
    documents: dict[str, Any], errors: list[str]
) -> tuple[int, int]:
    rules_document = documents.get("phase1_5/templates/asset_required_fields.yaml")
    if not isinstance(rules_document, dict):
        errors.append(
            "phase1_5/templates/asset_required_fields.yaml: missing or invalid rules"
        )
        return 0, 0

    rules = rules_document.get("asset_rules", [])
    matched_assets = 0
    checked_paths = 0

    for file, document in documents.items():
        if not file.startswith("phase1_5/assets/") or not isinstance(document, dict):
            continue
        matched_rules = []
        for rule in rules:
            selector = rule.get("selector", {})
            if document.get(selector.get("key")) == selector.get("value"):
                matched_rules.append(rule)
        if not matched_rules:
            errors.append(f"{file}: no Required structure rule matches this asset")
            continue
        if len(matched_rules) > 1:
            errors.append(f"{file}: multiple Required structure rules match this asset")
            continue
        matched_assets += 1
        for required_path in matched_rules[0].get("required_paths", []):
            checked_paths += 1
            for detail in required_path_errors(document, required_path):
                errors.append(f"{file}:{detail}")
    return matched_assets, checked_paths


RESULT_FIELD_REQUIRED_KEYS = {
    "field_id",
    "field_name",
    "business_definition",
    "source_type",
    "data_type",
    "base_unit",
    "nullable",
    "value_status_allowed",
    "applicable_report_modes",
    "validation_requirement",
    "lineage_required",
}
NUMERIC_CONSTRAINT_REQUIRED_KEYS = {
    "numeric_semantics",
    "unit",
    "integer_only",
    "precision",
    "minimum",
    "maximum",
}
RESULT_FIELD_SOURCE_TYPES = {
    "metric_variant",
    "upstream_contract_field",
    "standardized_field",
    "policy_derived_field",
}


def result_contract_field_entries(
    document: dict[str, Any], file: str, errors: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    """Return addressable fields and record-set membership for one contract."""
    fields: dict[str, dict[str, Any]] = {}
    record_sets: dict[str, set[str]] = {}
    seen_ids: set[str] = set()

    def add_field(field: Any, path: str, record_set_id: str | None = None) -> None:
        if not isinstance(field, dict):
            errors.append(f"{file}:{path}: expected mapping")
            return
        field_id = field.get("field_id")
        if not isinstance(field_id, str) or not field_id:
            errors.append(f"{file}:{path}.field_id: missing or invalid field ID")
            return
        if field_id in seen_ids:
            errors.append(f"{file}:{path}.field_id: duplicate field ID {field_id}")
            return
        seen_ids.add(field_id)
        fields[field_id] = field
        if record_set_id:
            fields[f"{record_set_id}.{field_id}"] = field
            record_sets.setdefault(record_set_id, set()).add(field_id)

        missing = sorted(RESULT_FIELD_REQUIRED_KEYS - set(field))
        if missing:
            errors.append(
                f"{file}:{path}: missing Result Contract field keys {missing}"
            )
        source_type = field.get("source_type")
        if source_type not in RESULT_FIELD_SOURCE_TYPES:
            errors.append(
                f"{file}:{path}.source_type: unsupported source type {source_type!r}"
            )
        data_type = field.get("data_type")
        if data_type not in {"number", "string", "date", "boolean"}:
            errors.append(
                f"{file}:{path}.data_type: unsupported or non-unified type "
                f"{data_type!r}; numeric fields must use number"
            )
        if data_type == "number":
            constraints = field.get("numeric_constraints")
            if not isinstance(constraints, dict):
                errors.append(f"{file}:{path}: number field lacks numeric_constraints")
            else:
                missing_numeric = sorted(
                    NUMERIC_CONSTRAINT_REQUIRED_KEYS - set(constraints)
                )
                if missing_numeric:
                    errors.append(
                        f"{file}:{path}.numeric_constraints: missing keys "
                        f"{missing_numeric}"
                    )
                if not isinstance(constraints.get("integer_only"), bool):
                    errors.append(
                        f"{file}:{path}.numeric_constraints.integer_only: "
                        "expected boolean"
                    )
        elif "numeric_constraints" in field:
            errors.append(
                f"{file}:{path}: numeric_constraints requires data_type number"
            )

    for index, field in enumerate(document.get("contract_fields", [])):
        add_field(field, f"contract_fields[{index}]")
    for set_index, record_set in enumerate(document.get("record_sets", [])):
        if not isinstance(record_set, dict):
            errors.append(f"{file}:record_sets[{set_index}]: expected mapping")
            continue
        record_set_id = record_set.get("record_set_id")
        if not isinstance(record_set_id, str) or not record_set_id:
            errors.append(
                f"{file}:record_sets[{set_index}].record_set_id: missing or invalid"
            )
            continue
        if record_set_id in record_sets:
            errors.append(f"{file}: duplicate record_set_id {record_set_id}")
            continue
        record_sets[record_set_id] = set()
        for group in ("context_fields", "record_fields"):
            for field_index, field in enumerate(record_set.get(group, [])):
                add_field(
                    field,
                    f"record_sets[{set_index}].{group}[{field_index}]",
                    record_set_id,
                )
    return fields, record_sets


def validate_result_contract_semantics(
    documents: dict[str, Any], definitions_by_kind: dict[str, set[str]], errors: list[str]
) -> dict[str, int]:
    """Validate contract fields, producer/output bindings, and dependency acyclicity."""
    contract_documents: dict[str, tuple[str, dict[str, Any]]] = {}
    contract_fields: dict[str, dict[str, dict[str, Any]]] = {}
    contract_record_sets: dict[str, dict[str, set[str]]] = {}
    standardized_fields: set[str] = set()
    pipelines: dict[str, tuple[str, dict[str, Any]]] = {}
    metric_variants: dict[str, tuple[str, dict[str, Any]]] = {}
    metric_inputs_by_output_contract: dict[
        str, set[tuple[str, str, str | None, str]]
    ] = defaultdict(set)
    record_grains_checked = 0
    source_contract_routes_checked = 0
    input_lineage_contracts_checked = 0
    display_tbd_checked = 0

    for file, document in documents.items():
        if not isinstance(document, dict):
            continue
        if file.startswith("phase1_5/assets/") and document.get(
            "config_type"
        ) == "result_contract":
            contract_id = document.get("result_contract_id")
            if isinstance(contract_id, str):
                contract_documents[contract_id] = (file, document)
        for mapping in document.get("field_mappings", []):
            if isinstance(mapping, dict) and isinstance(
                mapping.get("standard_field_id"), str
            ):
                standardized_fields.add(mapping["standard_field_id"])
        for mapping in document.get("query_context_fields", []):
            if isinstance(mapping, dict) and isinstance(
                mapping.get("standard_field_id"), str
            ):
                standardized_fields.add(mapping["standard_field_id"])
        for pipeline in document.get("pipelines", []):
            if isinstance(pipeline, dict) and isinstance(pipeline.get("pipeline_id"), str):
                pipelines[pipeline["pipeline_id"]] = (file, pipeline)
        for variant in document.get("metric_variants", []):
            if isinstance(variant, dict) and isinstance(
                variant.get("metric_variant_id"), str
            ):
                metric_variants[variant["metric_variant_id"]] = (file, variant)

    for contract_id, (file, document) in contract_documents.items():
        fields, record_sets = result_contract_field_entries(document, file, errors)
        contract_fields[contract_id] = fields
        contract_record_sets[contract_id] = record_sets

        def declared_dimension_ids(
            dimensions: Any, location: str
        ) -> set[str]:
            dimension_ids: set[str] = set()
            if dimensions is None:
                return dimension_ids
            if not isinstance(dimensions, list):
                errors.append(f"{file}:{location}: expected list")
                return dimension_ids
            for index, dimension in enumerate(dimensions):
                if not isinstance(dimension, dict) or not isinstance(
                    dimension.get("dimension_id"), str
                ):
                    errors.append(
                        f"{file}:{location}[{index}].dimension_id: missing or invalid"
                    )
                    continue
                dimension_id = dimension["dimension_id"]
                if dimension_id in dimension_ids:
                    errors.append(
                        f"{file}:{location}[{index}].dimension_id: duplicate "
                        f"dimension {dimension_id}"
                    )
                dimension_ids.add(dimension_id)
            return dimension_ids

        def validate_grain(
            grain: Any, allowed: set[str], location: str
        ) -> None:
            nonlocal record_grains_checked
            record_grains_checked += 1
            if not isinstance(grain, list) or not grain:
                errors.append(f"{file}:{location}: expected non-empty list")
                return
            for item in grain:
                if not isinstance(item, str) or item not in allowed:
                    errors.append(
                        f"{file}:{location}: grain item {item!r} is not a "
                        "declared field or dimension"
                    )

        top_dimensions = declared_dimension_ids(
            document.get("contract_dimensions"), "contract_dimensions"
        )
        if "record_grain" in document:
            top_fields = {
                field_id for field_id in fields if "." not in field_id
            }
            validate_grain(
                document.get("record_grain"),
                top_fields | top_dimensions,
                "record_grain",
            )
        for set_index, record_set in enumerate(document.get("record_sets", [])):
            if not isinstance(record_set, dict) or "record_grain" not in record_set:
                continue
            record_set_id = record_set.get("record_set_id")
            set_dimensions = declared_dimension_ids(
                record_set.get("record_dimensions"),
                f"record_sets[{set_index}].record_dimensions",
            )
            validate_grain(
                record_set.get("record_grain"),
                record_sets.get(record_set_id, set()) | set_dimensions,
                f"record_sets[{set_index}].record_grain",
            )
        required_fields = document.get("validation", {}).get("required_fields", [])
        for field_id in required_fields:
            if field_id not in fields:
                errors.append(
                    f"{file}:validation.required_fields: unknown field {field_id}"
                )
        producer_id = document.get("producer", {}).get("producer_pipeline_id")
        if producer_id not in pipelines:
            errors.append(f"{file}: producer pipeline {producer_id!r} is not defined")
        else:
            pipeline_contracts = (
                pipelines[producer_id][1].get("outputs", {}).get(
                    "result_contract_ids", []
                )
            )
            if contract_id not in pipeline_contracts:
                errors.append(
                    f"{file}: producer {producer_id} does not declare output {contract_id}"
                )

    producer_counts: dict[str, int] = defaultdict(int)
    for pipeline_id, (file, pipeline) in pipelines.items():
        for contract_id in pipeline.get("outputs", {}).get("result_contract_ids", []):
            if contract_id not in contract_documents:
                errors.append(
                    f"{file}:{pipeline_id}: output contract {contract_id} lacks a formal asset"
                )
            else:
                producer_counts[contract_id] += 1
    for contract_id in contract_documents:
        if producer_counts[contract_id] != 1:
            errors.append(
                f"{contract_documents[contract_id][0]}: {contract_id} must have exactly "
                f"one declaring producer; found {producer_counts[contract_id]}"
            )

    dependency_graph: dict[str, set[str]] = defaultdict(set)

    def validate_contract_field_reference(
        contract_id: Any, field_id: Any, location: str, record_set_id: Any = None
    ) -> None:
        if contract_id not in contract_fields:
            errors.append(f"{location}: unknown result contract {contract_id!r}")
            return
        address = (
            f"{record_set_id}.{field_id}" if isinstance(record_set_id, str) else field_id
        )
        if address not in contract_fields[contract_id]:
            errors.append(
                f"{location}: unknown field {field_id!r} in contract {contract_id}"
            )

    output_bound_variants = 0
    for variant_id, (file, variant) in metric_variants.items():
        if "result_contract_dependency" in variant:
            errors.append(
                f"{file}:{variant_id}: legacy result_contract_dependency is prohibited"
            )
        binding = variant.get("output_binding")
        if not isinstance(binding, dict):
            errors.append(f"{file}:{variant_id}: exactly one output_binding is required")
            continue
        output_contract = binding.get("result_contract_id")
        output_field = binding.get("result_field_id")
        validate_contract_field_reference(
            output_contract,
            output_field,
            f"{file}:{variant_id}.output_binding",
            binding.get("record_set_id"),
        )
        if output_contract in contract_fields:
            address = (
                f"{binding.get('record_set_id')}.{output_field}"
                if binding.get("record_set_id")
                else output_field
            )
            field = contract_fields[output_contract].get(address)
            if field and field.get("source_metric_variant_id") != variant_id:
                errors.append(
                    f"{file}:{variant_id}.output_binding: target field does not source "
                    f"this Metric Variant"
                )
        output_bound_variants += 1
        for input_index, dependency in enumerate(variant.get("input_contract_fields", [])):
            if not isinstance(dependency, dict):
                errors.append(
                    f"{file}:{variant_id}.input_contract_fields[{input_index}]: "
                    "expected mapping"
                )
                continue
            input_contract = dependency.get("result_contract_id")
            validate_contract_field_reference(
                input_contract,
                dependency.get("result_field_id"),
                f"{file}:{variant_id}.input_contract_fields[{input_index}]",
                dependency.get("record_set_id"),
            )
            if isinstance(input_contract, str) and isinstance(output_contract, str):
                dependency_graph[input_contract].add(output_contract)
            if all(
                isinstance(value, str) and value
                for value in (
                    output_contract,
                    input_contract,
                    dependency.get("result_field_id"),
                    dependency.get("required_or_optional"),
                )
            ):
                metric_inputs_by_output_contract[output_contract].add(
                    (
                        input_contract,
                        dependency["result_field_id"],
                        dependency.get("record_set_id"),
                        dependency["required_or_optional"],
                    )
                )

    for contract_id, (file, document) in contract_documents.items():
        for field_id, field in contract_fields[contract_id].items():
            if "." in field_id:
                continue
            source_type = field.get("source_type")
            if source_type == "metric_variant":
                source_id = field.get("source_metric_variant_id")
                if source_id not in metric_variants:
                    errors.append(f"{file}:{field_id}: unknown source Metric Variant {source_id}")
            elif source_type == "standardized_field":
                source_id = field.get("source_standard_field_id")
                if source_id not in standardized_fields:
                    errors.append(f"{file}:{field_id}: unknown standardized field {source_id}")
            elif source_type == "policy_derived_field":
                source_id = field.get("source_policy_id")
                if source_id not in definitions_by_kind.get("policy", set()):
                    errors.append(f"{file}:{field_id}: unknown source policy {source_id}")
            elif source_type == "upstream_contract_field":
                routes = field.get("source_contract_routes")
                if not isinstance(routes, list) or not routes:
                    errors.append(f"{file}:{field_id}: source_contract_routes is required")
                    continue
                route_resolution = field.get("route_resolution")
                if not isinstance(route_resolution, dict) or route_resolution.get(
                    "exactly_one_route_required"
                ) is not True:
                    errors.append(
                        f"{file}:{field_id}: route_resolution must require exactly one route"
                    )
                seen_route_ids: set[str] = set()
                for route_index, route in enumerate(routes):
                    source_contract_routes_checked += 1
                    if not isinstance(route, dict):
                        errors.append(f"{file}:{field_id}: invalid source route")
                        continue
                    upstream_contract = route.get("result_contract_id")
                    route_id = route.get("route_id")
                    route_condition = route.get("route_condition")
                    if not isinstance(route_id, str) or not route_id:
                        errors.append(
                            f"{file}:{field_id}.source_contract_routes[{route_index}]: "
                            "route_id is required"
                        )
                    elif route_id in seen_route_ids:
                        errors.append(
                            f"{file}:{field_id}.source_contract_routes[{route_index}]: "
                            f"duplicate route_id {route_id}"
                        )
                    else:
                        seen_route_ids.add(route_id)
                    if not isinstance(route_condition, str) or not route_condition.strip():
                        errors.append(
                            f"{file}:{field_id}.source_contract_routes[{route_index}]: "
                            "explicit route_condition is required"
                        )
                    upstream_fields = route.get("result_field_ids", [])
                    if not isinstance(upstream_fields, list) or not upstream_fields:
                        errors.append(
                            f"{file}:{field_id}.source_contract_routes[{route_index}]: "
                            "at least one result_field_id is required"
                        )
                        upstream_fields = []
                    for upstream_field in upstream_fields:
                        validate_contract_field_reference(
                            upstream_contract,
                            upstream_field,
                            f"{file}:{field_id}.source_contract_routes[{route_index}]",
                        )
                    if isinstance(upstream_contract, str):
                        dependency_graph[upstream_contract].add(contract_id)

        declared_inputs: set[tuple[str, str, str | None, str]] = set()
        direct_inputs = document.get("input_contract_fields", [])
        routed_inputs = document.get("input_contract_field_routes", [])
        input_groups: list[tuple[str, Any]] = [("input_contract_fields", direct_inputs)]
        if isinstance(routed_inputs, list):
            for route_index, route in enumerate(routed_inputs):
                if not isinstance(route, dict):
                    errors.append(
                        f"{file}:input_contract_field_routes[{route_index}]: expected mapping"
                    )
                    continue
                if not isinstance(route.get("route_condition"), str) or not route.get(
                    "route_condition", ""
                ).strip():
                    errors.append(
                        f"{file}:input_contract_field_routes[{route_index}]: "
                        "explicit route_condition is required"
                    )
                input_groups.append(
                    (
                        f"input_contract_field_routes[{route_index}].input_fields",
                        route.get("input_fields", []),
                    )
                )
        elif routed_inputs is not None:
            errors.append(f"{file}:input_contract_field_routes: expected list")
        for group_path, dependencies in input_groups:
            if not isinstance(dependencies, list):
                errors.append(f"{file}:{group_path}: expected list")
                continue
            for input_index, dependency in enumerate(dependencies):
                if not isinstance(dependency, dict):
                    errors.append(
                        f"{file}:{group_path}[{input_index}]: expected mapping"
                    )
                    continue
                input_contract = dependency.get("result_contract_id")
                input_field = dependency.get("result_field_id")
                requiredness = dependency.get("required_or_optional")
                validate_contract_field_reference(
                    input_contract,
                    input_field,
                    f"{file}:{group_path}[{input_index}]",
                    dependency.get("record_set_id"),
                )
                if all(
                    isinstance(value, str) and value
                    for value in (input_contract, input_field, requiredness)
                ):
                    declared_inputs.add(
                        (
                            input_contract,
                            input_field,
                            dependency.get("record_set_id"),
                            requiredness,
                        )
                    )
                    dependency_graph[input_contract].add(contract_id)

        canonical_inputs = metric_inputs_by_output_contract.get(contract_id, set())
        if declared_inputs != canonical_inputs:
            errors.append(
                f"{file}: Contract input lineage {sorted(declared_inputs)} does not "
                f"match canonical Metric input dependencies {sorted(canonical_inputs)}"
            )
        if declared_inputs or canonical_inputs:
            input_lineage_contracts_checked += 1
            authority = document.get("input_dependency_authority")
            if not isinstance(authority, dict):
                errors.append(f"{file}: input_dependency_authority is required")
            else:
                expected_authority = {
                    "canonical_source_path": "metric_variants[].input_contract_fields",
                    "contract_lineage_role": "derived_validation_summary",
                    "pipeline_registry_role": "orchestration_routing_only",
                }
                for key, expected in expected_authority.items():
                    if authority.get(key) != expected:
                        errors.append(
                            f"{file}:input_dependency_authority.{key}: expected {expected!r}"
                        )
        if document.get("analysis_only_pipeline") is True and document.get(
            "metric_variant_required"
        ) is False:
            metric_sourced = sorted(
                field_id
                for field_id, field in contract_fields[contract_id].items()
                if "." not in field_id and field.get("source_type") == "metric_variant"
            )
            if metric_sourced:
                errors.append(
                    f"{file}: analysis-only contract must not fabricate Metric Variant "
                    f"sources: {metric_sourced}"
                )
            producer_id = document.get("producer", {}).get("producer_pipeline_id")
            if producer_id in pipelines and pipelines[producer_id][1].get(
                "execution", {}
            ).get("metric_variant_ids", []) != []:
                errors.append(
                    f"{file}: analysis-only producer must declare no Metric Variants"
                )

    explicit_output_fields = 0
    for file, document in documents.items():
        if not isinstance(document, dict) or document.get("config_type") != "output_mapping":
            continue

        def walk_output(value: Any, path: str = "") -> None:
            nonlocal explicit_output_fields, display_tbd_checked
            if isinstance(value, dict):
                if "metric_variant_ids" in value:
                    errors.append(f"{file}:{path}.metric_variant_ids: parallel list is prohibited")
                if isinstance(value.get("display_fields"), list):
                    errors.append(f"{file}:{path}.display_fields: parallel list is prohibited")
                if value.get("display_fields") == "TBD":
                    display_tbd_checked += 1
                    errors.append(
                        f"{file}:{path}.display_fields: TBD is prohibited in all active MVP outputs"
                    )
                output_fields = value.get("output_fields")
                if isinstance(output_fields, list):
                    seen_output_ids: set[str] = set()
                    for index, output_field in enumerate(output_fields):
                        location = f"{file}:{path}.output_fields[{index}]"
                        if not isinstance(output_field, dict):
                            errors.append(f"{location}: expected mapping")
                            continue
                        output_id = output_field.get("output_field_id")
                        if not isinstance(output_id, str) or output_id in seen_output_ids:
                            errors.append(f"{location}: missing or duplicate output_field_id")
                        else:
                            seen_output_ids.add(output_id)
                        binding = output_field.get("result_field_binding")
                        if not isinstance(binding, dict):
                            errors.append(f"{location}: result_field_binding is required")
                        else:
                            validate_contract_field_reference(
                                binding.get("result_contract_id"),
                                binding.get("result_field_id"),
                                location,
                                binding.get("record_set_id"),
                            )
                        explicit_output_fields += 1
                record_binding = value.get("result_record_set_binding")
                if isinstance(record_binding, dict):
                    contract_id = record_binding.get("result_contract_id")
                    record_set_id = record_binding.get("record_set_id")
                    if record_set_id not in contract_record_sets.get(contract_id, {}):
                        errors.append(
                            f"{file}:{path}.result_record_set_binding: unknown record set"
                        )
                for key, child in value.items():
                    walk_output(child, f"{path}.{key}" if path else str(key))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk_output(child, f"{path}[{index}]")

        walk_output(document)

    for contract_id, targets in dependency_graph.items():
        if contract_id in targets:
            errors.append(f"Result Contract dependency self-cycle: {contract_id}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(contract_id: str, trail: list[str]) -> None:
        if contract_id in visiting:
            errors.append(
                "Result Contract dependency cycle: " + " -> ".join(trail + [contract_id])
            )
            return
        if contract_id in visited:
            return
        visiting.add(contract_id)
        for target in dependency_graph.get(contract_id, set()):
            visit(target, trail + [contract_id])
        visiting.remove(contract_id)
        visited.add(contract_id)

    for contract_id in contract_documents:
        visit(contract_id, [])

    pipeline_registry = next(
        (
            document
            for document in documents.values()
            if isinstance(document, dict)
            and document.get("config_type") == "pipeline_registry"
        ),
        None,
    )
    authority = (
        pipeline_registry.get("input_contract_dependency_authority", {})
        if isinstance(pipeline_registry, dict)
        else {}
    )
    expected_registry_authority = {
        "canonical_source_path": "metric_variants[].input_contract_fields",
        "result_contract_role": "derived_input_lineage_summary",
        "pipeline_registry_role": "orchestration_routing_only",
        "duplicate_field_level_dependency_authority_allowed": False,
    }
    for key, expected in expected_registry_authority.items():
        if authority.get(key) != expected:
            errors.append(
                "phase1_5/assets/pipelines/pipeline_registry.yaml:"
                f"input_contract_dependency_authority.{key}: expected {expected!r}"
            )

    return {
        "contracts": len(contract_documents),
        "contract_fields": sum(
            len({id(field): field for field in fields.values()})
            for fields in contract_fields.values()
        ),
        "output_bindings": output_bound_variants,
        "output_fields": explicit_output_fields,
        "record_sets": sum(len(value) for value in contract_record_sets.values()),
        "record_grains": record_grains_checked,
        "source_contract_routes": source_contract_routes_checked,
        "input_lineage_contracts": input_lineage_contracts_checked,
        "display_tbd_exceptions": display_tbd_checked,
    }


def validate_configured_display_value_policy(
    documents: dict[str, Any], errors: list[str]
) -> int:
    """Validate the Owner-approved non-Metric configured display value policy."""
    policy_id = "POLICY_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE_DISPLAY_V1"
    policy_file = (
        "phase1_5/assets/policies/"
        "POLICY_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE_DISPLAY_V1.yaml"
    )
    policy = documents.get(policy_file)
    if not isinstance(policy, dict):
        errors.append(f"{policy_file}: configured display value policy is required")
        return 0

    def require_equal(path: str, actual: Any, expected: Any) -> None:
        if actual != expected:
            errors.append(f"{policy_file}:{path}: expected {expected!r}, got {actual!r}")

    require_equal("policy_id", policy.get("policy_id"), policy_id)
    require_equal("status", policy.get("status"), "owner_approved")
    require_equal("workflow_id", policy.get("workflow_id"), "WF_WEEKLY_BUSINESS_REPORT")

    binding = policy.get("display_binding", {})
    expected_binding = {
        "output_mapping_id": "OM_WEEKLY_BUSINESS_REPORT_V1",
        "output_slot_id": "SLOT_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE",
        "display_order": 2,
        "display_label": "订单整体曝光完成率",
    }
    for key, expected in expected_binding.items():
        require_equal(f"display_binding.{key}", binding.get(key), expected)

    require_equal(
        "allowed_display_values",
        policy.get("allowed_display_values"),
        ["92%", "93%", "94%", "95%"],
    )

    classification = policy.get("value_classification", {})
    expected_classification = {
        "value_type": "configured_display_value",
        "owner_approved": True,
        "measured_metric": False,
        "pending_business_metric": False,
        "data_calculation_result": False,
    }
    for key, expected in expected_classification.items():
        require_equal(f"value_classification.{key}", classification.get(key), expected)

    selection = policy.get("selection_policy", {})
    expected_selection = {
        "selection_method": "random_choice",
        "current_period_state_precedence": "reuse_saved_value_without_reselection",
        "previous_period_value_source": "same_local_configured_display_value_state",
        "previous_period_report_body_parsing_allowed": False,
    }
    for key, expected in expected_selection.items():
        require_equal(f"selection_policy.{key}", selection.get(key), expected)
    previous_exists = selection.get("when_previous_period_value_exists", {})
    require_equal(
        "selection_policy.when_previous_period_value_exists.candidate_values",
        previous_exists.get("candidate_values"),
        "allowed_display_values_excluding_exact_previous_period_value",
    )
    require_equal(
        "selection_policy.when_previous_period_value_exists.previous_period_repeat_allowed",
        previous_exists.get("previous_period_repeat_allowed"),
        False,
    )
    previous_missing = selection.get("when_previous_period_value_does_not_exist", {})
    require_equal(
        "selection_policy.when_previous_period_value_does_not_exist.candidate_values",
        previous_missing.get("candidate_values"),
        "all_allowed_display_values",
    )

    responsibility = policy.get("execution_responsibility", {})
    require_equal(
        "execution_responsibility.responsible_component",
        responsibility.get("responsible_component"),
        "workflow_orchestrator",
    )
    require_equal(
        "execution_responsibility.responsibilities",
        responsibility.get("responsibilities"),
        [
            "select_first_value_for_reporting_period",
            "save_selected_value",
            "read_saved_value_for_same_period_rerun",
            "read_previous_period_value_from_same_local_state",
        ],
    )
    require_equal(
        "execution_responsibility.output_assembly_responsibility",
        responsibility.get("output_assembly_responsibility"),
        "place_resolved_value_only",
    )

    persistence = policy.get("persistence_policy", {})
    expected_persistence = {
        "persistence_required": True,
        "persistence_class": "configured_display_value_selection_state",
        "persistence_scope": "local_workflow_state",
        "persistence_key_fields": ["policy_id", "workflow_id", "reporting_period_id"],
        "first_selection_action": "save_selected_value_before_output_assembly",
        "same_period_rerun_action": "read_and_reuse_saved_value",
        "same_period_reselection_allowed": False,
        "previous_period_lookup_uses_same_state": True,
        "metric_result_store": False,
    }
    for key, expected in expected_persistence.items():
        require_equal(f"persistence_policy.{key}", persistence.get(key), expected)

    boundaries = policy.get("dependency_boundaries", {})
    for key in (
        "metric_library_registration",
        "result_contract_creation",
        "metric_result_store_write",
        "dataset_dependency",
        "pipeline_dependency",
    ):
        require_equal(f"dependency_boundaries.{key}", boundaries.get(key), False)

    assembly = policy.get("output_assembly_boundary", {})
    require_equal(
        "output_assembly_boundary.resolved_value_may_be_placed_in_bound_slot",
        assembly.get("resolved_value_may_be_placed_in_bound_slot"),
        True,
    )
    for key in (
        "configured_value_resolution_is_metric_calculation",
        "may_calculate_other_business_metrics",
        "may_supply_input_to_other_business_metrics",
    ):
        require_equal(f"output_assembly_boundary.{key}", assembly.get(key), False)

    output_file = "phase1_5/assets/output_mappings/OM_WEEKLY_BUSINESS_REPORT_V1.yaml"
    output_mapping = documents.get(output_file, {})
    matching_slots: list[dict[str, Any]] = []
    if isinstance(output_mapping, dict):
        for section in output_mapping.get("section_order", []):
            if not isinstance(section, dict):
                continue
            for entry in section.get("mapping_entries", []):
                if (
                    isinstance(entry, dict)
                    and entry.get("output_slot_id")
                    == "SLOT_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE"
                ):
                    matching_slots.append(entry)
    if len(matching_slots) != 1:
        errors.append(f"{output_file}: expected exactly one configured display value slot")
    else:
        slot = matching_slots[0]
        reference = slot.get("configured_display_value_policy_reference", {})
        expected_slot_values = {
            "display_order": 2,
            "display_label": "订单整体曝光完成率",
            "source_type": "Configured display value",
            "validated_result_contract_ids": [],
            "metric_library_registration": False,
            "pipeline_dependency": False,
            "calculation_in_output_mapping": False,
        }
        for key, expected in expected_slot_values.items():
            if slot.get(key) != expected:
                errors.append(f"{output_file}:{key}: expected {expected!r}")
        expected_reference = {
            "policy_id": policy_id,
            "resolution_role": "display_value_selection_only",
            "same_period_saved_value_must_be_reused": True,
            "output_assembly_may_calculate_other_business_metrics": False,
        }
        for key, expected in expected_reference.items():
            if reference.get(key) != expected:
                errors.append(
                    f"{output_file}:configured_display_value_policy_reference.{key}: "
                    f"expected {expected!r}"
                )

    registry_file = "phase1_5/assets/pipelines/pipeline_registry.yaml"
    registry = documents.get(registry_file, {})
    configured_entries = []
    if isinstance(registry, dict):
        configured_entries = [
            item
            for item in registry.get("initialization_scope", {}).get(
                "deferred_to_output_mapping", []
            )
            if isinstance(item, dict)
            and item.get("display_item") == "订单整体曝光完成率"
        ]
    if len(configured_entries) != 1:
        errors.append(f"{registry_file}: expected exactly one configured display summary")
    else:
        entry = configured_entries[0]
        reference = entry.get("configured_display_value_policy_reference", {})
        require_registry_values = {
            "asset_classification": "Configured display value",
            "dataset_dependency": "none",
            "pipeline_dependency": "none",
            "metric_library_registration": False,
            "metrics_store_write": False,
            "allowed_display_range": "92%-95%",
            "allowed_values": ["92%", "93%", "94%", "95%"],
            "decimal_values_allowed": False,
            "consecutive_week_repeat_allowed": False,
            "prior_display_value_source": "Same local configured display value state for the previous reporting period",
            "previous_report_body_parsing_allowed": False,
        }
        for key, expected in require_registry_values.items():
            if entry.get(key) != expected:
                errors.append(f"{registry_file}:{key}: expected {expected!r}")
        if reference.get("policy_id") != policy_id:
            errors.append(
                f"{registry_file}:configured_display_value_policy_reference.policy_id: "
                f"expected {policy_id!r}"
            )
        if reference.get("authority_role") != "canonical_selection_and_persistence_policy":
            errors.append(
                f"{registry_file}:configured_display_value_policy_reference.authority_role: "
                "expected canonical_selection_and_persistence_policy"
            )

    prohibited_config_types = {
        "dataset_inventory",
        "metric_library",
        "result_contract",
    }

    def contains_policy_id(value: Any) -> bool:
        if value == policy_id:
            return True
        if isinstance(value, dict):
            return any(contains_policy_id(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_policy_id(item) for item in value)
        return False

    for file, document in documents.items():
        if not isinstance(document, dict) or not contains_policy_id(document):
            continue
        if document.get("config_type") in prohibited_config_types or document.get(
            "asset_type"
        ) in {"Metric Library", "Metric Result Store"}:
            errors.append(
                f"{file}: configured display value policy must not enter Metric, "
                "Dataset, Result Contract, or Metric Result Store assets"
            )

    baseline_file = "phase1_5/assets/readiness/implementation_baseline.yaml"
    baseline = documents.get(baseline_file, {})
    frozen_versions = (
        baseline.get("frozen_asset_versions", {})
        if isinstance(baseline, dict)
        else {}
    )
    expected_baseline_values = {
        "configured_display_value_policy_count": 1,
        "order_overall_impression_completion_rate_display_policy_version": "1.0.0",
    }
    for key, expected in expected_baseline_values.items():
        if frozen_versions.get(key) != expected:
            errors.append(f"{baseline_file}:frozen_asset_versions.{key}: expected {expected!r}")

    return 1


def validate_mvp_acceptance_semantics(
    documents: dict[str, Any], errors: list[str]
) -> int:
    """Validate the accepted MVP runtime boundaries without adding asset layers."""
    registry_file = "phase1_5/assets/pipelines/pipeline_registry.yaml"
    registry = documents.get(registry_file, {})
    pipelines = {
        item.get("pipeline_id"): item
        for item in registry.get("pipelines", [])
        if isinstance(item, dict) and isinstance(item.get("pipeline_id"), str)
    } if isinstance(registry, dict) else {}

    customer_id = "PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"
    sell_through_id = "PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY"
    policy_id = "POLICY_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS_V1"
    product_routing_asset_id = "BR_APOLLO_PRODUCT_FILTER_MAPPING"
    customer = pipelines.get(customer_id, {})
    sell_through = pipelines.get(sell_through_id, {})

    if customer.get("analysis_only_pipeline") is not True:
        errors.append(f"{registry_file}:{customer_id}: analysis_only_pipeline must be true")
    execution = customer.get("execution", {})
    stages = execution.get("stages", [])
    if "METRIC_CALCULATION" in stages:
        errors.append(f"{registry_file}:{customer_id}: METRIC_CALCULATION is prohibited")
    if "BUSINESS_RULE_PROCESSING" not in stages:
        errors.append(f"{registry_file}:{customer_id}: BUSINESS_RULE_PROCESSING is required")
    if execution.get("metric_variant_ids") != []:
        errors.append(f"{registry_file}:{customer_id}: metric_variant_ids must remain empty")
    if execution.get("business_rule_processing_policy_id") != policy_id:
        errors.append(
            f"{registry_file}:{customer_id}: business-rule processing must use {policy_id}"
        )
    expected_responsibilities = {
        "exact_customer_id_cross_period_matching",
        "impression_change_derivation",
        "materiality_filtering",
        "scenario_ranking_measure_derivation",
        "sorting",
        "customer_ranking",
    }
    if set(execution.get("business_rule_processing_responsibilities", [])) != expected_responsibilities:
        errors.append(f"{registry_file}:{customer_id}: incomplete business-rule responsibilities")

    policy_file = (
        "phase1_5/assets/pipelines/"
        "PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS_policy_v1.yaml"
    )
    policy = documents.get(policy_file, {})
    allowed_source_types = policy.get("output_boundary", {}).get(
        "field_source_types_allowed", []
    )
    if allowed_source_types != [
        "upstream_contract_field",
        "standardized_field",
        "policy_derived_field",
    ]:
        errors.append(f"{policy_file}: field_source_types_allowed must exclude metric_variant")
    if policy.get("trigger", {}).get("trigger_policy_id") != policy_id:
        errors.append(f"{policy_file}: trigger_policy_id must use {policy_id}")

    customer_dependencies = {
        item.get("dataset_id"): item
        for item in customer.get("dataset_dependencies", [])
        if isinstance(item, dict)
    }
    customer_dataset = customer_dependencies.get(
        "DS_AD_PRODUCT_CUSTOMER_DELIVERY_CHANGE_ANALYSIS", {}
    )
    if customer_dataset.get("required_when_triggered") is not True:
        errors.append(
            f"{registry_file}:{customer_id}: customer Dataset must be required_when_triggered"
        )
    if "required" in customer_dataset:
        errors.append(
            f"{registry_file}:{customer_id}: unconditional Dataset required flag is prohibited"
        )
    if customer_dataset.get("business_rule_processing_policy_id") != policy_id:
        errors.append(
            f"{registry_file}:{customer_id}: Dataset processing policy must use {policy_id}"
        )

    readiness_file = "phase1_5/assets/datasets/dataset_readiness_matrix.yaml"
    readiness = documents.get(readiness_file, {})
    readiness_record = next(
        (
            item
            for item in readiness.get("dataset_readiness", [])
            if isinstance(item, dict)
            and item.get("dataset_id")
            == "DS_AD_PRODUCT_CUSTOMER_DELIVERY_CHANGE_ANALYSIS"
        ),
        {},
    )
    if not readiness_record:
        readiness_record = next(
            (
                item
                for item in readiness.get("readiness_records", [])
                if isinstance(item, dict)
                and item.get("dataset_id")
                == "DS_AD_PRODUCT_CUSTOMER_DELIVERY_CHANGE_ANALYSIS"
            ),
            {},
        )
    usage = readiness_record.get("workflow_usage", {})
    if usage.get("usage_role") != "conditional_required_when_triggered" or usage.get(
        "triggered_behavior"
    ) != "Dataset instance is required for the triggered product analysis":
        errors.append(f"{readiness_file}: customer Dataset trigger requiredness is inconsistent")

    trigger = customer.get("trigger_contract", {})
    if trigger.get("trigger_policy_id") != policy_id:
        errors.append(f"{registry_file}:{customer_id}: trigger_policy_id must use {policy_id}")
    runtime_routing = trigger.get("upstream_trigger_field_routing", {})
    expected_routes = {
        (
            "RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY",
            "patch_brand_sell_through_wow_change_pp",
        ),
        (
            "RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY",
            "non_patch_product_brand_sell_through_wow_change_pp",
        ),
        (
            "RC_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY",
            "sell_through_wow_change_pp",
        ),
    }
    actual_routes = {
        (route.get("result_contract_id"), route.get("result_field_id"))
        for route in runtime_routing.get("routes", [])
        if isinstance(route, dict) and isinstance(route.get("route_condition"), str)
        and route.get("route_condition", "").strip()
    }
    if actual_routes != expected_routes or len(runtime_routing.get("routes", [])) != 3:
        errors.append(f"{registry_file}:{customer_id}: runtime trigger routes are not exact")
    if any(contract_id == "RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS" for contract_id, _ in actual_routes):
        errors.append(f"{registry_file}:{customer_id}: output Contract cannot be runtime trigger input")
    if runtime_routing.get("result_contract_lineage_summary_is_runtime_input") is not False:
        errors.append(f"{registry_file}:{customer_id}: Result Contract routes must be lineage-only")

    if sell_through.get("result_contract_dependency_routing", {}).get(
        "product_routing_asset_id"
    ) != product_routing_asset_id:
        errors.append(
            f"{registry_file}:{sell_through_id}: product_routing_asset_id must use "
            f"{product_routing_asset_id}"
        )
    advertising_routing = registry.get("constraints", {}).get(
        "advertising_product_inventory_source_routing", {}
    )
    for route_name in ("regular_patch_products", "all_products_except_regular_patch"):
        if advertising_routing.get(route_name, {}).get(
            "product_routing_asset_id"
        ) != product_routing_asset_id:
            errors.append(
                f"{registry_file}:constraints.{route_name}: product routing must use "
                f"{product_routing_asset_id}"
            )

    registered_pipeline_ids = [
        item.get("pipeline_id")
        for item in registry.get("pipelines", [])
        if isinstance(item, dict) and isinstance(item.get("pipeline_id"), str)
    ]
    mvp_execution = registry.get("constraints", {}).get(
        "mvp_pipeline_execution", {}
    )
    if mvp_execution.get("pipeline_ids") != registered_pipeline_ids or mvp_execution.get(
        "first_phase_scheduling_mode"
    ) != "sequential" or mvp_execution.get("registered_pipeline_order_required") is not True or mvp_execution.get(
        "failed_run_recovery"
    ) != "rerun_pipeline_from_start" or mvp_execution.get(
        "parallel_scheduler_required"
    ) is not False or mvp_execution.get("stage_checkpointing_required") is not False or mvp_execution.get(
        "resume_from_failed_stage_required"
    ) is not False:
        errors.append(f"{registry_file}: all initial MVP Pipeline execution semantics are incomplete")

    for pipeline_id in registered_pipeline_ids:
        pipeline = pipelines.get(pipeline_id, {})
        pipeline_trigger = pipeline.get("trigger_contract", {})
        pipeline_execution = pipeline.get("execution", {})
        for container_name, container in (
            ("execution", pipeline_execution),
            ("trigger_contract", pipeline_trigger),
        ):
            if container.get("multiple_product_runs_may_execute_in_parallel") is True:
                errors.append(f"{registry_file}:{pipeline_id}.{container_name}: parallel execution is prohibited")
        recovery = pipeline.get("recovery", {})
        expected_recovery = {
            "recovery_mode": "rerun_pipeline_from_start",
            "resume_from_failed_stage": False,
            "restart_from_pipeline_start": True,
            "restart_must_be_idempotent": True,
            "stage_checkpoint_persistence_required": False,
        }
        for key, expected in expected_recovery.items():
            if recovery.get(key) != expected:
                errors.append(f"{registry_file}:{pipeline_id}.recovery.{key}: expected {expected!r}")

    def contains_text(value: Any, needle: str) -> bool:
        if isinstance(value, str):
            return needle.lower() in value.lower()
        if isinstance(value, dict):
            return any(contains_text(item, needle) for item in value.values())
        if isinstance(value, list):
            return any(contains_text(item, needle) for item in value)
        return False

    if contains_text(registry, "eligible for parallel execution"):
        errors.append(f"{registry_file}: stale parallel-execution eligibility text is prohibited")

    workflow_file = "phase1_5/workflows/weekly_business_report/WORKFLOW_v2.md"
    workflow_text = (REPOSITORY_ROOT / workflow_file).read_text(encoding="utf-8")
    required_workflow_terms = (
        "12 个 Pipeline 全部按 Registry 登记顺序 `sequential` 执行",
        "恢复模式统一为 `rerun_pipeline_from_start`",
        "不支持 `resume_from_failed_stage`",
    )
    if any(term not in workflow_text for term in required_workflow_terms) or (
        "eligible for parallel execution" in workflow_text.lower()
    ):
        errors.append(f"{workflow_file}: MVP sequential and full-rerun semantics are not synchronized")

    customer_contract_file = (
        "phase1_5/assets/result_contracts/"
        "RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS.yaml"
    )
    customer_contract = documents.get(customer_contract_file, {})
    if customer_contract.get("validation", {}).get("metric_variant_binding_check") is not False:
        errors.append(f"{customer_contract_file}: metric_variant_binding_check must be false")
    lineage = customer_contract.get("lineage", {})
    if lineage.get("metric_variant_versions") != [] or lineage.get(
        "metric_variant_versions_applicability"
    ) != "not_applicable" or "metric_variant_versions" in lineage.get(
        "required_instance_fields", []
    ):
        errors.append(f"{customer_contract_file}: metric_variant_versions must be empty and not applicable")
    trigger_field = None
    for record_set in customer_contract.get("record_sets", []):
        for field in record_set.get("context_fields", []):
            if field.get("field_id") == "trigger_sell_through_wow_change_pp":
                trigger_field = field
    if not isinstance(trigger_field, dict) or trigger_field.get(
        "source_contract_routes_role"
    ) != "lineage_summary_only":
        errors.append(f"{customer_contract_file}: source_contract_routes must be lineage summary only")

    expected_omission = {
        "trigger_not_met": ("normal_omission", False),
        "qualified_customer_zero_rows": ("normal_empty_record_set", False),
        "raw_query_zero_rows": ("source_or_routing_exception", True),
        "query_failure": ("source_or_routing_exception", True),
        "invalid_product_mapping": ("source_or_routing_exception", True),
    }
    for container_file, container in (
        (policy_file, policy.get("failure_handling", {})),
        (
            customer_contract_file,
            customer_contract.get("generation_policy", {}).get("omission_semantics", {}),
        ),
        (
            registry_file,
            customer.get("workflow_bindings", [{}])[0].get("failure_handling", {}),
        ),
    ):
        for reason, (classification, notify_owner) in expected_omission.items():
            semantics = container.get(reason, {})
            if semantics.get("classification") != classification or semantics.get(
                "notify_owner"
            ) is not notify_owner:
                errors.append(f"{container_file}: omission semantics invalid for {reason}")

    generation = customer_contract.get("generation_policy", {})
    if "qualified_customer_zero_rows" in generation.get("omit_contract_when", []) or generation.get(
        "generate_when_valid_product_context_exists"
    ) is not True or generation.get("customer_records_required_for_generation") is not False:
        errors.append(f"{customer_contract_file}: qualified zero rows must retain the existing context Contract")
    customer_record_set = next(
        (
            item
            for item in customer_contract.get("record_sets", [])
            if item.get("record_set_id") == "customer_delivery_changes"
        ),
        {},
    )
    zero_semantics = customer_record_set.get("zero_record_semantics", {})
    if customer_record_set.get("context_field_materialization") != "record_set_header" or customer_record_set.get(
        "context_fields_exist_independently_of_customer_records"
    ) is not True or zero_semantics.get("allowed_when") != "qualified_customer_zero_rows" or zero_semantics.get(
        "customer_record_count"
    ) != 0 or zero_semantics.get("fabricated_customer_record_allowed") is not False:
        errors.append(f"{customer_contract_file}: empty customer record-set context semantics are incomplete")
    customer_outputs = customer.get("outputs", {})
    if "qualified_customer_zero_rows" in customer_outputs.get(
        "no_placeholder_result_contract_when", []
    ) or customer_outputs.get("qualified_customer_zero_rows_result", {}).get(
        "result_contract_generated"
    ) is not True:
        errors.append(f"{registry_file}:{customer_id}: qualified zero-row output must generate the existing Contract")
    policy_generation = policy.get("failure_handling", {}).get("result_contract_generation", {})
    if "qualified_customer_zero_rows" in policy_generation.get("omit_when", []) or policy_generation.get(
        "generate_product_context_with_empty_customer_record_set_when"
    ) != "qualified_customer_zero_rows":
        errors.append(f"{policy_file}: qualified zero rows cannot omit the existing Contract")

    non_patch_file = (
        "phase1_5/assets/result_contracts/RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY.yaml"
    )
    non_patch = documents.get(non_patch_file, {})
    brand_field = next(
        (
            field
            for field in non_patch.get("contract_fields", [])
            if field.get("field_id") == "brand_moment_available_inventory_count"
        ),
        {},
    )
    validation = non_patch.get("validation", {})
    if "brand_moment_available_inventory_count" in validation.get("required_fields", []):
        errors.append(f"{non_patch_file}: Brand Moment field must not be globally required")
    conditional_ids = {
        field_id
        for item in validation.get("conditional_fields", [])
        if isinstance(item, dict)
        for field_id in item.get("field_ids", [])
    }
    if "brand_moment_available_inventory_count" not in conditional_ids:
        errors.append(f"{non_patch_file}: Brand Moment field must be conditional")
    if brand_field.get("non_brand_moment_instance_value_status") != "not_applicable":
        errors.append(f"{non_patch_file}: non-Brand-Moment value status must be not_applicable")

    dau_file = "phase1_5/assets/result_contracts/RC_USER_ANALYTICS_PLATFORM_DAU_WEEKLY.yaml"
    dau = documents.get(dau_file, {})
    daily = next(
        (item for item in dau.get("record_sets", []) if item.get("record_set_id") == "daily_activity"),
        {},
    )
    if daily.get("record_grain") != ["activity_date", "platform_scope"]:
        errors.append(f"{dau_file}: DAU grain must use platform_scope")
    dimension_ids = {
        item.get("dimension_id") for item in daily.get("record_dimensions", [])
        if isinstance(item, dict)
    }
    if dimension_ids != {"platform_scope"} or daily.get("fixed_dimensions") != {
        "platform_scope": "full_platform"
    }:
        errors.append(f"{dau_file}: DAU fixed dimension must be platform_scope=full_platform")

    dau_pipeline_id = "PL_USER_ANALYTICS_PLATFORM_DAU_WEEKLY"
    dau_pipeline = pipelines.get(dau_pipeline_id, {})
    dau_workflow_binding = next(
        (
            item
            for item in dau_pipeline.get("workflow_bindings", [])
            if item.get("workflow_id") == "WF_WEEKLY_BUSINESS_REPORT"
        ),
        {},
    )
    dau_dataset = next(
        (
            item
            for item in dau_pipeline.get("dataset_dependencies", [])
            if item.get("dataset_id") == "DS_NOVABI_PLATFORM_DAU"
        ),
        {},
    )
    if dau_workflow_binding.get("required_or_optional") != "optional":
        errors.append(f"{registry_file}:{dau_pipeline_id}: Workflow binding must remain optional")
    if dau_dataset.get("required") is not True or dau_dataset.get(
        "required_scope"
    ) != "pipeline_execution" or dau_dataset.get(
        "workflow_pipeline_required_or_optional_remains"
    ) != "optional":
        errors.append(f"{registry_file}:{dau_pipeline_id}: DAU Dataset must be required at Pipeline execution")
    dau_readiness = next(
        (
            item
            for item in readiness.get("dataset_readiness", [])
            if item.get("dataset_id") == "DS_NOVABI_PLATFORM_DAU"
        ),
        {},
    )
    dau_usage = dau_readiness.get("workflow_usage", {})
    if dau_usage.get("usage_role") != "optional" or dau_usage.get(
        "pipeline_execution_input_required"
    ) is not True:
        errors.append(f"{readiness_file}: DAU Workflow/Pipeline requiredness is inconsistent")

    store_file = "phase1_5/assets/metric_stores/metric_result_store_registry.yaml"
    stores = documents.get(store_file, {})
    strategy = stores.get("mvp_physical_store_adapter_strategy", {})
    expected_store_ids = {
        "STORE_WEEKLY_INVENTORY_HISTORICAL",
        "STORE_WEEKLY_USER_ANALYTICS_HISTORICAL",
        "STORE_WEEKLY_ADVERTISING_HISTORICAL",
    }
    if strategy.get("shared_generic_local_adapter_required") is not True or strategy.get(
        "physical_store_strategy"
    ) != "shared_local_sqlite" or strategy.get("provider") != "SQLite" or strategy.get(
        "shared_table_name"
    ) != "metric_results" or strategy.get("logical_store_discriminator_fields") != [
        "store_id", "store_asset_id"
    ] or set(
        strategy.get("applies_to_logical_store_ids", [])
    ) != expected_store_ids or strategy.get(
        "separate_storage_engine_per_logical_store_required"
    ) is not False or any(
        strategy.get(key) is not False
        for key in (
            "physical_store_file_created_by_this_configuration_change",
            "multi_provider_architecture_required",
            "plugin_store_architecture_required",
            "complex_orm_required",
        )
    ):
        errors.append(f"{store_file}: MVP shared local SQLite strategy is incomplete")
    physical_schema = strategy.get("physical_schema", {})
    expected_schema_columns = [
        "result_id",
        "workflow_id",
        "workflow_run_id",
        "pipeline_id",
        "pipeline_run_id",
        "store_id",
        "store_asset_id",
        "metric_variant_id",
        "metric_variant_version",
        "reporting_period_start",
        "reporting_period_end",
        "dimensions_json",
        "value_numeric",
        "numeric_semantics",
        "unit",
        "integer_only",
        "precision",
        "validation_status",
        "generated_at",
    ]
    actual_schema_columns = [
        item.get("column_name")
        for item in physical_schema.get("columns", [])
        if isinstance(item, dict)
    ]
    expected_unique_key = [
        "store_id",
        "store_asset_id",
        "metric_variant_id",
        "metric_variant_version",
        "reporting_period_start",
        "reporting_period_end",
        "dimensions_json",
    ]
    dimension_representation = physical_schema.get("dimension_representation", {})
    idempotent_key = physical_schema.get("idempotent_unique_key", {})
    if physical_schema.get("table_name") != "metric_results" or actual_schema_columns != expected_schema_columns or dimension_representation.get(
        "format"
    ) != "canonical_json_object" or dimension_representation.get("key_order") != "lexicographic" or dimension_representation.get(
        "no_dimension_value"
    ) != "{}" or idempotent_key.get("columns") != expected_unique_key or idempotent_key.get(
        "same_key_same_value_action"
    ) != "no_op_success" or idempotent_key.get("same_key_different_value_action") != "reject_write_notify_owner" or idempotent_key.get(
        "automatic_overwrite_allowed"
    ) is not False:
        errors.append(f"{store_file}: shared SQLite minimum schema or idempotent key is incomplete")
    for store in stores.get("metric_result_stores", []):
        if store.get("store_id") in expected_store_ids and (
            store.get("storage_type") != "Shared local SQLite metric_results table"
            or store.get("storage_location")
            != "${SHARED_WEEKLY_METRIC_STORE_SQLITE_LOCAL_ONLY}"
            or store.get("physical_table_name") != "metric_results"
            or store.get("discriminator_fields") != ["store_id", "store_asset_id"]
        ):
            errors.append(f"{store_file}:{store.get('store_id')}: must use shared SQLite table")
    revenue_store = next(
        (
            store
            for store in stores.get("metric_result_stores", [])
            if store.get("store_id") == "STORE_WEEKLY_REVENUE_HISTORICAL"
        ),
        {},
    )
    if revenue_store.get("storage_type") != "Excel Workbooks in Fixed Directory":
        errors.append(f"{store_file}: Revenue must retain its Excel historical Store")

    store_readiness_file = (
        "phase1_5/assets/metric_stores/metric_result_store_readiness_matrix.yaml"
    )
    store_readiness_strategy = documents.get(store_readiness_file, {}).get(
        "mvp_physical_store_adapter_strategy", {}
    )
    for key, expected in {
        "physical_store_strategy": "shared_local_sqlite",
        "provider": "SQLite",
        "physical_store_file_created_by_this_configuration_change": False,
        "shared_table_name": "metric_results",
        "logical_store_discriminator_fields": ["store_id", "store_asset_id"],
        "separate_storage_engine_per_logical_store_required": False,
        "multi_provider_architecture_required": False,
        "plugin_store_architecture_required": False,
        "complex_orm_required": False,
        "physical_schema_contract_status": "confirmed",
        "dimension_representation_status": "confirmed_canonical_json",
        "idempotent_unique_key_status": "confirmed",
    }.items():
        if store_readiness_strategy.get(key) != expected:
            errors.append(f"{store_readiness_file}:mvp_physical_store_adapter_strategy.{key}: expected {expected!r}")
    store_readiness = documents.get(store_readiness_file, {})
    if store_readiness.get("gate_conclusion", {}).get(
        "code_implementation_start"
    ) != "wait_for_explicit_owner_approval":
        errors.append(f"{store_readiness_file}: code implementation must wait for explicit Owner approval")
    for record in store_readiness.get("readiness_records", []):
        if record.get("store_id") in expected_store_ids and (
            record.get("local_storage_contract_confirmed") is not True
            or record.get("local_physical_store_initialized") is not False
        ):
            errors.append(f"{store_readiness_file}:{record.get('store_id')}: schema readiness and runtime initialization state conflict")

    dataset_file = "phase1_5/assets/datasets/dataset_inventory.yaml"
    dataset_document = documents.get(dataset_file, {})
    customer_query = next(
        (
            item
            for item in dataset_document.get("query_assets", [])
            if item.get("query_asset_id")
            == "QRY_APOLLO_PRODUCT_CUSTOMER_DELIVERY_CHANGE_ANALYSIS"
        ),
        {},
    )
    product_route = next(
        (
            item
            for item in customer_query.get("external_dependency_candidates", [])
            if item.get("product_routing_asset_id") == product_routing_asset_id
        ),
        {},
    )
    if product_route.get("asset_type") != "local-only External Asset":
        errors.append(f"{dataset_file}: product routing must reference the local-only External Asset")

    return 12


def validate_customer_analysis_narrative_mapping(
    documents: dict[str, Any], errors: list[str]
) -> int:
    """Require the fixed Inventory narrative mapping and prohibit dynamic tables."""
    mapping_file = "phase1_5/assets/output_mappings/OM_WEEKLY_BUSINESS_REPORT_V1.yaml"
    mapping = documents.get(mapping_file, {})
    sections = {
        section.get("section_id"): section
        for section in mapping.get("section_order", [])
        if isinstance(section, dict)
    } if isinstance(mapping, dict) else {}
    inventory = sections.get("INVENTORY_AND_SELL_THROUGH", {})
    revenue = sections.get("REVENUE", {})
    narrative = inventory.get("customer_analysis_narrative_mapping", {})

    if any(
        isinstance(entry, dict)
        and entry.get("output_slot_id") == "SLOT_CONDITIONAL_PRODUCT_CUSTOMER_ANALYSIS"
        for entry in revenue.get("mapping_entries", [])
    ):
        errors.append(f"{mapping_file}: Revenue dynamic customer-analysis slot is prohibited")

    if narrative.get("implementation_method") != "fixed_narrative_template_rendering":
        errors.append(f"{mapping_file}: customer analysis must use fixed_narrative_template_rendering")
    if narrative.get("validated_result_contract_id") != (
        "RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"
    ) or narrative.get("record_set_id") != "customer_delivery_changes":
        errors.append(f"{mapping_file}: customer narrative Result Contract binding is invalid")
    if narrative.get("business_rule_processing_policy_id") != (
        "POLICY_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS_V1"
    ):
        errors.append(f"{mapping_file}: customer narrative processing Policy reference is invalid")

    if narrative.get("rendering_sequence") != [
        "render_inventory_and_sell_through_overview",
        "append_triggered_product_sentences",
    ]:
        errors.append(f"{mapping_file}: inventory overview must precede abnormal-product sentences")
    trigger_semantics = narrative.get("precomputed_trigger_and_scenario", {})
    expected_trigger_semantics = {
        "trigger_decision_consumed_from_policy": True,
        "scenario_consumed_from_result_contract": True,
        "output_mapping_may_evaluate_trigger_threshold": False,
        "output_mapping_may_derive_scenario": False,
    }
    for key, expected in expected_trigger_semantics.items():
        if trigger_semantics.get(key) != expected:
            errors.append(f"{mapping_file}:precomputed_trigger_and_scenario.{key}: expected {expected!r}")

    expected_positions = [
        {
            "output_field_id": "patch_and_similar_resource_commentary",
            "resource_module": "patch_and_similar_resources",
        },
        {
            "output_field_id": "page_resource_commentary",
            "resource_module": "page_resources",
        },
    ]
    if narrative.get("target_output_positions") != expected_positions:
        errors.append(f"{mapping_file}: customer narrative output positions must remain fixed")

    route = narrative.get("product_module_and_fixed_order_resolution", {})
    if route.get("product_routing_asset_id") != "BR_APOLLO_PRODUCT_FILTER_MAPPING" or route.get(
        "product_name_inference_allowed"
    ) is not False or route.get("fixed_product_order_required") is not True:
        errors.append(f"{mapping_file}: product module and order must use approved mapping")

    expected_fields = [
        "target_ad_product_name",
        "analysis_scenario",
        "trigger_sell_through_wow_change_pp",
        "customer_name",
        "current_period_impression_count",
        "impression_change_count",
        "customer_rank",
        "applied_output_limit",
    ]
    if narrative.get("fixed_consumed_result_fields") != expected_fields:
        errors.append(f"{mapping_file}: customer narrative must consume exactly eight fixed fields")

    expected_field_scope = {
        "record_set_header_context_fields": [
            "target_ad_product_name",
            "analysis_scenario",
            "trigger_sell_through_wow_change_pp",
            "applied_output_limit",
        ],
        "customer_record_fields": [
            "customer_name",
            "current_period_impression_count",
            "impression_change_count",
            "customer_rank",
        ],
    }
    if narrative.get("result_field_scope") != expected_field_scope:
        errors.append(f"{mapping_file}: fixed customer narrative field scopes are invalid")
    consumption = narrative.get("contract_consumption_by_outcome", {})
    qualified_zero = consumption.get("qualified_customer_zero_rows", {})
    omitted_consumption = consumption.get("omitted_contract_outcomes", {})
    if qualified_zero.get("result_contract_required") is not True or qualified_zero.get(
        "consume_record_set_header_context"
    ) is not True or qualified_zero.get("consume_customer_records") is not False or qualified_zero.get(
        "template_mode"
    ) != "product_change_only":
        errors.append(f"{mapping_file}: qualified zero rows must consume only the existing Contract context")
    if omitted_consumption.get("reasons") != [
        "trigger_not_met",
        "raw_query_zero_rows",
        "query_failure",
        "invalid_product_mapping",
    ] or omitted_consumption.get("output_mapping_reads_omitted_contract") is not False:
        errors.append(f"{mapping_file}: Output Mapping cannot read an omitted customer Contract")

    contract_file = (
        "phase1_5/assets/result_contracts/"
        "RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS.yaml"
    )
    contract = documents.get(contract_file, {})
    contract_fields = {
        field.get("field_id")
        for record_set in contract.get("record_sets", [])
        if isinstance(record_set, dict)
        for group in ("context_fields", "record_fields")
        for field in record_set.get(group, [])
        if isinstance(field, dict)
    }
    if not set(expected_fields).issubset(contract_fields):
        errors.append(f"{mapping_file}: fixed customer narrative fields do not resolve")

    selection = narrative.get("precomputed_customer_selection", {})
    expected_selection = {
        "inclusion_condition": "customer_rank <= applied_output_limit",
        "output_mapping_may_resort": False,
        "output_mapping_may_recalculate_impression_change": False,
        "output_mapping_may_reapply_materiality_threshold": False,
        "output_mapping_may_change_applied_output_limit": False,
    }
    for key, expected in expected_selection.items():
        if selection.get(key) != expected:
            errors.append(f"{mapping_file}:precomputed_customer_selection.{key}: expected {expected!r}")

    templates = narrative.get("fixed_templates", {})
    expected_templates = {
        "positive_product_template": "{product_name}售卖率环比上涨{absolute_change_pp}pp，主要投放客户为{ranked_customer_text}。",
        "negative_product_template": "{product_name}售卖率环比下降{absolute_change_pp}pp，主要减投客户为{ranked_customer_text}。",
        "positive_product_change_only_template": "{product_name}售卖率环比上涨{absolute_change_pp}pp。",
        "negative_product_change_only_template": "{product_name}售卖率环比下降{absolute_change_pp}pp。",
        "positive_customer_template": "{customer_name}（本周曝光{current_period_impression_count}）",
        "negative_customer_template": "{customer_name}（较上周减少{absolute_impression_change_count}曝光）",
        "template_selection_source_field": "analysis_scenario",
        "scenario_derivation_in_output_mapping_allowed": False,
        "absolute_change_pp_source_field": "trigger_sell_through_wow_change_pp",
        "absolute_impression_change_count_source_field": "impression_change_count",
        "absolute_value_handling": "presentation_magnitude_format_only",
        "absolute_value_handling_is_metric_calculation": False,
    }
    for key, expected in expected_templates.items():
        if templates.get(key) != expected:
            errors.append(f"{mapping_file}:fixed_templates.{key}: expected {expected!r}")

    concatenation = narrative.get("concatenation", {})
    if concatenation.get("unit") != "one_sentence_per_triggered_product" or concatenation.get(
        "product_order_source"
    ) != "approved_mapping_fixed_order" or concatenation.get(
        "dynamic_table_generation_allowed"
    ) is not False:
        errors.append(f"{mapping_file}: abnormal products must use fixed-order sentence concatenation")

    omissions = narrative.get("omission_and_empty_selection", {})
    expected_omissions = {
        "trigger_not_met": ("normal_omission", False),
        "qualified_customer_zero_rows": ("normal_empty_record_set", False),
        "raw_query_zero_rows": ("source_or_routing_exception", True),
        "query_failure": ("source_or_routing_exception", True),
        "invalid_product_mapping": ("source_or_routing_exception", True),
    }
    if set(omissions) != set(expected_omissions):
        errors.append(f"{mapping_file}: customer narrative omission semantics are incomplete")
    for reason, (classification, notify_owner) in expected_omissions.items():
        semantics = omissions.get(reason, {})
        if semantics.get("classification") != classification or semantics.get(
            "notify_owner"
        ) is not notify_owner or semantics.get("fail_workflow") is not False:
            errors.append(f"{mapping_file}: omission semantics invalid for {reason}")
    if "fabricate" not in omissions.get("qualified_customer_zero_rows", {}).get(
        "action", ""
    ):
        errors.append(f"{mapping_file}: qualified-customer empty result must not fabricate")

    anchor_contract = mapping.get("output_target", {}).get(
        "local_inventory_commentary_anchor_bindings", {}
    )
    expected_anchor_refs = {
        "patch_and_similar_resource_commentary": (
            "${WEEKLY_REPORT_PATCH_AND_SIMILAR_RESOURCE_COMMENTARY_ANCHOR_LOCAL_ONLY}",
            "${WEEKLY_REPORT_PATCH_AND_SIMILAR_RESOURCE_COMMENTARY_PLACEHOLDER_LOCAL_ONLY}",
        ),
        "page_resource_commentary": (
            "${WEEKLY_REPORT_PAGE_RESOURCE_COMMENTARY_ANCHOR_LOCAL_ONLY}",
            "${WEEKLY_REPORT_PAGE_RESOURCE_COMMENTARY_PLACEHOLDER_LOCAL_ONLY}",
        ),
    }
    actual_anchor_refs = {
        item.get("output_field_id"): (
            item.get("local_anchor_reference"),
            item.get("local_placeholder_reference"),
        )
        for item in anchor_contract.get("anchors", [])
        if isinstance(item, dict) and item.get("required") is True
    }
    anchor_validation = anchor_contract.get("runtime_presence_validation", {})
    if anchor_contract.get("template_asset_id") != "TEMPLATE_WEEKLY_REPORT_0724_LOCAL_ONLY" or actual_anchor_refs != expected_anchor_refs or any(
        anchor_validation.get(key) is not True
        for key in (
            "validate_before_output_assembly",
            "resolved_template_file_must_exist",
            "each_anchor_must_exist_exactly_once",
            "each_placeholder_must_exist_exactly_once",
            "anchor_must_contain_bound_placeholder",
        )
    ) or anchor_contract.get("rendering_scope") != "fixed_narrative_template_rendering_only" or anchor_contract.get(
        "generic_anchor_discovery_allowed"
    ) is not False or anchor_contract.get("generic_dynamic_renderer_allowed") is not False:
        errors.append(f"{mapping_file}: local commentary anchor bindings are incomplete")

    prohibitions = narrative.get("implementation_prohibitions", {})
    required_prohibitions = {
        "generic_dynamic_table_renderer_allowed",
        "arbitrary_record_set_to_table_conversion_allowed",
        "dynamic_field_selection_framework_allowed",
        "generic_record_set_display_engine_allowed",
        "dynamic_html_column_generation_allowed",
        "new_metric_variant_allowed",
        "new_result_contract_allowed",
    }
    if set(prohibitions) != required_prohibitions or any(
        prohibitions.get(key) is not False for key in required_prohibitions
    ):
        errors.append(f"{mapping_file}: dynamic-rendering implementation prohibitions are incomplete")
    if narrative.get("allowed_output_assembly_operations") != [
        "fixed_template_field_substitution",
        "fixed_order_sentence_concatenation",
    ]:
        errors.append(f"{mapping_file}: Output Assembly operations exceed fixed rendering")
    if narrative.get("output_mapping_may_calculate_metric") is not False or narrative.get(
        "output_mapping_may_apply_business_judgment"
    ) is not False:
        errors.append(f"{mapping_file}: Output Mapping calculation or judgment is prohibited")

    baseline_file = "phase1_5/assets/readiness/implementation_baseline.yaml"
    baseline_constraints = documents.get(baseline_file, {}).get(
        "mvp_development_constraints", {}
    )
    expected_baseline = {
        "customer_analysis_output_implementation": "fixed_narrative_template_rendering",
        "customer_analysis_output_section": "inventory_and_sell_through",
        "customer_analysis_output_positions": [
            "patch_and_similar_resource_commentary",
            "page_resource_commentary",
        ],
        "generic_dynamic_table_renderer_required": False,
        "generic_record_set_display_engine_required": False,
        "dynamic_html_column_generation_required": False,
    }
    for key, expected in expected_baseline.items():
        if baseline_constraints.get(key) != expected:
            errors.append(f"{baseline_file}:mvp_development_constraints.{key}: expected {expected!r}")
    impact_review = documents.get(baseline_file, {}).get("change_control", {}).get(
        "latest_baseline_version_impact_review", {}
    )
    expected_impact_review = {
        "review_date": "2026-08-08",
        "behavior_or_output_change": False,
        "customer_analysis_output_strategy": "fixed_narrative_template_rendering",
        "customer_analysis_initial_mvp_status": "included",
        "physical_metric_store_strategy": "shared_local_sqlite",
        "mvp_execution_mode": "sequential",
        "mvp_recovery_mode": "rerun_pipeline_from_start",
        "development_complexity_reduction": True,
        "code_implementation_owner_approved": False,
        "baseline_version_increment_required": False,
    }
    for key, expected in expected_impact_review.items():
        actual = impact_review.get(key)
        if key == "review_date":
            actual = str(actual)
        if actual != expected:
            errors.append(f"{baseline_file}:latest_baseline_version_impact_review.{key}: expected {expected!r}")
    if documents.get(baseline_file, {}).get("baseline_version") != "1.0.0":
        errors.append(f"{baseline_file}: baseline_version must remain 1.0.0")
    baseline_document = documents.get(baseline_file, {})
    change_control = baseline_document.get("change_control", {})
    pre_freeze_review = change_control.get(
        "pre_freeze_customer_narrative_acceptance_review", {}
    )
    if str(baseline_document.get("freeze_date")) != "2026-08-08" or baseline_document.get(
        "freeze_revision_status"
    ) != "refrozen_after_acceptance_consistency_corrections" or change_control.get(
        "baseline_is_logically_frozen"
    ) is not True or change_control.get("repository_commit_binding_status") != "tracked_on_draft_pr_5_head" or pre_freeze_review.get(
        "behavior_or_output_change"
    ) is not True or pre_freeze_review.get("incorporated_before_current_freeze") is not True:
        errors.append(f"{baseline_file}: frozen state and version-impact history are inconsistent")

    baseline_sequence = baseline_constraints.get("implementation_sequence", [])
    if baseline_constraints.get("mvp_execution_mode") != "sequential" or baseline_constraints.get(
        "mvp_recovery_mode"
    ) != "rerun_pipeline_from_start" or baseline_constraints.get(
        "physical_metric_store_strategy"
    ) != "shared_local_sqlite" or [item.get("sequence") for item in baseline_sequence] != [1, 2, 3] or baseline_sequence[1].get(
        "auto_send"
    ) is not False:
        errors.append(f"{baseline_file}: lean MVP implementation sequence is incomplete")

    status_file = "phase1_5/assets/readiness/status_index.yaml"
    status_scope = documents.get(status_file, {}).get("scope_boundaries", {})
    expected_status = {
        "customer_analysis_output_implementation": "fixed_narrative_template_rendering",
        "customer_analysis_output_section": "inventory_and_sell_through",
        "customer_analysis_output_positions": [
            "patch_and_similar_resource_commentary",
            "page_resource_commentary",
        ],
        "customer_analysis_dynamic_table_renderer_required": False,
        "customer_analysis_initial_mvp_status": "included",
        "physical_metric_store_strategy": "shared_local_sqlite",
        "shared_metric_store_table": "metric_results",
        "shared_metric_store_discriminator_fields": ["store_id", "store_asset_id"],
        "revenue_metric_store_strategy": "existing_excel_history_store",
        "mvp_execution_mode": "sequential",
        "mvp_recovery_mode": "rerun_pipeline_from_start",
        "development_complexity_reduction": True,
        "code_implementation_owner_approved": False,
        "code_implementation_start": "wait_for_explicit_owner_approval",
        "initial_mvp_pipeline_count_with_sequential_execution": 12,
        "initial_mvp_pipeline_count_with_rerun_from_start": 12,
        "customer_analysis_qualified_zero_row_contract_mode": "product_context_with_empty_customer_record_set",
        "shared_metric_store_schema_status": "confirmed_runtime_not_initialized",
        "inventory_commentary_template_anchor_binding_status": "confirmed_runtime_validation_required",
    }
    for key, expected in expected_status.items():
        if status_scope.get(key) != expected:
            errors.append(f"{status_file}:scope_boundaries.{key}: expected {expected!r}")
    if str(documents.get(status_file, {}).get("last_semantic_sync_date")) != "2026-08-08":
        errors.append(f"{status_file}: last_semantic_sync_date must be 2026-08-08")

    output_gate_file = (
        "phase1_5/assets/output_mappings/"
        "weekly_report_output_mapping_readiness_gate.yaml"
    )
    output_gate = documents.get(output_gate_file, {})
    required_gate_checks = {
        "customer_analysis_removed_from_revenue_dynamic_table_slot",
        "customer_analysis_fixed_inventory_commentary_positions",
        "customer_analysis_fixed_eight_field_selection",
        "customer_analysis_fixed_narrative_templates",
        "inventory_overview_precedes_customer_analysis_sentences",
        "customer_analysis_precomputed_rank_and_limit_consumption",
        "customer_analysis_qualified_zero_rows_context_contract_consumable",
        "customer_analysis_omitted_contract_not_read",
        "inventory_commentary_local_template_anchors_explicit",
        "inventory_commentary_runtime_anchor_presence_validation",
        "customer_analysis_dynamic_rendering_prohibited",
    }
    gate_checks = output_gate.get("gate_checks", {})
    if any(gate_checks.get(key) != "pass" for key in required_gate_checks):
        errors.append(f"{output_gate_file}: fixed narrative Gate checks must pass")

    code_gate_file = "phase1_5/assets/readiness/code_implementation_readiness_gate.yaml"
    code_gate = documents.get(code_gate_file, {})
    if code_gate.get("scope", {}).get("code_implementation_started") is not False or code_gate.get(
        "implementation_entry_decision", {}
    ).get("code_implementation_may_start") is not False:
        errors.append(f"{code_gate_file}: code implementation must remain unauthorized")
    if code_gate.get("governance", {}).get("outlook_auto_send") is not False:
        errors.append(f"{code_gate_file}: outlook_auto_send must remain false")
    entry = code_gate.get("implementation_entry_decision", {})
    if entry.get("mvp_execution_mode") != "sequential" or entry.get(
        "mvp_recovery_mode"
    ) != "rerun_pipeline_from_start" or entry.get("physical_metric_store_strategy") != (
        "shared_local_sqlite"
    ) or entry.get("metric_store_table") != "metric_results":
        errors.append(f"{code_gate_file}: lean MVP entry semantics are incomplete")
    if entry.get("code_implementation_start") != "wait_for_explicit_owner_approval" or code_gate.get(
        "scope", {}
    ).get("implementation_baseline_status") != "frozen_awaiting_explicit_owner_code_implementation_approval":
        errors.append(f"{code_gate_file}: implementation authorization and frozen baseline state conflict")

    return 1


def validate_external_asset_versions(
    documents: dict[str, Any], errors: list[str]
) -> tuple[int, int]:
    """Validate local-only External Asset version contracts and consumer bindings."""
    registry_file = "phase1_5/assets/external_asset_references.yaml"
    registry = documents.get(registry_file)
    if not isinstance(registry, dict):
        errors.append(f"{registry_file}: missing or invalid External Asset registry")
        return 0, 0

    assets: dict[str, dict[str, Any]] = {}
    required_keys = {
        "reference_contract_version",
        "runtime_version_placeholder",
        "runtime_content_fingerprint_placeholder",
        "version_resolution_status",
        "runtime_verification_required",
        "owner_confirmation_required_on_version_change",
    }
    for index, asset in enumerate(registry.get("external_assets", [])):
        if not isinstance(asset, dict) or not isinstance(asset.get("asset_id"), str):
            errors.append(f"{registry_file}:external_assets[{index}]: invalid asset")
            continue
        asset_id = asset["asset_id"]
        missing = sorted(required_keys - set(asset))
        if missing:
            errors.append(
                f"{registry_file}:external_assets[{index}]: missing version keys {missing}"
            )
        for key in (
            "runtime_version_placeholder",
            "runtime_content_fingerprint_placeholder",
        ):
            if not PLACEHOLDER_PATTERN.fullmatch(str(asset.get(key, ""))):
                errors.append(
                    f"{registry_file}:external_assets[{index}].{key}: "
                    "expected a LOCAL_ONLY placeholder"
                )
        if asset.get("runtime_verification_required") is not True:
            errors.append(
                f"{registry_file}:external_assets[{index}]: runtime verification must be true"
            )
        assets[asset_id] = asset

    binding_count = 0

    def walk(file: str, value: Any, path: str = "") -> None:
        nonlocal binding_count
        if isinstance(value, dict):
            checks = (
                (
                    "asset_id",
                    "reference_contract_version",
                    "runtime_version_reference",
                ),
                (
                    "filter_id",
                    "reference_contract_version",
                    "runtime_version_reference",
                ),
                (
                    "reference_mapping_asset_id",
                    "reference_mapping_contract_version",
                    "reference_mapping_runtime_version",
                ),
            )
            for id_key, version_key, runtime_key in checks:
                asset_id = value.get(id_key)
                if asset_id not in assets:
                    continue
                binding_count += 1
                expected = assets[asset_id]
                if value.get(version_key) != expected.get("reference_contract_version"):
                    errors.append(
                        f"{file}:{path}: External Asset {asset_id} contract version mismatch"
                    )
                if value.get(runtime_key) != expected.get("runtime_version_placeholder"):
                    errors.append(
                        f"{file}:{path}: External Asset {asset_id} runtime version binding mismatch"
                    )
            for key, child in value.items():
                walk(file, child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(file, child, f"{path}[{index}]")

    for file, document in documents.items():
        if file.startswith("phase1_5/assets/") and file != registry_file:
            walk(file, document)

    gate_file = (
        "phase1_5/assets/policies/"
        "inventory_advertising_policy_readiness_gate.yaml"
    )
    gate = documents.get(gate_file)
    if isinstance(gate, dict):
        declared_count = gate.get("external_asset_version_gate", {}).get(
            "registered_external_asset_count"
        )
        if declared_count != len(assets):
            errors.append(
                f"{gate_file}: registered External Asset count {declared_count!r} "
                f"does not match registry count {len(assets)}"
            )
    return len(assets), binding_count


def validate_implementation_baseline(
    documents: dict[str, Any], errors: list[str]
) -> int:
    """Keep the frozen baseline, Status Index, and implementation Gate aligned."""
    baseline_file = "phase1_5/assets/readiness/implementation_baseline.yaml"
    status_file = "phase1_5/assets/readiness/status_index.yaml"
    gate_file = "phase1_5/assets/readiness/code_implementation_readiness_gate.yaml"
    baseline = documents.get(baseline_file)
    status_index = documents.get(status_file)
    code_gate = documents.get(gate_file)
    if not all(isinstance(item, dict) for item in (baseline, status_index, code_gate)):
        errors.append("Implementation Baseline validation inputs are missing or invalid")
        return 0

    checked = 0
    indexed_gates = {
        item.get("gate_id"): item.get("status")
        for item in status_index.get("asset_stage_gates", [])
        if isinstance(item, dict)
    }
    for index, item in enumerate(baseline.get("frozen_gate_results", [])):
        if not isinstance(item, dict):
            errors.append(f"{baseline_file}:frozen_gate_results[{index}]: expected mapping")
            continue
        gate_id = item.get("gate_id")
        if indexed_gates.get(gate_id) != item.get("status"):
            errors.append(
                f"{baseline_file}:frozen_gate_results[{index}]: status does not "
                f"match Status Index for {gate_id}"
            )
        checked += 1

    baseline_index = status_index.get("implementation_baseline", {})
    for key in (
        "baseline_id",
        "baseline_version",
        "status",
        "freeze_date",
        "freeze_revision_status",
    ):
        if baseline_index.get(key) != baseline.get(key):
            errors.append(
                f"{status_file}:implementation_baseline.{key}: does not match baseline asset"
            )
        checked += 1

    authorization = baseline.get("implementation_authorization", {})
    gate_decision = code_gate.get("implementation_entry_decision", {})
    required_false = (
        (authorization, "explicit_owner_code_implementation_approval_received"),
        (authorization, "code_implementation_may_start"),
        (gate_decision, "explicit_owner_code_implementation_approval_received"),
        (gate_decision, "code_implementation_may_start"),
    )
    for container, key in required_false:
        if container.get(key) is not False:
            errors.append(f"Implementation Baseline approval gate requires {key}=false")
        checked += 1

    store_gate_file = (
        "phase1_5/assets/metric_stores/metric_result_store_readiness_matrix.yaml"
    )
    store_gate = documents.get(store_gate_file, {})
    if store_gate.get("gate_conclusion", {}).get(
        "code_implementation_start"
    ) != "wait_for_explicit_owner_approval" or gate_decision.get(
        "code_implementation_start"
    ) != "wait_for_explicit_owner_approval":
        errors.append("Metric Store and final Code Implementation Gates must both wait for explicit Owner approval")
    checked += 1

    stale_publication_paths: list[str] = []

    def find_stale_publication_state(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            if value.get("committed_or_pushed") is False:
                stale_publication_paths.append(path or "<root>")
            for key, child in value.items():
                find_stale_publication_state(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                find_stale_publication_state(child, f"{path}[{index}]")

    for file, document in documents.items():
        if file.startswith("phase1_5/assets/"):
            find_stale_publication_state(document, file)
    if stale_publication_paths:
        errors.append(
            "stale committed_or_pushed=false states remain: "
            + ", ".join(stale_publication_paths)
        )
    if baseline.get("change_control", {}).get(
        "repository_commit_binding_status"
    ) != "tracked_on_draft_pr_5_head":
        errors.append(f"{baseline_file}: repository publication state is stale")
    checked += 1

    expected_counts = {
        "external_asset_reference_count": len(
            documents.get("phase1_5/assets/external_asset_references.yaml", {}).get(
                "external_assets", []
            )
        ),
        "metric_variant_count": sum(
            len(document.get("metric_variants", []))
            for document in documents.values()
            if isinstance(document, dict)
        ),
        "result_contract_count": sum(
            1
            for file, document in documents.items()
            if file.startswith("phase1_5/assets/result_contracts/RC_")
            and isinstance(document, dict)
            and document.get("config_type") == "result_contract"
        ),
    }
    frozen_versions = baseline.get("frozen_asset_versions", {})
    for key, expected in expected_counts.items():
        if frozen_versions.get(key) != expected:
            errors.append(
                f"{baseline_file}:frozen_asset_versions.{key}: expected {expected}"
            )
        checked += 1

    base_sha = os.environ.get("ASSET_VALIDATION_BASE_SHA", "").strip()
    base_sha_source = "ASSET_VALIDATION_BASE_SHA"
    if not re.fullmatch(r"[0-9a-fA-F]{40}", base_sha):
        base_sha = ""
        for candidate in ("origin/main", "main", "HEAD^1"):
            result = subprocess.run(
                ["git", "rev-parse", "--verify", candidate],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            resolved = result.stdout.strip()
            if result.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{40}", resolved):
                base_sha = resolved
                base_sha_source = candidate
                break
    if not base_sha:
        errors.append(
            f"{baseline_file}: cannot resolve validation Base SHA from the "
            "Actions context or local Git refs"
        )
    elif baseline.get("source_main_commit_sha") != base_sha:
        errors.append(
            f"{baseline_file}: source_main_commit_sha does not match Base SHA "
            f"resolved from {base_sha_source}"
        )
    checked += 1
    return checked


def validate_status_consistency(
    documents: dict[str, Any], errors: list[str]
) -> int:
    """Ensure Status Index, source Gates, and the final Gate report one result."""
    index_path = "phase1_5/assets/readiness/status_index.yaml"
    index_document = documents.get(index_path)
    if not isinstance(index_document, dict):
        errors.append(f"{index_path}: missing or invalid status index")
        return 0

    indexed_results: dict[str, str] = {}
    checked = 0
    for item_index, item in enumerate(index_document.get("asset_stage_gates", [])):
        if not isinstance(item, dict):
            errors.append(
                f"{index_path}:asset_stage_gates[{item_index}]: expected mapping"
            )
            continue
        gate_id = item.get("gate_id")
        indexed_status = item.get("status")
        source_artifact = item.get("source_artifact")
        if not all(isinstance(value, str) and value for value in (
            gate_id,
            indexed_status,
            source_artifact,
        )):
            errors.append(
                f"{index_path}:asset_stage_gates[{item_index}]: incomplete gate status entry"
            )
            continue
        source_document = documents.get(source_artifact)
        if not isinstance(source_document, dict):
            errors.append(
                f"{index_path}:asset_stage_gates[{item_index}]: "
                f"source artifact not found: {source_artifact}"
            )
            continue
        if source_document.get("gate_id") != gate_id:
            errors.append(
                f"{source_artifact}: gate_id does not match Status Index entry {gate_id}"
            )
        source_result = source_document.get("gate_result")
        if source_result != indexed_status:
            errors.append(
                f"{source_artifact}: gate_result {source_result!r} does not match "
                f"Status Index status {indexed_status!r}"
            )
        indexed_results[gate_id] = indexed_status
        checked += 1

    final_gate_path = (
        "phase1_5/assets/readiness/code_implementation_readiness_gate.yaml"
    )
    final_gate = documents.get(final_gate_path)
    if isinstance(final_gate, dict):
        for item_index, item in enumerate(final_gate.get("prerequisite_gate_results", [])):
            if not isinstance(item, dict):
                continue
            gate_id = item.get("gate_id")
            reported_status = item.get("status")
            indexed_status = indexed_results.get(gate_id)
            if indexed_status != reported_status:
                errors.append(
                    f"{final_gate_path}:prerequisite_gate_results[{item_index}]: "
                    f"status {reported_status!r} does not match Status Index "
                    f"status {indexed_status!r} for {gate_id}"
                )
            checked += 1
    return checked


def main() -> int:
    errors: list[str] = []
    documents: dict[str, Any] = {}
    yaml_files = repository_yaml_files()

    for file in yaml_files:
        try:
            documents[file] = load_yaml(REPOSITORY_ROOT / file)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            errors.append(f"{file}: YAML parse failed: {exc}")

    if errors:
        print("Asset integrity validation FAILED during YAML parsing:")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1

    definitions, definition_paths = collect_definitions(documents)
    by_kind_and_id: dict[tuple[str, str], list[Definition]] = defaultdict(list)
    for definition in definitions:
        by_kind_and_id[(definition.kind, definition.asset_id)].append(definition)

    for (kind, asset_id), occurrences in sorted(by_kind_and_id.items()):
        if len(occurrences) > 1:
            locations = ", ".join(
                f"{item.file}:{item.path}" for item in occurrences
            )
            errors.append(f"duplicate {kind} ID {asset_id}: {locations}")

    definitions_by_kind: dict[str, set[str]] = defaultdict(set)
    for definition in definitions:
        definitions_by_kind[definition.kind].add(definition.asset_id)
    external_ids = definitions_by_kind.get("external_asset", set())

    references = collect_references(documents, definition_paths)
    for reference in references:
        if (
            reference.asset_id not in definitions_by_kind.get(reference.kind, set())
            and reference.asset_id not in external_ids
        ):
            errors.append(
                f"{reference.file}:{reference.path}: unresolved "
                f"{reference.kind} ID {reference.asset_id}"
            )

    matched_assets, checked_paths = validate_required_structures(documents, errors)
    contract_counts = validate_result_contract_semantics(
        documents, definitions_by_kind, errors
    )
    configured_display_policy_count = validate_configured_display_value_policy(
        documents, errors
    )
    mvp_acceptance_check_count = validate_mvp_acceptance_semantics(
        documents, errors
    )
    customer_narrative_mapping_count = validate_customer_analysis_narrative_mapping(
        documents, errors
    )
    external_asset_count, external_asset_binding_count = (
        validate_external_asset_versions(documents, errors)
    )
    implementation_baseline_checks = validate_implementation_baseline(
        documents, errors
    )
    checked_status_entries = validate_status_consistency(documents, errors)

    if errors:
        print("Asset integrity validation FAILED:")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1

    core_counts = {
        "datasets": len(definitions_by_kind.get("dataset", set())),
        "pipelines": len(definitions_by_kind.get("pipeline", set())),
        "metrics": len(definitions_by_kind.get("metric", set())),
    }
    print(
        "Asset integrity validation passed: "
        f"{len(yaml_files)} YAML files parsed; "
        f"{core_counts['datasets']} Dataset IDs, "
        f"{core_counts['pipelines']} Pipeline IDs, and "
        f"{core_counts['metrics']} Metric IDs are unique; "
        f"{contract_counts['contracts']} formal Result Contracts with "
        f"{contract_counts['contract_fields']} fields and "
        f"{contract_counts['record_sets']} record sets validated; "
        f"{contract_counts['record_grains']} record grains, "
        f"{contract_counts['source_contract_routes']} conditional upstream routes, "
        f"{contract_counts['input_lineage_contracts']} Contract input-lineage summaries, "
        f"and {contract_counts['display_tbd_exceptions']} allowed display TBD exception checked; "
        f"{configured_display_policy_count} configured display value policy validated; "
        f"{mvp_acceptance_check_count} MVP runtime acceptance boundaries checked; "
        f"{customer_narrative_mapping_count} fixed customer-analysis narrative mapping validated; "
        f"{contract_counts['output_bindings']} Metric Variant output bindings and "
        f"{contract_counts['output_fields']} explicit Output Mapping fields checked; "
        f"{external_asset_count} versioned External Assets with "
        f"{external_asset_binding_count} consumer bindings checked; "
        f"{implementation_baseline_checks} Implementation Baseline checks passed; "
        f"{len(references)} asset references resolved; "
        f"{checked_paths} Required paths checked across {matched_assets} assets; "
        f"{checked_status_entries} Gate status links are consistent."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
