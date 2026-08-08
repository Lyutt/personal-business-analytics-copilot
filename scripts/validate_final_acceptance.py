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
RUNTIME = ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1.yaml"
DCP = ROOT / "phase1_5/assets/analysis/dcp_registry_v1.yaml"
STORE = ROOT / "phase1_5/assets/metric_stores/metric_result_store_registry.yaml"


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
    for profile in registry.get("capability_profiles", []):
        metadata = profile.get("metadata", {})
        if (
            metadata.get("domain") == request.get("domain")
            and request.get("intent") in metadata.get("intents", [])
            and (
                request.get("capability_scope_id") is None
                or metadata.get("capability_scope_id") == request.get("capability_scope_id")
            )
            and metadata.get("dimensions") == request.get("dimensions")
            and metadata.get("metrics") == request.get("metrics")
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
    arc = dict(matches[0]["arc_metadata"])
    arc["request_id"] = request_id
    period = re.search(r"(\d{4}-\d{2}-\d{2})至(\d{4}-\d{2}-\d{2})", brief)
    assert period, "Brief period must be directly parseable"
    arc["period"] = {"start_date": period.group(1), "end_date": period.group(2)}
    arc["comparison"] = {"mode": "none"}
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
    runtime = load(RUNTIME)
    dcp = load(DCP)
    store = load(STORE)
    scenarios = scenario_map(suite)

    assert suite.get("contains_real_business_data") is False
    assert suite.get("external_side_effects_allowed") is False

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

    print(f"Phase 1.5 final acceptance passed: {len(scenarios)} synthetic scenarios; no real business data or external side effects.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
