from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from weekly_acquisition_runtime.contracts import BusinessKey
from weekly_business_runtime.models import (
    PipelineExecutionStatus,
    ResultFieldValue,
    ResultValueStatus,
    Stage3CResultContractInstance,
)
from weekly_business_runtime.stage3c import (
    FULL_SITE,
    NON_PATCH,
    PATCH,
    BrandMomentDeliveryExecutor,
    BrandMomentSellThroughExecutor,
    CustomerChangeAnalysisExecutor,
    InventoryPipelineExecutor,
    LocalRuleBindings,
    PlatformDauExecutor,
    ProductSellThroughExecutor,
    _validate_stage3c_contract,
)
from weekly_business_runtime.store import InMemoryMetricStore


@dataclass(frozen=True)
class _Entry:
    local_input_reference: str


class _Manifest:
    def __init__(self, entries): self.entries = entries
    def get_entry(self, key): return self.entries[key]


class _Runtime:
    def __init__(self, inputs): self.inputs = inputs
    def consume_bound_input(self, _run, key): return self.inputs[key]


def _run(entries):
    values = {
        "workflow_run_id": "RUN_3C", "workflow_reporting_date": "2026-08-21",
        "reporting_period_start_date": "2026-08-17", "reporting_period_end_date": "2026-08-23",
        "current_period_start_date": "2026-08-17", "current_period_end_date": "2026-08-23",
        "comparison_period_start_date": "2026-08-10", "comparison_period_end_date": "2026-08-16",
    }
    return SimpleNamespace(context=SimpleNamespace(workflow_run_id="RUN_3C", values=values), run_input_manifest=_Manifest(entries))


def _key(dataset, role="current", product="not_applicable"):
    return BusinessKey("RUN_3C", dataset, role, product)


def _input(tmp_path: Path, name: str, rows):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


def _rules():
    return LocalRuleBindings(
        versions={
            "MAP_COMMERCIAL_SELLABILITY_INDEX_LOCAL_ONLY": "1.0.0",
            "SPECIAL_PLACEMENT_EXCEPTION_LOCAL_ONLY": "1.0.0",
            "FILTER_PATCH_TIME_SLICE_EXCLUSION_LOCAL_ONLY": "1.0.0",
            "RULESET_NON_PATCH_PRODUCT_INVENTORY_LOCAL_ONLY": "1.0.0",
            "BR_APOLLO_PRODUCT_FILTER_MAPPING": "1.0.0",
        },
        commercial_sellability_by_placement={"full": True},
        special_placement_ids=frozenset(), excluded_patch_time_slot_ids=frozenset({"drop"}),
        product_by_placement_id={"nonpatch": "Product A"},
        product_route_by_name={"Product A": "non_patch", "Patch Product": "patch", "Brand Moment": "brand_moment"},
        customer_analysis_by_product={"Product A": {"output_limit": 2, "trigger_threshold_pp": "10"}, "Patch Product": {"output_limit": 1}, "Brand Moment": {"output_limit": 1}},
    )


@pytest.mark.parametrize("profile,row,product", [
    (FULL_SITE, {"广告位ID": "full", "是否VIP": "vip", "库存": "10", "禁用库存": "2", "品牌商广整体投放库存": "3", "效果投放库存": "4"}, "not_applicable"),
    (PATCH, {"时段片ID": "keep", "库存": "10", "禁用库存": "2", "品牌投放库存": "3", "效果投放库存": "4"}, "not_applicable"),
    (NON_PATCH, {"广告位ID": "nonpatch", "总库存": "10", "禁用库存": "2", "品牌投放库存": "3", "效果投放库存": "4"}, "Product A"),
])
def test_inventory_executors_are_manifest_bound_and_persist(tmp_path, profile, row, product):
    key = _key(profile.dataset_id, product=product)
    path = _input(tmp_path, profile.kind, [row])
    run = _run({key: _Entry(f"fixture://{profile.kind}")})
    result = InventoryPipelineExecutor(profile, _Runtime({key: path}), InMemoryMetricStore()).execute(
        run=run, pipeline_run_id=f"RUN_{profile.kind}", input_key=key,
        generated_at="2026-08-22T00:00:00+08:00", rules=_rules(),
    )
    assert result.execution_status is PipelineExecutionStatus.COMPLETED
    assert result.result_contract.field("available_inventory_count").value == Decimal("8")


def test_delivery_dau_and_downstream_contract_bindings(tmp_path):
    delivery_key = _key("DS_ADVERTISING_APOLLO_BRAND_MOMENT_DELIVERY_EXECUTION")
    dau_key = _key("DS_NOVABI_PLATFORM_DAU")
    delivery_path = _input(tmp_path, "delivery", [{"曝光量": "10"}])
    dau_path = _input(tmp_path, "dau", [{"日期": f"2026-08-{day:02d}", "全平台日活跃用户数": "70"} for day in range(17, 24)])
    run = _run({delivery_key: _Entry("fixture://delivery"), dau_key: _Entry("fixture://dau")})
    runtime = _Runtime({delivery_key: delivery_path, dau_key: dau_path})
    store = InMemoryMetricStore()
    delivery = BrandMomentDeliveryExecutor(runtime, store).execute(run=run, pipeline_run_id="DELIVERY", input_key=delivery_key, generated_at="2026-08-22T00:00:00+08:00")
    dau = PlatformDauExecutor(runtime, store).execute(run=run, pipeline_run_id="DAU", input_key=dau_key, generated_at="2026-08-22T00:00:00+08:00")
    inventory = Stage3CResultContractInstance("RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", "1.0.0", "INV", "RUN_3C", "INV", "2026-08-17..2026-08-23", "CTX_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", (), {}, {}, {}, "now", "passed", "approved", (ResultFieldValue("brand_moment_available_inventory_count", "MV_INVENTORY_BRAND_MOMENT_AVAILABLE_VOLUME_V1", Decimal("5"), ResultValueStatus.VALID_VALUE, "inventory_count", ()),), product_parameter="Brand Moment")
    brand = BrandMomentSellThroughExecutor(store).execute(run=run, pipeline_run_id="BRAND", delivery=delivery.result_contract, inventory=inventory, generated_at="2026-08-22T00:00:00+08:00")
    assert delivery.execution_status is PipelineExecutionStatus.COMPLETED
    assert dau.execution_status is PipelineExecutionStatus.COMPLETED
    assert brand.execution_status is PipelineExecutionStatus.COMPLETED
    assert brand.result_contract.field("sell_through_rate").value == Decimal("2")


def test_product_and_customer_paths_fail_closed_or_return_normal_omission(tmp_path):
    run = _run({})
    store = InMemoryMetricStore()
    upstream = Stage3CResultContractInstance("RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", "1.0.0", "UP", "RUN_3C", "UP", "2026-08-17..2026-08-23", "CTX_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", (), {}, {}, {}, "now", "passed", "approved", (ResultFieldValue("brand_delivery_inventory_count", "MV_INVENTORY_NON_PATCH_PRODUCT_BRAND_DELIVERY_VOLUME_V1", Decimal("3"), ResultValueStatus.VALID_VALUE, "inventory_count", ()), ResultFieldValue("available_inventory_count", "MV_INVENTORY_NON_PATCH_PRODUCT_AVAILABLE_VOLUME_V1", Decimal("6"), ResultValueStatus.VALID_VALUE, "inventory_count", ())), product_parameter="Product A")
    product = ProductSellThroughExecutor(store).execute(run=run, pipeline_run_id="PRODUCT", product="Product A", rules=_rules(), upstream=upstream, generated_at="2026-08-22T00:00:00+08:00")
    assert product.execution_status is PipelineExecutionStatus.COMPLETED
    assert product.result_contract.field("non_patch_product_brand_sell_through_rate").value == Decimal("0.5")
    omitted = CustomerChangeAnalysisExecutor(_Runtime({})).execute(run=run, pipeline_run_id="CUSTOMER", product="Product A", rules=_rules(), trigger_contract=product.result_contract, current_key=None, comparison_key=None, generated_at="2026-08-22T00:00:00+08:00")
    assert omitted.execution_status is PipelineExecutionStatus.COMPLETED
    assert omitted.produced_result_contract_reference == "normal-omission://trigger-not-met"


def test_customer_triggered_path_uses_exact_two_period_product_inputs(tmp_path):
    current_key = _key("DS_AD_PRODUCT_CUSTOMER_DELIVERY_CHANGE_ANALYSIS", "current", "Product A")
    comparison_key = _key("DS_AD_PRODUCT_CUSTOMER_DELIVERY_CHANGE_ANALYSIS", "comparison", "Product A")
    current = _input(tmp_path, "customer-current", [{"客户ID": "c1", "客户名": "A", "曝光量": "1200000"}, {"客户ID": "c2", "客户名": "B", "曝光量": "-1"}])
    comparison = _input(tmp_path, "customer-comparison", [{"客户ID": "c1", "客户名": "A", "曝光量": "200000"}, {"客户ID": "c3", "客户名": "C", "曝光量": "300000"}])
    run = _run({current_key: _Entry("fixture://customer-current"), comparison_key: _Entry("fixture://customer-comparison")})
    trigger = Stage3CResultContractInstance("RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY", "1.0.0", "TRIGGER", "RUN_3C", "TRIGGER", "2026-08-17..2026-08-23", "CTX_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY", (), {}, {}, {}, "now", "passed", "approved", (ResultFieldValue("non_patch_product_brand_sell_through_wow_change_pp", "MV_INVENTORY_NON_PATCH_PRODUCT_BRAND_SELL_THROUGH_WOW_CHANGE_V1", Decimal("12"), ResultValueStatus.VALID_VALUE, "percentage_point", ()),), product_parameter="Product A")
    result = CustomerChangeAnalysisExecutor(_Runtime({current_key: current, comparison_key: comparison})).execute(run=run, pipeline_run_id="CUSTOMER_TRIGGERED", product="Product A", rules=_rules(), trigger_contract=trigger, current_key=current_key, comparison_key=comparison_key, generated_at="2026-08-22T00:00:00+08:00")
    assert result.execution_status is PipelineExecutionStatus.COMPLETED_WITH_WARNING
    assert {warning.code for warning in result.warnings} == {"CUSTOMER_NEGATIVE_IMPRESSION_EXCLUDED"}
    assert result.result_contract.record_set == ({"customer_id": "c1", "customer_name": "A", "current_period_impression_count": Decimal("1200000"), "comparison_period_impression_count": Decimal("200000"), "impression_change_count": Decimal("1000000"), "ranking_measure": Decimal("1200000"), "customer_rank": 1},)
    assert result.result_contract.context_values["applied_output_limit"] == 2


def test_product_executor_rejects_ambiguous_route(tmp_path):
    result = ProductSellThroughExecutor(InMemoryMetricStore()).execute(run=_run({}), pipeline_run_id="BAD", product="Unknown", rules=_rules(), upstream=None, generated_at="2026-08-22T00:00:00+08:00")
    assert result.execution_status is PipelineExecutionStatus.BLOCKED
    assert result.error_code == "STAGE3C_PRODUCT_ROUTE_AMBIGUOUS"


def test_stage3c_contract_validator_rejects_unit_version_and_required_field_errors():
    field = ResultFieldValue("impression_count", "MV_ADVERTISING_BRAND_MOMENT_WEEKLY_IMPRESSION_COUNT_V1", Decimal("1"), ResultValueStatus.VALID_VALUE, "impression", ("fixture://input",))
    base = Stage3CResultContractInstance("RC_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY", "1.0.0", "RESULT", "RUN", "PIPE", "2026-08-17..2026-08-23", "CTX_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY", ("fixture://input",), {"MAP": "1.0.0"}, {}, {field.metric_variant_id: "1.0.0-draft"}, "now", "passed", "approved", (field,), cutoff_date="2026-08-21", report_mode="regular_week")
    _validate_stage3c_contract(base)
    with pytest.raises(Exception):
        _validate_stage3c_contract(replace(base, fields=(replace(field, unit="impression_count"),)))
    with pytest.raises(Exception):
        _validate_stage3c_contract(replace(base, metric_variant_versions={field.metric_variant_id: "1.0.0"}))
    with pytest.raises(Exception):
        _validate_stage3c_contract(replace(base, fields=()))


def test_full_site_uses_complete_rows_except_available_and_mapping_fallback(tmp_path):
    key = _key(FULL_SITE.dataset_id)
    rows = [{"广告位ID": "off", "是否VIP": "vip", "库存": "10", "禁用库存": "1", "品牌商广整体投放库存": "2", "效果投放库存": "3"}, {"广告位ID": "missing", "是否VIP": "vip", "库存": "20", "禁用库存": "2", "品牌商广整体投放库存": "4", "效果投放库存": "5"}, {"广告位ID": "special", "是否VIP": "vip", "库存": "30", "禁用库存": "3", "品牌商广整体投放库存": "6", "效果投放库存": "7"}, {"广告位ID": "special", "是否VIP": "非vip", "库存": "40", "禁用库存": "4", "品牌商广整体投放库存": "8", "效果投放库存": "9"}]
    path = _input(tmp_path, "full-scope", rows)
    rules = _rules()
    rules = LocalRuleBindings(versions=rules.versions, commercial_sellability_by_placement={"off": False}, special_placement_ids=frozenset({"special"}), product_by_placement_id=rules.product_by_placement_id, product_route_by_name=rules.product_route_by_name, customer_analysis_by_product=rules.customer_analysis_by_product)
    result = InventoryPipelineExecutor(FULL_SITE, _Runtime({key: path}), InMemoryMetricStore()).execute(run=_run({key: _Entry("fixture://full")}), pipeline_run_id="FULL", input_key=key, generated_at="2026-08-22T00:00:00+08:00", rules=rules)
    assert result.execution_status is PipelineExecutionStatus.COMPLETED
    assert result.result_contract.field("gross_inventory_count").value == Decimal("100")
    assert result.result_contract.field("available_inventory_count").value == Decimal("54")


def test_customer_negative_selection_excludes_duplicates_conflicts_and_applies_limit():
    records = CustomerChangeAnalysisExecutor._compare(
        [{"客户ID": "a", "客户名": "A", "曝光量": "0"}, {"客户ID": "dup", "客户名": "D", "曝光量": "0"}, {"客户ID": "dup", "客户名": "D", "曝光量": "0"}, {"客户ID": "conflict", "客户名": "Now", "曝光量": "0"}],
        [{"客户ID": "a", "客户名": "A", "曝光量": "3000000"}, {"客户ID": "dup", "客户名": "D", "曝光量": "2000000"}, {"客户ID": "conflict", "客户名": "Before", "曝光量": "2000000"}, {"客户ID": "small", "客户名": "S", "曝光量": "999999"}],
        "negative_sell_through_change", 1,
    )
    assert records == [{"customer_id": "a", "customer_name": "A", "current_period_impression_count": Decimal("0"), "comparison_period_impression_count": Decimal("3000000"), "impression_change_count": Decimal("-3000000"), "ranking_measure": Decimal("3000000"), "customer_rank": 1}]


def test_dau_composite_contract_contains_daily_records_and_blocks_invalid_coverage(tmp_path):
    key = _key("DS_NOVABI_PLATFORM_DAU")
    valid = _input(tmp_path, "dau-valid", [{"日期": f"2026-08-{day:02d}", "全平台日活跃用户数": "70"} for day in range(17, 24)])
    run = _run({key: _Entry("fixture://dau")})
    result = PlatformDauExecutor(_Runtime({key: valid}), InMemoryMetricStore()).execute(run=run, pipeline_run_id="DAU_FULL", input_key=key, generated_at="2026-08-22T00:00:00+08:00")
    assert len(result.result_contract.record_set) == 7
    assert result.result_contract.record_set[0]["platform_scope"] == "full_platform"
    assert result.result_contract.field("weekly_average_dau").unit == "user"
    invalid = _input(tmp_path, "dau-invalid", [{"日期": "2026-08-17", "全平台日活跃用户数": "0"}] * 7)
    blocked = PlatformDauExecutor(_Runtime({key: invalid}), InMemoryMetricStore()).execute(run=run, pipeline_run_id="DAU_BAD", input_key=key, generated_at="2026-08-22T00:00:00+08:00")
    assert blocked.execution_status is PipelineExecutionStatus.COMPLETED_WITH_WARNING
    assert blocked.result_contract is None
    assert blocked.warnings[0].code == "STAGE3C_DAU_CONTENT_OMITTED"
