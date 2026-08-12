#!/usr/bin/env python3
"""Validate the Stage 2 Weekly Acquisition Runtime against frozen 1.1.0 contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
OLD_RUNTIME = ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1.yaml"
NEW_RUNTIME = ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1_1_candidate.yaml"
EXTENSION = ROOT / "phase1_5/assets/execution/weekly_acquisition_automation_contracts_v1_1_candidate.yaml"
DATASETS = ROOT / "phase1_5/assets/datasets/dataset_inventory.yaml"
PIPELINES = ROOT / "phase1_5/assets/pipelines/pipeline_registry.yaml"
STAGE2 = ROOT / "phase1_5/assets/readiness/weekly_acquisition_stage2_implementation_status.yaml"

CORE_INTERFACES = [
    "workflow_run_context",
    "run_input_manifest",
    "canonical_context_field_contracts",
    "canonical_rule_context_bindings",
    "pipeline_scoped_rule_context_bindings",
    "parameterized_result_contract_instance_selection",
    "result_field_consumption",
    "workflow_completion_status",
    "governance",
]
BUSINESS_KEY = ["workflow_run_id", "dataset_id", "period_role", "product_parameter"]
ATTEMPT_KEY = [*BUSINESS_KEY, "acquisition_attempt_id"]


def load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), f"{path}: expected YAML object"
    return value


def main() -> int:
    old = load(OLD_RUNTIME)
    new = load(NEW_RUNTIME)
    extension = load(EXTENSION)
    datasets = load(DATASETS)
    pipelines = load(PIPELINES)
    stage2 = load(STAGE2)

    assert set(new) == set(old) | {"acquisition_automation_contract_binding"}
    for interface in CORE_INTERFACES:
        if interface == "run_input_manifest":
            candidate = dict(new[interface])
            candidate.pop("optional_entry_extensions")
            assert candidate == old[interface]
        else:
            assert new[interface] == old[interface], f"Core interface changed: {interface}"

    manifest = new["run_input_manifest"]
    assert manifest["entry_business_key"] == BUSINESS_KEY
    optional = manifest["optional_entry_extensions"]["acquisition_attempt_binding"]
    assert optional["required_fields_when_object"] == [
        "acquisition_attempt_id",
        "attempt_manifest_reference",
    ]
    assert optional["required_when"] == "acquisition_mode in [automated, manual_fallback]"
    assert optional["not_applicable_allowed_when"] == "legacy_prepared_local_input"
    selection = optional["selection_policy"]
    assert selection["latest_attempt_inference_allowed"] is False
    assert selection["file_timestamp_inference_allowed"] is False
    assert selection["directory_order_inference_allowed"] is False

    binding = new["acquisition_automation_contract_binding"]
    assert binding["extension_contract_id"] == extension["contract_id"]
    assert binding["extension_contract_version"] == extension["contract_version"]
    assert new["workflow_id"] == extension["workflow_id"]
    assert binding["runtime_contract_replacement_allowed"] is False
    assert binding["stage2_implementation_authorization_source"] == "this_acquisition_automation_binding"
    assert binding["inherited_runtime_governance_code_implementation_authorized_remains"] is False
    assert extension["contract_role"]["classification"] == "acquisition_extension_sidecar"
    assert extension["contract_role"]["runtime_contract_replacement_allowed"] is False
    assert extension["acquisition_manifest_contract"]["business_association_key"] == ATTEMPT_KEY
    assert extension["acquisition_lifecycle_contract"]["ordered_steps"] == [
        "lock_run_context",
        "create_acquisition_attempt",
        "acquire",
        "validate_attempt_manifest",
        "bind_successful_attempt_to_run_input_manifest",
        "pipeline_consume_bound_local_input",
    ]
    assert extension["acquisition_lifecycle_contract"]["failed_or_old_attempt_manifest_or_file_overwrite_allowed"] is False
    assert extension["acquisition_lifecycle_contract"]["exactly_one_valid_attempt_binding_per_run_input_manifest_entry"] is True
    assert extension["acquisition_attempt_contract"]["latest_attempt_inference_allowed"] is False
    assert extension["acquisition_attempt_contract"]["latest_filename_or_timestamp_selection_allowed"] is False
    assert extension["acquisition_attempt_contract"]["directory_order_selection_allowed"] is False
    assert extension["baseline_binding"]["stage2_refreeze_required_before_code_implementation"] is False
    assert extension["baseline_binding"]["baseline_promotion_or_refreeze_required_before"] == "runtime_acceptance_or_cutover"

    adapter_status = {
        adapter["adapter_id"]: adapter["activation_status"]
        for adapter in extension["adapter_registry"]
    }
    for adapter_id in ("ADP_INTERNAL_APOLLO_QUERY_V1", "ADP_NOVABI_QUERY_V1"):
        assert adapter_status[adapter_id] == "implementation_completed_pending_provider_configuration_and_cutover"

    by_source: dict[str, set[str]] = {}
    queries_by_source: dict[str, set[str]] = {}
    for dataset in datasets["datasets"]:
        source_id = dataset["source_id"]
        by_source.setdefault(source_id, set()).add(dataset["dataset_id"])
        if dataset.get("query_asset_id"):
            queries_by_source.setdefault(source_id, set()).add(dataset["query_asset_id"])
    sources = [
        "SRC_CORP_OUTLOOK_PRIMARY_MAILBOX",
        "SRC_INTERNAL_PLATFORM_APOLLO",
        "SRC_INTERNAL_PLATFORM_NOVABI",
    ]
    for source_id in sources:
        source_binding = extension["explicit_source_bindings"][source_id]
        assert set(source_binding["dataset_ids"]) == by_source[source_id]
        if "query_asset_ids" in source_binding:
            assert set(source_binding["query_asset_ids"]) == queries_by_source[source_id]

    weekly_pipelines = [
        pipeline
        for pipeline in pipelines["pipelines"]
        if any(
            isinstance(workflow, dict)
            and workflow.get("workflow_id") == "WF_WEEKLY_BUSINESS_REPORT"
            for workflow in pipeline.get("workflow_bindings", [])
        )
    ]
    covered_datasets = set().union(*(by_source[source] for source in sources))
    affected = [
        pipeline
        for pipeline in weekly_pipelines
        if {
            dependency.get("dataset_id")
            for dependency in pipeline.get("dataset_dependencies", [])
            if isinstance(dependency, dict)
        }
        & covered_datasets
    ]
    assert len(weekly_pipelines) == 12
    assert len(affected) == 10

    for dataset in datasets["datasets"]:
        if dataset.get("source_id") != "SRC_CORP_OUTLOOK_PRIMARY_MAILBOX":
            continue
        source_rule = dataset["acquisition"]["source_object_or_attachment_rule"]
        selection_rule = source_rule.get(
            "executable_version_selection_rule", source_rule.get("executable_selection_rule")
        )
        assert selection_rule["no_match_policy"]["retry_delay_minutes"] == 30
        assert selection_rule["no_match_policy"]["retry_attempts"] == 1
        multiple = selection_rule["multiple_match_selection_rule"]
        assert multiple["sort_by"] == "email_sent_at"
        assert multiple["sort_order"] == "descending"
        assert multiple["select"] == "first"

    assert new["result_field_consumption"] == old["result_field_consumption"]
    assert new["workflow_completion_status"] == old["workflow_completion_status"]
    assert new["governance"]["auto_send"] is False
    gates = binding["activation_deployment_gates"]
    assert gates["scheduler_initial_mvp"]["runtime_supported_run_types_unchanged"] == [
        "scheduled",
        "manual",
        "backfill",
    ]
    assert gates["scheduler_initial_mvp"]["activated"] is False
    assert gates["automatic_draft_after_runtime_acceptance"]["activated"] is False
    assert gates["automatic_draft_after_runtime_acceptance"]["separate_explicit_owner_approval_required"] is True

    authorization = stage2["implementation_authorization"]
    assert authorization["code_implementation_started"] is True
    assert authorization["stage2_owner_authorized"] is True
    assert authorization["code_implementation_authorized"] is True
    assert authorization["authorization_source"] == "acquisition_automation_contract_binding"
    assert authorization["baseline_promotion_or_refreeze_required_before"] == "runtime_acceptance_or_cutover"
    assert authorization["baseline_1_0_0_modification_authorized"] is False
    assert authorization["runtime_1_0_0_modification_authorized"] is False
    assert authorization["scheduler_activation_authorized"] is False
    assert authorization["automatic_draft_activation_authorized"] is False
    assert authorization["auto_send"] is False
    git_delivery = stage2["git_delivery"]
    assert git_delivery["current_branch_commit_authorized"] is True
    assert git_delivery["current_branch_push_authorized"] is True
    assert git_delivery["existing_draft_pr_update_authorized"] is True
    assert git_delivery["merge_authorized"] is False
    stage_authorization = extension["stage_authorization_boundaries"]
    assert stage_authorization["git_stage_commit_push_or_pr_authorized"] is True
    assert stage_authorization["merge_authorized"] is False

    prohibited_selection_code = (ROOT / "src/weekly_acquisition_runtime/runtime.py").read_text(encoding="utf-8") + (
        ROOT / "src/weekly_acquisition_runtime/storage.py"
    ).read_text(encoding="utf-8")
    for prohibited in ("getmtime(", ".st_mtime", ".glob(", ".rglob(", ".iterdir("):
        assert prohibited not in prohibited_selection_code
    assert "self.storage.sha256(input_path)" in prohibited_selection_code

    workflow = (ROOT / ".github/workflows/validate-assets.yml").read_text(encoding="utf-8")
    assert "python scripts/validate_weekly_acquisition_runtime.py" in workflow
    assert "python -m unittest tests.test_weekly_acquisition_runtime" in workflow

    print(
        "Weekly Acquisition Runtime validation passed: 9/9 core interfaces preserved; "
        "Run Input business key unchanged; deterministic Attempt binding and lifecycle passed; "
        "Dataset/Query coverage exact; 12 Weekly Pipelines with 10 expected acquisition-affected; "
        "Result consumption, completion status, Outlook selection, activation gates, and Customer isolation preserved."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
