from __future__ import annotations

import copy
import hashlib
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from weekly_business_runtime import (
    ConfiguredDisplayValueResolver,
    PreviewProductBinding,
    ResolvedWeeklyTemplate,
    TemplateAnchorOccurrence,
    WeeklyOutputAssembler,
)
from weekly_business_runtime.errors import Stage3AError
from weekly_business_runtime.models import (
    CtvResultContractInstance,
    PipelineExecutionResult,
    PipelineExecutionStatus,
    ResultFieldValue,
    ResultValueStatus,
    Stage3CResultContractInstance,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "WORKFLOW_RUN_STAGE3E_SYNTH"
PERIOD = "2026-W34"


def _policy():
    return yaml.safe_load(
        (
            ROOT
            / "phase1_5/assets/policies/POLICY_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE_DISPLAY_V1.yaml"
        ).read_text(encoding="utf-8")
    )


def _resolver(tmp_path, choices=("93%",)):
    remaining = list(choices)
    return ConfiguredDisplayValueResolver(
        repository_root=ROOT,
        database_path=tmp_path / "weekly.sqlite",
        choose=lambda candidates: remaining.pop(0),
        policy=_policy(),
    )


def test_configured_value_first_selection_and_same_period_reuse(tmp_path):
    resolver = _resolver(tmp_path, ("93%",))
    first = resolver.resolve(
        reporting_period_id=PERIOD,
        previous_reporting_period_id="2026-W33",
        selected_at="2026-08-22T20:00:00+08:00",
        workflow_run_id=RUN_ID,
    )
    second = resolver.resolve(
        reporting_period_id=PERIOD,
        previous_reporting_period_id="2026-W33",
        selected_at="2026-08-22T21:00:00+08:00",
        workflow_run_id="RERUN",
    )
    assert first == second == "93%"


def test_configured_value_previous_period_non_repeat(tmp_path):
    resolver = _resolver(tmp_path, ("94%",))
    resolver.persist_selected(
        reporting_period_id="2026-W33",
        configured_value="93%",
        selected_at="2026-08-15T20:00:00+08:00",
        workflow_run_id="PRIOR",
    )
    assert (
        resolver.resolve(
            reporting_period_id=PERIOD,
            previous_reporting_period_id="2026-W33",
            selected_at="2026-08-22T20:00:00+08:00",
            workflow_run_id=RUN_ID,
        )
        == "94%"
    )


def test_configured_value_conflicting_overwrite_rejected(tmp_path):
    resolver = _resolver(tmp_path)
    resolver.persist_selected(
        reporting_period_id=PERIOD,
        configured_value="93%",
        selected_at="2026-08-22T20:00:00+08:00",
        workflow_run_id=RUN_ID,
    )
    with pytest.raises(Stage3AError) as exc_info:
        resolver.persist_selected(
            reporting_period_id=PERIOD,
            configured_value="94%",
            selected_at="2026-08-22T20:01:00+08:00",
            workflow_run_id="CONFLICT",
        )
    assert exc_info.value.code == "STAGE3E_CONFIGURED_VALUE_CONFLICT"


def test_configured_state_is_only_its_registered_table(tmp_path):
    resolver = _resolver(tmp_path)
    resolver.resolve(
        reporting_period_id=PERIOD,
        previous_reporting_period_id=None,
        selected_at="2026-08-22T20:00:00+08:00",
        workflow_run_id=RUN_ID,
    )
    with sqlite3.connect(resolver.database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert tables == {"configured_display_values"}


def _mapping():
    return yaml.safe_load(
        (
            ROOT / "phase1_5/assets/output_mappings/OM_WEEKLY_BUSINESS_REPORT_V1.yaml"
        ).read_text(encoding="utf-8")
    )


def _registry():
    return yaml.safe_load(
        (ROOT / "phase1_5/assets/pipelines/pipeline_registry.yaml").read_text(
            encoding="utf-8"
        )
    )


def _field_ids_by_contract():
    result = {}
    for section in _mapping()["section_order"]:
        for entry in section.get("mapping_entries", ()):
            for output in entry.get("output_fields", ()):
                binding = output["result_field_binding"]
                result.setdefault(binding["result_contract_id"], set()).add(
                    binding["result_field_id"]
                )
    return result


def _unit(field_id):
    if "revenue" in field_id and not any(key in field_id for key in ("wow", "yoy")):
        return "yuan"
    if field_id.endswith("_pp"):
        return "percentage_point"
    if any(key in field_id for key in ("wow", "yoy", "rate")):
        return "ratio"
    return "inventory_unit"


def _fields(contract_id):
    return tuple(
        ResultFieldValue(
            field_id,
            f"MV_{field_id.upper()}",
            Decimal("100000000") if _unit(field_id) == "yuan" else Decimal("1"),
            ResultValueStatus.VALID_VALUE,
            _unit(field_id),
            ("fixture://field",),
        )
        for field_id in sorted(_field_ids_by_contract().get(contract_id, ()))
    )


def _contract(contract_id, pipeline_run_id, product="not_applicable"):
    if contract_id.startswith("RC_REVENUE_"):
        return CtvResultContractInstance(
            contract_id,
            "1.0.0",
            f"{pipeline_run_id}:{contract_id}",
            RUN_ID,
            pipeline_run_id,
            "2026-08-17..2026-08-23",
            "2026-08-20",
            "2026-08-20",
            "regular_week",
            (),
            {},
            {},
            {},
            "2026-08-22T20:00:00+08:00",
            "passed",
            "approved",
            _fields(contract_id),
        )
    context = {}
    records = ()
    if contract_id == "RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS":
        context = {
            "target_ad_product_name": product,
            "analysis_scenario": "positive_sell_through_change",
            "trigger_sell_through_wow_change_pp": Decimal("12"),
        }
        records = (
            {
                "customer_name": "Synthetic Customer",
                "current_period_impression_count": 2000000,
                "impression_change_count": 1500000,
                "customer_rank": 1,
            },
        )
    return Stage3CResultContractInstance(
        contract_id,
        "1.0.0",
        f"{pipeline_run_id}:{contract_id}",
        RUN_ID,
        pipeline_run_id,
        "2026-08-17..2026-08-23",
        "CTX_SYNTH",
        (),
        {},
        {},
        {},
        "2026-08-22T20:00:00+08:00",
        "passed",
        "approved",
        _fields(contract_id),
        records,
        product,
        "2026-08-20",
        context,
        "2026-08-20",
        "regular_week",
    )


def _summary(include_customer=True):
    by_contract = {}
    for pipeline in _registry()["pipelines"]:
        for contract_id in pipeline.get("outputs", {}).get("result_contract_ids", ()):
            by_contract[contract_id] = pipeline["pipeline_id"]
    products = {
        "RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY": ("Product A", "Brand Moment"),
        "RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY": ("Patch Product", "Product A"),
        "RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS": ("Product A",),
    }
    results = []
    for contract_id in set(_field_ids_by_contract()) | {
        "RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"
    }:
        if (
            contract_id == "RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"
            and not include_customer
        ):
            continue
        for product in products.get(contract_id, ("not_applicable",)):
            pipeline_id = by_contract[contract_id]
            run_id = f"PIPELINE:{pipeline_id}:{product}"
            contract = _contract(contract_id, run_id, product)
            results.append(
                PipelineExecutionResult(
                    RUN_ID,
                    pipeline_id,
                    run_id,
                    {"product": product},
                    (),
                    PipelineExecutionStatus.COMPLETED,
                    produced_result_contract_reference=f"result-contract://{contract.result_id}",
                    lineage_references=("fixture://lineage",),
                    result_contract=contract,
                )
            )
    return {
        "workflow_run_id": RUN_ID,
        "pipeline_run_results": tuple(results),
        "warnings": (),
        "normal_omissions": ()
        if include_customer
        else (
            {
                "pipeline_id": "PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS",
                "product": "Product A",
                "reason": "trigger_not_met",
            },
        ),
        "result_contract_references": tuple(
            x.produced_result_contract_reference for x in results
        ),
        "lineage": ("fixture://lineage",),
    }


def _bindings():
    return (
        PreviewProductBinding(
            "Patch Product", "patch", "patch_and_similar_resource_commentary"
        ),
        PreviewProductBinding("Product A", "non_patch", "page_resource_commentary"),
        PreviewProductBinding(
            "Brand Moment", "brand_moment", "page_resource_commentary"
        ),
    )


def _template(
    mode="regular_week",
    duplicate=False,
    missing=False,
    duplicate_placeholder=False,
    anchor_without_placeholder=False,
    bad_fingerprint=False,
):
    body = "HEADER\nANCHOR_PATCH:<PATCH_COMMENT>\nANCHOR_PAGE:<PAGE_COMMENT>"
    if duplicate_placeholder:
        body += "\n<PATCH_COMMENT>"
    occurrences = (
        TemplateAnchorOccurrence(
            "patch_and_similar_resource_commentary",
            "ANCHOR_PATCH"
            if anchor_without_placeholder
            else "ANCHOR_PATCH:<PATCH_COMMENT>",
            "<PATCH_COMMENT>",
        ),
        TemplateAnchorOccurrence(
            "page_resource_commentary",
            "ANCHOR_PAGE:<PAGE_COMMENT>",
            "<PAGE_COMMENT>",
        ),
    )
    if duplicate:
        occurrences += (occurrences[0],)
    if missing:
        occurrences = occurrences[1:]
    asset = (
        "TEMPLATE_WEEKLY_REPORT_0724_LOCAL_ONLY"
        if mode == "regular_week"
        else "TEMPLATE_WEEKLY_REPORT_QUARTER_TRANSITION_REVENUE_LOCAL_ONLY"
    )
    fingerprint = hashlib.sha256(body.encode()).hexdigest()
    return ResolvedWeeklyTemplate(
        asset, "1.0.0", "bad" if bad_fingerprint else fingerprint, body, occurrences
    )


def _assemble(mode="regular_week", summary=None, template=None, configured_value="93%"):
    return WeeklyOutputAssembler(repository_root=ROOT).assemble(
        context={"workflow_run_id": RUN_ID, "report_mode": mode},
        execution_summary=summary or _summary(),
        configured_display_value=configured_value,
        template=template or _template(mode),
        product_bindings=_bindings(),
    )


def test_integrated_synthetic_regular_preview_and_fixed_narrative(tmp_path):
    resolver = _resolver(tmp_path, ("93%",))
    first = resolver.resolve(
        reporting_period_id=PERIOD,
        previous_reporting_period_id="2026-W33",
        selected_at="2026-08-22T20:00:00+08:00",
        workflow_run_id=RUN_ID,
    )
    reused = resolver.resolve(
        reporting_period_id=PERIOD,
        previous_reporting_period_id="2026-W33",
        selected_at="2026-08-22T20:01:00+08:00",
        workflow_run_id=RUN_ID,
    )
    preview = _assemble(configured_value=reused)
    assert first == reused == "93%"
    assert preview["completion_status"] == "complete_draft"
    assert preview["review_only"] is True
    assert preview["outlook_draft_created"] is False
    assert "Synthetic Customer" in preview["rendered_body"]
    assert "93%" in preview["rendered_body"]
    assert [section["section_id"] for section in preview["sections"]] == [
        "INVENTORY_AND_SELL_THROUGH",
        "REVENUE",
    ]


def test_quarter_transition_hides_technical_weekly_fields_and_fails_on_authority_gap():
    assembler = WeeklyOutputAssembler(repository_root=ROOT)
    for field_id in (
        "weekly_incremental_executed_revenue",
        "weekly_incremental_executed_revenue_wow",
        "weekly_incremental_executed_revenue_yoy",
    ):
        assert assembler._hidden_quarter_field(
            "quarter_transition_week", "SLOT_REVENUE_TECHNICAL", field_id
        )
    for field_id in (
        "qtd_performance_revenue",
        "qtd_performance_revenue_yoy",
        "qtd_executed_revenue",
    ):
        assert not assembler._hidden_quarter_field(
            "quarter_transition_week", "SLOT_REVENUE_TECHNICAL", field_id
        )
    for slot in ("SLOT_REVENUE_SMART_SPEAKER", "SLOT_REVENUE_FAST_VERSION"):
        assert assembler._hidden_quarter_field(
            "quarter_transition_week", slot, "weekly_executed_revenue"
        )
        assert assembler._hidden_quarter_field(
            "quarter_transition_week", slot, "weekly_executed_revenue_wow"
        )
        assert not assembler._hidden_quarter_field(
            "quarter_transition_week", slot, "qtd_executed_revenue"
        )
    with pytest.raises(Stage3AError) as exc_info:
        _assemble("quarter_transition_week")
    assert exc_info.value.code == "STAGE3E_QUARTER_TRANSITION_AUTHORITY_GAP"


def test_product_rows_follow_explicit_bindings():
    preview = _assemble()
    inventory = preview["sections"][0]
    products = {(row["output_slot_id"], row["product"]) for row in inventory["rows"]}
    assert ("SLOT_INVENTORY_PATCH", "Patch Product") in products
    assert ("SLOT_INVENTORY_NON_PATCH_PRODUCTS", "Product A") in products
    assert ("SLOT_INVENTORY_BRAND_MOMENT", "Brand Moment") in products


def test_result_contract_must_be_exact_current_workflow_run():
    summary = _summary()
    results = list(summary["pipeline_run_results"])
    results[0] = replace(
        results[0],
        result_contract=replace(
            results[0].result_contract, workflow_run_id="OTHER_RUN"
        ),
    )
    summary["pipeline_run_results"] = tuple(results)
    with pytest.raises(Stage3AError) as exc_info:
        _assemble(summary=summary)
    assert exc_info.value.code == "STAGE3E_RESULT_CONTRACT_NOT_CONSUMABLE"


def test_customer_normal_omission_does_not_render_placeholder():
    preview = _assemble(summary=_summary(include_customer=False))
    assert "Synthetic Customer" not in preview["rendered_body"]
    assert preview["normal_omissions"][0]["reason"] == "trigger_not_met"


def test_required_blocked_result_produces_partial_draft_warning():
    summary = _summary()
    results = list(summary["pipeline_run_results"])
    target = next(
        i
        for i, item in enumerate(results)
        if item.pipeline_id == "PL_REVENUE_TECHNICAL_WEEKLY"
    )
    results[target] = replace(
        results[target],
        execution_status=PipelineExecutionStatus.BLOCKED,
        result_contract=None,
        error_code="SYNTH_BLOCKED",
        error_message="synthetic failure",
    )
    summary["pipeline_run_results"] = tuple(results)
    preview = _assemble(summary=summary)
    assert preview["completion_status"] == "partial_draft"
    assert "数据质量提醒" in preview["rendered_body"]
    assert preview["warnings"][-1]["fallback_used"] == "revenue_section_failure"


def test_required_blocked_without_approved_fallback_blocks_preview():
    summary = _summary()
    results = list(summary["pipeline_run_results"])
    target = next(
        i
        for i, item in enumerate(results)
        if item.pipeline_id == "PL_REVENUE_TECHNICAL_WEEKLY"
    )
    results[target] = replace(
        results[target],
        pipeline_id="PL_SYNTHETIC_REQUIRED_NO_FALLBACK",
        execution_status=PipelineExecutionStatus.BLOCKED,
        result_contract=None,
        error_code="SYNTH_BLOCKED",
        error_message="synthetic failure",
    )
    summary["pipeline_run_results"] = tuple(results)
    preview = _assemble(summary=summary)
    assert preview["completion_status"] == "blocked"
    assert "fallback_used" not in preview["warnings"][-1]


def test_zero_growth_and_sell_through_change_display_as_flat():
    assert (
        WeeklyOutputAssembler._format(
            Decimal("0.004"), "ratio", "weekly_executed_revenue_wow"
        )
        == "持平"
    )
    assert (
        WeeklyOutputAssembler._format(
            Decimal("-0.004"), "ratio", "qtd_performance_revenue_yoy"
        )
        == "持平"
    )
    assert (
        WeeklyOutputAssembler._format(
            Decimal("0.4"),
            "percentage_point",
            "product_brand_sell_through_wow_change_pp",
        )
        == "持平"
    )
    assert (
        WeeklyOutputAssembler._format(
            Decimal("0"), "ratio", "product_brand_sell_through_rate"
        )
        == "0%"
    )


def test_template_identity_or_exact_once_failure_is_closed():
    for template in (
        _template(duplicate=True),
        _template(missing=True),
        _template(duplicate_placeholder=True),
        _template(anchor_without_placeholder=True),
        _template(bad_fingerprint=True),
    ):
        with pytest.raises(Stage3AError):
            _assemble(template=template)
