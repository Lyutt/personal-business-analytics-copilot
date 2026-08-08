#!/usr/bin/env python3
"""Run deterministic synthetic acceptance checks for the Phase 1.5 asset contracts."""

from __future__ import annotations

import re
import math
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
    }
    assert all(
        required_metadata.issubset(item) for item in outputs
    ), "previous output metadata contract is incomplete"
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
    scenarios = scenario_map(suite)
    customer_scenarios = scenario_map(customer_suite)
    semantic_cases = semantic_case_map(suite)

    assert suite.get("contains_real_business_data") is False
    assert suite.get("external_side_effects_allowed") is False
    assert customer_suite.get("contains_real_business_data") is False
    assert customer_suite.get("external_side_effects_allowed") is False
    assert customer_suite.get("suite_id") == "CUSTOMER_REVENUE_DETAIL_ACCEPTANCE_V1"
    assert len(customer_scenarios) == 22
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
            },
        ),
        "field_mapping": (
            customer_field_mapping_gate["consistency_checks"],
            {"unmatched_notification_payload_is_grouped_and_local_only"},
        ),
        "result_contract": (
            customer_result_gate["gate_checks"],
            {
                "missing_field_status_renders_blank_in_output_mapping",
                "calculation_failed_and_pending_confirmation_output_prohibited",
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
        else "unavailable",
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
