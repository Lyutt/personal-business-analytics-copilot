"""Validate the frozen Runtime Bundle plus Acquisition Extension composition."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from .errors import ContractViolation
from .contracts import InputBindingRegistry, NOT_APPLICABLE, RegisteredInputBinding


CORE_INTERFACES = {
    "workflow_run_context",
    "run_input_manifest",
    "canonical_context_field_contracts",
    "canonical_rule_context_bindings",
    "pipeline_scoped_rule_context_bindings",
    "parameterized_result_contract_instance_selection",
    "result_field_consumption",
    "workflow_completion_status",
    "governance",
}
BUSINESS_KEY = ["workflow_run_id", "dataset_id", "period_role", "product_parameter"]


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractViolation(f"Expected YAML object: {path}")
    return value


def validate_composition(runtime_bundle: dict[str, Any], extension: dict[str, Any]) -> None:
    missing = sorted(CORE_INTERFACES - set(runtime_bundle))
    if missing:
        raise ContractViolation(f"Runtime Bundle is missing core interfaces: {missing}")
    binding = runtime_bundle.get("acquisition_automation_contract_binding", {})
    if binding.get("extension_contract_id") != extension.get("contract_id"):
        raise ContractViolation("Runtime Bundle does not bind the exact Acquisition Extension")
    if binding.get("extension_contract_version") != extension.get("contract_version"):
        raise ContractViolation("Runtime Bundle and Acquisition Extension versions do not match")
    if runtime_bundle.get("workflow_id") != extension.get("workflow_id"):
        raise ContractViolation("Runtime Bundle and Acquisition Extension workflow_id values do not match")
    if binding.get("runtime_contract_replacement_allowed") is not False:
        raise ContractViolation("Acquisition Extension cannot replace the Runtime Contract")
    if extension.get("contract_role", {}).get("classification") != "acquisition_extension_sidecar":
        raise ContractViolation("Acquisition contract is not classified as a sidecar")
    manifest = runtime_bundle["run_input_manifest"]
    if manifest.get("entry_business_key") != BUSINESS_KEY:
        raise ContractViolation("Run Input Manifest business key changed")
    optional = manifest.get("optional_entry_extensions", {}).get("acquisition_attempt_binding", {})
    if optional.get("required_fields_when_object") != [
        "acquisition_attempt_id",
        "attempt_manifest_reference",
    ]:
        raise ContractViolation("acquisition_attempt_binding is incomplete")
    selection = optional.get("selection_policy", {})
    if any(
        selection.get(name) is not False
        for name in (
            "latest_attempt_inference_allowed",
            "file_timestamp_inference_allowed",
            "directory_order_inference_allowed",
        )
    ):
        raise ContractViolation("Implicit Acquisition Attempt selection is prohibited")
    if runtime_bundle.get("governance", {}).get("auto_send") is not False:
        raise ContractViolation("auto_send must remain false")


def build_input_binding_registry(
    extension: dict[str, Any], dataset_inventory: dict[str, Any], pipeline_registry: dict[str, Any]
) -> InputBindingRegistry:
    """Build exact Dataset/Query/Adapter bindings without name similarity or inference."""

    source_bindings = extension.get("explicit_source_bindings")
    if not isinstance(source_bindings, dict):
        raise ContractViolation("Acquisition Extension explicit_source_bindings is missing")
    datasets = dataset_inventory.get("datasets")
    if not isinstance(datasets, list):
        raise ContractViolation("Dataset Inventory datasets is missing")
    dataset_records = {
        item.get("dataset_id"): item
        for item in datasets
        if isinstance(item, dict) and isinstance(item.get("dataset_id"), str)
    }
    product_scoped = set(
        extension.get("runtime_contract_enforcement", {}).get(
            "product_scoped_dataset_ids", []
        )
    )
    pipelines = pipeline_registry.get("pipelines")
    if not isinstance(pipelines, list):
        raise ContractViolation("Pipeline Registry pipelines is missing")
    workflow_id = extension["workflow_id"]
    constraints_by_dataset: dict[str, set[str]] = {}
    for pipeline in pipelines:
        if not isinstance(pipeline, dict) or not any(
            isinstance(workflow, dict) and workflow.get("workflow_id") == workflow_id
            for workflow in pipeline.get("workflow_bindings", [])
        ):
            continue
        for dependency in pipeline.get("dataset_dependencies", []):
            if not isinstance(dependency, dict):
                continue
            dataset_id = dependency.get("dataset_id")
            constraint = dependency.get("dataset_version_constraint")
            if isinstance(dataset_id, str) and isinstance(constraint, str):
                constraints_by_dataset.setdefault(dataset_id, set()).add(constraint)
    bindings: dict[str, RegisteredInputBinding] = {}
    for source_id, source_binding in source_bindings.items():
        if source_id == "validation":
            continue
        if not isinstance(source_binding, dict):
            raise ContractViolation(f"Invalid source binding: {source_id}")
        adapter_id = source_binding.get("adapter_id", source_binding.get("input_adapter_id"))
        for dataset_id in source_binding.get("dataset_ids", []):
            if dataset_id in bindings:
                raise ContractViolation(f"Dataset has multiple acquisition bindings: {dataset_id}")
            try:
                dataset = dataset_records[dataset_id]
            except KeyError as exc:
                raise ContractViolation(
                    f"Acquisition Dataset is not registered in Dataset Inventory: {dataset_id}"
                ) from exc
            if dataset.get("source_id") != source_id:
                raise ContractViolation(f"Dataset source does not match explicit binding: {dataset_id}")
            query_asset_id = dataset.get("query_asset_id") or NOT_APPLICABLE
            allowed_queries = source_binding.get("query_asset_ids")
            if allowed_queries is not None and query_asset_id not in allowed_queries:
                raise ContractViolation(f"Dataset Query Asset is not registered: {dataset_id}")
            if allowed_queries is None and query_asset_id != NOT_APPLICABLE:
                raise ContractViolation(f"Unexpected Query Asset for Provider Dataset: {dataset_id}")
            bindings[dataset_id] = RegisteredInputBinding(
                dataset_id=dataset_id,
                query_asset_id_or_not_applicable=query_asset_id,
                adapter_id=adapter_id,
                source_id=source_id,
                product_scoped=dataset_id in product_scoped,
                dataset_version_constraints=tuple(
                    sorted(constraints_by_dataset.get(dataset_id, set()))
                ),
            )
    unknown_product_scope = product_scoped - set(bindings)
    if unknown_product_scope:
        raise ContractViolation(
            f"product_scoped_dataset_ids are not registered: {sorted(unknown_product_scope)}"
        )
    return InputBindingRegistry(
        workflow_id=workflow_id,
        bindings=MappingProxyType(bindings),
    )
