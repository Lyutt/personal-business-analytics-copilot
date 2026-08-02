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
        f"{len(references)} asset references resolved; "
        f"{checked_paths} Required paths checked across {matched_assets} assets; "
        f"{checked_status_entries} Gate status links are consistent."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
