#!/usr/bin/env python3
"""Validate YAML syntax, asset IDs, references, and Required structures."""

from __future__ import annotations

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
        "recipient_configuration_id": "external_asset",
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
                for route_index, route in enumerate(routes):
                    if not isinstance(route, dict):
                        errors.append(f"{file}:{field_id}: invalid source route")
                        continue
                    upstream_contract = route.get("result_contract_id")
                    for upstream_field in route.get("result_field_ids", []):
                        validate_contract_field_reference(
                            upstream_contract,
                            upstream_field,
                            f"{file}:{field_id}.source_contract_routes[{route_index}]",
                        )
                    if isinstance(upstream_contract, str):
                        dependency_graph[upstream_contract].add(contract_id)
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
            nonlocal explicit_output_fields
            if isinstance(value, dict):
                if "metric_variant_ids" in value:
                    errors.append(f"{file}:{path}.metric_variant_ids: parallel list is prohibited")
                if isinstance(value.get("display_fields"), list):
                    errors.append(f"{file}:{path}.display_fields: parallel list is prohibited")
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

    return {
        "contracts": len(contract_documents),
        "contract_fields": sum(
            len({id(field): field for field in fields.values()})
            for fields in contract_fields.values()
        ),
        "output_bindings": output_bound_variants,
        "output_fields": explicit_output_fields,
        "record_sets": sum(len(value) for value in contract_record_sets.values()),
    }


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
    for key in ("baseline_id", "baseline_version", "status"):
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

    try:
        origin_main = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        if baseline.get("source_main_commit_sha") != origin_main:
            errors.append(
                f"{baseline_file}: source_main_commit_sha does not match origin/main"
            )
    except subprocess.CalledProcessError as exc:
        errors.append(f"{baseline_file}: cannot resolve origin/main: {exc}")
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
