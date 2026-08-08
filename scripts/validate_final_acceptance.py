#!/usr/bin/env python3
"""Run deterministic synthetic acceptance checks for the Phase 1.5 asset contracts."""

from __future__ import annotations

import re
import math
import subprocess
from datetime import date, timedelta
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "phase1_5/tests/final_acceptance_scenarios.yaml"
CUSTOMER_SCENARIOS = ROOT / "phase1_5/tests/customer_revenue_detail_acceptance_scenarios.yaml"
RUNTIME = ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1.yaml"
DCP = ROOT / "phase1_5/assets/analysis/dcp_registry_v1.yaml"
STORE = ROOT / "phase1_5/assets/metric_stores/metric_result_store_registry.yaml"
CUSTOMER_CONTEXT = ROOT / "phase1_5/assets/execution/customer_revenue_detail_run_context_v1.yaml"
CUSTOMER_POLICY = ROOT / "phase1_5/assets/policies/PL_CUSTOMER_REVENUE_DETAIL_POLICY_V1.yaml"
CUSTOMER_OUTPUT = ROOT / "phase1_5/assets/output_mappings/OM_CUSTOMER_REVENUE_DETAIL_EXCEL_V1.yaml"
TECHNICAL_RULE = ROOT / "phase1_5/assets/business_rules/BR_REVENUE_TECHNICAL_SINGLE_COUNT_ELIGIBILITY_V1.yaml"
WEEKLY_COMPARABLE_RULE = ROOT / "phase1_5/assets/business_rules/BR_REVENUE_PRIOR_YEAR_COMPARABLE_SOURCE_SELECTION_V1.yaml"
CUSTOMER_FULL_QUARTER_RULE = ROOT / "phase1_5/assets/business_rules/BR_CUSTOMER_REVENUE_PRIOR_YEAR_FULL_QUARTER_SOURCE_SELECTION_V1.yaml"
CUSTOMER_COMPARABLE_RULE = ROOT / "phase1_5/assets/business_rules/BR_CUSTOMER_REVENUE_PRIOR_YEAR_COMPARABLE_SOURCE_SELECTION_V1.yaml"
CUSTOMER_TECHNICAL_ADAPTER = ROOT / "phase1_5/assets/business_rules/BR_CUSTOMER_REVENUE_TECHNICAL_ELIGIBILITY_ADAPTER_V1.yaml"
CUSTOMER_LOCAL_INPUTS = ROOT / "phase1_5/assets/execution/customer_revenue_detail_local_input_contracts_v1.yaml"
CUSTOMER_RESULT_CONTRACT = ROOT / "phase1_5/assets/result_contracts/RC_CUSTOMER_REVENUE_DETAIL_WEEKLY.yaml"
PIPELINE_REGISTRY = ROOT / "phase1_5/assets/pipelines/pipeline_registry.yaml"
CUSTOMER_BASELINE = ROOT / "phase1_5/assets/readiness/implementation_baseline_customer_revenue_detail.yaml"
CUSTOMER_BUSINESS_RULE_GATE = ROOT / "phase1_5/assets/business_rules/business_rule_readiness_gate_customer_revenue_detail.yaml"
CUSTOMER_PIPELINE_GATE = ROOT / "phase1_5/assets/pipelines/pipeline_registry_readiness_gate_customer_revenue_detail.yaml"
CUSTOMER_FIELD_MAPPING_GATE = ROOT / "phase1_5/assets/field_mappings/field_mapping_readiness_gate_customer_revenue_detail.yaml"
CUSTOMER_RESULT_GATE = ROOT / "phase1_5/assets/result_contracts/result_contract_readiness_gate_customer_revenue_detail.yaml"
CUSTOMER_OUTPUT_GATE = ROOT / "phase1_5/assets/output_mappings/output_mapping_readiness_gate_customer_revenue_detail.yaml"
CUSTOMER_CODE_GATE = ROOT / "phase1_5/assets/readiness/code_implementation_readiness_gate_customer_revenue_detail.yaml"
DATASET_INVENTORY = ROOT / "phase1_5/assets/datasets/dataset_inventory.yaml"


def load(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: expected YAML object")
    return value


def scenario_map(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scenarios = document.get("scenarios", [])
    ids = [item.get("scenario_id") for item in scenarios]
    assert len(ids) == len(set(ids)), "duplicate synthetic scenario_id"
    for item in scenarios:
        assert item.get("expected_result"), f"{item.get('scenario_id')}: Expected Result is required"
    return {item["scenario_id"]: item for item in scenarios}


def resolve_forecast_d(
    customers: list[str], owner_values: dict[str, float], available: bool
) -> dict[str, float | None]:
    if not available:
        return {customer: None for customer in customers}
    return {customer: owner_values.get(customer, 0) for customer in customers}


def select_previous_period_output(
    outputs: list[dict[str, Any]], previous_reporting_period_id: str
) -> str:
    required_metadata = {
        "workflow_run_id",
        "result_id",
        "reporting_period_id",
        "output_version",
        "output_file_reference",
        "validation_status",
        "completed_at",
        "current_revenue_cutoff_date",
        "prior_comparable_as_of_date",
        "target_fiscal_quarter",
        "history_consumption_status",
    }
    assert all(
        required_metadata.issubset(item) for item in outputs
    ), "previous output metadata contract is incomplete"
    candidates = [
        item
        for item in outputs
        if item.get("reporting_period_id") == previous_reporting_period_id
        and item.get("validation_status") == "passed"
        and item.get("history_consumption_status") == "consumable"
    ]
    assert candidates, "previous reporting period validated output is required"
    highest_version = max(item["output_version"] for item in candidates)
    selected = [item for item in candidates if item["output_version"] == highest_version]
    assert len(selected) == 1, "previous output highest version must be unique"
    return selected[0]["result_id"]


def derive_customer_reporting_period_context(
    current_year: int, quarter: int, current_cutoff_text: str
) -> dict[str, str]:
    cutoff = date.fromisoformat(current_cutoff_text)
    quarter_start = date(current_year, (quarter - 1) * 3 + 1, 1)
    first_thursday = quarter_start + timedelta(
        days=(3 - quarter_start.weekday()) % 7
    )
    assert cutoff.weekday() == 3, "Customer reporting cutoff must follow Thursday cadence"
    expected_previous_cutoff = cutoff - timedelta(days=7)
    previous_quarter = (expected_previous_cutoff.month - 1) // 3 + 1
    expected_previous_reporting_period_id = (
        f"CUSTOMER:{expected_previous_cutoff.year}Q{previous_quarter}:"
        f"{expected_previous_cutoff.isoformat()}"
    )
    return {
        "report_mode": (
            "quarter_first_week" if cutoff == first_thursday else "regular_week"
        ),
        "expected_previous_cutoff": expected_previous_cutoff.isoformat(),
        "expected_previous_reporting_period_id": expected_previous_reporting_period_id,
    }


def excel_forecast_yoy(d_value: float | None, c_value: float | None) -> float | None:
    numerator = 0 if d_value is None else d_value
    if c_value in (None, 0):
        return None
    return numerator / c_value - 1


def select_industry(
    template_industry: str | None, candidates: list[dict[str, Any]]
) -> tuple[str | None, bool]:
    if template_industry:
        return template_industry, False
    ordered = sorted(
        candidates,
        key=lambda row: (
            -row["F"],
            -row["J"],
            not row["matches_previous"],
            row["industry"],
        ),
    )
    if not ordered:
        return None, True
    name_fallback_used = len(ordered) > 1 and (
        ordered[0]["F"], ordered[0]["J"], ordered[0]["matches_previous"]
    ) == (
        ordered[1]["F"], ordered[1]["J"], ordered[1]["matches_previous"]
    )
    return ordered[0]["industry"], name_fallback_used


def sort_customer_detail(rows: list[dict[str, Any]]) -> list[str]:
    def sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        c_value = row.get("C")
        new_zero_partition = not row.get("existing") and c_value in (None, 0)
        numeric_c = float("-inf") if c_value is None else c_value
        if row.get("existing"):
            tie_key = (0, row["previous_order"], 0, 0, "")
        else:
            d_value = row.get("D")
            d_blank = d_value is None
            tie_key = (
                1,
                0,
                d_blank,
                -(0 if d_value is None else d_value),
                -row["F"],
                row["customer"],
            )
        return (new_zero_partition, -numeric_c, *tie_key)

    return [row["customer"] for row in sorted(rows, key=sort_key)]


def template_is_eligible(
    metadata: dict[str, Any], context: dict[str, Any]
) -> bool:
    return (
        metadata.get("template_asset_id")
        == "TEMPLATE_CUSTOMER_REVENUE_DETAIL_LATEST_LOCAL_ONLY"
        and metadata.get("template_version") == context.get("confirmed_template_version")
        and metadata.get("template_current_year") == context.get("current_year")
        and metadata.get("template_quarter") == context.get("quarter")
        and bool(metadata.get("template_file_reference"))
        and metadata.get("structure_validation_status") == "passed"
    )


def rank_customer_top20(
    rows: list[dict[str, Any]], ranking: str, forecast_d_available: bool = True
) -> list[str]:
    if ranking == "prior_year":
        if forecast_d_available:
            ordered = sorted(rows, key=lambda row: (-row["C"], -row["D"], row["customer"]))
        else:
            ordered = sorted(rows, key=lambda row: (-row["C"], row["customer"]))
    else:
        ordered = sorted(rows, key=lambda row: (-row["D"], -row["C"], row["customer"]))
    return [row["customer"] for row in ordered]


def semantic_case_map(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = document.get("dcp_semantic_test_cases", [])
    ids = [item.get("case_id") for item in cases]
    assert len(ids) == len(set(ids)), "duplicate DCP semantic case_id"
    for item in cases:
        assert item.get("expected_result"), f"{item.get('case_id')}: Expected Result is required"
    return {item["case_id"]: item for item in cases}


def select_latest_instances(instances: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in instances:
        if item.get("validation_status") == "passed":
            by_product[item["product"]].append(item)
    selected: list[str] = []
    blocked: list[str] = []
    for product, rows in sorted(by_product.items()):
        latest = max(row["attempt"] for row in rows)
        candidates = [row for row in rows if row["attempt"] == latest]
        if len(candidates) != 1:
            blocked.append(product)
        else:
            selected.append(candidates[0]["result_id"])
    return selected, blocked


def filter_customer_rows(rows: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    keys = [(r["product"], r["period"], r["customer_id"]) for r in rows]
    duplicate_keys = {key for key, count in Counter(keys).items() if count > 1}
    retained: list[str] = []
    duplicates: set[str] = set()
    negatives: set[str] = set()
    for row, key in zip(rows, keys):
        if key in duplicate_keys:
            duplicates.add(row["customer_id"])
        elif row["impression_count"] < 0:
            negatives.add(row["customer_id"])
        else:
            retained.append(row["customer_id"])
    return retained, sorted(duplicates), sorted(negatives)


def exact_dcp_match(registry: dict[str, Any], request: dict[str, Any]) -> list[str]:
    matches: list[str] = []
    requested_dimensions = set(request.get("dimensions", []))
    requested_metrics = set(request.get("metrics", []))
    period_semantics = request.get("period", {}).get("semantics")
    comparison_mode = request.get("comparison", {}).get("mode")
    if not requested_metrics or not period_semantics or not comparison_mode:
        return matches
    for profile in registry.get("capability_profiles", []):
        metadata = profile.get("metadata", {})
        compatibility = profile.get("period_comparison_contract", {})
        dependencies = compatibility.get("metric_comparison_dependencies", {})
        if (
            metadata.get("domain") == request.get("domain")
            and request.get("intent") in metadata.get("intents", [])
            and metadata.get("capability_scope_id") == request.get("capability_scope_id")
            and requested_dimensions.issubset(set(metadata.get("supported_dimensions", [])))
            and requested_metrics.issubset(set(metadata.get("supported_metrics", [])))
            and period_semantics in compatibility.get("supported_period_semantics", [])
            and comparison_mode in compatibility.get("supported_comparison_modes", [])
            and all(
                dependencies.get(metric) in (None, comparison_mode)
                for metric in requested_metrics
            )
        ):
            matches.append(profile["dcp_id"])
    return matches


def natural_language_brief_to_arc(
    registry: dict[str, Any], request_id: str, brief: str
) -> dict[str, Any]:
    """Resolve a synthetic Brief through explicit catalog terms, never similarity."""
    catalog = registry.get("brief_canonicalization_catalog", {})
    matches = [
        entry
        for entry in catalog.get("entries", [])
        if any(term in brief for term in entry.get("registered_brief_terms", []))
    ]
    assert len(matches) == 1, "Brief requires one explicitly registered canonical concept"
    matched_entry = matches[0]
    arc = dict(matched_entry["arc_metadata"])
    arc["dimensions"] = list(arc.get("dimensions", []))
    arc["metrics"] = list(arc.get("metrics", []))
    arc["request_id"] = request_id
    period = re.search(r"(\d{4}-\d{2}-\d{2})至(\d{4}-\d{2}-\d{2})", brief)
    assert period, "Brief period must be directly parseable"
    arc["period"] = {
        "semantics": "explicit_date_range",
        "start_date": period.group(1),
        "end_date": period.group(2),
    }
    asks_wow = "环比" in brief
    asks_yoy = "同比" in brief
    assert not (asks_wow and asks_yoy), "Brief comparison is ambiguous and requires Owner confirmation"
    comparison_mode = "week_over_week" if asks_wow else "year_over_year" if asks_yoy else "none"
    arc["comparison"] = {"mode": comparison_mode}
    arc["metrics"].extend(
        matched_entry.get("comparison_metric_additions", {}).get(comparison_mode, [])
    )
    arc["filters"] = []
    if "输出表格" in brief:
        arc["output"] = {"format": "table", "audience": "WORKFLOW_OWNER"}
    else:
        raise AssertionError("Synthetic Brief output must be directly expressed")
    assert not any(
        key in arc
        for key in (
            "dataset_ids",
            "query_asset_ids",
            "mapping_profile_ids",
            "business_rule_dependencies",
            "metric_variant_ids",
            "result_contract_ids",
        )
    ), "Brief understanding cannot select business assets"
    return arc


def main() -> int:
    suite = load(SCENARIOS)
    customer_suite = load(CUSTOMER_SCENARIOS)
    runtime = load(RUNTIME)
    dcp = load(DCP)
    store = load(STORE)
    customer_context = load(CUSTOMER_CONTEXT)
    customer_policy = load(CUSTOMER_POLICY)
    customer_output = load(CUSTOMER_OUTPUT)
    technical_rule = load(TECHNICAL_RULE)
    weekly_comparable_rule = load(WEEKLY_COMPARABLE_RULE)
    full_quarter_rule = load(CUSTOMER_FULL_QUARTER_RULE)
    comparable_rule = load(CUSTOMER_COMPARABLE_RULE)
    customer_technical_adapter = load(CUSTOMER_TECHNICAL_ADAPTER)
    customer_local_inputs = load(CUSTOMER_LOCAL_INPUTS)
    customer_result_contract = load(CUSTOMER_RESULT_CONTRACT)
    pipeline_registry = load(PIPELINE_REGISTRY)
    customer_baseline = load(CUSTOMER_BASELINE)
    customer_business_rule_gate = load(CUSTOMER_BUSINESS_RULE_GATE)
    customer_pipeline_gate = load(CUSTOMER_PIPELINE_GATE)
    customer_field_mapping_gate = load(CUSTOMER_FIELD_MAPPING_GATE)
    customer_result_gate = load(CUSTOMER_RESULT_GATE)
    customer_output_gate = load(CUSTOMER_OUTPUT_GATE)
    customer_code_gate = load(CUSTOMER_CODE_GATE)
    dataset_inventory = load(DATASET_INVENTORY)
    scenarios = scenario_map(suite)
    customer_scenarios = scenario_map(customer_suite)
    semantic_cases = semantic_case_map(suite)

    assert suite.get("contains_real_business_data") is False
    assert suite.get("external_side_effects_allowed") is False
    assert customer_suite.get("contains_real_business_data") is False
    assert customer_suite.get("external_side_effects_allowed") is False
    assert customer_suite.get("suite_id") == "CUSTOMER_REVENUE_DETAIL_ACCEPTANCE_V1"
    assert len(customer_scenarios) == 42
    customer_pipeline = next(
        pipeline
        for pipeline in pipeline_registry["pipelines"]
        if pipeline.get("pipeline_id") == "PL_CUSTOMER_REVENUE_DETAIL_WEEKLY"
    )
    gate_check_contract = {
        "business_rule": (
            customer_business_rule_gate["gate_checks"],
            {
                "industry_selection_structured_contract",
                "detail_sorting_structured_contract",
                "unmatched_advertiser_notification_payload_contract",
                "quarter_template_eligibility_contract",
                "previous_output_metadata_contract_without_filename_parsing",
                "formula_mirror_numeric_tolerance_contract",
                "frozen_weekly_comparable_rule_unchanged",
                "quarter_first_week_template_order_tie_break",
                "effective_layout_source_no_business_value_inheritance",
                "canonical_input_role_ids_and_role_specific_fiscal_quarters",
                "no_template_existing_customer_industry_inheritance",
                "cross_run_top20_membership_state_priority_and_no_rerank",
                "forecast_top20_first_freeze_requires_D_available",
                "absolute_100_percent_warning_limited_to_yoy_fields",
                "quarter_first_week_mode_independent_of_output_history",
                "missing_immediate_previous_period_blocks_without_two_week_fallback",
                "quarter_first_week_blank_M_N_Q_R_and_I_equals_F",
            },
        ),
        "pipeline": (
            customer_pipeline_gate["checks"],
            {
                "canonical_context_binding_and_target_fiscal_quarter_derivation",
                "customer_specific_technical_adapter_without_weekly_asset_mutation",
                "quarter_template_current_year_quarter_version_eligibility",
                "prior_output_local_metadata_selection_without_filename_parsing",
                "workflow_dependency_count_isolation",
                "deterministic_context_initialization_for_all_run_types",
                "conditional_confirmed_template_version_requirement",
                "customer_run_input_manifest_and_workflow_scoped_dataset_contract",
                "effective_layout_source_and_layout_only_fallback",
                "local_output_metadata_write_and_integer_version_contract",
                "frozen_weekly_comparable_rule_zero_change",
                "four_owner_parameter_manual_and_backfill_contract",
                "technical_period_identity_without_calendar_dependency",
                "role_specific_current_and_prior_year_fiscal_quarter_binding",
                "manifest_role_cardinality_and_business_key",
                "weekly_manifest_and_selection_zero_regression",
                "source_wait_policy_scoped_by_run_type_and_role",
                "local_output_metadata_failure_and_filesystem_collision_consistency",
                "top20_cross_run_quarter_state_freeze",
                "report_mode_derived_from_actual_quarter_first_reporting_cutoff",
                "expected_previous_period_and_cutoff_independently_derived",
                "regular_week_requires_exact_immediately_preceding_output_without_older_fallback",
                "quarter_first_week_previous_output_usage_limited_to_layout_customer_and_A",
            },
        ),
        "code_implementation": (
            customer_code_gate["implementation_readiness_checks"],
            {
                "actual_quarter_first_week_mode_and_independent_expected_previous_period_derivation",
                "exact_immediately_preceding_output_required_without_history_gap_fallback",
                "same_week_rerun_retains_locked_expected_previous_period_selection",
            },
        ),
        "field_mapping": (
            customer_field_mapping_gate["consistency_checks"],
            {
                "unmatched_notification_payload_is_grouped_and_local_only",
                "advertiser_mapping_version_bound_before_data_collection",
                "canonical_advertiser_field_chain",
                "industry_level_1_explicit_binding",
                "business_line_delta_mapping_not_customer_dependency",
                "shared_profile_customer_name_alias_not_customer_identity_authority",
            },
        ),
        "result_contract": (
            customer_result_gate["gate_checks"],
            {
                "missing_field_status_renders_blank_in_output_mapping",
                "calculation_failed_and_pending_confirmation_output_prohibited",
                "customer_name_valid_value_only",
                "explicit_FJ_KL_MN_QR_and_filename_date_lineage",
            },
        ),
        "output_mapping": (
            customer_output_gate["gate_checks"],
            {
                "formula_mirror_numeric_tolerance_without_authority_change",
                "strict_E_IFERROR_blank_D_as_zero",
                "result_contract_missing_to_blank_rendering_only",
                "template_eligibility_current_year_quarter_version",
                "previous_output_metadata_selection_not_filename_parsing",
                "effective_layout_source_current_template_or_layout_only_previous_output",
                "no_template_previous_output_business_value_and_top20_inheritance_prohibited",
                "local_output_metadata_write_with_integer_version_mapping",
                "absent_template_fallback_but_invalid_present_template_blocks",
                "local_output_metadata_failure_and_collision_consistency",
                "explicit_date_header_and_filename_lineage",
                "completion_rate_above_100_percent_not_warning",
            },
        ),
    }
    for gate_name, (checks, required_check_ids) in gate_check_contract.items():
        assert required_check_ids.issubset(checks), f"{gate_name} Gate checks incomplete"
        assert all(checks[check_id] == "pass" for check_id in required_check_ids)

    context = runtime["workflow_run_context"]
    assert context["query_parameter_authority"]["actual_execution_date_business_date_inference_allowed"] is False
    assert set(context["required_fields"]["run_type"]["allowed_values"]) == {"scheduled", "manual", "backfill"}
    for scenario_id in ("manual_run_context", "backfill_run_context"):
        expected = scenarios[scenario_id]["expected_result"]
        assert expected == {"period_source": "workflow_run_context", "execution_date_inference_used": False}

    assert scenarios["normal_week"]["expected_result"]["workflow_completion_status"] == "complete_draft"
    assert scenarios["quarter_transition"]["expected_result"] == {
        "report_mode": "quarter_transition",
        "date_source": "workflow_run_context",
    }

    pp = scenarios["twelve_pp_anomaly_trigger"]
    current = pp["synthetic_input"]["current_rate"]
    prior = pp["synthetic_input"]["prior_rate"]
    threshold = pp["synthetic_input"]["threshold_pp"]
    change_pp = round((current - prior) * 100, 10)
    assert change_pp == pp["expected_result"]["wow_change_pp"] == 12.0
    assert pp["expected_result"]["unit"] == "percentage_point"
    assert (abs(change_pp) >= threshold) is pp["expected_result"]["trigger"]

    zero = scenarios["qualified_customer_zero_rows"]["expected_result"]
    assert zero["result_contract_generated"] and zero["customer_record_count"] == 0
    assert zero["narrative_mode"] == "product_change_only" and zero["fabricated_customer"] is False

    repeated = scenarios["multiple_products_with_repeated_attempt"]
    selected, blocked = select_latest_instances(repeated["synthetic_input"]["instances"])
    assert selected == repeated["expected_result"]["selected_result_ids"] and blocked == []
    ambiguous = scenarios["duplicate_latest_attempt_blocks_product"]
    selected, blocked = select_latest_instances(ambiguous["synthetic_input"]["instances"])
    assert selected == [] and blocked == ambiguous["expected_result"]["blocked_products"]

    customer = scenarios["duplicate_and_negative_customer_rows"]
    retained, duplicates, negatives = filter_customer_rows(customer["synthetic_input"]["rows"])
    expected = customer["expected_result"]
    assert retained == expected["retained_customer_ids"]
    assert duplicates == expected["excluded_duplicate_customer_ids"]
    assert negatives == expected["excluded_negative_customer_ids"]

    physical = store["mvp_physical_store_adapter_strategy"]
    assert physical["physical_schema"]["table_name"] == "metric_results"
    assert physical["configured_display_value_state_schema"]["table_name"] == "configured_display_values"
    sqlite_expected = scenarios["sqlite_idempotency_and_conflict"]["expected_result"]
    assert physical["physical_schema"]["idempotent_unique_key"]["same_key_same_value_action"] == sqlite_expected["metric_same_value_action"]
    assert physical["physical_schema"]["idempotent_unique_key"]["same_key_different_value_action"] == sqlite_expected["metric_conflict_action"]
    display_key = physical["configured_display_value_state_schema"]["idempotent_unique_key"]
    assert display_key["same_key_same_value_action"] == sqlite_expected["configured_display_same_value_action"]
    assert display_key["same_key_different_value_action"] == sqlite_expected["configured_display_conflict_action"]

    partial = scenarios["partial_draft"]["expected_result"]
    assert partial == {
        "workflow_completion_status": "partial_draft",
        "warning_required": True,
        "complete_success_label_allowed": False,
    }

    adhoc = scenarios["adhoc_brief_exact_dcp_match"]
    matches = exact_dcp_match(dcp, adhoc["synthetic_input"]["request"])
    assert matches == [adhoc["expected_result"]["matched_dcp_id"]]
    assert set(adhoc["synthetic_input"]["operations"]).issubset(set(dcp["allowed_standard_analysis_operations"]))
    assert adhoc["expected_result"]["plan_type"] == "Temporary Execution Plan"
    assert adhoc["expected_result"]["creates_formal_workflow"] is False
    assert adhoc["expected_result"]["creates_new_business_metric_formula"] is False

    for scenario_id in (
        "natural_language_revenue_brief_to_plan",
        "natural_language_inventory_brief_to_plan",
    ):
        scenario = scenarios[scenario_id]
        synthetic_input = scenario["synthetic_input"]
        arc = natural_language_brief_to_arc(
            dcp,
            synthetic_input["generated_request_id"],
            synthetic_input["brief"],
        )
        expected = scenario["expected_result"]
        assert arc == expected["arc"]
        matches = exact_dcp_match(dcp, arc)
        assert matches == [expected["matched_dcp_id"]]
        assert expected["plan_type"] == "Temporary Execution Plan"
        assert expected["creates_formal_workflow"] is False
        assert expected["creates_new_business_metric_formula"] is False

    none_case = semantic_cases["comparison_none_excludes_comparison_metrics"]
    none_input = none_case["synthetic_input"]
    none_arc = natural_language_brief_to_arc(
        dcp, none_input["generated_request_id"], none_input["brief"]
    )
    none_expected = none_case["expected_result"]
    assert none_arc["comparison"] == none_expected["comparison"]
    assert none_arc["metrics"] == none_expected["metrics"]
    assert set(none_expected["excluded_metrics"]).isdisjoint(none_arc["metrics"])
    assert exact_dcp_match(dcp, none_arc) == [none_expected["matched_dcp_id"]]

    subset_case = semantic_cases["requested_metric_subset_matches_capability"]
    subset_expected = subset_case["expected_result"]
    assert exact_dcp_match(dcp, subset_case["synthetic_input"]["request"]) == [
        subset_expected["matched_dcp_id"]
    ]
    assert subset_expected["whole_dcp_metric_list_required"] is False

    for case_id in ("unsupported_period_is_rejected", "unsupported_comparison_is_rejected"):
        case = semantic_cases[case_id]
        expected = case["expected_result"]
        assert exact_dcp_match(dcp, case["synthetic_input"]["request"]) == expected[
            "matched_dcp_ids"
        ]
        assert expected["action"] == "request_owner_confirmation"

    explicit_case = semantic_cases["explicit_comparison_adds_registered_metrics"]
    for comparison_key in ("week_over_week", "year_over_year"):
        comparison_input = explicit_case["synthetic_input"][comparison_key]
        comparison_expected = explicit_case["expected_result"][comparison_key]
        comparison_arc = natural_language_brief_to_arc(
            dcp,
            comparison_input["generated_request_id"],
            comparison_input["brief"],
        )
        assert comparison_arc["comparison"] == comparison_expected["comparison"]
        assert set(comparison_expected["added_metrics"]).issubset(comparison_arc["metrics"])
        assert set(comparison_expected["excluded_metrics"]).isdisjoint(
            comparison_arc["metrics"]
        )
        assert exact_dcp_match(dcp, comparison_arc) == ["DCP_REVENUE_TECHNICAL_V1"]

    source_roles = customer_scenarios["source_role_separation"]
    source_input = source_roles["synthetic_input"]
    assert {
        "C": source_input["full_quarter_C"],
        "K": source_input["comparable_K"],
        "L": source_input["comparable_L"],
        "source_roles_distinct": True,
    } == source_roles["expected_result"]
    source_policy = customer_policy["processing"]["prior_year_source_roles"]
    assert source_policy["C_full_quarter"]["rule_id"] == full_quarter_rule["rule_id"]
    assert source_policy["K_L_comparable_qtd"]["rule_id"] == comparable_rule["rule_id"]
    assert full_quarter_rule["conditions"]["comparable_cutoff_snapshot_allowed"] is False
    assert comparable_rule["conditions"]["full_quarter_history_allowed"] is False
    assert "WF_CUSTOMER_REVENUE_DETAIL" not in weekly_comparable_rule["applicable_workflow_ids"]

    signed = customer_scenarios["signed_comparable_values"]
    assert signed["expected_result"] == {
        "accepted": True,
        "K": signed["synthetic_input"]["comparable_K"],
        "L": signed["synthetic_input"]["comparable_L"],
    }
    assert comparable_rule["conditions"]["signed_numeric_values_allowed"] is True
    assert comparable_rule["conditions"]["zero_or_negative_value_allowed"] is True
    assert customer_scenarios["quarter_first_week_applicability"]["expected_result"][
        "technical_eligibility_applies"
    ] is True
    assert technical_rule["applicable_workflow_ids"] == ["WF_WEEKLY_BUSINESS_REPORT"]
    assert technical_rule["applicability"]["report_modes"] == [
        "regular_week",
        "quarter_transition_week",
    ]
    assert customer_technical_adapter["applicability"]["report_modes"] == [
        "regular_week",
        "quarter_first_week",
    ]
    assert customer_scenarios[
        "quarter_first_week_without_template_requires_previous_output"
    ]["expected_result"] == {
        "workflow_blocked": True,
        "missing_dependency": "previous_reporting_period_validated_output",
    }
    first_week_dependency = customer_policy["processing"][
        "quarter_first_week_prior_output_dependency"
    ]
    assert first_week_dependency["template_available"] == "optional"
    assert first_week_dependency["template_unavailable"] == "required_for_layout_customer_list_and_industry"
    assert first_week_dependency["template_and_previous_output_both_unavailable"] == "block_customer_workflow"
    assert first_week_dependency["permitted_previous_output_reuse"] == [
        "layout",
        "customer_membership",
        "detail_A_industry",
    ]
    assert first_week_dependency["M_N_Q_R"] == "blank"
    assert first_week_dependency["I"] == "F"
    assert customer_policy["processing"]["quarter_first_week_prior_output_fields"] == "blank_M_N_Q_R"
    assert customer_policy["processing"]["quarter_first_week_incremental_performance"] == "I_equals_F"

    forecast = customer_scenarios["forecast_D_availability_blank_vs_zero"]
    forecast_input = forecast["synthetic_input"]
    unavailable = resolve_forecast_d(
        forecast_input["customers"], forecast_input["unavailable_owner_values"], False
    )
    available = resolve_forecast_d(
        forecast_input["customers"], forecast_input["available_owner_values"], True
    )
    assert unavailable | {"top20_mode": "header_only"} == forecast["expected_result"][
        "unavailable"
    ]
    assert available | {"top20_mode": "frozen_members_plus_Other"} == forecast[
        "expected_result"
    ]["available"]
    forecast_policy = customer_policy["processing"]["forecast_D_semantics"]
    assert "including an explicit zero" in forecast_policy["available_when"]
    assert "Preserve D as blank" in forecast_policy["unavailable_behavior"]
    assert customer_policy["processing"]["formulas"]["E"] == 'IFERROR(D/C-1,"")'
    assert customer_policy["processing"]["formulas"][
        "E_blank_D_numeric_semantics"
    ] == "treat_as_zero"
    assert customer_policy["processing"]["formulas"]["G"].startswith('IF(D="",""')

    coverage = customer_scenarios["explicit_output_binding_coverage"]["expected_result"]
    assert coverage == {
        "detail_binding_count": 18,
        "prior_top20_binding_count": 7,
        "forecast_top20_binding_count": 7,
    }
    output_sheets = {
        sheet["sheet_id"]: sheet for sheet in customer_output["workbook_layout"]["sheets"]
    }
    expected_columns = {
        "CUSTOMER_DETAIL_LIST": list("ABCDEFGHIJKLMNOPQR"),
        "PRIOR_YEAR_TOP20": list("ABCDEFG"),
        "CURRENT_QUARTER_TOP20": list("ABCDEFG"),
    }
    for sheet_id, columns in expected_columns.items():
        fields = output_sheets[sheet_id]["output_fields"]
        assert [field["target_column"] for field in fields] == columns
        assert all(
            set(field["result_field_binding"])
            == {"result_contract_id", "record_set_id", "result_field_id"}
            for field in fields
        )
    template_membership = customer_scenarios["template_top20_membership_preserved"]
    assert template_membership["synthetic_input"]["template_members"] == template_membership[
        "expected_result"
    ]["frozen_members"]
    assert template_membership["expected_result"]["reranked"] is False
    top20_policy = customer_policy["processing"]["top20"]
    assert top20_policy["existing_template_membership"][
        "preserve_and_freeze_without_reranking"
    ] is True
    generated = customer_scenarios["generated_top20_rankings"]
    assert rank_customer_top20(generated["synthetic_input"]["rows"], "prior_year") == generated[
        "expected_result"
    ]["prior_year_order"]
    assert rank_customer_top20(generated["synthetic_input"]["rows"], "forecast") == generated[
        "expected_result"
    ]["forecast_order"]
    assert rank_customer_top20(
        generated["synthetic_input"]["D_unavailable_rows"],
        "prior_year",
        forecast_d_available=False,
    ) == generated["expected_result"]["prior_year_order_when_D_unavailable"]
    assert top20_policy["prior_year_ranking"] == {
        "D_available": ["C_desc", "D_desc", "customer_name_asc"],
        "D_unavailable": ["C_desc", "customer_name_asc"],
    }
    assert top20_policy["forecast_ranking"] == {
        "D_available": ["D_desc", "C_desc", "customer_name_asc"],
        "D_unavailable": "not_applicable_header_only",
    }
    assert top20_policy["forecast_availability"]["unavailable_output"] == "header_only"

    rerun = customer_scenarios["same_week_rerun_previous_period_selection"]
    selected_previous = select_previous_period_output(
        rerun["synthetic_input"]["outputs"],
        rerun["synthetic_input"]["previous_reporting_period_id"],
    )
    assert selected_previous == rerun["expected_result"]["selected_previous_result_id"]
    assert rerun["expected_result"]["same_week_attempt_used"] is False
    previous_policy = customer_policy["processing"]["previous_output_selection"]
    assert previous_policy["same_reporting_period_attempt_allowed"] is False
    assert previous_policy["same_week_rerun_reuses_locked_selection"] is True
    assert previous_policy["older_reporting_period_fallback_allowed"] is False
    assert previous_policy["required_reporting_period_id"] == "locked expected_previous_reporting_period_id"
    assert previous_policy["required_cutoff"] == "locked expected_previous_cutoff"
    assert rerun["expected_result"]["rerun_retains_expected_previous_cutoff"] is customer_context["selection_and_rerun_semantics"]["same_week_rerun_retains_expected_previous_cutoff"]
    assert rerun["expected_result"]["rerun_retains_expected_previous_reporting_period_id"] is customer_context["selection_and_rerun_semantics"]["same_week_rerun_retains_expected_previous_reporting_period_id"]

    missing_previous = customer_scenarios[
        "missing_immediate_previous_week_does_not_fallback"
    ]
    missing_context = derive_customer_reporting_period_context(
        missing_previous["synthetic_input"]["current_year"],
        missing_previous["synthetic_input"]["quarter"],
        missing_previous["synthetic_input"]["current_revenue_cutoff_date"],
    )
    exact_matches = [
        item
        for item in missing_previous["synthetic_input"]["available_outputs"]
        if item["reporting_period_id"]
        == missing_context["expected_previous_reporting_period_id"]
        and item["current_revenue_cutoff_date"]
        == missing_context["expected_previous_cutoff"]
        and item["validation_status"] == "passed"
        and item["history_consumption_status"] == "consumable"
    ]
    assert {
        **missing_context,
        "blocked": not exact_matches,
        "older_output_selected": False,
    } == missing_previous["expected_result"]

    mid_quarter = customer_scenarios[
        "mid_quarter_without_history_is_regular_week_and_blocks"
    ]
    mid_context = derive_customer_reporting_period_context(
        mid_quarter["synthetic_input"]["current_year"],
        mid_quarter["synthetic_input"]["quarter"],
        mid_quarter["synthetic_input"]["current_revenue_cutoff_date"],
    )
    assert {
        **mid_context,
        "blocked": not mid_quarter["synthetic_input"]["available_outputs"],
        "identified_as_quarter_first_week": mid_context["report_mode"]
        == "quarter_first_week",
    } == mid_quarter["expected_result"]

    locked = customer_scenarios["customer_run_context_lock"]
    changed_locked_key = any(
        locked["synthetic_input"]["locked"].get(key) != value
        for key, value in locked["synthetic_input"]["attempted_update"].items()
    )
    assert changed_locked_key is locked["expected_result"]["update_rejected"] is True
    assert locked["expected_result"]["workflow_blocked"] is True
    assert customer_context["lock_policy"]["lock_before_stage"] == "DATA_COLLECTION"
    assert customer_context["lock_policy"]["immutable_after_lock"] is True
    assert customer_context["selection_and_rerun_semantics"][
        "same_reporting_period_attempt_may_be_previous_output"
    ] is False

    zero_fill = customer_scenarios["missing_current_customer_zero_fill"]
    zero_filled = {
        customer: zero_fill["synthetic_input"]["current_values"].get(
            customer, {"F": 0, "J": 0}
        )
        for customer in zero_fill["synthetic_input"]["customer_universe"]
    }
    assert zero_filled == zero_fill["expected_result"]
    assert customer_policy["processing"]["missing_current_customer_zero_fill"] == {
        "condition": "Customer is in the union but has no eligible row in the validated current snapshot.",
        "F": 0,
        "J": 0,
        "missing_or_blank_allowed": False,
    }

    dependency_case = customer_scenarios["workflow_dependency_count_isolation"]
    expected_dependencies = dependency_case["expected_result"]
    assert pipeline_registry["readiness_gate"][
        "weekly_declared_dataset_dependency_count"
    ] == expected_dependencies["weekly_dataset_dependency_count"]
    assert customer_baseline["workflow_dependency_counts"][
        "dataset_dependency_count"
    ] == expected_dependencies["customer_dataset_dependency_count"]
    assert customer_pipeline_gate["scope"][
        "declared_dataset_dependency_count"
    ] == expected_dependencies["customer_dataset_dependency_count"]
    workflow_dataset_counts: dict[str, int] = defaultdict(int)
    for pipeline in pipeline_registry["pipelines"]:
        workflows = {
            binding.get("workflow_id")
            for binding in pipeline.get("workflow_bindings", [])
            if isinstance(binding, dict)
        }
        for workflow_id in workflows:
            workflow_dataset_counts[workflow_id] += sum(
                isinstance(item, dict)
                for item in pipeline.get("dataset_dependencies", [])
            )
    assert workflow_dataset_counts["WF_WEEKLY_BUSINESS_REPORT"] == 10
    assert workflow_dataset_counts["WF_CUSTOMER_REVENUE_DETAIL"] == 1
    assert expected_dependencies["legacy_global_mixed_count_used"] is False

    forecast_e = customer_scenarios["forecast_E_blank_D_as_zero"]
    assert [
        excel_forecast_yoy(row["D"], row["C"])
        for row in forecast_e["synthetic_input"]["rows"]
    ] == forecast_e["expected_result"]
    detail_formula = customer_output["workbook_layout"]["sheets"][0][
        "formula_policy"
    ]["formulas"]
    assert detail_formula["E"] == 'IFERROR(D/C-1,"")'
    assert detail_formula["E_blank_D_numeric_semantics"] == "treat_as_zero"

    context_case = customer_scenarios[
        "canonical_context_binding_and_target_quarter"
    ]
    context_input = context_case["synthetic_input"]
    derived_quarter = f"{context_input['current_year']}Q{context_input['quarter']}"
    assert {
        "target_fiscal_quarter": derived_quarter,
        "runtime_guessing_used": False,
    } == context_case["expected_result"]
    assert customer_context["derived_field_bindings"]["target_fiscal_quarter"][
        "source_field_ids"
    ] == ["current_year", "quarter"]
    assert customer_context["constraints"][
        "runtime_field_alias_guessing_allowed"
    ] is False
    for rule in (customer_technical_adapter, full_quarter_rule, comparable_rule):
        required_fields = set(rule["inputs"]["required_context_fields"])
        assert required_fields.issubset(customer_context["required_fields"])
        assert set(rule["context_binding"]["exact_field_bindings"]) == required_fields

    missing_case = customer_scenarios["result_missing_output_rendering"]
    downstream = customer_result_contract["mode_semantics"]["downstream_consumption"]
    assert missing_case["expected_result"] == {
        "rendered_value": downstream["missing_output_rendering"],
        "calculation_failed_output_allowed": downstream[
            "calculation_failed_output_allowed"
        ],
        "pending_confirmation_output_allowed": downstream[
            "pending_confirmation_output_allowed"
        ],
    }
    assert downstream["output_allowed_value_statuses"] == ["valid_value", "missing"]
    output_consumption = customer_output["assembly_constraints"][
        "result_field_consumption_contract"
    ]
    assert output_consumption["missing_output_rendering"] == "blank_cell"
    assert output_consumption["calculation_failed_output_allowed"] is False
    assert output_consumption["pending_confirmation_output_allowed"] is False

    industry_case = customer_scenarios["industry_selection_order"]
    selected_industry, name_fallback = select_industry(
        industry_case["synthetic_input"]["template_industry"],
        industry_case["synthetic_input"]["candidates"],
    )
    assert {
        "selected_industry": selected_industry,
        "final_name_fallback_used": name_fallback,
    } == industry_case["expected_result"]["previous_industry_case"]
    fallback_industry, fallback_used = select_industry(
        None, industry_case["synthetic_input"]["name_fallback_candidates"]
    )
    assert {
        "selected_industry": fallback_industry,
        "final_name_fallback_used": fallback_used,
        "notification_required": customer_policy["processing"][
            "industry_selection"
        ]["final_name_order_fallback_notification_required"],
    } == industry_case["expected_result"]["name_fallback_case"]
    industry_policy = customer_policy["processing"]["industry_selection"]
    assert [item["key"] for item in industry_policy["source_fallback_order"]] == [
        "grouped_signed_current_performance_F",
        "grouped_signed_current_executed_J",
        "matches_previous_reporting_period_industry",
        "industry_name",
    ]

    detail_sort_case = customer_scenarios["detail_sorting_contract"]
    assert sort_customer_detail(detail_sort_case["synthetic_input"]["rows"]) == detail_sort_case[
        "expected_result"
    ]
    assert customer_policy["processing"]["detail_sorting"][
        "runtime_sort_key_inference_allowed"
    ] is False

    unmatched_case = customer_scenarios[
        "unmatched_advertiser_notification_payload"
    ]["expected_result"]
    notification_contract = customer_policy["processing"]["advertiser_mapping"][
        "unmatched_advertiser_notification"
    ]
    assert notification_contract["payload_fields"] == unmatched_case["payload_fields"]
    assert "raw_order_rows" in notification_contract["prohibited_payload"]
    assert notification_contract["persistence_scope"] == unmatched_case[
        "persistence_scope"
    ]

    template_case = customer_scenarios["quarter_template_eligibility"]
    template_input = template_case["synthetic_input"]
    assert {
        "matching_template": "available"
        if template_is_eligible(template_input["matching_template"], template_input["context"])
        else "unavailable",
        "wrong_quarter_template": "available"
        if template_is_eligible(template_input["wrong_quarter_template"], template_input["context"])
        else "blocked_invalid_candidate",
        "prior_quarter_fields_inherited": False,
    } == template_case["expected_result"]
    template_contract = customer_local_inputs["quarter_template_eligibility_contract"]
    assert template_contract["unavailable_behavior"]["prohibited_inheritance_fields"] == [
        "C",
        "D",
        "O",
        "P",
    ]
    assert template_contract["filename_parsing_for_eligibility_allowed"] is False
    assert customer_policy["processing"]["top20"]["existing_template_membership"][
        "eligibility_prerequisite"
    ].startswith("quarter_template_eligibility equals available")

    metadata_case = customer_scenarios["previous_output_metadata_contract"][
        "expected_result"
    ]
    metadata_contract = customer_local_inputs[
        "previous_reporting_period_output_metadata_contract"
    ]
    assert list(metadata_contract["required_metadata_fields"]) == metadata_case[
        "required_metadata_fields"
    ]
    assert metadata_contract["filename_parsing_for_history_selection_allowed"] is False
    assert metadata_contract["selection_predicates"]["reporting_period_id"] == "exactly equals locked expected_previous_reporting_period_id"
    assert metadata_contract["selection_predicates"]["current_revenue_cutoff_date"] == "exactly equals locked expected_previous_cutoff"
    assert metadata_contract["selection_predicates"]["older_reporting_period_fallback_allowed"] is False

    frozen_case = customer_scenarios[
        "frozen_weekly_rule_and_customer_adapter"
    ]["expected_result"]
    assert technical_rule["applicable_workflow_ids"] == frozen_case["weekly_workflows"]
    assert technical_rule["applicability"]["report_modes"] == frozen_case[
        "weekly_report_modes"
    ]
    assert customer_technical_adapter["applicability"]["report_modes"] == frozen_case[
        "customer_report_modes"
    ]
    assert customer_technical_adapter["governance"][
        "frozen_weekly_asset_is_runtime_dependency"
    ] is frozen_case["weekly_rule_is_customer_runtime_dependency"]

    context_init = customer_scenarios["deterministic_context_initialization"]
    init_input = context_init["synthetic_input"]
    cutoff = date.fromisoformat(init_input["current_revenue_cutoff_date"])
    prior_comparable = cutoff.replace(year=cutoff.year - 1) + timedelta(days=1)
    period_context = derive_customer_reporting_period_context(
        init_input["current_year"], init_input["quarter"], init_input["current_revenue_cutoff_date"]
    )
    assert {
        "prior_year": init_input["current_year"] - 1,
        "target_fiscal_quarter": f"{init_input['current_year']}Q{init_input['quarter']}",
        "prior_year_fiscal_quarter": f"{init_input['current_year'] - 1}Q{init_input['quarter']}",
        "reporting_period_id": f"CUSTOMER:{init_input['current_year']}Q{init_input['quarter']}:{init_input['current_revenue_cutoff_date']}",
        "report_mode": period_context["report_mode"],
        "expected_previous_cutoff": period_context["expected_previous_cutoff"],
        "expected_previous_reporting_period_id": period_context["expected_previous_reporting_period_id"],
        "previous_reporting_period_id": period_context["expected_previous_reporting_period_id"],
        "prior_year_full_quarter_period_id": f"{init_input['current_year'] - 1}Q{init_input['quarter']}",
        "prior_comparable_as_of_date": prior_comparable.isoformat(),
        "confirmed_template_version": None,
        "locked_before_data_collection": customer_context["lock_policy"]["lock_before_stage"] == "DATA_COLLECTION",
    } == context_init["expected_result"]
    assert customer_context["required_fields"]["confirmed_template_version"]["required_when"] == "quarter_template_candidate_presence equals present"
    assert set(customer_context["required_fields"]["run_type"]["allowed_values"]) == {"scheduled", "manual", "backfill", "rerun"}
    assert customer_context["deterministic_initialization_contract"]["all_required_and_conditionally_required_fields_resolved_before_lock"] is True
    assert customer_context["derived_field_bindings"]["report_mode"]["history_output_presence_or_target_quarter_membership_may_influence_mode"] is False

    manifest_case = customer_scenarios["customer_run_input_manifest_scope"]["expected_result"]
    manifest = customer_local_inputs["customer_run_input_manifest_contract"]
    customer_scope = dataset_inventory["runtime_input_contract"]["workflow_scopes"]["WF_CUSTOMER_REVENUE_DETAIL"]
    assert {
        "workflow_scope": manifest["workflow_scope"],
        "manifest_id": manifest["manifest_id"],
        "weekly_contract_reused": customer_scope["weekly_runtime_contract_reuse_allowed"],
        "filename_or_latest_guessing": manifest["filename_latest_file_or_execution_date_selection_allowed_for_WF_CUSTOMER_REVENUE_DETAIL"],
    } == manifest_case
    assert dataset_inventory["runtime_input_contract"]["applies_by_workflow_scope"] is True
    assert dataset_inventory["runtime_input_contract"]["unscoped_runtime_contract_fallback_allowed"] is False

    layout_case = customer_scenarios["effective_layout_source_selection"]["expected_result"]
    layout_contract = customer_local_inputs["effective_layout_source_contract"]
    layout_reuse = layout_contract["previous_period_output_layout_only_reuse"]
    assert {
        "effective_layout_source": "previous_period_output_layout_only",
        "allowed_reuse": layout_reuse["allowed"],
        "inherited_fields": [],
        "inherited_top20": False,
    } == layout_case
    assert layout_reuse["prohibited_detail_fields"] == ["C", "D", "O", "P"]
    assert layout_reuse["prohibited_record_sets"] == ["prior_year_top20", "forecast_top20"]

    metadata_write_case = customer_scenarios["local_output_metadata_write_and_version"]
    write_contract = customer_local_inputs["local_output_metadata_write_contract"]
    next_version = max(metadata_write_case["synthetic_input"]["existing_passed_output_versions"]) + 1
    suffix = write_contract["version_contract"]["filename_suffix_mapping"][next_version]
    assert {
        "next_output_version": next_version,
        "filename_suffix": suffix,
        "output_version_type": write_contract["required_metadata_fields"]["output_version"]["data_type"],
        "filename_parsing_used": write_contract["version_contract"]["filename_parsing_to_determine_output_version_allowed"],
        "metadata_written_after_success_only": customer_output["output_target"]["local_output_metadata_write_contract"]["write_after_successful_validation_only"],
    } == metadata_write_case["expected_result"]
    assert {"current_revenue_cutoff_date", "prior_comparable_as_of_date"}.issubset(write_contract["required_metadata_fields"])

    template_tie = customer_scenarios["quarter_first_week_template_order_tie"]
    assert [row["customer"] for row in sorted(template_tie["synthetic_input"]["rows"], key=lambda row: (-row["C"], row["template_order"]))] == template_tie["expected_result"]
    existing_tie = customer_policy["processing"]["detail_sorting"]["existing_customer_equal_C_tie"]
    assert existing_tie["quarter_first_week_without_previous_output_with_eligible_current_template"] == "preserve_current_quarter_template_row_order"
    assert existing_tie["any_other_runtime_tie_break_allowed"] is False

    frozen_comparable = customer_scenarios["frozen_weekly_comparable_rule_zero_diff"]["expected_result"]
    assert all(key not in weekly_comparable_rule["governance"] for key in frozen_comparable["customer_governance_keys_absent"])
    assert comparable_rule["constraints"]["weekly_prior_year_rule_reuse_allowed"] is False
    zero_diff_contract = customer_baseline["change_control"]["weekly_frozen_asset_zero_diff_contract"]
    assert zero_diff_contract["exact_files"] == frozen_comparable["exact_zero_diff_assets"]
    for frozen_path in zero_diff_contract["exact_files"]:
        baseline_bytes = subprocess.check_output(
            ["git", "show", f"{zero_diff_contract['comparison_base_commit_sha']}:{frozen_path}"],
            cwd=ROOT,
        )
        assert baseline_bytes == (ROOT / frozen_path).read_bytes(), f"{frozen_path}: frozen Weekly asset changed"

    fiscal_case = customer_scenarios["fiscal_quarter_role_binding"]["expected_result"]
    role_quarters = customer_technical_adapter["conditions"]["role_specific_fiscal_quarter_binding"]
    assert {
        "current_qtd_context_field": role_quarters["current_qtd"]["context_field"],
        "historical_context_field": role_quarters["prior_year_full_quarter"]["context_field"],
        "historical_current_quarter_filter_allowed": customer_technical_adapter["conditions"]["historical_role_may_filter_on_target_fiscal_quarter"],
    } == fiscal_case
    assert role_quarters["prior_year_comparable"]["context_field"] == "prior_year_fiscal_quarter"
    assert full_quarter_rule["conditions"]["input_role"] == "prior_year_full_quarter"
    assert comparable_rule["conditions"]["input_role"] == "prior_year_comparable"

    manual_case = customer_scenarios["four_parameter_manual_run"]
    manual_inputs = customer_pipeline["execution"]["manual_trigger_required_parameters"]
    manual_values = manual_case["synthetic_input"]
    manual_reporting_id = f"CUSTOMER:{manual_values['CurrentYear']}Q{manual_values['Quarter']}:{manual_values['CurrentRevenueCutoffDate']}"
    assert {
        "required_parameter_count": len(manual_inputs),
        "reporting_period_id": manual_reporting_id,
        "reporting_period_owner_input": "ReportingPeriodId" in manual_inputs,
        "external_calendar_dependency": customer_policy["trigger"]["run_context"]["technical_period_identity"]["external_calendar_dependency_allowed"],
    } == manual_case["expected_result"]
    assert manual_inputs == ["WorkflowExecutionDate", "CurrentRevenueCutoffDate", "CurrentYear", "Quarter"]

    weekly_case = customer_scenarios["weekly_manifest_selection_zero_regression"]["expected_result"]
    runtime_scopes = dataset_inventory["runtime_input_contract"]["workflow_scopes"]
    assert {
        "weekly_manifest_id": runtime_scopes["WF_WEEKLY_BUSINESS_REPORT"]["run_input_manifest_id"],
        "weekly_multiple_email_selection_unchanged": runtime_scopes["WF_WEEKLY_BUSINESS_REPORT"]["existing_multiple_email_selection_semantics_unchanged"],
        "customer_latest_rule_scoped_only": "unconditional_latest_file_selection_allowed" in runtime_scopes["WF_CUSTOMER_REVENUE_DETAIL"] and "unconditional_latest_file_selection_allowed" not in dataset_inventory["runtime_input_contract"],
    } == weekly_case
    assert runtime["run_input_manifest"]["manifest_id"] == weekly_case["weekly_manifest_id"]
    rolling_deck_dataset = next(item for item in dataset_inventory["datasets"] if item.get("dataset_id") == "DS_REVENUE_SALES_ROLLING_DECK_QTD")
    weekly_multiple_selection = rolling_deck_dataset["acquisition"]["source_object_or_attachment_rule"]["executable_version_selection_rule"]["multiple_match_selection_rule"]
    assert weekly_multiple_selection["sort_by"] == "email_sent_at"
    assert weekly_multiple_selection["sort_order"] == "descending"
    assert weekly_multiple_selection["select"] == "first"

    wait_case = customer_scenarios["source_wait_scope"]["expected_result"]
    wait_policy = customer_policy["trigger"]["source_wait_policy"]
    assert {
        "scheduled_current_qtd": "unlimited_30_minute_recheck_after_notify_once"
        if all((wait_policy["scheduled_current_qtd"]["initial_missing_action"] == "notify_once", wait_policy["scheduled_current_qtd"]["recheck_interval_minutes"] == 30, wait_policy["scheduled_current_qtd"]["maximum_rechecks"] == "unlimited", wait_policy["scheduled_current_qtd"]["auto_resume_on_arrival"] is True, wait_policy["scheduled_current_qtd"]["multiple_or_unreadable_action"] == "block_customer_workflow"))
        else "invalid",
        "scheduled_current_multiple_or_unreadable": "direct_block" if wait_policy["scheduled_current_qtd"]["multiple_or_unreadable_action"] == "block_customer_workflow" else "invalid",
        "scheduled_historical": "direct_block" if wait_policy["scheduled_historical_C_and_K_L"]["wait_or_recheck_allowed"] is False else "wait",
        "manual_or_backfill_all_roles": "direct_block" if wait_policy["manual_or_backfill_all_roles"]["wait_or_recheck_allowed"] is False else "wait",
        "unconditional_latest_allowed": customer_pipeline["dataset_dependencies"][0]["source_instance_selection"]["unconditional_latest_selection_allowed"],
    } == wait_case

    mapping_case = customer_scenarios["customer_mapping_dependency_and_fields"]["expected_result"]
    mapping_dependency = next(item for item in customer_pipeline["local_input_dependencies"] if item.get("input_role") == "advertiser_ownership_mapping")
    field_chain = customer_policy["processing"]["advertiser_mapping"]["explicit_field_chain"]
    assert {
        "mapping_profiles": customer_pipeline["execution"]["mapping_profile_ids"],
        "external_asset_id": mapping_dependency["external_asset_id"],
        "field_chain": [field_chain["standardized_source_field"], field_chain["canonical_raw_field"], field_chain["mapped_output_field"]],
        "industry_field": customer_policy["processing"]["advertiser_mapping"]["industry_source_field"],
    } == mapping_case
    assert customer_pipeline["execution"]["customer_standard_field_bindings"]["MAP_REVENUE_SALES_ROLLING_DECK_QTD_V1.customer_name_is_customer_identity_authority"] is False

    template_branch_case = customer_scenarios["template_absent_vs_invalid"]["expected_result"]
    layout_contract = customer_local_inputs["effective_layout_source_contract"]
    assert {
        "absent_with_valid_previous_output": layout_contract["deterministic_priority"][1]["value"],
        "present_invalid_metadata_version_or_structure": "blocked" if layout_contract["candidate_present_but_invalid_action"] == "block_customer_workflow" else "not_blocked",
        "invalid_may_be_treated_as_absent": customer_policy["processing"]["effective_layout_source"]["invalid_current_quarter_candidate_may_be_treated_as_absent"],
    } == template_branch_case

    date_case = customer_scenarios["output_date_lineage"]["expected_result"]
    date_lineage = customer_result_contract["date_lineage_contract"]
    assert {
        "AsOfDate": date_lineage["F_J_and_output_AsOfDate"],
        "K_L": date_lineage["K_L"],
        "M_N": date_lineage["M_N"],
        "Q_R": date_lineage["Q_R"],
        "filename_YYYYMMDD": date_lineage["filename_YYYYMMDD"],
    } == date_case
    output_bindings = customer_output["parameterization"]["explicit_run_context_bindings"]
    assert output_bindings["PriorWeekComparableAsOfDate"] == "prior_week_comparable_as_of_date"

    top20_case = customer_scenarios["top20_cross_run_freeze"]["expected_result"]
    top20_state = customer_policy["processing"]["top20"]["cross_run_membership_state"]
    assert {
        "priority": top20_state["selection_priority"],
        "subsequent_rerank_allowed": top20_state["subsequent_week_or_rerun_reranking_allowed"],
        "previous_quarter_reuse_allowed": top20_state["previous_quarter_membership_reuse_allowed"],
        "forecast_freeze_requires_D_available": customer_policy["processing"]["top20"]["forecast_availability"]["first_freeze_only_after_D_available"],
        "ordinary_layout_inheritance": top20_state["top20_is_ordinary_layout_inheritance"],
    } == top20_case

    industry_inherit_case = customer_scenarios["no_template_industry_inheritance"]["expected_result"]
    no_template_industry = customer_policy["processing"]["industry_selection"]
    assert {
        "existing_customer_nonblank_previous_A": "inherit_directly" if "inherit A directly" in no_template_industry["no_template_previous_output_choice"]["action"] else "fallback",
        "new_or_blank_previous_A": "apply_confirmed_fallback" if len(no_template_industry["fallback_applies_only_when"]) == 2 else "invalid",
    } == industry_inherit_case

    warning_case = customer_scenarios["yoy_warning_scope"]
    warning_scope = customer_policy["failure_handling"]["warning_field_scope"]
    assert {
        "completion_rate_warning": warning_scope["performance_completion_rate_triggers_absolute_100_percent_warning"],
        "forecast_yoy_warning": abs(warning_case["synthetic_input"]["forecast_yoy"]) >= 1,
        "warning_scope": "yoy_only" if "absolute_yoy_rate_at_least_100_percent" in warning_scope else "invalid",
    } == warning_case["expected_result"]

    manifest_identity_scenario = customer_scenarios["manifest_role_cardinality_and_customer_identity"]
    manifest_identity_case = manifest_identity_scenario["expected_result"]
    role_inputs = manifest_identity_scenario["synthetic_input"]
    required_roles = manifest["required_input_roles"]
    def exact_roles_once(roles: list[str]) -> bool:
        return Counter(roles) == Counter(required_roles)
    customer_fields = customer_result_contract["record_sets"][0]["record_fields"]
    customer_name_field = next(field for field in customer_fields if field["field_id"] == "customer_name")
    assert {
        "business_key": manifest["entry_business_key"],
        "required_roles": manifest["required_input_roles"],
        "exactly_one_each": manifest["exactly_one_entry_per_required_input_role"],
        "valid_roles_accepted": exact_roles_once(role_inputs["valid_roles"]),
        "duplicate_roles_blocked": not exact_roles_once(role_inputs["duplicate_roles"]),
        "missing_roles_blocked": not exact_roles_once(role_inputs["missing_roles"]),
        "duplicate_or_missing_action": manifest["duplicate_or_missing_required_role_action"],
        "customer_name_statuses": customer_name_field["value_status_allowed"],
    } == manifest_identity_case

    metadata_failure_case = customer_scenarios["metadata_failure_and_collision_consistency"]["expected_result"]
    transaction = write_contract["transactional_success_contract"]
    consistency = write_contract["filesystem_metadata_consistency"]
    assert {
        "metadata_failure_consumable_history": transaction["workbook_may_be_reported_as_next_period_history_when_metadata_write_fails"],
        "overwrite_allowed": consistency["existing_physical_file_overwrite_allowed"],
        "orphan_file_action": consistency["orphan_physical_file_without_metadata_action"],
        "duplicate_version_action": consistency["duplicate_output_version_action"],
        "filesystem_metadata_version_match_required": consistency["physical_file_and_metadata_output_version_must_match"],
    } == metadata_failure_case

    tolerance_case = customer_scenarios["formula_mirror_numeric_tolerance"]
    tolerance_contract = customer_policy["output_boundary"][
        "formula_mirror_numeric_comparison"
    ]
    output_tolerance_contract = customer_output["workbook_layout"]["sheets"][0][
        "formula_policy"
    ]["numeric_comparison"]
    assert output_tolerance_contract["relative_tolerance"] == tolerance_contract[
        "relative_tolerance"
    ]
    assert output_tolerance_contract["absolute_tolerance"] == tolerance_contract[
        "absolute_tolerance"
    ]
    comparison_passed = math.isclose(
        tolerance_case["synthetic_input"]["calculation_engine_value"],
        tolerance_case["synthetic_input"]["excel_value"],
        rel_tol=tolerance_contract["relative_tolerance"],
        abs_tol=tolerance_contract["absolute_tolerance"],
    )
    assert {
        "comparison_passed": comparison_passed,
        "calculation_engine_remains_authoritative": tolerance_contract[
            "comparison_pass_may_change_authoritative_result"
        ]
        is False,
    } == tolerance_case["expected_result"]

    print(
        "Phase 1.5 final acceptance passed: "
        f"{len(scenarios)} synthetic scenarios and {len(semantic_cases)} DCP semantic cases; "
        f"{len(customer_scenarios)} Customer Revenue Detail scenarios; "
        "no real business data or external side effects."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
