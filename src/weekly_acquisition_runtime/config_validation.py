"""Validate the frozen Runtime Bundle plus Acquisition Extension composition."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import ContractViolation


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
    if binding.get("extension_contract_id") != "ACQUISITION_AUTOMATION_WF_WEEKLY_BUSINESS_REPORT_V1_1":
        raise ContractViolation("Runtime Bundle does not bind the exact Acquisition Extension")
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
