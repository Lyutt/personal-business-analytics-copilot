from __future__ import annotations

import json
from dataclasses import dataclass
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
        "expected_previous_revenue_workflow_reporting_date": "2026-08-14",
        "reporting_period_start_date": "2026-08-17", "reporting_period_end_date": "2026-08-23",
        "current_period_start_date": "2026-08-17", "current_period_end_date": "2026-08-23",
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
    current = _input(tmp_path, "customer-current", [{"客户ID": "c1", "客户名": "A", "曝光量": "12"}, {"客户ID": "c2", "客户名": "B", "曝光量": "-1"}])
    comparison = _input(tmp_path, "customer-comparison", [{"客户ID": "c1", "客户名": "A", "曝光量": "2"}, {"客户ID": "c3", "客户名": "C", "曝光量": "3"}])
    run = _run({current_key: _Entry("fixture://customer-current"), comparison_key: _Entry("fixture://customer-comparison")})
    trigger = Stage3CResultContractInstance("RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY", "1.0.0", "TRIGGER", "RUN_3C", "TRIGGER", "2026-08-17..2026-08-23", "CTX_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY", (), {}, {}, {}, "now", "passed", "approved", (ResultFieldValue("non_patch_product_brand_sell_through_wow_change_pp", "MV_INVENTORY_NON_PATCH_PRODUCT_BRAND_SELL_THROUGH_WOW_CHANGE_V1", Decimal("12"), ResultValueStatus.VALID_VALUE, "percentage_point", ()),), product_parameter="Product A")
    result = CustomerChangeAnalysisExecutor(_Runtime({current_key: current, comparison_key: comparison})).execute(run=run, pipeline_run_id="CUSTOMER_TRIGGERED", product="Product A", rules=_rules(), trigger_contract=trigger, current_key=current_key, comparison_key=comparison_key, generated_at="2026-08-22T00:00:00+08:00")
    assert result.execution_status is PipelineExecutionStatus.COMPLETED
    assert result.result_contract.record_set == ({"customer_id": "c1", "customer_name": "A", "current_period_impression_count": Decimal("12"), "impression_change_count": Decimal("10")}, {"customer_id": "c3", "customer_name": "C", "current_period_impression_count": Decimal("0"), "impression_change_count": Decimal("-3")})


def test_product_executor_rejects_ambiguous_route(tmp_path):
    result = ProductSellThroughExecutor(InMemoryMetricStore()).execute(run=_run({}), pipeline_run_id="BAD", product="Unknown", rules=_rules(), upstream=None, generated_at="2026-08-22T00:00:00+08:00")
    assert result.execution_status is PipelineExecutionStatus.BLOCKED
    assert result.error_code == "STAGE3C_PRODUCT_ROUTE_AMBIGUOUS"
