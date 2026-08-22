#!/usr/bin/env python3
"""Validate frozen Stage 2 composition and the active Runtime Candidate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
OLD_RUNTIME = ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1.yaml"
STAGE2_RUNTIME = ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1_1_candidate.yaml"
ACTIVE_RUNTIME = ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1_2_candidate.yaml"
EXTENSION = ROOT / "phase1_5/assets/execution/weekly_acquisition_automation_contracts_v1_1_candidate.yaml"
DATASETS = ROOT / "phase1_5/assets/datasets/dataset_inventory.yaml"
PIPELINES = ROOT / "phase1_5/assets/pipelines/pipeline_registry.yaml"
STAGE2 = ROOT / "phase1_5/assets/readiness/weekly_acquisition_stage2_implementation_status.yaml"
STATUS_INDEX = ROOT / "phase1_5/assets/readiness/status_index.yaml"
STORE_REGISTRY = ROOT / "phase1_5/assets/metric_stores/metric_result_store_registry.yaml"

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
    new = load(STAGE2_RUNTIME)
    active = load(ACTIVE_RUNTIME)
    store_registry = load(STORE_REGISTRY)
    extension = load(EXTENSION)
    datasets = load(DATASETS)
    pipelines = load(PIPELINES)
    stage2 = load(STAGE2)
    status_index = load(STATUS_INDEX)

    assert set(new) == set(old) | {"acquisition_automation_contract_binding"}
    for interface in CORE_INTERFACES:
        if interface == "run_input_manifest":
            candidate = dict(new[interface])
            candidate.pop("optional_entry_extensions")
            assert candidate == old[interface]
        else:
            assert new[interface] == old[interface], f"Core interface changed: {interface}"

    assert set(active) == set(new)
    assert active["contract_bundle_id"] == (
        "RUNTIME_CONTRACTS_WF_WEEKLY_BUSINESS_REPORT_V1_2_CANDIDATE"
    )
    assert active["contract_bundle_version"] == "1.2.0"
    correction_interfaces = {
        "canonical_rule_context_bindings",
        "pipeline_scoped_rule_context_bindings",
    }
    for interface in CORE_INTERFACES:
        if interface not in correction_interfaces:
            assert active[interface] == new[interface], (
                f"Unexpected active Runtime interface change: {interface}"
            )
    stage2_rules = new["canonical_rule_context_bindings"]
    active_rules = active["canonical_rule_context_bindings"]
    ctv_rule_id = "BR_REVENUE_CTV_PRIOR_YEAR_HISTORICAL_STORE_SELECTION_V1"
    technical_rule_id = "BR_REVENUE_PRIOR_YEAR_COMPARABLE_SOURCE_SELECTION_V1"
    assert set(active_rules) == set(stage2_rules) | {ctv_rule_id}
    for rule_id in set(stage2_rules) - {"validation"}:
        assert active_rules[rule_id] == stage2_rules[rule_id]
    assert set(active_rules[ctv_rule_id]) == {
        "target_business_line",
        "current_revenue_cutoff_date",
        "workflow_year",
    }
    stage2_pipeline_bindings = new["pipeline_scoped_rule_context_bindings"]
    active_pipeline_bindings = active["pipeline_scoped_rule_context_bindings"]
    assert set(active_pipeline_bindings) == set(stage2_pipeline_bindings)
    for pipeline_id in set(stage2_pipeline_bindings) - {"PL_REVENUE_CTV_WEEKLY"}:
        assert active_pipeline_bindings[pipeline_id] == stage2_pipeline_bindings[pipeline_id]
    assert active_pipeline_bindings["PL_REVENUE_TECHNICAL_WEEKLY"][
        "target_business_line"
    ]["applies_to_rule_ids"] == [
        technical_rule_id,
        "BR_REVENUE_PREVIOUS_QUARTER_RESULT_SOURCE_SELECTION_V1",
    ]
    assert active_pipeline_bindings["PL_REVENUE_CTV_WEEKLY"][
        "target_business_line"
    ]["applies_to_rule_ids"] == [
        ctv_rule_id,
        "BR_REVENUE_PREVIOUS_QUARTER_RESULT_SOURCE_SELECTION_V1",
    ]

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
    active_binding = active["acquisition_automation_contract_binding"]
    assert active_binding["extension_contract_id"] == extension["contract_id"]
    assert active_binding["extension_contract_version"] == extension["contract_version"]
    correction = active_binding["inherited_runtime_contract"][
        "authorized_semantic_correction_scope"
    ]
    assert correction == {
        "pipeline_id": "PL_REVENUE_CTV_WEEKLY",
        "removed_rule_id": technical_rule_id,
        "replacement_rule_id": ctv_rule_id,
        "technical_pipeline_semantics_changed": False,
        "breaking_change_requiring_major": False,
    }
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
    local_runtime_data = extension["local_runtime_data_contract"]
    assert local_runtime_data["file_write_policy"] == "create_new_never_overwrite"
    write_scope = local_runtime_data["file_write_policy_scope"]
    assert write_scope["immutable_business_or_audit_artifacts"] == [
        "acquisition_attempt",
        "acquisition_attempt_manifest",
        "attempt_input",
        "attempt_intermediate",
        "attempt_output",
        "attempt_diagnostic",
    ]
    operational_state = write_scope["browser_lock_operational_state_in_state_directory"]
    assert operational_state["classification"] == "controlled_runtime_operational_state"
    assert operational_state["included_in_create_new_never_overwrite_scope"] is False
    assert operational_state["controlled_overwrite_allowed"] is True
    assert operational_state["append_only_lock_framework_required"] is False
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

    pipeline_by_id = {pipeline["pipeline_id"]: pipeline for pipeline in pipelines["pipelines"]}
    for pipeline_id in ("PL_REVENUE_TECHNICAL_WEEKLY", "PL_REVENUE_CTV_WEEKLY"):
        registry_rules = pipeline_by_id[pipeline_id]["pipeline_rule_context_bindings"][
            "target_business_line"
        ]["applicable_rule_ids"]
        runtime_rules = active_pipeline_bindings[pipeline_id]["target_business_line"][
            "applies_to_rule_ids"
        ]
        assert registry_rules == runtime_rules

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
    assert stage2["local_validation_result"]["runtime_unit_test_count"] == 38
    stage2_index = status_index["stage2_acquisition_runtime_implementation"]
    active_index = status_index["current_runtime_candidate"]
    assert active_index["runtime_bundle_id"] == active["contract_bundle_id"]
    assert active_index["runtime_bundle_version"] == active["contract_bundle_version"]
    assert active_index["runtime_bundle_source"] == (
        "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1_2_candidate.yaml"
    )
    assert active_index["baseline_1_0_0_unchanged"] is True
    assert active_index["stage2_v1_1_candidate_unchanged"] is True
    assert active_index["runtime_acceptance_authorized"] is False
    assert active_index["automatic_next_stage_allowed"] is False
    assert status_index["current_stage"] == (
        "Stage 3E Weekly Output Assembly and Review Preview Implementation "
        "Completed - Exit Qualified; Stage 3F Not Authorized"
    )
    assert status_index["phase_status"]["code_implementation"] == (
        "stage3e_weekly_output_assembly_review_preview_completed_exit_qualified"
    )
    stage3b = status_index["stage3b_revenue_expansion_implementation"]
    assert stage3b["scope_contract_id"] == "SCOPE_STAGE3B_REVENUE_EXPANSION_V1"
    assert stage3b["scope_contract_version"] == "1.0.0"
    assert stage3b["owner_authorization_received"] is True
    assert stage3b["implementation_authorized"] is True
    assert stage3b["implementation_completed"] is True
    assert stage3b["implementation_code_changed"] is True
    assert stage3b["explicit_exclusions_unchanged"] is True
    assert stage3b["runtime_acceptance_authorized"] is False
    assert stage3b["baseline_promotion_or_refreeze_authorized"] is False
    assert stage3b["automatic_next_stage_allowed"] is False
    reconciliation = stage3b["store_contract_evidence_reconciliation"]
    assert reconciliation["status"] == "resolved_by_owner_decision"
    assert reconciliation["technical_store_only_helper_fields"] == ["G", "I"]
    assert reconciliation["technical_quarter_transition_blank_not_applicable_columns"] == [
        "H",
        "K",
        "L",
    ]
    assert reconciliation["owner_decision_required"] is False
    physical_reconciliation = stage3b["physical_lineage_binding_reconciliation"]
    assert physical_reconciliation["status"] == (
        "resolved_registered_and_synthetic_validated"
    )
    assert physical_reconciliation["root_cause_classification"] == (
        "missing_physical_lineage_binding_not_missing_business_rule"
    )
    assert physical_reconciliation["adapter_technical_metadata_only"] is True
    assert physical_reconciliation["business_value_storage_allowed"] is False
    assert physical_reconciliation["owner_decision_required"] is False
    static_adapter = stage3b["static_value_excel_metric_store_adapter_increment"]
    assert static_adapter["status"] == "implemented_and_synthetic_validated"
    assert static_adapter["applicable_store_assets"] == [
        "STORE_ASSET_WEEKLY_REVENUE_SMART_SPEAKER",
        "STORE_ASSET_WEEKLY_REVENUE_FAST_VERSION",
    ]
    assert static_adapter["result_contract_wow_persisted_to_physical_store"] is False
    assert static_adapter["technical_and_ctv_formula_capable_write_status"] == (
        "implemented_and_validated"
    )
    assert static_adapter["owner_decision_required"] is False
    assert static_adapter["frozen_contracts_modified"] is False
    business_lines = stage3b[
        "smart_speaker_fast_version_business_execution_increment"
    ]
    assert business_lines["status"] == "implemented_and_synthetic_validated"
    assert business_lines["provider_acquisition_implemented"] is False
    assert business_lines["provider_dependent_repair_query_implemented"] is False
    qualification = stage3b["revenue_pipeline_exit_qualification"]
    assert qualification["status"] == "passed_for_stage3b_implementation_scope_only"
    assert qualification["full_repository_regression_status"] == "passed"
    assert qualification["real_metric_store_write_run"] is False
    assert qualification["provider_query_run"] is False
    assert qualification["runtime_acceptance_implication"] == "none"
    assert qualification["automatic_next_stage_allowed"] is False
    stage3c = status_index["stage3c_weekly_executor_completion_implementation"]
    assert stage3c["scope_contract_id"] == (
        "SCOPE_STAGE3C_WEEKLY_EXECUTOR_COMPLETION_RETROSPECTIVE_V1"
    )
    assert stage3c["pre_implementation_scope_authorization_retroactively_claimed"] is False
    assert stage3c["implementation_pull_request"] == 18
    assert stage3c["implementation_merge_commit_sha"] == (
        "71e035ba94be0ddd32cc359ab37312dcfab0120a"
    )
    assert stage3c["implementation_completed"] is True
    assert stage3c["implementation_merged"] is True
    assert stage3c["pipeline_executor_count"] == 8
    assert stage3c["sqlite_metric_store_increment"]["store_asset_count"] == 7
    assert stage3c["execution_boundaries"]["provider_query_implemented"] is False
    assert stage3c["execution_boundaries"]["runner_or_orchestrator_implemented"] is False
    assert stage3c["execution_boundaries"]["output_assembly_implemented"] is False
    assert stage3c["runtime_acceptance_authorized"] is False
    assert stage3c["stage3d_authorized"] is False
    assert stage3c["automatic_next_stage_allowed"] is False
    stage3d = status_index["stage3d_weekly_workflow_runner_implementation"]
    assert stage3d["scope_contract_id"] == "SCOPE_STAGE3D_WEEKLY_WORKFLOW_RUNNER_V1"
    assert stage3d["runner_entry_point"] == (
        "weekly_business_runtime.WeeklyWorkflowRunner.execute"
    )
    assert stage3d["implementation_completed"] is True
    assert stage3d["exit_qualification_passed"] is True
    assert stage3d["stage3d_completed"] is True
    assert stage3d["validation_evidence"]["stage3d_total_test_count"] == 13
    assert stage3d["validation_evidence"]["combined_targeted_and_affected_test_count"] == 56
    assert stage3d["provider_added"] is False
    assert stage3d["scheduler_or_queue_added"] is False
    assert stage3d["generic_dag_or_workflow_framework_added"] is False
    assert stage3d["stage3_completed"] is False
    assert stage3d["stage3e_authorized"] is False
    assert stage3d["stage3f_authorized"] is False
    assert stage3d["runtime_acceptance_authorized"] is False
    assert stage3d["automatic_next_stage_allowed"] is False
    stage3e = status_index[
        "stage3e_weekly_output_assembly_review_preview_scope_registration"
    ]
    assert stage3e["scope_contract_id"] == (
        "SCOPE_STAGE3E_WEEKLY_OUTPUT_ASSEMBLY_REVIEW_PREVIEW_V1"
    )
    assert stage3e["owner_scope_decision_received"] is True
    assert stage3e["weekly_output_assembly_in_scope"] is True
    assert stage3e["review_preview_in_scope"] is True
    assert stage3e["configured_display_value_resolver_in_scope"] is True
    assert stage3e["configured_display_values_sqlite_persistence_in_scope"] is True
    assert stage3e["output_assembly_consumes_resolved_value_only"] is True
    assert stage3e["configured_display_state_is_metric_store"] is False
    assert stage3e["generic_state_store_added"] is False
    assert stage3e["repository_or_persistence_framework_added"] is False
    assert stage3e["outlook_draft_or_send_in_scope"] is False
    assert stage3e["stage3f_qualification_in_scope"] is False
    assert stage3e["runtime_acceptance_in_scope"] is False
    assert stage3e["implementation_authorized"] is True
    assert stage3e["implementation_started"] is True
    assert stage3e["implementation_completed"] is True
    assert stage3e["exit_qualification_passed"] is True
    assert stage3e["stage3e_completed"] is True
    assert stage3e["validation_evidence"]["stage3e_total_test_count"] == 11
    assert stage3e["generic_renderer_added"] is False
    assert stage3e["repository_added"] is False
    assert stage3e["metric_store_extended_for_configured_values"] is False
    assert stage3e["outlook_draft_or_send_added"] is False
    assert stage3e["stage3f_work_added"] is False
    assert stage3e["stage3_completed"] is False
    assert stage3e["automatic_next_stage_allowed"] is False
    revenue_store = next(
        item
        for item in store_registry["metric_result_stores"]
        if item["store_id"] == "STORE_WEEKLY_REVENUE_HISTORICAL"
    )
    physical_binding = revenue_store["revenue_date_lineage_contract"][
        "physical_lineage_binding"
    ]
    assert physical_binding["binding_status"] == "registered"
    assert physical_binding["metadata_worksheet_name"] == (
        "_pbac_metric_store_metadata"
    )
    assert physical_binding["adapter_technical_metadata_only"] is True
    assert physical_binding["business_value_storage_allowed"] is False
    assert status_index["phase_status"]["stage_2_5_governance_sync"] == "synchronized"
    assert status_index["scope_boundaries"]["code_implementation_owner_approved"] is True
    assert status_index["implementation_baseline"]["baseline_version"] == "1.0.0"
    assert status_index["implementation_baseline"]["record_scope"] == (
        "stable_1_0_0_baseline_not_promoted_or_refrozen_by_stage2"
    )
    for field_name in (
        "stage2_owner_authorized",
        "code_implementation_authorized",
        "code_implementation_started",
    ):
        assert stage2_index[field_name] == authorization[field_name] is True
    assert stage2_index["local_runtime_unit_tests"] == stage2["local_validation_result"][
        "runtime_unit_test_count"
    ]
    for field_name in (
        "scheduler_activation_authorized",
        "automatic_draft_activation_authorized",
        "stage5_provider_capability_validation_authorized",
    ):
        assert stage2_index[field_name] == authorization[field_name] is False
    assert stage2_index["scheduler_activated"] is False
    assert stage2_index["automatic_draft_activated"] is False
    assert stage2_index["auto_send"] == authorization["auto_send"] is False
    assert stage2_index["runtime_acceptance_started"] is False
    assert authorization["runtime_acceptance_execution_authorized"] is False
    git_delivery = stage2["git_delivery"]
    assert stage2["status"] == stage2_index["status"] == "completed_and_merged"
    assert git_delivery["status"] == "completed_and_merged"
    assert git_delivery["pull_request"] == 8
    assert git_delivery["merge_completed"] is True
    assert git_delivery["merge_commit_sha"] == "7412d09bcb544061beec69471684b2a246bff6a0"
    historical_git_delivery = git_delivery["historical_pre_merge_record"]
    assert historical_git_delivery["record_scope"] == "historical_record"
    assert historical_git_delivery["current_branch_commit_authorized"] is True
    assert historical_git_delivery["current_branch_push_authorized"] is True
    assert historical_git_delivery["existing_draft_pr_update_authorized"] is True
    assert historical_git_delivery["merge_authorized"] is False
    stage2_5 = status_index["stage2_5_governance_and_implementation_boundary_sync"]
    assert stage2_5["status"] == "governance_synchronized"
    assert stage2_5["next_candidate_stage"] == "Data Engine / Business Execution Implementation"
    assert stage2_5["next_candidate_stage_status"] == (
        "not_started_requires_independent_owner_authorization"
    )
    assert stage2_5["real_data_calculation_status"] == "not_started"
    assert stage2_5["provider_capability_validation_status"] == "not_started"
    assert stage2_5["runtime_acceptance_status"] == "not_started"
    assert stage2_5["scheduler_status"] == "inactive_not_authorized"
    assert stage2_5["automatic_draft_status"] == "inactive_not_authorized"
    assert stage2_5["auto_send"] is False
    assert stage2_5["automatic_next_stage_allowed"] is False
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
        "Weekly Acquisition Runtime validation passed: frozen v1.0 -> v1.1 9/9 interfaces "
        "preserved; active v1.2 CTV/Technical Rule bindings exact; "
        "Run Input business key unchanged; deterministic Attempt binding and lifecycle passed; "
        "Dataset/Query coverage exact; 12 Weekly Pipelines with 10 expected acquisition-affected; "
        "Result consumption, completion status, Outlook selection, activation gates, and Customer isolation preserved."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
