#!/usr/bin/env python3
"""Run deterministic synthetic acceptance checks for the Phase 1.5 asset contracts."""

from __future__ import annotations

import re
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
    candidates = [
        item
        for item in outputs
        if item.get("reporting_period_id") == previous_reporting_period_id
        and item.get("validation_status") == "passed"
    ]
    assert candidates, "previous reporting period validated output is required"
    highest_version = max(item["output_version"] for item in candidates)
    selected = [item for item in candidates if item["output_version"] == highest_version]
    assert len(selected) == 1, "previous output highest version must be unique"
    return selected[0]["result_id"]


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
    scenarios = scenario_map(suite)
    customer_scenarios = scenario_map(customer_suite)
    semantic_cases = semantic_case_map(suite)

    assert suite.get("contains_real_business_data") is False
    assert suite.get("external_side_effects_allowed") is False
    assert customer_suite.get("contains_real_business_data") is False
    assert customer_suite.get("external_side_effects_allowed") is False
    assert customer_suite.get("suite_id") == "CUSTOMER_REVENUE_DETAIL_ACCEPTANCE_V1"
    assert len(customer_scenarios) == 12

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
    assert "quarter_first_week" in technical_rule["applicability"][
        "report_modes_by_workflow"
    ]["WF_CUSTOMER_REVENUE_DETAIL"]
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
    assert customer_policy["processing"]["formulas"]["E"].startswith('IF(D="",""')
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

    expected_dependencies = customer_scenarios[
        "workflow_dependency_count_isolation"
    ]["expected_result"]
    customer_baseline = load(
        ROOT / "phase1_5/assets/readiness/implementation_baseline_customer_revenue_detail.yaml"
    )
    assert customer_baseline["workflow_dependency_counts"] == expected_dependencies

    print(
        "Phase 1.5 final acceptance passed: "
        f"{len(scenarios)} synthetic scenarios and {len(semantic_cases)} DCP semantic cases; "
        f"{len(customer_scenarios)} Customer Revenue Detail scenarios; "
        "no real business data or external side effects."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
