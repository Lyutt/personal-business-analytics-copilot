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
    return bool(normalized) and normalized.upper() not in {"TBD", "NOT_APPLICABLE"} and not bool(
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
        "dataset_ids": "dataset",
        "dataset_dependencies": "dataset",
        "output_dataset_id": "dataset",
        "fallback_dataset_id": "dataset",
        "primary_dataset_id": "dataset",
        "out_of_scope_dataset_id": "dataset",
        "affected_dataset_ids": "dataset",
        "query_asset_id": "query_asset",
        "query_asset_ids": "query_asset",
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
        "independent_peer_pipeline_ids": "pipeline",
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
            dimensions: Any, location: str, source_file: str
        ) -> set[str]:
            dimension_ids: set[str] = set()
            if dimensions is None:
                return dimension_ids
            if not isinstance(dimensions, list):
                errors.append(f"{source_file}:{location}: expected list")
                return dimension_ids
            for index, dimension in enumerate(dimensions):
                if not isinstance(dimension, dict) or not isinstance(
                    dimension.get("dimension_id"), str
                ):
                    errors.append(
                        f"{source_file}:{location}[{index}].dimension_id: missing or invalid"
                    )
                    continue
                dimension_id = dimension["dimension_id"]
                if dimension_id in dimension_ids:
                    errors.append(
                        f"{source_file}:{location}[{index}].dimension_id: duplicate "
                        f"dimension {dimension_id}"
                    )
                dimension_ids.add(dimension_id)
            return dimension_ids

        def validate_grain(
            grain: Any, allowed: set[str], location: str, source_file: str
        ) -> None:
            nonlocal record_grains_checked
            record_grains_checked += 1
            if not isinstance(grain, list) or not grain:
                errors.append(f"{source_file}:{location}: expected non-empty list")
                return
            for item in grain:
                if not isinstance(item, str) or item not in allowed:
                    errors.append(
                        f"{source_file}:{location}: grain item {item!r} is not a "
                        "declared field or dimension"
                    )

        top_dimensions = declared_dimension_ids(
            document.get("contract_dimensions"), "contract_dimensions", file
        )
        if "record_grain" in document:
            top_fields = {
                field_id for field_id in fields if "." not in field_id
            }
            validate_grain(
                document.get("record_grain"),
                top_fields | top_dimensions,
                "record_grain",
                file,
            )
        for set_index, record_set in enumerate(document.get("record_sets", [])):
            if not isinstance(record_set, dict) or "record_grain" not in record_set:
                continue
            record_set_id = record_set.get("record_set_id")
            set_dimensions = declared_dimension_ids(
                record_set.get("record_dimensions"),
                f"record_sets[{set_index}].record_dimensions",
                file,
            )
            validate_grain(
                record_set.get("record_grain"),
                record_sets.get(record_set_id, set()) | set_dimensions,
                f"record_sets[{set_index}].record_grain",
                file,
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

        def walk_output(value: Any, source_file: str, path: str = "") -> None:
            nonlocal explicit_output_fields, display_tbd_checked
            if isinstance(value, dict):
                if "metric_variant_ids" in value:
                    errors.append(
                        f"{source_file}:{path}.metric_variant_ids: parallel list is prohibited"
                    )
                if isinstance(value.get("display_fields"), list):
                    errors.append(
                        f"{source_file}:{path}.display_fields: parallel list is prohibited"
                    )
                if value.get("display_fields") == "TBD":
                    display_tbd_checked += 1
                    errors.append(
                        f"{source_file}:{path}.display_fields: TBD is prohibited in all active MVP outputs"
                    )
                output_fields = value.get("output_fields")
                if isinstance(output_fields, list):
                    seen_output_ids: set[str] = set()
                    for index, output_field in enumerate(output_fields):
                        location = f"{source_file}:{path}.output_fields[{index}]"
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
                            f"{source_file}:{path}.result_record_set_binding: unknown record set"
                        )
                for key, child in value.items():
                    walk_output(
                        child,
                        source_file,
                        f"{path}.{key}" if path else str(key),
                    )
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    walk_output(child, source_file, f"{path}[{index}]")

        walk_output(document, file)

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
        "duplicate_and_negative_row_exclusion",
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
    weekly_pipeline_ids = [
        item.get("pipeline_id")
        for item in registry.get("pipelines", [])
        if isinstance(item, dict)
        and any(
            isinstance(binding, dict)
            and binding.get("workflow_id") == "WF_WEEKLY_BUSINESS_REPORT"
            for binding in item.get("workflow_bindings", [])
        )
    ]
    if mvp_execution.get("pipeline_ids") != weekly_pipeline_ids or mvp_execution.get(
        "first_phase_scheduling_mode"
    ) != "sequential" or mvp_execution.get("registered_pipeline_order_required") is not True or mvp_execution.get(
        "failed_run_recovery"
    ) != "rerun_pipeline_from_start" or mvp_execution.get(
        "parallel_scheduler_required"
    ) is not False or mvp_execution.get("stage_checkpointing_required") is not False or mvp_execution.get(
        "resume_from_failed_stage_required"
    ) is not False:
        errors.append(f"{registry_file}: Weekly MVP Pipeline execution semantics are incomplete")

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
        "workflow_reporting_date",
        "current_revenue_cutoff_date",
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
        "review_date": "2026-08-09",
        "change_class": "phase1_5_design_contract_closure",
        "behavior_or_output_change": True,
        "customer_analysis_output_strategy": "fixed_narrative_template_rendering",
        "customer_analysis_initial_mvp_status": "included",
        "physical_metric_store_strategy": "shared_local_sqlite",
        "mvp_execution_mode": "sequential",
        "mvp_recovery_mode": "rerun_pipeline_from_start",
        "development_complexity_reduction": True,
        "code_implementation_started": False,
        "code_implementation_owner_approved": False,
        "baseline_version_increment_required": False,
        "final_acceptance_synthetic_scenario_count": 14,
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
    final_acceptance_review = change_control.get(
        "final_acceptance_behavior_change_review", {}
    )
    adhoc_patch_review = change_control.get(
        "final_adhoc_capability_patch_review", {}
    )
    if str(baseline_document.get("freeze_date")) != "2026-08-08" or baseline_document.get(
        "last_refreeze_date"
    ).isoformat() != "2026-08-09" or baseline_document.get(
        "freeze_revision_status"
    ) != "refrozen_after_design_contract_closure" or change_control.get(
        "baseline_is_logically_frozen"
    ) is not True or change_control.get("repository_commit_binding_status") != "freeze_candidate_reviewed" or pre_freeze_review.get(
        "behavior_or_output_change"
    ) is not True or pre_freeze_review.get("incorporated_before_current_freeze") is not True or final_acceptance_review.get(
        "behavior_or_output_change"
    ) is not True or final_acceptance_review.get(
        "incorporated_before_current_freeze"
    ) is not True or final_acceptance_review.get(
        "code_implementation_started"
    ) is not False or final_acceptance_review.get(
        "owner_code_implementation_approval_granted"
    ) is not False or adhoc_patch_review.get(
        "weekly_mvp_behavior_or_output_change"
    ) is not False or adhoc_patch_review.get(
        "business_asset_selection_remains_exact_registry_id_only"
    ) is not True or adhoc_patch_review.get(
        "new_metric_rule_result_contract_pipeline_or_workflow_created"
    ) is not False or adhoc_patch_review.get(
        "incorporated_before_current_freeze"
    ) is not True:
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
        "code_implementation_owner_approved": True,
        "code_implementation_start": "stage2_acquisition_runtime_foundation_completed_and_merged",
        "initial_mvp_pipeline_count_with_sequential_execution": 12,
        "initial_mvp_pipeline_count_with_rerun_from_start": 12,
        "customer_analysis_qualified_zero_row_contract_mode": "product_context_with_empty_customer_record_set",
        "shared_metric_store_schema_status": "confirmed_runtime_not_initialized",
        "inventory_commentary_template_anchor_binding_status": "confirmed_runtime_validation_required",
    }
    for key, expected in expected_status.items():
        if status_scope.get(key) != expected:
            errors.append(f"{status_file}:scope_boundaries.{key}: expected {expected!r}")
    if str(documents.get(status_file, {}).get("last_semantic_sync_date")) != "2026-08-20":
        errors.append(f"{status_file}: last_semantic_sync_date must be 2026-08-20")

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
    if code_gate.get("record_scope") != "historical_pre_stage2_authorization" or code_gate.get(
        "current_state_classification"
    ) != "superseded_as_current_operational_status":
        errors.append(f"{code_gate_file}: historical Gate scope must be explicit")
    if code_gate.get("scope", {}).get("code_implementation_started") is not False or code_gate.get(
        "implementation_entry_decision", {}
    ).get("code_implementation_may_start") is not False:
        errors.append(f"{code_gate_file}: original pre-Stage 2 authorization judgment must remain unchanged")
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


def validate_phase1_5_final_closure(
    documents: dict[str, Any], errors: list[str]
) -> int:
    """Validate the final Phase 1.5 runtime, persistence, and ad-hoc contracts."""
    checked = 0
    runtime_file = "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1_2_candidate.yaml"
    store_file = "phase1_5/assets/metric_stores/metric_result_store_registry.yaml"
    scenarios_file = "phase1_5/tests/final_acceptance_scenarios.yaml"
    registry_file = "phase1_5/assets/pipelines/pipeline_registry.yaml"
    dataset_file = "phase1_5/assets/datasets/dataset_inventory.yaml"
    readiness_file = "phase1_5/assets/datasets/dataset_readiness_matrix.yaml"
    metric_file = "phase1_5/assets/metrics/metric_library_inventory_baseline_v1.yaml"
    policy_file = "phase1_5/assets/pipelines/PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS_policy_v1.yaml"
    customer_mapping_file = "phase1_5/assets/field_mappings/MAP_ADVERTISING_APOLLO_PRODUCT_CUSTOMER_DELIVERY_CHANGE_V1.yaml"
    output_file = "phase1_5/assets/output_mappings/OM_WEEKLY_BUSINESS_REPORT_V1.yaml"
    outlook_file = "phase1_5/assets/output_mappings/OM_WEEKLY_BUSINESS_REPORT_OUTLOOK_DRAFT_V1.yaml"
    external_file = "phase1_5/assets/external_asset_references.yaml"
    store_file = "phase1_5/assets/metric_stores/metric_result_store_registry.yaml"
    display_policy_file = "phase1_5/assets/policies/POLICY_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE_DISPLAY_V1.yaml"
    dcp_file = "phase1_5/assets/analysis/dcp_registry_v1.yaml"
    arc_file = "phase1_5/templates/analysis_request_contract.template.yaml"
    scenarios_file = "phase1_5/tests/final_acceptance_scenarios.yaml"
    gate_file = "phase1_5/assets/readiness/code_implementation_readiness_gate.yaml"
    baseline_file = "phase1_5/assets/readiness/implementation_baseline.yaml"

    runtime = documents.get(runtime_file, {})
    context = runtime.get("workflow_run_context", {})
    required_context_fields = {
        "workflow_run_id",
        "run_type",
        "workflow_execution_date",
        "workflow_reporting_date",
        "reporting_period_id",
        "reporting_period_start_date",
        "reporting_period_end_date",
        "current_period_start_date",
        "current_period_end_date",
        "comparison_period_start_date",
        "comparison_period_end_date",
        "cutoff_date",
        "timezone",
    }
    required_revenue_scope_fields = {
        "current_revenue_cutoff_date",
        "expected_previous_revenue_workflow_reporting_date",
        "target_report_period",
        "workflow_year",
        "target_fiscal_quarter",
        "target_previous_calendar_quarter",
        "report_mode",
        "target_revenue_cutoff_date",
    }
    revenue_scope = context.get("revenue_scope_fields", {})
    if (
        runtime.get("workflow_id") != "WF_WEEKLY_BUSINESS_REPORT"
        or context.get("one_context_per_workflow_run") is not True
        or context.get("lock_before_any_pipeline_data_acquisition") is not True
        or context.get("lock_before_any_pipeline_data_acquisition_scope") != "core required_fields only"
        or context.get("immutable_after_context_lock") is not True
        or set(context.get("required_fields", {})) != required_context_fields
        or set(revenue_scope.get("fields", {})) != required_revenue_scope_fields
        or revenue_scope.get("non_revenue_pipeline_continuation_required") is not True
        or revenue_scope.get("partial_draft_allowed_under_existing_fallback") is not True
        or revenue_scope.get("core_context_relock_or_mutation_allowed") is not False
        or context.get("required_fields", {}).get("run_type", {}).get("allowed_values")
        != ["scheduled", "manual", "backfill"]
        or context.get("query_parameter_authority", {}).get(
            "actual_execution_date_business_date_inference_allowed"
        )
        is not False
    ):
        errors.append(f"{runtime_file}: Workflow Run Context is incomplete")
    checked += 1

    manifest = runtime.get("run_input_manifest", {})
    required_manifest_fields = {
        "workflow_run_id",
        "dataset_id",
        "dataset_version",
        "query_asset_binding",
        "period_role",
        "local_input_reference",
        "product_parameter",
        "source_report_date",
        "source_business_data_cutoff_date",
    }
    if (
        manifest.get("one_manifest_per_workflow_run") is not True
        or set(manifest.get("required_entry_fields", {})) != required_manifest_fields
        or manifest.get("entry_business_key")
        != ["workflow_run_id", "dataset_id", "period_role", "product_parameter"]
        or any(manifest.get("prohibited_inference", {}).values())
    ):
        errors.append(f"{runtime_file}: Run Input Manifest is incomplete")
    checked += 1

    registry = documents.get(registry_file, {})
    pipelines = registry.get("pipelines", []) if isinstance(registry, dict) else []
    weekly_pipelines = [
        pipeline
        for pipeline in pipelines
        if any(
            isinstance(binding, dict)
            and binding.get("workflow_id") == "WF_WEEKLY_BUSINESS_REPORT"
            for binding in pipeline.get("workflow_bindings", [])
        )
    ]
    dependencies = [
        dependency
        for pipeline in weekly_pipelines
        for dependency in pipeline.get("dataset_dependencies", [])
        if isinstance(dependency, dict)
    ]
    declared_weekly_dependency_count = registry.get("readiness_gate", {}).get(
        "weekly_declared_dataset_dependency_count"
    )
    if (
        not isinstance(declared_weekly_dependency_count, int)
        or len(dependencies) != declared_weekly_dependency_count
        or any(
            dependency.get("dataset_version_constraint") != ">=0.1.0,<0.2.0"
            or dependency.get("join_or_relationship_rule_id") != "not_applicable"
            or dependency.get("run_input_manifest_required") is not True
            or dependency.get("period_context_source") != "workflow_run_context"
            for dependency in dependencies
        )
    ):
        errors.append(
            f"{registry_file}: Weekly Dataset dependencies must match the "
            f"workflow-isolated Gate declaration {declared_weekly_dependency_count!r} "
            "and retain active Version, Join, Context, and Manifest bindings"
        )
    if "TBD" in yaml.safe_dump(registry, allow_unicode=True):
        errors.append(f"{registry_file}: Active Pipeline Registry must not retain runtime TBD")
    checked += 1

    runtime_input_contract = documents.get(dataset_file, {}).get("runtime_input_contract", {})
    workflow_scopes = runtime_input_contract.get("workflow_scopes", {})
    weekly_scope = workflow_scopes.get("WF_WEEKLY_BUSINESS_REPORT", {})
    customer_scope = workflow_scopes.get("WF_CUSTOMER_REVENUE_DETAIL", {})
    if (
        runtime_input_contract.get("applies_by_workflow_scope") is not True
        or runtime_input_contract.get("unscoped_runtime_contract_fallback_allowed") is not False
        or weekly_scope.get("contract_source") != "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1.yaml"
        or weekly_scope.get("run_input_manifest_id") != "RUN_INPUT_MANIFEST_WF_WEEKLY_BUSINESS_REPORT_V1"
        or weekly_scope.get("existing_multiple_email_selection_semantics_unchanged") is not True
        or customer_scope.get("run_input_manifest_id") != "CUSTOMER_REVENUE_DETAIL_RUN_INPUT_MANIFEST_V1"
        or customer_scope.get("context_id") != "CUSTOMER_REVENUE_DETAIL_RUN_CONTEXT_V1"
        or customer_scope.get("weekly_runtime_contract_reuse_allowed") is not False
        or customer_scope.get("rolling_deck_roles") != ["current_qtd", "prior_year_full_quarter", "prior_year_comparable"]
        or customer_scope.get("unconditional_latest_file_selection_allowed") is not False
        or "unconditional_latest_file_selection_allowed" in runtime_input_contract
        or runtime_input_contract.get("workflow_run_context_required") is not True
        or runtime_input_contract.get("run_input_manifest_required") is not True
        or runtime_input_contract.get("actual_execution_date_business_date_inference_allowed") is not False
    ):
        errors.append(f"{dataset_file}: Dataset and Query runtime authority is incomplete")
    forbidden_date_phrases = (
        "execution date minus",
        "Query date minus",
        "Workflow execution date",
    )
    for file in (dataset_file, registry_file):
        text = (REPOSITORY_ROOT / file).read_text(encoding="utf-8")
        if any(phrase in text for phrase in forbidden_date_phrases):
            errors.append(f"{file}: business dates cannot be derived from execution or query date")
    checked += 1

    active_dataset_ids = {dependency.get("dataset_id") for dependency in dependencies}
    for file, document in documents.items():
        if (
            isinstance(document, dict)
            and document.get("config_type") == "field_mapping_profile"
            and document.get("dataset_id") in active_dataset_ids
            and document.get("dataset_version_constraint") != ">=0.1.0,<0.2.0"
        ):
            errors.append(f"{file}: active Mapping Profile Dataset version constraint is unresolved")
    readiness = documents.get(readiness_file, {})
    baseline = readiness.get("standardization_baseline", {})
    runtime_tbd_policy = readiness.get("active_runtime_tbd_policy", {})
    if (
        baseline.get("active_pipeline_dataset_version_constraint") != ">=0.1.0,<0.2.0"
        or baseline.get("single_dataset_join_rule") != "not_applicable"
        or runtime_tbd_policy.get("tbd_allowed_in_runtime_authoritative_paths") is not False
        or runtime_tbd_policy.get(
            "tbd_may_be_treated_as_non_blocking_without_explicit_classification"
        )
        is not False
    ):
        errors.append(f"{readiness_file}: active Dataset version baseline is incomplete")
    checked += 1

    metrics = {
        item.get("metric_variant_id"): item
        for item in documents.get(metric_file, {}).get("metric_variants", [])
        if isinstance(item, dict)
    }
    pp_formulas = {
        "MV_INVENTORY_PATCH_BRAND_SELL_THROUGH_WOW_CHANGE_V1": "(Current-week Brand Sell-through Rate - prior-week Brand Sell-through Rate) * 100.",
        "MV_INVENTORY_NON_PATCH_PRODUCT_BRAND_SELL_THROUGH_WOW_CHANGE_V1": "(Current-week Product Brand Sell-through Rate - prior-week Product Brand Sell-through Rate) * 100.",
        "MV_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WOW_CHANGE_V1": "(Current-week Brand Moment Sell-through Rate - prior-week Brand Moment Sell-through Rate) * 100.",
    }
    for variant_id, formula in pp_formulas.items():
        variant = metrics.get(variant_id, {})
        if (
            variant.get("formula") != formula
            or variant.get("base_unit") != "percentage_point"
            or variant.get("numeric_semantics") != "percentage_point_change"
        ):
            errors.append(f"{metric_file}:{variant_id}: percentage-point formula or unit is invalid")
    trigger_baseline = documents.get(metric_file, {}).get("conditional_analysis_handoff", {})
    if trigger_baseline.get("default_threshold_value") != 10.0 or trigger_baseline.get(
        "default_threshold_unit"
    ) != "percentage_point":
        errors.append(f"{metric_file}: customer trigger threshold must be numeric 10.0 percentage_point")
    checked += 1

    contract_files = [
        file
        for file, document in documents.items()
        if file.startswith("phase1_5/assets/result_contracts/")
        and isinstance(document, dict)
        and document.get("config_type") == "result_contract"
    ]
    for file in contract_files:
        document = documents[file]
        downstream = document.get("mode_semantics", {}).get("downstream_consumption", {})
        is_customer_revenue_contract = (
            document.get("result_contract_id")
            == "RC_CUSTOMER_REVENUE_DETAIL_WEEKLY"
        )
        expected_output_statuses = (
            ["valid_value", "missing"]
            if is_customer_revenue_contract
            else ["valid_value"]
        )
        if (
            downstream.get("calculation_allowed_value_statuses") != ["valid_value"]
            or downstream.get("output_allowed_value_statuses")
            != expected_output_statuses
            or downstream.get("pending_confirmation_calculation_allowed") is not False
            or downstream.get("pending_confirmation_output_allowed") is not False
        ):
            errors.append(f"{file}: Result field consumption statuses are incomplete")
        if is_customer_revenue_contract and (
            downstream.get("missing_output_requires_nullable_or_missing_field_contract")
            is not True
            or downstream.get("missing_output_rendering") != "blank_cell"
            or downstream.get("calculation_failed_calculation_allowed") is not False
            or downstream.get("calculation_failed_output_allowed") is not False
        ):
            errors.append(
                f"{file}: Customer missing rendering or failed-value prohibitions are incomplete"
            )
        for field in document.get("contract_fields", []):
            field_id = str(field.get("field_id", ""))
            constraints = field.get("numeric_constraints", {})
            if field_id.endswith("_wow_change_pp") and (
                field.get("base_unit") != "percentage_point"
                or constraints.get("unit") != "percentage_point"
            ):
                errors.append(f"{file}:{field_id}: percentage-point Contract unit is invalid")
            if field_id.endswith("sell_through_rate") and constraints.get("unit") != "decimal_ratio":
                errors.append(f"{file}:{field_id}: ratio Contract must remain decimal_ratio")
    checked += 1

    product_contract_ids = {
        "RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY",
        "RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY",
        "RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS",
    }
    for file in contract_files:
        document = documents[file]
        if document.get("result_contract_id") not in product_contract_ids:
            continue
        selection = document.get("instance_selection_policy", {})
        if (
            selection.get("required_match")
            != ["current_workflow_run", "current_reporting_period", "explicit_product", "validation_status_passed"]
            or selection.get("attempt_selection") != "latest_valid_attempt"
            or selection.get("random_selection_allowed") is not False
        ):
            errors.append(f"{file}: parameterized Result Contract instance selection is incomplete")
    checked += 1

    customer_mapping = documents.get(customer_mapping_file, {})
    duplicate_policy = customer_mapping.get("record_identity", {}).get("duplicate_key_policy", {})
    impression_validation = customer_mapping.get("validation", {}).get("impression_value_validation", {})
    grain = customer_mapping.get("customer_grain_execution", {})
    if (
        duplicate_policy.get("exclude_duplicate_business_key") is not True
        or duplicate_policy.get("automatic_merge_allowed") is not False
        or impression_validation.get("negative_behavior", {}).get("exclude_row") is not True
        or impression_validation.get("negative_behavior", {}).get(
            "excluded_before_change_materiality_and_ranking"
        )
        is not True
        or grain.get("owner_confirmed_final_grain") != "customer_id"
        or grain.get("aggregate_multiple_customer_ids_by_mapped_name") is not False
    ):
        errors.append(f"{customer_mapping_file}: customer duplicate, negative, or final-grain policy is invalid")
    customer_policy = documents.get(policy_file, {})
    if customer_policy.get("trigger", {}).get("default_threshold_value") != 10.0 or customer_policy.get(
        "comparison_inputs", {}
    ).get("row_integrity_before_cross_period_match", {}).get(
        "excluded_rows_enter_change_materiality_or_ranking"
    ) is not False:
        errors.append(f"{policy_file}: trigger or customer integrity policy is invalid")
    checked += 1

    output = documents.get(output_file, {})
    outlook = documents.get(outlook_file, {})
    if (
        output.get("result_field_consumption_contract", {}).get("output_allowed_value_statuses")
        != ["valid_value"]
        or output.get("result_field_consumption_contract", {}).get(
            "pending_confirmation_output_allowed"
        )
        is not False
        or outlook.get("assembly_constraints", {}).get("partial_draft_warning_required") is not True
        or outlook.get("assembly_constraints", {}).get("partial_draft_may_be_labeled_complete_success")
        is not False
        or outlook.get("output_target", {}).get("auto_send") is not False
    ):
        errors.append(f"{output_file}/{outlook_file}: output status or partial Draft policy is invalid")
    checked += 1

    external = documents.get(external_file, {})
    assets = external.get("external_assets", [])
    if len(assets) != 10 or any(not item.get("interface_schema") for item in assets):
        errors.append(f"{external_file}: every runtime local asset requires an interface schema")
    product_asset = next(
        (item for item in assets if item.get("asset_id") == "BR_APOLLO_PRODUCT_FILTER_MAPPING"),
        {},
    )
    product_fields = {
        item.get("field_id") for item in product_asset.get("interface_schema", {}).get("required_fields", [])
    }
    expected_product_fields = {
        "product_key",
        "target_ad_product_name",
        "apollo_filter_definition",
        "inventory_route_type",
        "commentary_resource_module",
        "fixed_display_order",
        "sell_through_trigger_threshold_pp",
        "customer_output_limit",
    }
    if product_fields != expected_product_fields or product_asset.get("interface_schema", {}).get(
        "name_similarity_inference_allowed"
    ) is not False:
        errors.append(f"{external_file}: product Mapping interface schema is incomplete")
    checked += 1

    store = documents.get(store_file, {}).get("mvp_physical_store_adapter_strategy", {})
    display_schema = store.get("configured_display_value_state_schema", {})
    if (
        store.get("physical_schema", {}).get("table_name") != "metric_results"
        or display_schema.get("table_name") != "configured_display_values"
        or display_schema.get("shares_same_sqlite_file_as_metric_results") is not True
        or display_schema.get("metric_result_table") is not False
        or display_schema.get("idempotent_unique_key", {}).get("columns")
        != ["policy_id", "workflow_id", "reporting_period_id"]
    ):
        errors.append(f"{store_file}: configured display value SQLite state contract is invalid")
    revenue_store = next(
        (
            item
            for item in documents.get(store_file, {}).get("metric_result_stores", [])
            if item.get("store_id") == "STORE_WEEKLY_REVENUE_HISTORICAL"
        ),
        {},
    )
    formula_write = revenue_store.get("write_policy", {}).get("formula_write_and_verification", {})
    if (
        len(formula_write.get("required_sequence", [])) != 5
        or formula_write.get("formula_text_only_check_is_sufficient") is not False
        or "Warning" not in formula_write.get("calculation_engine_unavailable_action", "")
        or "Continue Weekly Report" not in formula_write.get("weekly_report_continuation", "")
    ):
        errors.append(f"{store_file}: Revenue Excel recalculate and data-only re-read contract is incomplete")
    display_policy = documents.get(display_policy_file, {}).get("persistence_policy", {}).get(
        "physical_state_store", {}
    )
    if (
        display_policy.get("table_name") != "configured_display_values"
        or display_policy.get("shares_sqlite_file_with_metric_results") is not True
        or display_policy.get("metric_results_table_write") is not False
    ):
        errors.append(f"{display_policy_file}: configured display value persistence binding is invalid")
    checked += 1

    arc = documents.get(arc_file, {}).get("analysis_request_contract", {})
    brief_conversion = arc.get("brief_conversion", {})
    metric_resolution = brief_conversion.get("metric_resolution_policy", {})
    selection_contract = brief_conversion.get("dcp_selection_contract", {})
    conversion_stages = brief_conversion.get("conversion_stages", [])
    if (
        set(arc.get("required_fields", {}))
        != {
            "request_id",
            "domain",
            "intent",
            "period",
            "comparison",
            "dimensions",
            "metrics",
            "filters",
            "output",
        }
        or set(
            arc.get("required_fields", {}).get("period", {}).get("required_fields", [])
        )
        != {"semantics", "start_date", "end_date"}
        or brief_conversion.get("natural_language_brief_supported") is not True
        or brief_conversion.get("user_must_supply_metric_id") is not False
        or brief_conversion.get("user_must_supply_complete_dimensions_list") is not False
        or brief_conversion.get("business_asset_name_similarity_selection_allowed") is not False
        or len(conversion_stages) != 2
        or conversion_stages[0].get(
            "may_select_dataset_query_mapping_rule_metric_or_contract"
        )
        is not False
        or conversion_stages[1].get("selection_method")
        != "Exact registered domain, intent, and capability_scope_id plus requested capability inclusion and period/comparison compatibility."
        or arc.get("optional_fields", {}).get("capability_scope_id", {}).get("role")
        != "Canonical DCP capability selector resolved during Brief understanding; not a business asset ID."
        or brief_conversion.get("required_field_completion")
        != {
            "request_id": "generated_by_request_intake",
            "comparison_when_not_expressed": {"mode": "none"},
            "filters_when_not_expressed": [],
            "output_audience_when_not_expressed": "WORKFLOW_OWNER",
        }
        or metric_resolution.get("user_requested_metric_subset_allowed") is not True
        or metric_resolution.get("whole_dcp_metric_list_required") is not False
        or metric_resolution.get("metric_without_registered_canonical_id_allowed") is not False
        or selection_contract.get("exact_identity_fields")
        != ["domain", "intent", "capability_scope_id"]
        or selection_contract.get(
            "requested_dimensions_must_be_subset_of_supported_dimensions"
        )
        is not True
        or selection_contract.get("requested_metrics_must_be_subset_of_supported_metrics")
        is not True
        or selection_contract.get("period_and_comparison_must_be_supported") is not True
        or selection_contract.get("incompatible_period_or_comparison_action")
        != "request_owner_confirmation"
        or selection_contract.get("automatic_first_match_selection_allowed") is not False
    ):
        errors.append(f"{arc_file}: Analysis Request Contract template is incomplete")
    dcp = documents.get(dcp_file, {})
    required_operations = {
        "filter",
        "group_by",
        "sum",
        "avg",
        "count",
        "period_compare",
        "sort",
        "rank",
        "topN",
        "share",
        "dimension_decomposition",
    }
    profiles = {
        item.get("dcp_id"): item
        for item in dcp.get("capability_profiles", [])
        if isinstance(item, dict)
    }
    for dcp_id, profile in profiles.items():
        metadata = profile.get("metadata", {})
        period_contract = profile.get("period_comparison_contract", {})
        supported_metrics = set(metadata.get("supported_metrics", []))
        supported_comparisons = set(period_contract.get("supported_comparison_modes", []))
        metric_dependencies = period_contract.get("metric_comparison_dependencies", {})
        if (
            not metadata.get("domain")
            or not metadata.get("intents")
            or not metadata.get("capability_scope_id")
            or not metadata.get("supported_dimensions")
            or not supported_metrics
            or not period_contract.get("supported_period_semantics")
            or not supported_comparisons
            or period_contract.get("incompatible_request_action")
            != "request_owner_confirmation"
            or not set(metric_dependencies).issubset(supported_metrics)
            or any(mode not in supported_comparisons for mode in metric_dependencies.values())
        ):
            errors.append(
                f"{dcp_file}:{dcp_id}: supported capability or period/comparison contract is incomplete"
            )
    required_revenue_profiles = {
        "DCP_REVENUE_TECHNICAL_V1",
        "DCP_REVENUE_CTV_V1",
        "DCP_REVENUE_SMART_SPEAKER_V1",
        "DCP_REVENUE_FAST_VERSION_V1",
    }
    required_inventory_profiles = {
        "DCP_INVENTORY_FULL_SITE_V1",
        "DCP_INVENTORY_PATCH_V1",
        "DCP_INVENTORY_NON_PATCH_PRODUCT_V1",
        "DCP_INVENTORY_PRODUCT_ANALYSIS_V1",
    }
    expected_dcp_pipeline_bindings = {
        "DCP_REVENUE_TECHNICAL_V1": "PL_REVENUE_TECHNICAL_WEEKLY",
        "DCP_REVENUE_CTV_V1": "PL_REVENUE_CTV_WEEKLY",
        "DCP_REVENUE_SMART_SPEAKER_V1": "PL_REVENUE_SMART_SPEAKER_WEEKLY",
        "DCP_REVENUE_FAST_VERSION_V1": "PL_REVENUE_FAST_VERSION_WEEKLY",
        "DCP_INVENTORY_FULL_SITE_V1": "PL_INVENTORY_FULL_SITE_WEEKLY",
        "DCP_INVENTORY_PATCH_V1": "PL_INVENTORY_PATCH_WEEKLY",
        "DCP_INVENTORY_NON_PATCH_PRODUCT_V1": "PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY",
    }
    pipeline_index = {
        item.get("pipeline_id"): item
        for item in pipelines
        if isinstance(item, dict)
    }
    for dcp_id, pipeline_id in expected_dcp_pipeline_bindings.items():
        profile = profiles.get(dcp_id, {})
        pipeline = pipeline_index.get(pipeline_id, {})
        pipeline_dependencies = pipeline.get("dataset_dependencies", [])
        expected_datasets = {
            item.get("dataset_id") for item in pipeline_dependencies if isinstance(item, dict)
        }
        expected_queries = {
            item.get("query_asset_id")
            for item in pipeline_dependencies
            if isinstance(item, dict) and item.get("query_asset_id")
        }
        execution = pipeline.get("execution", {})
        if (
            set(profile.get("dataset_ids", [])) != expected_datasets
            or set(profile.get("query_asset_ids", [])) != expected_queries
            or set(profile.get("mapping_profile_ids", []))
            != set(execution.get("mapping_profile_ids", []))
            or set(profile.get("business_rule_dependencies", []))
            != set(execution.get("ordered_rule_set_ids", []))
            or set(profile.get("metric_variant_ids", []))
            != set(execution.get("metric_variant_ids", []))
            or set(profile.get("result_contract_ids", []))
            != set(pipeline.get("outputs", {}).get("result_contract_ids", []))
        ):
            errors.append(
                f"{dcp_file}:{dcp_id}: capability index does not exactly match {pipeline_id}"
            )
    catalog = dcp.get("brief_canonicalization_catalog", {})
    catalog_entries = catalog.get("entries", [])
    catalog_concept_ids = [
        item.get("canonical_concept_id") for item in catalog_entries if isinstance(item, dict)
    ]
    for entry in catalog_entries:
        arc_metadata = entry.get("arc_metadata", {}) if isinstance(entry, dict) else {}
        scope_id = arc_metadata.get("capability_scope_id")
        matching_profiles = [
            profile
            for profile in profiles.values()
            if profile.get("metadata", {}).get("capability_scope_id") == scope_id
        ]
        if len(matching_profiles) != 1:
            errors.append(
                f"{dcp_file}:{entry.get('canonical_concept_id')}: catalog entry must resolve to exactly one capability scope"
            )
            continue
        profile = matching_profiles[0]
        supported_metrics = set(profile.get("metadata", {}).get("supported_metrics", []))
        period_contract = profile.get("period_comparison_contract", {})
        supported_comparisons = set(period_contract.get("supported_comparison_modes", []))
        dependencies = period_contract.get("metric_comparison_dependencies", {})
        default_metrics = set(arc_metadata.get("metrics", []))
        comparison_additions = entry.get("comparison_metric_additions", {})
        if (
            not default_metrics.issubset(supported_metrics)
            or any(metric in dependencies for metric in default_metrics)
            or any(mode not in supported_comparisons for mode in comparison_additions)
            or any(
                metric not in supported_metrics or dependencies.get(metric) != mode
                for mode, metrics in comparison_additions.items()
                for metric in metrics
            )
        ):
            errors.append(
                f"{dcp_file}:{entry.get('canonical_concept_id')}: default or comparison Metric resolution is invalid"
            )
    matching_policy = dcp.get("matching_policy", {})
    if (
        set(dcp.get("allowed_standard_analysis_operations", [])) != required_operations
        or matching_policy.get("match_method") != "exact_identity_with_capability_inclusion"
        or matching_policy.get("required_capability_selection_metadata")
        != ["domain", "intent", "capability_scope_id", "period", "comparison", "dimensions", "metrics"]
        or matching_policy.get("exact_identity_fields")
        != ["domain", "intent", "capability_scope_id"]
        or matching_policy.get("capability_inclusion_rules")
        != {
            "dimensions": "requested_dimensions_subset_of_supported_dimensions",
            "metrics": "requested_metrics_subset_of_supported_metrics",
        }
        or matching_policy.get("period_comparison_compatibility_required") is not True
        or matching_policy.get("incompatible_period_or_comparison_action")
        != "request_owner_confirmation"
        or matching_policy.get(
            "brief_semantic_parsing_is_separate_from_asset_selection"
        )
        is not True
        or matching_policy.get("brief_parser_may_emit_business_asset_ids") is not False
        or matching_policy.get("name_similarity_inference_allowed") is not False
        or matching_policy.get("automatic_first_match_selection_allowed") is not False
        or catalog.get("match_method") != "explicitly_registered_term_or_canonical_id"
        or catalog.get("semantic_similarity_fallback_allowed") is not False
        or len(catalog_entries) != 7
        or len(catalog_concept_ids) != len(set(catalog_concept_ids))
        or set(brief_conversion.get("owner_confirmation_required_only_when", []))
        != {
            "no_unique_registered_canonical_match",
            "business_definition_or_metric_semantics_are_ambiguous",
            "requested_analysis_requires_a_new_business_definition",
            "period_or_comparison_not_supported_by_candidate_dcp",
        }
        or not required_revenue_profiles.issubset(profiles)
        or not required_inventory_profiles.issubset(profiles)
        or any(item.get("formula_copy") != "prohibited" for item in profiles.values())
        or any(not item.get("metadata", {}).get("capability_scope_id") for item in profiles.values())
        or dcp.get("operation_boundary", {}).get("may_create_new_business_metric_formula") is not False
        or dcp.get("operation_boundary", {}).get("one_time_request_creates_formal_workflow") is not False
    ):
        errors.append(f"{dcp_file}: DCP matching or operation boundary is incomplete")
    plan_fields = dcp.get("temporary_execution_plan_contract", {}).get(
        "required_fields", {}
    )
    if (
        plan_fields.get("exact_metadata_evidence", {}).get("semantics")
        != "exact_identity_fields_only"
        or plan_fields.get("capability_inclusion_evidence") != "object"
        or plan_fields.get("period_comparison_compatibility_evidence") != "object"
    ):
        errors.append(f"{dcp_file}: Temporary Execution Plan match evidence is incomplete")
    checked += 1

    scenarios = documents.get(scenarios_file, {})
    scenario_ids = {item.get("scenario_id") for item in scenarios.get("scenarios", [])}
    required_scenarios = {
        "normal_week",
        "manual_run_context",
        "backfill_run_context",
        "quarter_transition",
        "twelve_pp_anomaly_trigger",
        "qualified_customer_zero_rows",
        "multiple_products_with_repeated_attempt",
        "duplicate_latest_attempt_blocks_product",
        "duplicate_and_negative_customer_rows",
        "sqlite_idempotency_and_conflict",
        "partial_draft",
        "adhoc_brief_exact_dcp_match",
        "natural_language_revenue_brief_to_plan",
        "natural_language_inventory_brief_to_plan",
    }
    if (
        scenario_ids != required_scenarios
        or any(not item.get("expected_result") for item in scenarios.get("scenarios", []))
        or scenarios.get("contains_real_business_data") is not False
    ):
        errors.append(f"{scenarios_file}: synthetic final acceptance coverage is incomplete")
    semantic_case_ids = {
        item.get("case_id") for item in scenarios.get("dcp_semantic_test_cases", [])
    }
    if semantic_case_ids != {
        "comparison_none_excludes_comparison_metrics",
        "requested_metric_subset_matches_capability",
        "unsupported_period_is_rejected",
        "unsupported_comparison_is_rejected",
        "explicit_comparison_adds_registered_metrics",
    } or any(
        not item.get("expected_result")
        for item in scenarios.get("dcp_semantic_test_cases", [])
    ):
        errors.append(f"{scenarios_file}: DCP semantic acceptance coverage is incomplete")
    ci_text = (REPOSITORY_ROOT / ".github/workflows/validate-assets.yml").read_text(encoding="utf-8")
    if "python scripts/validate_final_acceptance.py" not in ci_text:
        errors.append(".github/workflows/validate-assets.yml: final acceptance suite is not in CI")
    checked += 1

    frozen_versions = documents.get(baseline_file, {}).get("frozen_asset_versions", {})
    if (
        frozen_versions.get("analysis_request_contract_template_version")
        != documents.get(arc_file, {}).get("template_version")
        or frozen_versions.get("dcp_registry_version")
        != documents.get(dcp_file, {}).get("registry_version")
        or frozen_versions.get("synthetic_final_acceptance_suite_version")
        != scenarios.get("suite_version")
        or frozen_versions.get("synthetic_final_acceptance_scenario_count")
        != len(scenario_ids)
    ):
        errors.append(
            f"{baseline_file}: frozen ARC, DCP, or synthetic acceptance version does not match its asset"
        )
    checked += 1

    gate = documents.get(gate_file, {})
    if (
        gate.get("status") != "ready_awaiting_explicit_owner_approval"
        or gate.get("gate_result") != "ready_awaiting_explicit_owner_approval"
        or gate.get("scope", {}).get("code_implementation_started") is not False
        or gate.get("implementation_entry_decision", {}).get("code_implementation_may_start") is not False
        or gate.get("governance", {}).get("outlook_auto_send") is not False
    ):
        errors.append(f"{gate_file}: Final Gate must return to ready while still waiting for Owner approval")
    checked += 1
    return checked


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
    """Keep every Workflow baseline, Status Index, and implementation Gate aligned."""
    baselines: dict[str, tuple[str, dict[str, Any]]] = {}
    status_indexes: dict[str, tuple[str, dict[str, Any]]] = {}
    code_gates: dict[str, tuple[str, dict[str, Any]]] = {}
    contract_workflows: dict[str, str] = {}
    pipelines_by_workflow: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for file, document in documents.items():
        if not isinstance(document, dict):
            continue
        workflow_id = document.get("workflow_id")
        config_type = document.get("config_type")
        if config_type == "implementation_baseline" and isinstance(workflow_id, str):
            baselines[workflow_id] = (file, document)
        elif config_type == "business_asset_status_index" and isinstance(workflow_id, str):
            status_indexes[workflow_id] = (file, document)
        elif config_type == "code_implementation_readiness_gate" and isinstance(workflow_id, str):
            code_gates[workflow_id] = (file, document)
        elif (
            config_type == "result_contract"
            and isinstance(workflow_id, str)
            and file.startswith("phase1_5/assets/result_contracts/RC_")
        ):
            contract_id = document.get("result_contract_id")
            if isinstance(contract_id, str):
                contract_workflows[contract_id] = workflow_id
        if config_type == "pipeline_registry":
            for pipeline in document.get("pipelines", []):
                if not isinstance(pipeline, dict):
                    continue
                for binding in pipeline.get("workflow_bindings", []):
                    bound_workflow = (
                        binding.get("workflow_id")
                        if isinstance(binding, dict)
                        else None
                    )
                    if isinstance(bound_workflow, str):
                        pipelines_by_workflow[bound_workflow].append(pipeline)

    workflow_ids = set(baselines) | set(status_indexes) | set(code_gates)
    checked = 0
    for workflow_id in sorted(workflow_ids):
        baseline_entry = baselines.get(workflow_id)
        status_entry = status_indexes.get(workflow_id)
        gate_entry = code_gates.get(workflow_id)
        if not all((baseline_entry, status_entry, gate_entry)):
            errors.append(
                f"{workflow_id}: Baseline, Status Index, and Code Gate must all exist"
            )
            continue
        baseline_file, baseline = baseline_entry
        status_file, status_index = status_entry
        _, code_gate = gate_entry

        indexed_gates = {
            item.get("gate_id"): item.get("status")
            for item in status_index.get("asset_stage_gates", [])
            if isinstance(item, dict)
        }
        for index, item in enumerate(baseline.get("frozen_gate_results", [])):
            if not isinstance(item, dict):
                errors.append(
                    f"{baseline_file}:frozen_gate_results[{index}]: expected mapping"
                )
                continue
            gate_id = item.get("gate_id")
            if indexed_gates.get(gate_id) != item.get("status"):
                errors.append(
                    f"{baseline_file}:frozen_gate_results[{index}]: status does not "
                    f"match Status Index for {gate_id}"
                )
            checked += 1

        baseline_index = status_index.get("implementation_baseline", {})
        baseline_index_keys = ["baseline_id", "baseline_version", "status"]
        baseline_index_keys.extend(
            key
            for key in ("freeze_date", "freeze_revision_status")
            if key in baseline_index
        )
        for key in baseline_index_keys:
            if baseline_index.get(key) != baseline.get(key):
                errors.append(
                    f"{status_file}:implementation_baseline.{key}: does not match "
                    f"the {workflow_id} baseline asset"
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
                errors.append(
                    f"{baseline_file}: implementation approval gate requires {key}=false"
                )
            checked += 1

        workflow_contracts = {
            contract_id
            for contract_id, contract_workflow in contract_workflows.items()
            if contract_workflow == workflow_id
        }
        workflow_variant_count = 0
        for document in documents.values():
            if not isinstance(document, dict):
                continue
            for variant in document.get("metric_variants", []):
                if not isinstance(variant, dict):
                    continue
                target_contract = variant.get("output_binding", {}).get(
                    "result_contract_id"
                )
                if target_contract in workflow_contracts:
                    workflow_variant_count += 1
        expected_counts = {
            "external_asset_reference_count": len(
                documents.get(
                    "phase1_5/assets/external_asset_references.yaml", {}
                ).get("external_assets", [])
            ),
            "metric_variant_count": workflow_variant_count,
            "result_contract_count": len(workflow_contracts),
        }
        if workflow_id == "WF_WEEKLY_BUSINESS_REPORT":
            expected_counts["field_mapping_profile_count"] = sum(
                1
                for file, document in documents.items()
                if file.startswith("phase1_5/assets/field_mappings/MAP_")
                and isinstance(document, dict)
                and isinstance(document.get("mapping_profile_id"), str)
            )
        frozen_versions = baseline.get("frozen_asset_versions", {})
        for key, expected in expected_counts.items():
            if key not in frozen_versions:
                continue
            if frozen_versions.get(key) != expected:
                errors.append(
                    f"{baseline_file}:frozen_asset_versions.{key}: expected {expected} "
                    f"for {workflow_id}"
                )
            checked += 1

        declared_dependency_counts = baseline.get("workflow_dependency_counts")
        if declared_dependency_counts is not None:
            workflow_pipelines = pipelines_by_workflow.get(workflow_id, [])

            def distinct_values(values: list[Any]) -> set[str]:
                return {value for value in values if isinstance(value, str) and value}

            dataset_dependency_count = sum(
                1
                for pipeline in workflow_pipelines
                for dependency in pipeline.get("dataset_dependencies", [])
                if isinstance(dependency, dict)
            )
            mapping_ids = distinct_values(
                [
                    mapping_id
                    for pipeline in workflow_pipelines
                    for mapping_id in pipeline.get("execution", {}).get(
                        "mapping_profile_ids", []
                    )
                ]
            )
            rule_ids = distinct_values(
                [
                    rule_id
                    for pipeline in workflow_pipelines
                    for rule_id in pipeline.get("execution", {}).get(
                        "ordered_rule_set_ids", []
                    )
                ]
            )
            variant_ids = distinct_values(
                [
                    variant_id
                    for pipeline in workflow_pipelines
                    for variant_id in pipeline.get("execution", {}).get(
                        "metric_variant_ids", []
                    )
                ]
            )
            result_contract_ids = distinct_values(
                [
                    contract_id
                    for pipeline in workflow_pipelines
                    for contract_id in pipeline.get("outputs", {}).get(
                        "result_contract_ids", []
                    )
                ]
            )
            output_mapping_ids = distinct_values(
                [
                    mapping_id
                    for pipeline in workflow_pipelines
                    for mapping_id in pipeline.get("outputs", {}).get(
                        "output_mapping_ids", []
                    )
                ]
            )
            policy_ids = distinct_values(
                [
                    pipeline.get("execution", {}).get("source_wait_policy_id")
                    for pipeline in workflow_pipelines
                ]
            )
            external_asset_ids = distinct_values(
                [
                    dependency.get("external_asset_id")
                    for pipeline in workflow_pipelines
                    for dependency in pipeline.get("local_input_dependencies", [])
                    if isinstance(dependency, dict)
                ]
            )
            expected_dependency_counts = {
                "pipeline_count": len(workflow_pipelines),
                "dataset_dependency_count": dataset_dependency_count,
                "mapping_profile_dependency_count": len(mapping_ids),
                "business_rule_dependency_count": len(rule_ids),
                "metric_variant_dependency_count": len(variant_ids),
                "result_contract_dependency_count": len(result_contract_ids),
                "output_mapping_dependency_count": len(output_mapping_ids),
                "policy_dependency_count": len(policy_ids),
                "external_asset_dependency_count": len(external_asset_ids),
            }
            unknown_count_keys = set(declared_dependency_counts) - set(
                expected_dependency_counts
            )
            if unknown_count_keys:
                errors.append(
                    f"{baseline_file}:workflow_dependency_counts contains unknown keys "
                    f"{sorted(unknown_count_keys)}"
                )
            for count_key, declared_count in declared_dependency_counts.items():
                if expected_dependency_counts.get(count_key) != declared_count:
                    errors.append(
                        f"{baseline_file}:workflow_dependency_counts.{count_key} must "
                        f"be isolated to {workflow_id}; expected "
                        f"{expected_dependency_counts.get(count_key)!r}"
                    )
                checked += 1

        if workflow_id == "WF_WEEKLY_BUSINESS_REPORT":
            store_gate_file = (
                "phase1_5/assets/metric_stores/metric_result_store_readiness_matrix.yaml"
            )
            store_gate = documents.get(store_gate_file, {})
            if store_gate.get("gate_conclusion", {}).get(
                "code_implementation_start"
            ) != "wait_for_explicit_owner_approval" or gate_decision.get(
                "code_implementation_start"
            ) != "wait_for_explicit_owner_approval":
                errors.append(
                    "Metric Store and final Code Implementation Gates must both "
                    "wait for explicit Owner approval"
                )
            if baseline.get("change_control", {}).get(
                "repository_commit_binding_status"
            ) != "freeze_candidate_reviewed":
                errors.append(f"{baseline_file}: repository publication state is stale")
            checked += 2

    stale_publication_paths: list[str] = []

    def find_stale_publication_state(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            if value.get("committed_or_pushed") is False:
                stale_publication_paths.append(path or "<root>")
            for key, child in value.items():
                find_stale_publication_state(
                    child, f"{path}.{key}" if path else str(key)
                )
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
    checked += 1

    base_sha = os.environ.get("ASSET_VALIDATION_BASE_SHA", "").strip()
    explicit_validation_base = bool(re.fullmatch(r"[0-9a-fA-F]{40}", base_sha))
    if not explicit_validation_base:
        base_sha = ""
    if base_sha:
        changed_result = subprocess.run(
            ["git", "diff", "--name-only", base_sha, "--"],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if changed_result.returncode != 0:
            errors.append(
                f"cannot enumerate assets changed from Base SHA {base_sha}"
            )
    for workflow_id, (baseline_file, baseline) in baselines.items():
        impact_review = baseline.get("change_control", {}).get("latest_baseline_version_impact_review", {})
        if str(baseline.get("last_refreeze_date")) != "2026-08-09" or baseline.get(
            "freeze_revision_status"
        ) != "refrozen_after_design_contract_closure":
            errors.append(f"{baseline_file}: 2026-08-09 refreeze metadata is missing")
        if str(impact_review.get("review_date")) != "2026-08-09" or impact_review.get(
            "change_class"
        ) != "phase1_5_design_contract_closure" or impact_review.get(
            "behavior_or_output_change"
        ) is not True or impact_review.get("code_implementation_started") is not False or impact_review.get(
            "baseline_version_increment_required"
        ) is not False:
            errors.append(f"{baseline_file}: latest design-contract-closure impact review is incomplete")
        checked += 7
        lineage = baseline.get("git_lineage", {})
        review_base_sha = lineage.get("review_base_main_sha", "")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", review_base_sha):
            errors.append(f"{baseline_file}: git_lineage.review_base_main_sha is not a commit SHA")
        else:
            lineage_validation_target = base_sha if explicit_validation_base else "HEAD"
            historical_base = subprocess.run(
                ["git", "merge-base", "--is-ancestor", review_base_sha, lineage_validation_target],
                cwd=REPOSITORY_ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if historical_base.returncode != 0:
                errors.append(
                    f"{baseline_file}: freeze-time review Base SHA is not an ancestor "
                    "of the current validation base or target"
                )
        if baseline.get("publication_state") != "freeze_lineage_recorded":
            errors.append(f"{baseline_file}: publication state must use merge-neutral freeze lineage semantics")
        if lineage.get("lineage_semantics") != "freeze_time_historical_review_record" or lineage.get(
            "current_repository_merge_state_claimed"
        ) is not False:
            errors.append(f"{baseline_file}: freeze-time lineage must not assert current merge state")
        if lineage.get("frozen_candidate_commit_sha", {}).get("value_source") != "validation_target_HEAD":
            errors.append(f"{baseline_file}: frozen candidate commit SHA binding is missing")
        if lineage.get("frozen_candidate_tree_sha", {}).get("value_source") != "validation_target_HEAD^{tree}":
            errors.append(f"{baseline_file}: frozen candidate tree SHA binding is missing")
        if lineage.get("review_base_and_candidate_sha_semantics_must_not_be_conflated") is not True:
            errors.append(f"{baseline_file}: review Base and Candidate SHA semantics are conflated")
        checked += 7
        checked += 1
    return checked


def validate_active_tbd_classification(
    documents: dict[str, Any], errors: list[str]
) -> int:
    """Require every literal active-asset TBD to have one explicit classification."""

    classified_files = {
        "phase1_5/assets/data_sources/data_source_inventory.yaml",
        "phase1_5/assets/datasets/dataset_inventory.yaml",
        "phase1_5/assets/field_mappings/MAP_REVENUE_CTV_EXCL_PLACEMENT_QTD_V1.yaml",
        "phase1_5/assets/field_mappings/MAP_REVENUE_SALES_ROLLING_DECK_QTD_V1.yaml",
        "phase1_5/assets/field_mappings/MAP_REVENUE_SALES_ROLLING_DECK_QTD_BUSINESS_LINE_V1.yaml",
        "phase1_5/assets/metric_stores/metric_result_store_registry.yaml",
    }
    categories = ("blocking", "runtime-only", "not-required-for-MVP", "superseded")
    occurrences: dict[str, list[str]] = defaultdict(list)

    def collect(value: Any, file: str, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                collect(child, file, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect(child, file, f"{path}[{index}]")
        elif isinstance(value, str) and re.search(
            r"(?<![A-Za-z0-9])TBD(?:\\?_|(?![A-Za-z0-9]))",
            value,
            re.IGNORECASE,
        ):
            occurrences[file].append(path)

    for file, document in documents.items():
        if file.startswith("phase1_5/assets/"):
            collect(document, file)

    unexpected_files = set(occurrences) - classified_files
    if unexpected_files:
        errors.append(
            "literal active-asset TBD remains outside the classification registry: "
            + ", ".join(sorted(unexpected_files))
        )

    checked = 0
    for file in sorted(classified_files):
        classification = documents.get(file, {}).get("remaining_tbd_classification", {})
        if classification.get("literal_placeholder_coverage") != "all_remaining_occurrences":
            errors.append(f"{file}: literal placeholder coverage must include all remaining occurrences")
        if classification.get("unclassified_occurrence_count") != 0:
            errors.append(f"{file}: unclassified_occurrence_count must be zero")
        if classification.get("business_value_inference_allowed") is not False:
            errors.append(f"{file}: business value inference must remain prohibited")

        covered_paths: set[str] = set()
        for category in categories:
            category_value = classification.get(category)
            if isinstance(category_value, dict):
                paths = category_value.get("covered_field_path_patterns", [])
            elif category_value == []:
                paths = []
            else:
                errors.append(f"{file}: missing or invalid TBD category {category}")
                paths = []
            covered_paths.update(str(item) for item in paths)

        normalized_occurrences = {
            re.sub(r"\[\d+\]", "[*]", path) for path in occurrences.get(file, [])
        }
        uncovered = normalized_occurrences - covered_paths
        if uncovered:
            errors.append(f"{file}: unclassified literal TBD paths {sorted(uncovered)}")
        checked += len(occurrences.get(file, [])) + len(categories) + 3

    return checked


def validate_weekly_canonical_rule_context_bindings(
    documents: dict[str, Any], errors: list[str]
) -> int:
    """Close every approved Weekly Rule Context field without runtime aliases."""

    runtime_file = (
        "phase1_5/assets/execution/"
        "weekly_workflow_runtime_contracts_v1_2_candidate.yaml"
    )
    revenue_metric_file = "phase1_5/assets/metrics/metric_library_revenue_technical_ctv_v1.yaml"
    store_file = "phase1_5/assets/metric_stores/metric_result_store_registry.yaml"
    scenarios_file = "phase1_5/tests/final_acceptance_scenarios.yaml"
    runtime = documents.get(runtime_file, {})
    binding_contract = runtime.get("canonical_rule_context_bindings", {})
    active_rules: dict[str, tuple[str, dict[str, Any]]] = {}
    for file, document in documents.items():
        if not isinstance(document, dict) or document.get("config_type") != "business_rule":
            continue
        if document.get("approval_status") != "approved":
            continue
        if "WF_WEEKLY_BUSINESS_REPORT" not in document.get("applicable_workflow_ids", []):
            continue
        rule_id = document.get("rule_id")
        if isinstance(rule_id, str):
            active_rules[rule_id] = (file, document)

    declared_rule_ids = set(binding_contract) - {"validation"}
    if declared_rule_ids != set(active_rules):
        errors.append(
            f"{runtime_file}: canonical Rule bindings must exactly cover approved Weekly Rules; "
            f"expected {sorted(active_rules)}, got {sorted(declared_rule_ids)}"
        )

    checked = 0
    for rule_id, (rule_file, rule) in sorted(active_rules.items()):
        required_fields = rule.get("inputs", {}).get("required_context_fields", [])
        bindings = binding_contract.get(rule_id, {})
        if set(bindings) != set(required_fields):
            errors.append(
                f"{runtime_file}:{rule_id}: Context bindings do not exactly match "
                f"{rule_file} required_context_fields"
            )
        for field_id in required_fields:
            binding = bindings.get(field_id, {})
            if binding.get("canonical_field_id") != field_id:
                errors.append(f"{runtime_file}:{rule_id}.{field_id}: canonical field ID mismatch")
            for required_key in ("source", "derivation", "lock"):
                if not isinstance(binding.get(required_key), str) or not binding[required_key].strip():
                    errors.append(f"{runtime_file}:{rule_id}.{field_id}: missing {required_key}")
            checked += 4

        standard_fields = rule.get("inputs", {}).get("required_standard_fields", [])
        if isinstance(standard_fields, str) and re.search(
            r"(?<![A-Za-z0-9])TBD(?:\\?_|(?![A-Za-z0-9]))",
            standard_fields,
            re.IGNORECASE,
        ):
            errors.append(f"{rule_file}: Active required_standard_fields retains a TBD placeholder")
        checked += 1

    validation = binding_contract.get("validation", {})
    if validation.get("runtime_alias_guessing_allowed") is not False:
        errors.append(f"{runtime_file}: runtime Context alias guessing must be false")
    checked += 1

    mapping_fields: dict[str, set[str]] = {}
    for document in documents.values():
        if not isinstance(document, dict) or not isinstance(document.get("mapping_profile_id"), str):
            continue
        mapping_fields[document["mapping_profile_id"]] = {
            item.get("standard_field_id")
            for item in document.get("field_mappings", [])
            if isinstance(item, dict) and isinstance(item.get("standard_field_id"), str)
        }

    for rule_id, (rule_file, rule) in active_rules.items():
        inputs = rule.get("inputs", {})
        profile_id = inputs.get("required_mapping_profile_id")
        fields = inputs.get("required_standard_fields")
        if isinstance(profile_id, str) and isinstance(fields, list):
            missing = set(fields) - mapping_fields.get(profile_id, set())
            if missing:
                errors.append(f"{rule_file}: required fields absent from {profile_id}: {sorted(missing)}")
            checked += len(fields) + 1
        role_fields = inputs.get("required_standard_fields_by_source_role", {})
        if isinstance(role_fields, dict):
            for role, declaration in role_fields.items():
                if not isinstance(declaration, dict):
                    errors.append(f"{rule_file}: invalid source-role field declaration {role}")
                    continue
                role_profile = declaration.get("mapping_profile_id")
                declared_fields = declaration.get("fields", [])
                missing = set(declared_fields) - mapping_fields.get(role_profile, set())
                if missing:
                    errors.append(f"{rule_file}:{role}: required fields absent from {role_profile}: {sorted(missing)}")
                checked += len(declared_fields) + 1
        serialized_inputs = yaml.safe_dump(inputs, allow_unicode=True)
        if "fiscal_quarter_or_source_period_range" in serialized_inputs:
            errors.append(f"{rule_file}: nonexistent combined fiscal-quarter/source-period field remains")
        checked += 1

    technical_prior_rule_file = (
        "phase1_5/assets/business_rules/"
        "BR_REVENUE_PRIOR_YEAR_COMPARABLE_SOURCE_SELECTION_V1.yaml"
    )
    technical_qtd_mapping_file = (
        "phase1_5/assets/field_mappings/"
        "MAP_REVENUE_SALES_ROLLING_DECK_QTD_V1.yaml"
    )
    complete_quarter_mapping_file = (
        "phase1_5/assets/field_mappings/"
        "MAP_REVENUE_SALES_ROLLING_DECK_QTD_BUSINESS_LINE_V1.yaml"
    )
    technical_prior_rule = documents.get(technical_prior_rule_file, {})
    technical_qtd_mapping = documents.get(technical_qtd_mapping_file, {})
    complete_quarter_mapping = documents.get(complete_quarter_mapping_file, {})
    eligibility_binding = technical_prior_rule.get(
        "technical_qtd_eligibility_binding", {}
    )
    registration = technical_qtd_mapping.get("scope", {}).get(
        "prior_year_comparable_qtd_registration", {}
    )
    if (
        technical_prior_rule.get("inputs", {}).get("required_mapping_profile_id")
        != "MAP_REVENUE_SALES_ROLLING_DECK_QTD_V1"
        or eligibility_binding.get("binding_type")
        != "prior_year_role_context_adapter"
        or eligibility_binding.get("semantic_authority_rule_id")
        != "BR_REVENUE_TECHNICAL_SINGLE_COUNT_ELIGIBILITY_V1"
        or eligibility_binding.get("input_role") != "prior_year_comparable"
        or eligibility_binding.get("source_mapping_profile_id")
        != "MAP_REVENUE_SALES_ROLLING_DECK_QTD_V1"
        or eligibility_binding.get("source_standard_fields")
        != {
            "performance": "performance_revenue_amount",
            "executed": "executed_revenue_amount",
        }
        or eligibility_binding.get("source_raw_field_independence_required") is not True
        or eligibility_binding.get("complete_quarter_business_line_equivalence_allowed")
        is not False
        or eligibility_binding.get("frozen_semantic_authority_modified") is not False
        or eligibility_binding.get("registration_status") != "registered"
    ):
        errors.append(
            f"{technical_prior_rule_file}: Technical prior-year QTD authority "
            "registration is incomplete"
        )
    if (
        technical_qtd_mapping.get("scope", {}).get("usage_contexts")
        != ["current_quarter_qtd", "prior_year_comparable_qtd"]
        or registration.get("input_role") != "prior_year_comparable"
        or registration.get("performance_source_mapping_entry_id") != "FM016"
        or registration.get("executed_source_mapping_entry_id") != "FM017"
        or registration.get("source_raw_fields_must_be_distinct") is not True
        or registration.get("complete_quarter_equivalence_allowed") is not False
        or registration.get("registration_status") != "registered"
    ):
        errors.append(
            f"{technical_qtd_mapping_file}: prior-year QTD Mapping registration is incomplete"
        )
    qtd_mapping_entries = {
        item.get("mapping_entry_id"): item
        for item in technical_qtd_mapping.get("field_mappings", [])
        if isinstance(item, dict)
    }
    qtd_raw_fields = {
        item.get("raw_field_inventory_id"): item
        for item in technical_qtd_mapping.get("raw_field_inventory", [])
        if isinstance(item, dict)
    }
    performance_entry = qtd_mapping_entries.get("FM016", {})
    executed_entry = qtd_mapping_entries.get("FM017", {})
    performance_raw = qtd_raw_fields.get(
        performance_entry.get("raw_field_inventory_id"), {}
    )
    executed_raw = qtd_raw_fields.get(executed_entry.get("raw_field_inventory_id"), {})
    if (
        performance_entry.get("standard_field_id") != "performance_revenue_amount"
        or executed_entry.get("standard_field_id") != "executed_revenue_amount"
        or performance_raw.get("raw_column") != "U"
        or executed_raw.get("raw_column") != "X"
        or performance_entry.get("raw_field_inventory_id")
        == executed_entry.get("raw_field_inventory_id")
    ):
        errors.append(
            f"{technical_qtd_mapping_file}: Technical QTD performance/executed "
            "sources must remain independent U/X fields"
        )
    complete_scope = complete_quarter_mapping.get("scope", {})
    complete_entries = {
        item.get("mapping_entry_id"): item
        for item in complete_quarter_mapping.get("field_mappings", [])
        if isinstance(item, dict)
    }
    if (
        complete_scope.get("usage_contexts")
        != ["quarter_transition_previous_quarter_final"]
        or "prior_year_comparable_qtd"
        not in complete_scope.get("excluded_usage_contexts", [])
        or "仅在完整季度收入场景中"
        not in str(complete_entries.get("FM004", {}).get("contextual_equivalence", ""))
    ):
        errors.append(
            f"{complete_quarter_mapping_file}: complete-quarter equivalence is not "
            "fail-closed outside its registered context"
        )
    checked += 30

    context_fields = runtime.get("workflow_run_context", {}).get("required_fields", {})
    if "target_business_line" in context_fields:
        errors.append(f"{runtime_file}: target_business_line must not be a Workflow-scoped scalar")
    scoped_bindings = runtime.get("pipeline_scoped_rule_context_bindings", {})
    registry_file = "phase1_5/assets/pipelines/pipeline_registry.yaml"
    pipelines = {
        item.get("pipeline_id"): item
        for item in documents.get(registry_file, {}).get("pipelines", [])
        if isinstance(item, dict) and isinstance(item.get("pipeline_id"), str)
    }
    technical_pipeline = pipelines.get("PL_REVENUE_TECHNICAL_WEEKLY", {})
    technical_execution = technical_pipeline.get("execution", {})
    technical_historical = [
        item
        for item in technical_pipeline.get("historical_input_dependencies", [])
        if isinstance(item, dict)
        and item.get("relationship_rule_id")
        == "BR_REVENUE_PRIOR_YEAR_COMPARABLE_SOURCE_SELECTION_V1"
    ]
    technical_conditional = {
        item.get("mapping_profile_id"): item
        for item in technical_execution.get("conditional_mapping_profile_ids", [])
        if isinstance(item, dict)
    }
    if (
        technical_execution.get("mapping_profile_ids")
        != ["MAP_REVENUE_SALES_ROLLING_DECK_QTD_V1"]
        or technical_conditional.get(
            "MAP_REVENUE_SALES_ROLLING_DECK_QTD_BUSINESS_LINE_V1", {}
        ).get("applies_when")
        != "Quarter-transition previous-quarter complete business-line result is required"
        or len(technical_historical) != 1
    ):
        errors.append(
            f"{registry_file}: Technical prior-year QTD Mapping route is not unique"
        )
    else:
        historical = technical_historical[0]
        physical_bindings = historical.get("physical_store_value_bindings", {})
        historical_eligibility = historical.get(
            "technical_qtd_eligibility_binding", {}
        )
        if (
            historical.get("dataset_id") != "DS_REVENUE_SALES_ROLLING_DECK_QTD"
            or historical.get("run_input_manifest_required") is not True
            or historical.get("run_input_role") != "prior_year_comparable"
            or historical.get("mapping_profile_id")
            != "MAP_REVENUE_SALES_ROLLING_DECK_QTD_V1"
            or historical.get("required_for_store_physical_fields") != ["D", "E"]
            or historical_eligibility.get("rule_id")
            != "BR_REVENUE_TECHNICAL_SINGLE_COUNT_ELIGIBILITY_V1"
            or historical_eligibility.get("binding_type")
            != "prior_year_role_context_adapter"
            or historical.get("complete_quarter_performance_executed_equivalence_allowed")
            is not False
            or physical_bindings.get("D", {}).get("standard_field_id")
            != "performance_revenue_amount"
            or physical_bindings.get("E", {}).get("standard_field_id")
            != "executed_revenue_amount"
            or physical_bindings.get("D", {}).get("metric_variant_id")
            != "MV_REVENUE_TECHNICAL_QTD_PERFORMANCE_V1"
            or physical_bindings.get("E", {}).get("metric_variant_id")
            != "MV_REVENUE_TECHNICAL_QTD_EXECUTED_V1"
        ):
            errors.append(
                f"{registry_file}: Technical prior-year QTD D/E source binding is incomplete"
            )
    checked += 16
    target_rules = {
        rule_id: rule
        for rule_id, (_, rule) in active_rules.items()
        if "target_business_line" in rule.get("inputs", {}).get("required_context_fields", [])
    }
    expected_pipeline_rules: dict[str, set[str]] = defaultdict(set)
    for rule_id, rule in target_rules.items():
        for pipeline_id in rule.get("applicable_pipeline_ids", []):
            expected_pipeline_rules[pipeline_id].add(rule_id)
    declared_pipeline_ids = set(scoped_bindings) - {"validation"}
    if declared_pipeline_ids != set(expected_pipeline_rules):
        errors.append(f"{runtime_file}: Pipeline-scoped business-line bindings do not exactly cover applicable Pipelines")
    expected_values = {
        "PL_REVENUE_TECHNICAL_WEEKLY": "Technical",
        "PL_REVENUE_CTV_WEEKLY": "CTV",
        "PL_REVENUE_SMART_SPEAKER_WEEKLY": "Smart Speaker",
        "PL_REVENUE_FAST_VERSION_WEEKLY": "Fast Version",
    }
    for pipeline_id, rule_ids in expected_pipeline_rules.items():
        pipeline = pipelines.get(pipeline_id, {})
        registry_binding = pipeline.get("pipeline_rule_context_bindings", {}).get("target_business_line", {})
        runtime_binding = scoped_bindings.get(pipeline_id, {}).get("target_business_line", {})
        if registry_binding.get("scope") != "pipeline" or registry_binding.get("display_name_inference_allowed") is not False:
            errors.append(f"{registry_file}:{pipeline_id}: target_business_line binding must be exact and Pipeline-scoped")
        if registry_binding.get("value") != expected_values.get(pipeline_id) or runtime_binding.get("value") != expected_values.get(pipeline_id):
            errors.append(f"{pipeline_id}: target_business_line exact value mismatch")
        if set(registry_binding.get("applicable_rule_ids", [])) != rule_ids or set(runtime_binding.get("applies_to_rule_ids", [])) != rule_ids:
            errors.append(f"{pipeline_id}: target_business_line Rule cardinality mismatch")
        if pipeline_id in {"PL_REVENUE_SMART_SPEAKER_WEEKLY", "PL_REVENUE_FAST_VERSION_WEEKLY"} and (
            registry_binding.get("value_source") != "exact_registered_pipeline_business_line"
            or registry_binding.get("value") != pipeline.get("business_line")
        ):
            errors.append(f"{pipeline_id}: existing registered business identity must be used without display-name inference")
        checked += 7

    dataset_file = "phase1_5/assets/datasets/dataset_inventory.yaml"
    datasets = {
        item.get("dataset_id"): item
        for item in documents.get(dataset_file, {}).get("datasets", [])
        if isinstance(item, dict)
    }
    rolling_rule = datasets.get("DS_REVENUE_SALES_ROLLING_DECK_QTD", {}).get("acquisition", {}).get(
        "source_object_or_attachment_rule", {}
    ).get("executable_version_selection_rule", {}).get("locked_weekly_date_binding", {})
    ctv_rule = datasets.get("DS_REVENUE_CTV_EXCL_PLACEMENT_QTD", {}).get("acquisition", {}).get(
        "source_object_or_attachment_rule", {}
    ).get("executable_selection_rule", {})
    if rolling_rule.get("selected_email_subject_date_must_equal") != "Workflow Run Context workflow_reporting_date" or rolling_rule.get(
        "generic_cutoff_date_selection_allowed"
    ) is not False:
        errors.append(f"{dataset_file}: Technical current Rolling Deck selection date binding is incomplete")
    ctv_dates = ctv_rule.get("manifest_date_bindings", {})
    if ctv_rule.get("primary_condition") != "Subject date equals locked Workflow Run Context workflow_reporting_date" or ctv_dates.get(
        "source_report_date_may_substitute_for_business_cutoff"
    ) is not False or ctv_dates.get("generic_cutoff_date_selection_allowed") is not False:
        errors.append(f"{dataset_file}: CTV current source date binding is incomplete")
    checked += 6

    report_mode_rule = active_rules.get("BR_WEEKLY_REVENUE_REPORT_MODE_SELECTION_V1", ("", {}))[1]
    manual = report_mode_rule.get("manual_execution", {})
    if manual.get("required_inputs") != ["explicit_target_report_period", "explicit_workflow_reporting_date"] or manual.get(
        "explicit_revenue_cutoff_date_is_report_mode_input"
    ) is not False:
        errors.append("BR_WEEKLY_REVENUE_REPORT_MODE_SELECTION_V1: manual inputs retain Revenue cutoff semantics")
    checked += 2

    canonical_fields = runtime.get("canonical_context_field_contracts", {})
    expected_previous = canonical_fields.get("expected_previous_revenue_workflow_reporting_date", {})
    previous_output_contract = report_mode_rule.get("actions", {}).get("previous_output_selection_contract", {})
    if (
        expected_previous.get("derivation") != "subtract exactly 7 calendar days under the locked Weekly cadence"
        or expected_previous.get("successful-output-selection_influence_allowed") is not False
        or expected_previous.get("older-period fallback") is not False
        or previous_output_contract.get("older_successful_output_fallback_allowed") is not False
        or previous_output_contract.get("report_mode_must_not_depend_on_output_existence") is not True
    ):
        errors.append(f"{runtime_file}: adjacent previous Revenue reporting-period contract is incomplete")
    checked += 5

    revenue_contract_files = [
        "phase1_5/assets/result_contracts/RC_REVENUE_TECHNICAL_WEEKLY.yaml",
        "phase1_5/assets/result_contracts/RC_REVENUE_CTV_WEEKLY.yaml",
        "phase1_5/assets/result_contracts/RC_REVENUE_SMART_SPEAKER_WEEKLY.yaml",
        "phase1_5/assets/result_contracts/RC_REVENUE_FAST_VERSION_WEEKLY.yaml",
    ]
    required_revenue_lineage = {"workflow_reporting_date", "current_revenue_cutoff_date"}
    for contract_file in revenue_contract_files:
        lineage = documents.get(contract_file, {}).get("lineage", {})
        fields = set(lineage.get("required_instance_fields", []))
        if (
            not required_revenue_lineage.issubset(fields)
            or "cutoff_date" in fields
            or lineage.get("revenue_business_cutoff_field") != "current_revenue_cutoff_date"
            or lineage.get("generic_cutoff_date_allowed") is not False
        ):
            errors.append(f"{contract_file}: Revenue date lineage is incomplete or uses generic cutoff_date")
        checked += 4

    revenue_metrics = {
        item.get("metric_variant_id"): item
        for item in documents.get(revenue_metric_file, {}).get("metric_variants", [])
        if isinstance(item, dict)
    }
    incremental_id = "MV_REVENUE_TECHNICAL_WEEKLY_INCREMENTAL_EXECUTED_V1"
    wow_dependency = revenue_metrics.get(
        "MV_REVENUE_TECHNICAL_WEEKLY_INCREMENTAL_EXECUTED_WOW_V1", {}
    ).get("denominator_result_dependency", {})
    yoy_dependency = revenue_metrics.get(
        "MV_REVENUE_TECHNICAL_WEEKLY_INCREMENTAL_EXECUTED_YOY_V1", {}
    ).get("denominator_result_dependency", {})
    for dependency in (wow_dependency, yoy_dependency):
        if (
            dependency.get("metric_variant_id") != incremental_id
            or dependency.get("store_asset_id") != "STORE_ASSET_WEEKLY_REVENUE_TECHNICAL"
            or dependency.get("validation_status_required") != "passed"
        ):
            errors.append(f"{revenue_metric_file}: Technical incremental denominator binding is not exact")
    if (
        wow_dependency.get("reporting_period_selection")
        != "exactly equals expected_previous_revenue_workflow_reporting_date"
        or wow_dependency.get("older_successful_period_fallback_allowed") is not False
        or yoy_dependency.get("owner_confirmation")
        != "confirmed_primary_store_result_with_exact_dual_qtd_reconstruction_fallback_2026-08-20"
        or yoy_dependency.get("unique_source_required") is not True
        or yoy_dependency.get("qtd_or_full_quarter_executed_revenue_amount_equivalence_allowed") is not False
    ):
        errors.append(f"{revenue_metric_file}: Technical incremental WoW/YoY dependency policy is incomplete")
    checked += 10
    yoy_fallback = yoy_dependency.get("fallback_reconstruction", {})
    previous_snapshot = yoy_fallback.get(
        "previous_prior_year_qtd_executed_snapshot", {}
    )
    if (
        yoy_fallback.get("allowed_when")
        != "primary_exact_store_result_not_found_only"
        or yoy_fallback.get("reconstruction_metric_variant_id") != incremental_id
        or yoy_fallback.get("exact_date_and_validated_lineage_required") is not True
        or yoy_fallback.get("nearby_date_or_row_order_fallback_allowed") is not False
        or yoy_fallback.get("rounded_value_or_fuzzy_inference_allowed") is not False
        or previous_snapshot.get("source")
        != "MetricStorePort exact physical snapshot read"
        or previous_snapshot.get("physical_field_id") != "E"
        or previous_snapshot.get("period_role") != "prior_year_comparable"
    ):
        errors.append(
            f"{revenue_metric_file}: Technical incremental YoY exact dual-QTD fallback is incomplete"
        )
    checked += 8

    store = documents.get(store_file, {})
    revenue_store = next(
        (item for item in store.get("metric_result_stores", []) if item.get("store_id") == "STORE_WEEKLY_REVENUE_HISTORICAL"),
        {},
    )
    date_lineage = revenue_store.get("revenue_date_lineage_contract", {})
    if (
        date_lineage.get("required_fields") != ["workflow_reporting_date", "current_revenue_cutoff_date"]
        or date_lineage.get("generic_cutoff_date_allowed") is not False
        or date_lineage.get("older_successful_period_fallback_allowed") is not False
    ):
        errors.append(f"{store_file}: Revenue Metric Store date lineage is incomplete")
    checked += 3
    physical_lineage = date_lineage.get("physical_lineage_binding", {})
    expected_metadata_columns = [
        "schema_version",
        "store_id",
        "store_asset_id",
        "business_context_id",
        "metric_variant_id",
        "workflow_reporting_date",
        "current_revenue_cutoff_date",
        "physical_worksheet",
        "physical_row",
        "business_date_column",
        "result_id",
        "validation_status",
        "business_digest",
    ]
    if (
        physical_lineage.get("binding_status") != "registered"
        or physical_lineage.get("metadata_worksheet_name")
        != "_pbac_metric_store_metadata"
        or physical_lineage.get("metadata_worksheet_visibility") != "veryHidden"
        or physical_lineage.get("adapter_technical_metadata_only") is not True
        or physical_lineage.get("business_output_or_source_dataset") is not False
        or physical_lineage.get("business_semantics_authority") != "none"
        or physical_lineage.get("business_value_storage_allowed") is not False
        or physical_lineage.get("required_columns") != expected_metadata_columns
        or physical_lineage.get("missing_duplicate_or_mismatched_metadata_action")
        != "fail closed for the affected Store read or write and notify owner"
        or physical_lineage.get("legacy_row_without_metadata")
        != "runtime bootstrap required; never infer the missing lineage binding"
    ):
        errors.append(
            f"{store_file}: Revenue Excel physical lineage metadata binding is incomplete"
        )
    if set(physical_lineage.get("prohibited_inference", [])) != {
        "reporting_date_equals_business_date",
        "reporting_date_plus_or_minus_calendar_offset",
        "weekday_based_business_date_derivation",
        "nearest_date",
        "previous_row",
        "latest_row",
        "worksheet_row_order",
    }:
        errors.append(
            f"{store_file}: Revenue Excel physical lineage inference prohibitions are incomplete"
        )
    checked += 12
    technical_store_asset = next(
        (
            item
            for item in revenue_store.get("store_assets", [])
            if item.get("store_asset_id")
            == "STORE_ASSET_WEEKLY_REVENUE_TECHNICAL"
        ),
        {},
    )
    qtd_snapshot = technical_store_asset.get(
        "prior_year_qtd_executed_snapshot_read", {}
    )
    if (
        qtd_snapshot.get("adapter_operation") != "read_exact_physical_snapshot"
        or qtd_snapshot.get("metric_store_port_required") is not True
        or qtd_snapshot.get("physical_field_id") != "E"
        or qtd_snapshot.get("metric_variant_id")
        != "MV_REVENUE_TECHNICAL_QTD_EXECUTED_V1"
        or qtd_snapshot.get("period_role") != "prior_year_comparable"
        or qtd_snapshot.get("adapter_technical_read_only") is not True
        or qtd_snapshot.get("result_contract_field_created") is not False
    ):
        errors.append(
            f"{store_file}: Technical prior-year QTD physical snapshot read is incomplete"
        )
    checked += 7

    assertions = documents.get(scenarios_file, {}).get("weekly_revenue_contract_assertions", [])
    expected_assertion_ids = {
        "revenue_context_missing_inventory_continues_partial_draft",
        "revenue_result_lineage_rejects_generic_cutoff",
        "technical_incremental_wow_exact_previous_metric",
        "technical_incremental_yoy_primary_store_precedence",
        "technical_incremental_yoy_exact_dual_qtd_fallback",
        "prior_year_unavailable_failure_precedence",
    }
    if {item.get("assertion_id") for item in assertions} != expected_assertion_ids:
        errors.append(f"{scenarios_file}: Weekly Revenue cross-asset synthetic assertions are incomplete")
    checked += 1
    email_rule = active_rules.get("BR_REVENUE_ROLLING_DECK_EMAIL_CLASSIFICATION_V1", ("", {}))[1]
    current_source_contract = email_rule.get("conditions", {}).get("current_weekly_source_selection_contract", {})
    if current_source_contract.get("current_source_pipeline_scope") != ["PL_REVENUE_TECHNICAL_WEEKLY"] or current_source_contract.get(
        "ctv_current_source_contract"
    ) != "DS_REVENUE_CTV_EXCL_PLACEMENT_QTD executable_selection_rule":
        errors.append("BR_REVENUE_ROLLING_DECK_EMAIL_CLASSIFICATION_V1: current-source Pipeline scope is ambiguous")
    checked += 2
    return checked


def validate_status_consistency(
    documents: dict[str, Any], errors: list[str]
) -> int:
    """Ensure each Workflow Status Index and its source/final Gates agree."""
    status_indexes = [
        (file, document)
        for file, document in documents.items()
        if isinstance(document, dict)
        and document.get("config_type") == "business_asset_status_index"
    ]
    code_gates = {
        document.get("workflow_id"): (file, document)
        for file, document in documents.items()
        if isinstance(document, dict)
        and document.get("config_type") == "code_implementation_readiness_gate"
    }
    checked = 0
    for index_path, index_document in status_indexes:
        workflow_id = index_document.get("workflow_id")
        indexed_results: dict[str, str] = {}
        for item_index, item in enumerate(index_document.get("asset_stage_gates", [])):
            if not isinstance(item, dict):
                errors.append(
                    f"{index_path}:asset_stage_gates[{item_index}]: expected mapping"
                )
                continue
            gate_id = item.get("gate_id")
            indexed_status = item.get("status")
            source_artifact = item.get("source_artifact")
            if not all(
                isinstance(value, str) and value
                for value in (gate_id, indexed_status, source_artifact)
            ):
                errors.append(
                    f"{index_path}:asset_stage_gates[{item_index}]: incomplete entry"
                )
                continue
            source_document = documents.get(source_artifact)
            if not isinstance(source_document, dict):
                errors.append(
                    f"{index_path}:asset_stage_gates[{item_index}]: source artifact "
                    f"not found: {source_artifact}"
                )
                continue
            if source_document.get("gate_id") != gate_id:
                errors.append(
                    f"{source_artifact}: gate_id does not match Status Index entry {gate_id}"
                )
            if source_document.get("gate_result") != indexed_status:
                errors.append(
                    f"{source_artifact}: gate_result does not match Status Index "
                    f"status {indexed_status!r}"
                )
            indexed_results[gate_id] = indexed_status
            checked += 1
        gate_entry = code_gates.get(workflow_id)
        if not gate_entry:
            errors.append(f"{index_path}: no Code Gate for {workflow_id}")
            continue
        final_gate_path, final_gate = gate_entry
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


def validate_stage3a_qualification_status_consistency(
    documents: dict[str, Any], errors: list[str]
) -> int:
    """Fail closed when Stage 3A qualification governance records drift."""
    qualification_path = (
        "phase1_5/assets/readiness/stage3a_ctv_qualification_status.yaml"
    )
    status_index_path = "phase1_5/assets/readiness/status_index.yaml"
    qualification = documents.get(qualification_path)
    status_index = documents.get(status_index_path)
    checked = 0

    if not isinstance(qualification, dict):
        errors.append(f"{qualification_path}: missing or invalid qualification status")
        return checked
    if not isinstance(status_index, dict):
        errors.append(f"{status_index_path}: missing or invalid status index")
        return checked

    missing = object()

    def nested_value(document: dict[str, Any], path: str) -> Any:
        value: Any = document
        for segment in path.split("."):
            if not isinstance(value, dict) or segment not in value:
                return missing
            value = value[segment]
        return value

    def require_exact(
        file: str, document: dict[str, Any], path: str, expected: Any
    ) -> Any:
        nonlocal checked
        actual = nested_value(document, path)
        if actual is missing:
            errors.append(f"{file}:{path}: missing fail-closed governance value")
        elif actual != expected:
            errors.append(
                f"{file}:{path}: expected {expected!r}, found {actual!r}"
            )
        checked += 1
        return actual

    def require_match(
        left_file: str,
        left_document: dict[str, Any],
        left_path: str,
        right_file: str,
        right_document: dict[str, Any],
        right_path: str,
    ) -> tuple[Any, Any]:
        nonlocal checked
        left = nested_value(left_document, left_path)
        right = nested_value(right_document, right_path)
        if left is missing or right is missing:
            errors.append(
                f"{left_file}:{left_path} and {right_file}:{right_path}: "
                "missing cross-file governance value"
            )
        elif left != right:
            errors.append(
                f"{left_file}:{left_path} value {left!r} does not match "
                f"{right_file}:{right_path} value {right!r}"
            )
        checked += 1
        return left, right

    evidence_id, _ = require_match(
        qualification_path,
        qualification,
        "qualification_evidence.evidence_record_id",
        status_index_path,
        status_index,
        "stage3a_ctv_vertical_slice_implementation."
        "local_real_data_calculation_qualification.evidence_record_id",
    )
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        errors.append(
            f"{qualification_path}:qualification_evidence.evidence_record_id: "
            "must be a non-empty sanitized evidence ID"
        )
    checked += 1

    manifest_hash, _ = require_match(
        qualification_path,
        qualification,
        "qualification_evidence.local_manifest_sha256",
        status_index_path,
        status_index,
        "stage3a_ctv_vertical_slice_implementation."
        "local_real_data_calculation_qualification.local_manifest_sha256",
    )
    if not isinstance(manifest_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", manifest_hash
    ):
        errors.append(
            f"{qualification_path}:qualification_evidence.local_manifest_sha256: "
            "must be a lowercase SHA-256"
        )
    checked += 1

    require_match(
        qualification_path,
        qualification,
        "status",
        status_index_path,
        status_index,
        "stage3a_ctv_vertical_slice_implementation."
        "local_real_data_calculation_qualification.qualification_result",
    )
    require_exact(
        qualification_path,
        qualification,
        "status",
        "passed_for_local_real_data_calculation_scope",
    )
    require_exact(
        qualification_path,
        qualification,
        "qualification_scope.ctv_local_real_data_calculation",
        "passed",
    )
    require_exact(
        status_index_path,
        status_index,
        "phase_status.local_real_data_calculation_qualification",
        "passed_for_calculation_scope_only",
    )

    require_match(
        qualification_path,
        qualification,
        "governance_boundaries.runtime_contract_v1_2_promotion_status",
        status_index_path,
        status_index,
        "stage3a_ctv_vertical_slice_implementation."
        "runtime_contract_v1_2_promotion_status",
    )
    require_match(
        status_index_path,
        status_index,
        "current_runtime_candidate.promotion_status",
        status_index_path,
        status_index,
        "stage3a_ctv_vertical_slice_implementation."
        "runtime_contract_v1_2_promotion_status",
    )
    require_exact(
        qualification_path,
        qualification,
        "governance_boundaries.runtime_contract_v1_2_promotion_status",
        "not_promoted",
    )

    for file, document, path, expected in (
        (
            qualification_path,
            qualification,
            "execution_capture.git_head_at_execution",
            "not_captured_at_execution",
        ),
        (
            qualification_path,
            qualification,
            "execution_capture.qualification_harness_sha256_at_execution",
            "not_captured_at_execution",
        ),
        (
            qualification_path,
            qualification,
            "execution_capture.retrospective_inference_allowed",
            False,
        ),
        (
            qualification_path,
            qualification,
            "qualification_scope.runtime_acceptance",
            "not_run",
        ),
        (
            qualification_path,
            qualification,
            "governance_boundaries.runtime_acceptance_may_be_inferred_from_qualification",
            False,
        ),
        (
            status_index_path,
            status_index,
            "stage3a_ctv_vertical_slice_implementation."
            "local_real_data_calculation_qualification."
            "full_runtime_qualification_result",
            "not_established",
        ),
        (
            status_index_path,
            status_index,
            "stage3a_ctv_vertical_slice_implementation."
            "local_real_data_calculation_qualification.runtime_acceptance_implication",
            "none",
        ),
        (
            status_index_path,
            status_index,
            "stage3a_ctv_vertical_slice_implementation.runtime_acceptance_started",
            False,
        ),
        (
            status_index_path,
            status_index,
            "stage3a_ctv_vertical_slice_implementation.runtime_acceptance_completed",
            False,
        ),
        (
            status_index_path,
            status_index,
            "current_next_stage_boundary.runtime_acceptance_status",
            "not_started",
        ),
        (
            status_index_path,
            status_index,
            "current_runtime_candidate.runtime_acceptance_authorized",
            False,
        ),
        (
            status_index_path,
            status_index,
            "phase_status.end_to_end_runtime_acceptance",
            "conditional",
        ),
    ):
        require_exact(file, document, path, expected)

    require_match(
        qualification_path,
        qualification,
        "governance_boundaries.stage3b_scope_contract_registered",
        status_index_path,
        status_index,
        "current_next_stage_boundary.stage3b_scope_contract_registered",
    )
    require_exact(
        qualification_path,
        qualification,
        "governance_boundaries.stage3b_scope_contract_registered",
        True,
    )
    require_match(
        qualification_path,
        qualification,
        "governance_boundaries.stage3b_authorized",
        status_index_path,
        status_index,
        "current_next_stage_boundary.stage3b_authorized",
    )
    require_exact(
        qualification_path,
        qualification,
        "governance_boundaries.stage3b_authorized",
        True,
    )

    stage3b_scope_path = (
        "phase1_5/assets/readiness/stage3b_revenue_expansion_exact_scope.yaml"
    )
    stage3b_scope = documents.get(stage3b_scope_path)
    if not isinstance(stage3b_scope, dict):
        errors.append(f"{stage3b_scope_path}: missing or invalid Stage 3B scope contract")
    else:
        for file, document, path, expected in (
            (
                stage3b_scope_path,
                stage3b_scope,
                "config_type",
                "stage_scope_contract",
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "scope_contract_id",
                "SCOPE_STAGE3B_REVENUE_EXPANSION_V1",
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "scope_contract_version",
                "1.0.0",
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "status",
                "registered_not_authorized",
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "authorization.stage3b_authorized",
                False,
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "authorization.implementation_may_start",
                False,
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "authorization.separate_explicit_owner_authorization_required",
                True,
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "authorization.automatic_next_stage_allowed",
                False,
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "included_scope.frozen_contract_preservation.default_modify_business_semantics",
                False,
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "included_scope.frozen_contract_preservation.modify_metric_definitions",
                False,
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "included_scope.frozen_contract_preservation.owner_authorized_scope_exception.exception_id",
                "STAGE3B_TECHNICAL_WEEKLY_YOY_DENOMINATOR_SOURCE_SELECTION",
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "included_scope.frozen_contract_preservation.owner_authorized_scope_exception.fallback_allowed_when",
                "primary exact Store Result is truly not_found only",
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "included_scope.frozen_contract_preservation.owner_authorized_scope_exception.fallback_prohibited_when_primary_is",
                ["metadata_missing", "ambiguous", "unverified", "invalid"],
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "included_scope.frozen_contract_preservation.owner_authorized_scope_exception.metric_definition_formula_modified",
                False,
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "included_scope.frozen_contract_preservation.owner_authorized_scope_exception.result_contract_modified",
                False,
            ),
            (
                stage3b_scope_path,
                stage3b_scope,
                "included_scope.frozen_contract_preservation.owner_authorized_scope_exception.manifest_business_key_modified",
                False,
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.status",
                "stage3b_completed_later_stages_not_authorized",
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.stage3b_scope_contract_id",
                "SCOPE_STAGE3B_REVENUE_EXPANSION_V1",
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.stage3b_scope_contract_version",
                "1.0.0",
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.stage3b_scope_contract_status",
                "registered_not_authorized",
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.stage3b_scope_contract_source",
                stage3b_scope_path,
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.separate_owner_authorization_required",
                True,
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.stage3b_authorization_received",
                True,
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.stage3b_implementation_started",
                True,
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.stage3b_implementation_completed",
                True,
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.stage3b_exit_qualification_passed",
                True,
            ),
            (
                status_index_path,
                status_index,
                "current_next_stage_boundary.stage3b_completed",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.status",
                "implementation_completed_exit_qualified",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.scope_contract_id",
                "SCOPE_STAGE3B_REVENUE_EXPANSION_V1",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.scope_contract_version",
                "1.0.0",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.owner_authorization_received",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.implementation_authorized",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.implementation_started",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.implementation_completed",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.exit_qualification_passed",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.stage3b_completed",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.implementation_code_changed",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.explicit_exclusions_unchanged",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.runtime_acceptance_authorized",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.baseline_promotion_or_refreeze_authorized",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.physical_lineage_binding_reconciliation.status",
                "resolved_registered_and_synthetic_validated",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.physical_lineage_binding_reconciliation.owner_decision_required",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.physical_lineage_binding_reconciliation.frozen_contracts_modified",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.static_value_excel_metric_store_adapter_increment.status",
                "implemented_and_synthetic_validated",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.static_value_excel_metric_store_adapter_increment.result_contract_wow_persisted_to_physical_store",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.static_value_excel_metric_store_adapter_increment.technical_and_ctv_formula_capable_write_status",
                "implemented_and_validated",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.static_value_excel_metric_store_adapter_increment.owner_decision_required",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.static_value_excel_metric_store_adapter_increment.frozen_contracts_modified",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.status",
                "implemented_and_synthetic_validated",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.authority_reconciliation_classification",
                "authority_exists_but_unregistered",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.authority_registration_corrected",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.complete_quarter_equivalence_allowed_for_prior_year_qtd",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.metric_store_port_only_for_historical_read_write_verify",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.owner_decision_required",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.scope_contract_evidence_aligned",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.primary_not_found_fallback_error_codes",
                [
                    "STORE_EXACT_BUSINESS_DATE_NOT_FOUND",
                    "STORE_EXCEL_LINEAGE_BUSINESS_DATE_KEY_NOT_FOUND",
                ],
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.non_not_found_fallback_prohibited",
                ["metadata_missing", "ambiguous", "unverified", "invalid"],
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.frozen_business_semantics_modified",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.stage3c_authorized",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.technical_business_execution_increment.synthetic_pipeline_test_count",
                9,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.smart_speaker_fast_version_business_execution_increment.status",
                "implemented_and_synthetic_validated",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.smart_speaker_fast_version_business_execution_increment.provider_acquisition_implemented",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.smart_speaker_fast_version_business_execution_increment.provider_dependent_repair_query_implemented",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.smart_speaker_fast_version_business_execution_increment.unknown_source_field_handling",
                "owner_notification_and_completed_with_warning",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.smart_speaker_fast_version_business_execution_increment.confirmed_field_processing_continues_on_unknown_source_field",
                True,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.smart_speaker_fast_version_business_execution_increment.frozen_business_semantics_modified",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.smart_speaker_fast_version_business_execution_increment.synthetic_pipeline_test_count",
                5,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.status",
                "passed_for_stage3b_implementation_scope_only",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.full_repository_regression_status",
                "passed",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.revenue_execution_and_store_synthetic_test_count",
                78,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.pr17_merge_fix_evidence.status",
                "passed",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.pr17_merge_fix_evidence.full_repository_regression_reexecuted",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.pr17_merge_fix_evidence.ctv_qualification_reexecuted",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.pr17_merge_fix_evidence.runtime_consistency_reexecuted",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.technical_real_data_calculation_qualification.status",
                "passed",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.ctv_real_data_calculation_qualification.status",
                "passed_reused_stage3a_evidence",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.smart_speaker_fast_version_real_data_source_to_result_qualification.status",
                "not_executed",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.smart_speaker_fast_version_real_data_source_to_result_qualification.stage3b_exit_requirement",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.local_source_values_or_fingerprints_recorded_in_git",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.real_metric_store_write_run",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.provider_query_run",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.runtime_acceptance_implication",
                "none",
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.automatic_next_stage_allowed",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.stage3c_authorized",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.provider_authorized",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.scheduler_or_queue_authorized",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.draft_or_send_authorized",
                False,
            ),
            (
                status_index_path,
                status_index,
                "stage3b_revenue_expansion_implementation.revenue_pipeline_exit_qualification.cutover_authorized",
                False,
            ),
        ):
            require_exact(file, document, path, expected)

        require_match(
            stage3b_scope_path,
            stage3b_scope,
            "scope_contract_id",
            qualification_path,
            qualification,
            "governance_boundaries.stage3b_scope_contract_id",
        )
        require_match(
            stage3b_scope_path,
            stage3b_scope,
            "status",
            qualification_path,
            qualification,
            "governance_boundaries.stage3b_scope_contract_status",
        )

        expected_pipeline_scope = [
            "PL_REVENUE_TECHNICAL_WEEKLY",
            "PL_REVENUE_CTV_WEEKLY",
            "PL_REVENUE_SMART_SPEAKER_WEEKLY",
            "PL_REVENUE_FAST_VERSION_WEEKLY",
        ]
        require_exact(
            stage3b_scope_path,
            stage3b_scope,
            "included_scope.pipeline_business_execution_slices",
            expected_pipeline_scope,
        )
        expected_entry_boundary = [
            "Begin from local Dataset inputs explicitly bound by the Run Input Manifest.",
            "Dataset loading and binding validation are included.",
            "Mapping is included.",
            "Standardization and validation are included.",
            "Business Rule execution is included.",
            "Metric and Metric Variant execution are included.",
            "Result Contract assembly and validation are included.",
        ]
        require_exact(
            stage3b_scope_path,
            stage3b_scope,
            "included_scope.execution_entry_boundary",
            expected_entry_boundary,
        )

        business_line_mapping_path = (
            "phase1_5/assets/field_mappings/"
            "MAP_REVENUE_APOLLO_BUSINESS_LINE_SUMMARY_V1.yaml"
        )
        business_line_mapping = documents.get(business_line_mapping_path, {})
        for path, expected in (
            (
                "source_schema.unknown_source_field_policy",
                "notify_and_request_owner_confirmation_without_blocking",
            ),
            ("validation.unknown_field_validation_required", True),
            ("validation.new_raw_field_policy.notify_owner", True),
            (
                "validation.new_raw_field_policy.block_confirmed_field_processing",
                False,
            ),
            ("validation.new_raw_field_policy.automatic_registration_allowed", False),
            ("validation.new_raw_field_policy.automatic_mapping_allowed", False),
        ):
            require_exact(
                business_line_mapping_path,
                business_line_mapping,
                path,
                expected,
            )

        exclusions = nested_value(stage3b_scope, "explicit_exclusions")
        required_exclusions = {
            "Provider acquisition",
            "Provider capability validation",
            "Provider-dependent repair query execution",
            "Runtime Acceptance",
            "Baseline promotion or refreeze",
            "Customer Revenue Detail Workflow",
        }
        if not isinstance(exclusions, list) or not required_exclusions.issubset(
            set(exclusions)
        ):
            errors.append(
                f"{stage3b_scope_path}:explicit_exclusions: missing fail-closed "
                "Stage 3B exclusions"
            )
        checked += 1

    for status_path in (
        "stage3a_ctv_vertical_slice_implementation.automatic_next_stage_allowed",
        "current_next_stage_boundary.automatic_next_stage_allowed",
        "current_runtime_candidate.automatic_next_stage_allowed",
    ):
        require_match(
            qualification_path,
            qualification,
            "governance_boundaries.automatic_next_stage_allowed",
            status_index_path,
            status_index,
            status_path,
        )
    require_exact(
        qualification_path,
        qualification,
        "governance_boundaries.automatic_next_stage_allowed",
        False,
    )

    for status_path in (
        "stage3a_ctv_vertical_slice_implementation.auto_send",
        "current_runtime_candidate.auto_send",
    ):
        require_match(
            qualification_path,
            qualification,
            "governance_boundaries.auto_send",
            status_index_path,
            status_index,
            status_path,
        )
    require_exact(
        qualification_path,
        qualification,
        "governance_boundaries.auto_send",
        False,
    )
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
    final_closure_check_count = validate_phase1_5_final_closure(documents, errors)
    external_asset_count, external_asset_binding_count = (
        validate_external_asset_versions(documents, errors)
    )
    implementation_baseline_checks = validate_implementation_baseline(
        documents, errors
    )
    active_tbd_classification_checks = validate_active_tbd_classification(
        documents, errors
    )
    weekly_rule_context_binding_checks = validate_weekly_canonical_rule_context_bindings(
        documents, errors
    )
    checked_status_entries = validate_status_consistency(documents, errors)
    qualification_status_checks = validate_stage3a_qualification_status_consistency(
        documents, errors
    )

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
        f"{final_closure_check_count} Phase 1.5 final-closure contracts checked; "
        f"{contract_counts['output_bindings']} Metric Variant output bindings and "
        f"{contract_counts['output_fields']} explicit Output Mapping fields checked; "
        f"{external_asset_count} versioned External Assets with "
        f"{external_asset_binding_count} consumer bindings checked; "
        f"{implementation_baseline_checks} Implementation Baseline checks passed; "
        f"{active_tbd_classification_checks} active TBD classification checks passed; "
        f"{weekly_rule_context_binding_checks} Weekly Rule Context binding checks passed; "
        f"{len(references)} asset references resolved; "
        f"{checked_paths} Required paths checked across {matched_assets} assets; "
        f"{checked_status_entries} Gate status links and "
        f"{qualification_status_checks} Stage 3A qualification governance "
        "boundaries are consistent."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
