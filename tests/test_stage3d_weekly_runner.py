from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from weekly_acquisition_runtime.contracts import BusinessKey
from weekly_business_runtime.errors import Stage3AError
from weekly_business_runtime.models import (
    CtvResultContractInstance,
    ExecutionWarning,
    PipelineExecutionResult,
    PipelineExecutionStatus,
    ResultFieldValue,
    ResultValueStatus,
    Stage3CResultContractInstance,
)
from weekly_business_runtime.stage3c import (
    FULL_SITE,
    InventoryPipelineExecutor,
    LocalRuleBindings,
)
from weekly_business_runtime.store import InMemoryMetricStore, MetricStoreRecord
from weekly_business_runtime.weekly_runner import WeeklyWorkflowRunner

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "phase1_5/assets/pipelines/pipeline_registry.yaml"
RUN_ID = "RUN_STAGE3D_SYNTHETIC"


@dataclass(frozen=True)
class _Entry:
    local_input_reference: str


class _Manifest:
    def __init__(self, keys: tuple[BusinessKey, ...], references=None):
        references = references or {}
        self.entries = {
            key: _Entry(str(references.get(key, f"fixture://{key.dataset_id}/{key.product_parameter}")))
            for key in keys
        }

    def get_entry(self, key):
        return self.entries[key]

    def finalize(self):
        return {
            "manifest_id": "RUN_INPUT_MANIFEST_WF_WEEKLY_BUSINESS_REPORT_V1",
            "workflow_run_id": RUN_ID,
            "entries": [
                {
                    "workflow_run_id": key.workflow_run_id,
                    "dataset_id": key.dataset_id,
                    "period_role": key.period_role,
                    "product_parameter": key.product_parameter,
                }
                for key in self.entries
            ],
        }


class _Runtime:
    def __init__(self, inputs=None):
        self.inputs = inputs or {}

    def consume_bound_input(self, _run, key):
        return self.inputs[key]


def _registry():
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def _rules(trigger_threshold="10"):
    return LocalRuleBindings(
        versions={
            "MAP_COMMERCIAL_SELLABILITY_INDEX_LOCAL_ONLY": "1.0.0",
            "SPECIAL_PLACEMENT_EXCEPTION_LOCAL_ONLY": "1.0.0",
            "FILTER_PATCH_TIME_SLICE_EXCLUSION_LOCAL_ONLY": "1.0.0",
            "RULESET_NON_PATCH_PRODUCT_INVENTORY_LOCAL_ONLY": "1.0.0",
            "BR_APOLLO_PRODUCT_FILTER_MAPPING": "1.0.0",
        },
        commercial_sellability_by_placement={"full": True},
        product_by_placement_id={"product": "Product A", "brand": "Brand Moment"},
        product_route_by_name={
            "Product A": "non_patch",
            "Patch Product": "patch",
            "Brand Moment": "brand_moment",
        },
        customer_analysis_by_product={
            "Product A": {"output_limit": 2, "trigger_threshold_pp": trigger_threshold}
        },
    )


def _context():
    values = {
        "workflow_run_id": RUN_ID,
        "run_type": "manual",
        "workflow_execution_date": "2026-08-22",
        "workflow_reporting_date": "2026-08-20",
        "reporting_period_id": "2026-W34",
        "reporting_period_start_date": "2026-08-17",
        "reporting_period_end_date": "2026-08-23",
        "current_period_start_date": "2026-08-17",
        "current_period_end_date": "2026-08-23",
        "comparison_period_start_date": "2026-08-10",
        "comparison_period_end_date": "2026-08-16",
        "cutoff_date": "2026-08-20",
        "timezone": "Asia/Shanghai",
        "current_revenue_cutoff_date": "2026-08-19",
        "expected_previous_revenue_workflow_reporting_date": "2026-08-13",
        "target_report_period": "2026-W34",
        "workflow_year": 2026,
        "target_fiscal_quarter": "2026Q3",
        "target_previous_calendar_quarter": "2026Q2",
        "report_mode": "regular_week",
        "target_revenue_cutoff_date": "2026-08-19",
    }
    return SimpleNamespace(workflow_run_id=RUN_ID, values=values)


def _manifest_keys(registry, *, include_dau=True):
    keys = []
    by_id = {item["pipeline_id"]: item for item in registry["pipelines"]}
    direct = (
        "PL_REVENUE_TECHNICAL_WEEKLY",
        "PL_REVENUE_CTV_WEEKLY",
        "PL_REVENUE_SMART_SPEAKER_WEEKLY",
        "PL_REVENUE_FAST_VERSION_WEEKLY",
        "PL_INVENTORY_FULL_SITE_WEEKLY",
        "PL_INVENTORY_PATCH_WEEKLY",
        "PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY",
    )
    for pipeline_id in direct:
        dataset = by_id[pipeline_id]["dataset_dependencies"][0]["dataset_id"]
        keys.append(BusinessKey(RUN_ID, dataset, "current", "not_applicable"))
    non_patch_dataset = by_id["PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY"][
        "dataset_dependencies"
    ][0]["dataset_id"]
    keys.extend(
        (
            BusinessKey(RUN_ID, non_patch_dataset, "current", "Product A"),
            BusinessKey(RUN_ID, non_patch_dataset, "current", "Brand Moment"),
        )
    )
    if include_dau:
        dau_dataset = by_id["PL_USER_ANALYTICS_PLATFORM_DAU_WEEKLY"][
            "dataset_dependencies"
        ][0]["dataset_id"]
        keys.append(BusinessKey(RUN_ID, dau_dataset, "current", "not_applicable"))
    customer_dataset = by_id["PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"][
        "dataset_dependencies"
    ][0]["dataset_id"]
    keys.extend(
        (
            BusinessKey(RUN_ID, customer_dataset, "current", "Product A"),
            BusinessKey(RUN_ID, customer_dataset, "comparison", "Product A"),
        )
    )
    return tuple(keys)


def _run(registry=None, *, include_dau=True, references=None):
    registry = registry or _registry()
    return SimpleNamespace(
        context=_context(),
        run_input_manifest=_Manifest(
            _manifest_keys(registry, include_dau=include_dau), references=references
        ),
    )


def _run_ids():
    ids = {
        pipeline_id: f"{RUN_ID}:{pipeline_id}"
        for pipeline_id in (
            "PL_REVENUE_TECHNICAL_WEEKLY",
            "PL_REVENUE_CTV_WEEKLY",
            "PL_REVENUE_SMART_SPEAKER_WEEKLY",
            "PL_REVENUE_FAST_VERSION_WEEKLY",
            "PL_INVENTORY_FULL_SITE_WEEKLY",
            "PL_INVENTORY_PATCH_WEEKLY",
            "PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY",
            "PL_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY",
            "PL_USER_ANALYTICS_PLATFORM_DAU_WEEKLY",
        )
    }
    for pipeline_id, products in (
        ("PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", ("Product A", "Brand Moment")),
        ("PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY", ("Product A", "Patch Product")),
        ("PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS", ("Product A",)),
    ):
        for product in products:
            ids[(pipeline_id, product)] = f"{RUN_ID}:{pipeline_id}:{product}"
    return ids


def _field(field_id, value):
    return ResultFieldValue(
        field_id,
        f"MV_{field_id.upper()}",
        Decimal(str(value)) if value is not None else None,
        ResultValueStatus.VALID_VALUE if value is not None else ResultValueStatus.MISSING,
        "percentage_point",
        ("fixture://lineage",),
    )


class _Executor:
    def __init__(
        self,
        pipeline,
        *,
        trigger_value="5",
        blocked=False,
        bad_cutoff=False,
        warning=False,
    ):
        self.pipeline = pipeline
        self.trigger_value = trigger_value
        self.blocked = blocked
        self.bad_cutoff = bad_cutoff
        self.warning = warning
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        run = kwargs["run"]
        pipeline_id = self.pipeline["pipeline_id"]
        pipeline_run_id = kwargs["pipeline_run_id"]
        product = kwargs.get("product", "not_applicable")
        if pipeline_id == "PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY":
            product = kwargs["input_key"].product_parameter
        if self.blocked:
            return PipelineExecutionResult(
                RUN_ID,
                pipeline_id,
                pipeline_run_id,
                {"product": product},
                (),
                PipelineExecutionStatus.BLOCKED,
                error_code="SYNTHETIC_BLOCKED",
                error_message="synthetic blocked scope",
            )
        contract_id = self.pipeline["outputs"]["result_contract_ids"][0]
        if pipeline_id in {
            "PL_REVENUE_TECHNICAL_WEEKLY",
            "PL_REVENUE_CTV_WEEKLY",
            "PL_REVENUE_SMART_SPEAKER_WEEKLY",
            "PL_REVENUE_FAST_VERSION_WEEKLY",
        }:
            contract = CtvResultContractInstance(
                contract_id,
                "1.0.0",
                f"{pipeline_run_id}:{contract_id}",
                RUN_ID,
                pipeline_run_id,
                run.context.values["reporting_period_id"],
                run.context.values["workflow_reporting_date"],
                run.context.values["current_revenue_cutoff_date"],
                run.context.values["report_mode"],
                (),
                {},
                {},
                {},
                kwargs["generated_at"],
                "passed",
                "approved",
                (),
            )
        else:
            fields = ()
            if pipeline_id == "PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY":
                fields = (
                    _field("patch_brand_sell_through_wow_change_pp", self.trigger_value),
                    _field(
                        "non_patch_product_brand_sell_through_wow_change_pp",
                        self.trigger_value,
                    ),
                )
            elif pipeline_id == "PL_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY":
                fields = (_field("sell_through_wow_change_pp", self.trigger_value),)
            contract = Stage3CResultContractInstance(
                contract_id,
                "1.0.0",
                f"{pipeline_run_id}:{contract_id}",
                RUN_ID,
                pipeline_run_id,
                f"{run.context.values['reporting_period_start_date']}.."
                f"{run.context.values['reporting_period_end_date']}",
                self.pipeline["business_context_id"],
                (),
                {},
                {},
                {},
                kwargs["generated_at"],
                "passed",
                "approved",
                fields,
                product_parameter=product,
                workflow_reporting_date=run.context.values["workflow_reporting_date"],
                cutoff_date="wrong" if self.bad_cutoff else run.context.values["cutoff_date"],
                report_mode=run.context.values["report_mode"],
            )
        warnings = (
            (ExecutionWarning("SYNTHETIC_WARNING", "synthetic warning"),)
            if self.warning
            else ()
        )
        return PipelineExecutionResult(
            RUN_ID,
            pipeline_id,
            pipeline_run_id,
            {"product": product},
            (),
            PipelineExecutionStatus.COMPLETED_WITH_WARNING
            if warnings
            else PipelineExecutionStatus.COMPLETED,
            warnings,
            produced_result_contract_reference=f"result-contract://{contract.result_id}",
            lineage_references=("fixture://lineage",),
            result_contract=contract,
        )


def _executors(registry, **overrides):
    executors = {
        pipeline["pipeline_id"]: _Executor(pipeline)
        for pipeline in registry["pipelines"]
        if pipeline["pipeline_id"] in registry["constraints"]["mvp_pipeline_execution"][
            "pipeline_ids"
        ]
    }
    executors.update(overrides)
    return executors


def _runner(registry=None, executors=None, rules=None):
    registry = registry or _registry()
    return WeeklyWorkflowRunner(
        repository_root=ROOT,
        executors=executors or _executors(registry),
        rules=rules or _rules(),
        registry=registry,
    )


def test_activation_dependencies_order_handoff_and_normal_omission():
    registry = _registry()
    executors = _executors(registry)
    summary = _runner(registry, executors).execute(
        run=_run(registry), generated_at="2026-08-22T20:00:00+08:00", pipeline_run_ids=_run_ids()
    )
    order = summary["execution_order"]
    assert order.index("PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY[Product A]") < order.index(
        "PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY[Product A]"
    )
    assert order.index("PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY") < order.index(
        "PL_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY"
    )
    assert order.index("PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY[Brand Moment]") < order.index(
        "PL_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY"
    )
    assert len(executors["PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"].calls) == 0
    assert any(item["reason"] == "trigger_not_met" for item in summary["normal_omissions"])
    assert any(item.startswith("result-contract://") for item in summary["result_contract_references"])
    assert "complete_draft" not in repr(summary)
    assert "partial_draft" not in repr(summary)


def test_trigger_met_executes_customer_change_analysis():
    registry = _registry()
    executors = _executors(registry)
    executors["PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY"].trigger_value = "12"
    summary = _runner(registry, executors).execute(
        run=_run(registry), generated_at="2026-08-22T20:00:00+08:00", pipeline_run_ids=_run_ids()
    )
    customer = executors["PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"]
    assert len(customer.calls) == 1
    assert not any(item["reason"] == "trigger_not_met" for item in summary["normal_omissions"])


def test_optional_dau_without_manifest_binding_is_normal_omission():
    registry = _registry()
    executors = _executors(registry)
    ids = _run_ids()
    ids.pop("PL_USER_ANALYTICS_PLATFORM_DAU_WEEKLY")
    summary = _runner(registry, executors).execute(
        run=_run(registry, include_dau=False),
        generated_at="2026-08-22T20:00:00+08:00",
        pipeline_run_ids=ids,
    )
    assert executors["PL_USER_ANALYTICS_PLATFORM_DAU_WEEKLY"].calls == []
    assert any(
        item["pipeline_id"] == "PL_USER_ANALYTICS_PLATFORM_DAU_WEEKLY"
        and item["reason"] == "optional_input_not_bound"
        for item in summary["normal_omissions"]
    )


def test_invalid_dependency_reference_fails_closed():
    registry = copy.deepcopy(_registry())
    brand = next(
        item
        for item in registry["pipelines"]
        if item["pipeline_id"] == "PL_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY"
    )
    brand["result_contract_dependencies"][0]["producer_pipeline_id"] = "PL_NOT_REGISTERED"
    with pytest.raises(Stage3AError) as exc_info:
        _runner(registry).execute(
            run=_run(registry),
            generated_at="2026-08-22T20:00:00+08:00",
            pipeline_run_ids=_run_ids(),
        )
    assert exc_info.value.code == "STAGE3D_DEPENDENCY_REFERENCE_INVALID"


def test_dependency_cycle_fails_closed():
    registry = copy.deepcopy(_registry())
    by_id = {item["pipeline_id"]: item for item in registry["pipelines"]}
    by_id["PL_INVENTORY_FULL_SITE_WEEKLY"]["result_contract_dependencies"] = [
        {"producer_pipeline_id": "PL_INVENTORY_PATCH_WEEKLY", "required": True}
    ]
    by_id["PL_INVENTORY_PATCH_WEEKLY"]["result_contract_dependencies"] = [
        {"producer_pipeline_id": "PL_INVENTORY_FULL_SITE_WEEKLY", "required": True}
    ]
    with pytest.raises(Stage3AError) as exc_info:
        _runner(registry).execute(
            run=_run(registry),
            generated_at="2026-08-22T20:00:00+08:00",
            pipeline_run_ids=_run_ids(),
        )
    assert exc_info.value.code == "STAGE3D_DEPENDENCY_CYCLE"


def test_blocked_upstream_isolates_failure_to_dependent_scope():
    registry = _registry()
    delivery = next(
        item
        for item in registry["pipelines"]
        if item["pipeline_id"] == "PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY"
    )
    executors = _executors(
        registry,
        PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY=_Executor(delivery, blocked=True),
    )
    summary = _runner(registry, executors).execute(
        run=_run(registry), generated_at="2026-08-22T20:00:00+08:00", pipeline_run_ids=_run_ids()
    )
    statuses = {item.pipeline_id: item.execution_status for item in summary["pipeline_run_results"]}
    assert statuses["PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY"] is PipelineExecutionStatus.BLOCKED
    assert statuses["PL_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY"] is PipelineExecutionStatus.BLOCKED
    assert statuses["PL_INVENTORY_FULL_SITE_WEEKLY"] is not PipelineExecutionStatus.BLOCKED


def test_invalid_result_contract_context_blocks_only_producer_scope():
    registry = _registry()
    full = next(
        item
        for item in registry["pipelines"]
        if item["pipeline_id"] == "PL_INVENTORY_FULL_SITE_WEEKLY"
    )
    executors = _executors(
        registry, PL_INVENTORY_FULL_SITE_WEEKLY=_Executor(full, bad_cutoff=True)
    )
    summary = _runner(registry, executors).execute(
        run=_run(registry), generated_at="2026-08-22T20:00:00+08:00", pipeline_run_ids=_run_ids()
    )
    full_result = next(
        item
        for item in summary["pipeline_run_results"]
        if item.pipeline_id == "PL_INVENTORY_FULL_SITE_WEEKLY"
    )
    assert full_result.execution_status is PipelineExecutionStatus.BLOCKED
    assert full_result.error_code == "STAGE3D_RESULT_CONTRACT_CONTEXT_MISMATCH"
    assert any(
        item.execution_status is PipelineExecutionStatus.COMPLETED
        for item in summary["pipeline_run_results"]
        if item.pipeline_id in {"PL_REVENUE_TECHNICAL_WEEKLY", "PL_INVENTORY_PATCH_WEEKLY"}
    )


def test_same_inputs_and_ids_rerun_deterministically():
    registry = _registry()
    first = _runner(registry).execute(
        run=_run(registry), generated_at="2026-08-22T20:00:00+08:00", pipeline_run_ids=_run_ids()
    )
    second = _runner(registry).execute(
        run=_run(registry), generated_at="2026-08-22T20:00:00+08:00", pipeline_run_ids=_run_ids()
    )
    assert first == second


def test_pipeline_run_identity_must_be_explicit():
    registry = _registry()
    ids = _run_ids()
    ids.pop("PL_INVENTORY_FULL_SITE_WEEKLY")
    with pytest.raises(Stage3AError) as exc_info:
        _runner(registry).execute(
            run=_run(registry),
            generated_at="2026-08-22T20:00:00+08:00",
            pipeline_run_ids=ids,
        )
    assert exc_info.value.code == "STAGE3D_PIPELINE_RUN_ID_UNBOUND"


def test_invalid_report_mode_blocks_revenue_scope_without_defaulting_or_cross_domain_block():
    registry = _registry()
    executors = _executors(registry)
    run = _run(registry)
    run.context.values["report_mode"] = "not_applicable"
    summary = _runner(registry, executors).execute(
        run=run, generated_at="2026-08-22T20:00:00+08:00", pipeline_run_ids=_run_ids()
    )
    revenue = [
        item
        for item in summary["pipeline_run_results"]
        if item.pipeline_id.startswith("PL_REVENUE_")
    ]
    assert revenue
    assert all(item.execution_status is PipelineExecutionStatus.BLOCKED for item in revenue)
    assert all(executors[item.pipeline_id].calls == [] for item in revenue)
    full = next(
        item
        for item in summary["pipeline_run_results"]
        if item.pipeline_id == "PL_INVENTORY_FULL_SITE_WEEKLY"
    )
    assert full.execution_status is PipelineExecutionStatus.COMPLETED


def test_warning_blocked_and_omission_aggregation_use_existing_statuses():
    registry = _registry()
    by_id = {item["pipeline_id"]: item for item in registry["pipelines"]}
    executors = _executors(
        registry,
        PL_INVENTORY_PATCH_WEEKLY=_Executor(
            by_id["PL_INVENTORY_PATCH_WEEKLY"], warning=True
        ),
        PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY=_Executor(
            by_id["PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY"], blocked=True
        ),
    )
    summary = _runner(registry, executors).execute(
        run=_run(registry), generated_at="2026-08-22T20:00:00+08:00", pipeline_run_ids=_run_ids()
    )
    assert any(item.code == "SYNTHETIC_WARNING" for item in summary["warnings"])
    assert any(
        item["pipeline_id"] == "PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY"
        for item in summary["blocked_scopes"]
    )
    assert any(item["reason"] == "trigger_not_met" for item in summary["normal_omissions"])


def test_product_scoped_runs_expand_only_from_explicit_routes_and_manifest():
    registry = _registry()
    summary = _runner(registry).execute(
        run=_run(registry), generated_at="2026-08-22T20:00:00+08:00", pipeline_run_ids=_run_ids()
    )
    order = summary["execution_order"]
    assert "PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY[Product A]" in order
    assert "PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY[Brand Moment]" in order
    assert "PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY[Product A]" in order
    assert "PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY[Patch Product]" in order
    assert "PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY[Brand Moment]" not in order


def test_integrated_synthetic_runner_invokes_existing_inventory_executor(tmp_path):
    registry = _registry()
    run = _run(registry)
    full_pipeline = next(
        item for item in registry["pipelines"] if item["pipeline_id"] == FULL_SITE.pipeline_id
    )
    full_key = next(
        key
        for key in run.run_input_manifest.entries
        if key.dataset_id == FULL_SITE.dataset_id
    )
    path = tmp_path / "full-site.json"
    path.write_text(
        json.dumps(
            [
                {
                    "广告位ID": "full",
                    "库存": "100",
                    "禁用库存": "10",
                    "品牌商广整体投放库存": "20",
                    "效果投放库存": "30",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    run.run_input_manifest.entries[full_key] = _Entry(str(path))
    runtime = _Runtime({full_key: path})
    store = InMemoryMetricStore()
    store.seed_historical(
        MetricStoreRecord(
            result_id="PRIOR_FULL_SITE_AVAILABLE",
            workflow_id="WF_WEEKLY_BUSINESS_REPORT",
            workflow_run_id="PRIOR_RUN",
            pipeline_id=FULL_SITE.pipeline_id,
            pipeline_run_id="PRIOR_PIPELINE_RUN",
            store_id="STORE_WEEKLY_INVENTORY_HISTORICAL",
            store_asset_id=FULL_SITE.store_asset_id,
            metric_variant_id="MV_INVENTORY_FULL_SITE_AVAILABLE_VOLUME_V1",
            metric_variant_version="1.0.0",
            workflow_reporting_date="2026-08-13",
            current_revenue_cutoff_date="not_applicable",
            business_context_id=FULL_SITE.context_id,
            reporting_period="2026-08-10..2026-08-16",
            value=Decimal("80"),
            value_status="valid_value",
            numeric_semantics="non_negative",
            unit="inventory_unit",
            precision="integer",
            validation_status="passed",
            generated_at="2026-08-13T20:00:00+08:00",
            lineage_references=("fixture://prior",),
        )
    )
    executors = _executors(
        registry,
        PL_INVENTORY_FULL_SITE_WEEKLY=InventoryPipelineExecutor(
            FULL_SITE, runtime, store
        ),
    )
    summary = _runner(registry, executors).execute(
        run=run, generated_at="2026-08-22T20:00:00+08:00", pipeline_run_ids=_run_ids()
    )
    result = next(
        item
        for item in summary["pipeline_run_results"]
        if item.pipeline_id == full_pipeline["pipeline_id"]
    )
    assert result.execution_status in {
        PipelineExecutionStatus.COMPLETED,
        PipelineExecutionStatus.COMPLETED_WITH_WARNING,
    }
    assert result.result_contract is not None
    assert result.result_contract.field("available_inventory_count").value == Decimal("90")
