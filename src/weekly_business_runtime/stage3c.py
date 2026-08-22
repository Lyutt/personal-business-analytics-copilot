"""Local deterministic executors for the eight Owner-authorized Stage 3C Pipelines.

These executors deliberately consume only an explicit Manifest-bound input or an
explicit validated upstream Result Contract.  They contain no acquisition,
scheduling, discovery, or workflow-orchestration behaviour.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from weekly_acquisition_runtime.contracts import BusinessKey
from weekly_acquisition_runtime.errors import AcquisitionError
from weekly_acquisition_runtime.runtime import AcquisitionRuntime, RuntimeRun

from .errors import (
    DatasetValidationError,
    MetricStoreError,
    ResultContractError,
    Stage3AError,
)
from .models import (
    ExecutionWarning,
    PipelineExecutionResult,
    PipelineExecutionStatus,
    ResultFieldValue,
    ResultValueStatus,
    Stage3CResultContractInstance,
)
from .store import MetricStorePort, MetricStoreRecord, StoreReadKey

WORKFLOW_ID = "WF_WEEKLY_BUSINESS_REPORT"
INVENTORY_STORE_ID = "STORE_WEEKLY_INVENTORY_HISTORICAL"
ADVERTISING_STORE_ID = "STORE_WEEKLY_ADVERTISING_HISTORICAL"
DAU_STORE_ID = "STORE_WEEKLY_USER_ANALYTICS_HISTORICAL"


@dataclass(frozen=True)
class LocalRuleBindings:
    """Explicit, local-only rule values; callers must resolve them before execution."""

    versions: Mapping[str, str]
    commercial_sellability_by_placement: Mapping[str, bool] = None  # type: ignore[assignment]
    special_placement_ids: frozenset[str] = frozenset()
    excluded_patch_time_slot_ids: frozenset[str] = frozenset()
    product_by_placement_id: Mapping[str, str] = None  # type: ignore[assignment]
    product_route_by_name: Mapping[str, str] = None  # type: ignore[assignment]
    customer_analysis_by_product: Mapping[str, Mapping[str, object]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        object.__setattr__(self, "commercial_sellability_by_placement", self.commercial_sellability_by_placement or {})
        object.__setattr__(self, "product_by_placement_id", self.product_by_placement_id or {})
        object.__setattr__(self, "product_route_by_name", self.product_route_by_name or {})
        object.__setattr__(self, "customer_analysis_by_product", self.customer_analysis_by_product or {})

    def require(self, asset_id: str) -> None:
        if self.versions.get(asset_id) != "1.0.0":
            raise Stage3AError("LOCAL_RULE_VERSION_UNVERIFIED", f"{asset_id} must be explicitly bound at version 1.0.0")


@dataclass(frozen=True)
class InventoryProfile:
    pipeline_id: str
    dataset_id: str
    mapping_id: str
    context_id: str
    contract_id: str
    store_asset_id: str
    variant_prefix: str
    kind: str


FULL_SITE = InventoryProfile("PL_INVENTORY_FULL_SITE_WEEKLY", "DS_INVENTORY_APOLLO_FULL_SITE_STOCK_SUMMARY", "MAP_INVENTORY_APOLLO_FULL_SITE_STOCK_SUMMARY_V1", "CTX_INVENTORY_FULL_SITE_WEEKLY", "RC_INVENTORY_FULL_SITE_WEEKLY", "STORE_ASSET_WEEKLY_INVENTORY_FULL_SITE", "MV_INVENTORY_FULL_SITE", "full")
PATCH = InventoryProfile("PL_INVENTORY_PATCH_WEEKLY", "DS_INVENTORY_APOLLO_PATCH_STOCK_SUMMARY", "MAP_INVENTORY_APOLLO_PATCH_STOCK_SUMMARY_V1", "CTX_INVENTORY_PATCH_WEEKLY", "RC_INVENTORY_PATCH_WEEKLY", "STORE_ASSET_WEEKLY_INVENTORY_PATCH", "MV_INVENTORY_PATCH", "patch")
NON_PATCH = InventoryProfile("PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", "DS_INVENTORY_APOLLO_NON_PATCH_PRODUCT_STOCK_SUMMARY", "MAP_INVENTORY_APOLLO_NON_PATCH_PRODUCT_STOCK_SUMMARY_V1", "CTX_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", "RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", "STORE_ASSET_WEEKLY_INVENTORY_NON_PATCH_PRODUCT", "MV_INVENTORY_NON_PATCH_PRODUCT", "non_patch")


def _decimal(value: object, field: str, *, zero_for_blank: bool = False) -> Decimal:
    if value is None or str(value).strip() == "":
        if zero_for_blank:
            return Decimal(0)
        raise DatasetValidationError("STAGE3C_VALUE_MISSING", f"{field} is required")
    try:
        result = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError("STAGE3C_VALUE_INVALID", f"{field} must be numeric") from exc
    if not result.is_finite():
        raise DatasetValidationError("STAGE3C_VALUE_INVALID", f"{field} must be finite")
    return result.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        value = value.get("rows", value) if isinstance(value, dict) else value
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise DatasetValidationError("STAGE3C_DATASET_SHAPE_INVALID", "Local JSON input must contain row objects")
        return [dict(row) for row in value]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _field(field_id: str, variant: str, value: Decimal | None, unit: str, lineage: tuple[str, ...], *, status: ResultValueStatus = ResultValueStatus.VALID_VALUE) -> ResultFieldValue:
    return ResultFieldValue(field_id, variant, value, status, unit, lineage)


def _contract(*, contract_id: str, result_id: str, run: RuntimeRun, pipeline_run_id: str, context_id: str, fields: Iterable[ResultFieldValue], generated_at: str, inputs: tuple[str, ...], mappings: Mapping[str, str], rules: Mapping[str, str] = {}, record_set: tuple[Mapping[str, Any], ...] = (), product: str = "not_applicable", context_values: Mapping[str, Any] = {}) -> Stage3CResultContractInstance:
    period = f"{run.context.values['reporting_period_start_date']}..{run.context.values['reporting_period_end_date']}"
    return Stage3CResultContractInstance(contract_id, "1.0.0", result_id, run.context.workflow_run_id, pipeline_run_id, period, context_id, inputs, mappings, rules, {field.metric_variant_id: "1.0.0" for field in fields}, generated_at, "passed", "approved", tuple(fields), record_set, product, str(run.context.values["workflow_reporting_date"]), context_values)


def _persist(result: Stage3CResultContractInstance, *, pipeline_id: str, store_id: str, asset_id: str, store: MetricStorePort, product: str = "not_applicable", extra_records: tuple[MetricStoreRecord, ...] = ()) -> tuple[ExecutionWarning, ...]:
    records = tuple(
        MetricStoreRecord(
            result_id=f"{result.result_id}:{field.metric_variant_id}", workflow_id=WORKFLOW_ID,
            workflow_run_id=result.workflow_run_id, pipeline_id=pipeline_id, pipeline_run_id=result.pipeline_run_id,
            store_id=store_id, store_asset_id=asset_id, metric_variant_id=field.metric_variant_id,
            metric_variant_version="1.0.0", workflow_reporting_date=result.workflow_reporting_date,
            current_revenue_cutoff_date=result.workflow_reporting_date, business_context_id=result.business_context_id,
            reporting_period=result.reporting_period, value=field.value or Decimal(0),
            value_status=field.value_status.value, numeric_semantics=("percentage_point_change" if field.unit == "percentage_point" else "ratio" if field.unit == "decimal_ratio" else "average_count" if field.unit == "user" else "integer_count"),
            unit=field.unit, precision="preserve_source_precision" if field.unit in {"decimal_ratio", "user"} else "integer", validation_status="passed",
            generated_at=result.generated_at, lineage_references=field.lineage_references, product_parameter=product,
        ) for field in result.fields if field.value_status is ResultValueStatus.VALID_VALUE and field.value is not None
    ) + extra_records
    try:
        plan = store.preflight_write(records)
        receipt = store.write_validated(plan)
        if not store.verify_write(receipt):
            raise MetricStoreError("STORE_WRITE_VERIFICATION_FAILED", "Store read-back verification failed")
        return ()
    except MetricStoreError as exc:
        return (ExecutionWarning(exc.code, str(exc)),)


def _previous_reporting_date(run: RuntimeRun) -> str:
    try:
        return (date.fromisoformat(str(run.context.values["workflow_reporting_date"])) - timedelta(days=7)).isoformat()
    except (KeyError, TypeError, ValueError) as exc:
        raise Stage3AError("STAGE3C_PREVIOUS_PERIOD_UNBOUND", "Core weekly reporting date is not locked") from exc


def _exact_prior(
    store: MetricStorePort, *, store_id: str, asset_id: str, variant_id: str,
    context_id: str, run: RuntimeRun, product: str = "not_applicable",
) -> Decimal | None:
    try:
        return store.read_exact(StoreReadKey(store_id, asset_id, variant_id, _previous_reporting_date(run), context_id, product)).value
    except MetricStoreError as exc:
        if exc.code == "STORE_EXACT_KEY_NOT_FOUND":
            return None
        raise


class InventoryPipelineExecutor:
    """The three direct inventory Dataset-to-Result executors, without orchestration."""

    def __init__(self, profile: InventoryProfile, acquisition_runtime: AcquisitionRuntime, metric_store: MetricStorePort) -> None:
        self.profile, self.acquisition_runtime, self.metric_store = profile, acquisition_runtime, metric_store

    def execute(self, *, run: RuntimeRun, pipeline_run_id: str, input_key: BusinessKey, generated_at: str, rules: LocalRuleBindings) -> PipelineExecutionResult:
        try:
            if input_key.workflow_run_id != run.context.workflow_run_id or input_key.dataset_id != self.profile.dataset_id or input_key.period_role != "current":
                raise Stage3AError("STAGE3C_INPUT_KEY_INVALID", "Input key does not match the registered Pipeline Dataset")
            product = input_key.product_parameter
            if self.profile.kind == "non_patch" and product == "not_applicable":
                raise Stage3AError("STAGE3C_PRODUCT_REQUIRED", "Non-patch inventory requires an explicit product")
            entry = run.run_input_manifest.get_entry(input_key)
            rows = _rows(self.acquisition_runtime.consume_bound_input(run, input_key))
            if not rows:
                raise DatasetValidationError("STAGE3C_DATASET_EMPTY", "Dataset must contain at least one row")
            selected = self._select(rows, rules, product)
            values = self._calculate(rows if self.profile.kind == "full" else selected, rules)
            if self.profile.kind == "full":
                values["available_inventory_count"] = self._calculate(selected, rules)["available_inventory_count"]
            fields = self._fields(values, product, rules, run)
            lineage = (entry.local_input_reference, f"mapping-consumed://{self.profile.mapping_id}")
            result = _contract(contract_id=self.profile.contract_id, result_id=f"{pipeline_run_id}:{self.profile.contract_id}", run=run, pipeline_run_id=pipeline_run_id, context_id=self.profile.context_id, fields=fields, generated_at=generated_at, inputs=(entry.local_input_reference,), mappings={self.profile.mapping_id: "1.0.0"}, product=product)
            warnings = _persist(result, pipeline_id=self.profile.pipeline_id, store_id=INVENTORY_STORE_ID, asset_id=self.profile.store_asset_id, store=self.metric_store, product=product)
            return PipelineExecutionResult(run.context.workflow_run_id, self.profile.pipeline_id, pipeline_run_id, {"business_context_id": self.profile.context_id, "product": product}, (entry.local_input_reference,), PipelineExecutionStatus.COMPLETED_WITH_WARNING if warnings else PipelineExecutionStatus.COMPLETED, warnings, produced_result_contract_reference=f"result-contract://{result.result_id}", lineage_references=(*lineage, *(f"metric-store://{result.result_id}" for _ in warnings)), result_contract=result)
        except (Stage3AError, AcquisitionError, KeyError) as exc:
            return PipelineExecutionResult(run.context.workflow_run_id, self.profile.pipeline_id, pipeline_run_id, {"business_context_id": self.profile.context_id}, (), PipelineExecutionStatus.BLOCKED, error_code=getattr(exc, "code", "STAGE3C_EXECUTION_BLOCKED"), error_message=str(exc))

    def _select(self, rows: list[dict[str, Any]], rules: LocalRuleBindings, product: str) -> list[dict[str, Any]]:
        if self.profile.kind == "full":
            rules.require("MAP_COMMERCIAL_SELLABILITY_INDEX_LOCAL_ONLY")
            rules.require("SPECIAL_PLACEMENT_EXCEPTION_LOCAL_ONLY")
            selected = []
            for row in rows:
                placement = str(row.get("广告位ID", "")).strip()
                mapped_sellability = rules.commercial_sellability_by_placement.get(placement)
                sellable = mapped_sellability not in {False, "否"}
                if placement in rules.special_placement_ids:
                    if str(row.get("是否VIP", "")).strip() == "非vip": selected.append(row)
                elif sellable: selected.append(row)
            return selected
        if self.profile.kind == "patch":
            rules.require("FILTER_PATCH_TIME_SLICE_EXCLUSION_LOCAL_ONLY")
            return [row for row in rows if str(row.get("时段片ID", "")).strip() not in rules.excluded_patch_time_slot_ids]
        rules.require("RULESET_NON_PATCH_PRODUCT_INVENTORY_LOCAL_ONLY")
        selected = []
        for row in rows:
            placement = str(row.get("广告位ID", "")).strip()
            if rules.product_by_placement_id.get(placement) == product: selected.append(row)
            elif placement not in rules.product_by_placement_id: raise Stage3AError("STAGE3C_PRODUCT_ASSIGNMENT_MISSING", "Product assignment is not explicit")
        return selected

    def _calculate(self, rows: list[dict[str, Any]], rules: LocalRuleBindings) -> dict[str, Decimal]:
        if not rows: raise DatasetValidationError("STAGE3C_NO_APPLICABLE_ROWS", "No rows satisfy the explicit local rule")
        def total(name: str, fallback: str | None = None) -> Decimal: return sum((_decimal(row.get(name, row.get(fallback)) if fallback else row.get(name), name, zero_for_blank=True) for row in rows), Decimal(0))
        total_name = "库存" if self.profile.kind in {"full", "patch"} else "总库存"
        disabled_name = "禁用库存"
        brand_name = "品牌商广整体投放库存" if self.profile.kind == "full" else "品牌投放库存"
        performance_name = "效果投放库存"
        gross, disabled, brand, performance = total(total_name), total(disabled_name), total(brand_name), total(performance_name)
        return {"gross_inventory_count": gross, "disabled_inventory_count": disabled, "available_inventory_count": gross-disabled, "brand_delivery_inventory_count": brand, "performance_delivery_inventory_count": performance, "commercial_delivery_inventory_count": brand+performance}

    def _fields(self, values: Mapping[str, Decimal], product: str, rules: LocalRuleBindings, run: RuntimeRun) -> tuple[ResultFieldValue, ...]:
        names = ("GROSS_VOLUME", "DISABLED_VOLUME", "AVAILABLE_VOLUME", "BRAND_DELIVERY_VOLUME", "PERFORMANCE_DELIVERY_VOLUME", "COMMERCIAL_DELIVERY_VOLUME")
        ids = ("gross_inventory_count", "disabled_inventory_count", "available_inventory_count", "brand_delivery_inventory_count", "performance_delivery_inventory_count", "commercial_delivery_inventory_count")
        lineage = (f"mapping-consumed://{self.profile.mapping_id}",)
        fields = [_field(field_id, f"{self.profile.variant_prefix}_{name}_V1", values[field_id], "inventory_count", lineage) for field_id, name in zip(ids, names, strict=True)]
        available_variant = f"{self.profile.variant_prefix}_AVAILABLE_VOLUME_V1"
        prior_available = _exact_prior(self.metric_store, store_id=INVENTORY_STORE_ID, asset_id=self.profile.store_asset_id, variant_id=available_variant, context_id=self.profile.context_id, run=run, product=product)
        wow = None if prior_available is None or prior_available <= 0 else values["available_inventory_count"] / prior_available - 1
        fields.append(_field("available_inventory_wow", f"{self.profile.variant_prefix}_AVAILABLE_VOLUME_WOW_V1", wow, "decimal_ratio", lineage, status=ResultValueStatus.VALID_VALUE if wow is not None else ResultValueStatus.MISSING))
        if self.profile.kind == "non_patch":
            rules.require("BR_APOLLO_PRODUCT_FILTER_MAPPING")
            if rules.product_route_by_name.get(product) == "brand_moment": fields.append(_field("brand_moment_available_inventory_count", "MV_INVENTORY_BRAND_MOMENT_AVAILABLE_VOLUME_V1", values["available_inventory_count"], "inventory_count", lineage))
            else: fields.append(_field("brand_moment_available_inventory_count", "MV_INVENTORY_BRAND_MOMENT_AVAILABLE_VOLUME_V1", None, "inventory_count", lineage, status=ResultValueStatus.NOT_APPLICABLE))
        return tuple(fields)


class BrandMomentDeliveryExecutor:
    pipeline_id = "PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY"
    dataset_id = "DS_ADVERTISING_APOLLO_BRAND_MOMENT_DELIVERY_EXECUTION"
    context_id = "CTX_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY"

    def __init__(self, acquisition_runtime: AcquisitionRuntime, metric_store: MetricStorePort) -> None:
        self.acquisition_runtime, self.metric_store = acquisition_runtime, metric_store

    def execute(self, *, run: RuntimeRun, pipeline_run_id: str, input_key: BusinessKey, generated_at: str) -> PipelineExecutionResult:
        try:
            if input_key.workflow_run_id != run.context.workflow_run_id or input_key.dataset_id != self.dataset_id or input_key.period_role != "current":
                raise Stage3AError("STAGE3C_INPUT_KEY_INVALID", "Brand Moment input binding is invalid")
            entry = run.run_input_manifest.get_entry(input_key)
            rows = _rows(self.acquisition_runtime.consume_bound_input(run, input_key))
            if len(rows) != 1:
                raise DatasetValidationError("BRAND_MOMENT_RESULT_SHAPE_INVALID", "Brand Moment Delivery requires exactly one row")
            value = _decimal(rows[0].get("曝光量"), "impression_count")
            if value <= 0:
                raise DatasetValidationError("BRAND_MOMENT_IMPRESSION_INVALID", "impression_count must be positive")
            lineage = (entry.local_input_reference, "mapping-consumed://MAP_ADVERTISING_APOLLO_BRAND_MOMENT_DELIVERY_EXECUTION_V1")
            result = _contract(contract_id="RC_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY", result_id=f"{pipeline_run_id}:RC_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY", run=run, pipeline_run_id=pipeline_run_id, context_id=self.context_id, fields=(_field("impression_count", "MV_ADVERTISING_BRAND_MOMENT_WEEKLY_IMPRESSION_COUNT_V1", value, "impression_count", lineage),), generated_at=generated_at, inputs=(entry.local_input_reference,), mappings={"MAP_ADVERTISING_APOLLO_BRAND_MOMENT_DELIVERY_EXECUTION_V1": "1.0.0"})
            warnings = _persist(result, pipeline_id=self.pipeline_id, store_id=ADVERTISING_STORE_ID, asset_id="STORE_ASSET_WEEKLY_BRAND_MOMENT_DELIVERY", store=self.metric_store)
            return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id}, (entry.local_input_reference,), PipelineExecutionStatus.COMPLETED_WITH_WARNING if warnings else PipelineExecutionStatus.COMPLETED, warnings, produced_result_contract_reference=f"result-contract://{result.result_id}", lineage_references=lineage, result_contract=result)
        except (Stage3AError, AcquisitionError, KeyError) as exc:
            return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id}, (), PipelineExecutionStatus.BLOCKED, error_code=getattr(exc, "code", "STAGE3C_EXECUTION_BLOCKED"), error_message=str(exc))


class PlatformDauExecutor:
    pipeline_id = "PL_USER_ANALYTICS_PLATFORM_DAU_WEEKLY"
    dataset_id = "DS_NOVABI_PLATFORM_DAU"
    context_id = "CTX_USER_ANALYTICS_PLATFORM_DAU_WEEKLY"

    def __init__(self, acquisition_runtime: AcquisitionRuntime, metric_store: MetricStorePort) -> None:
        self.acquisition_runtime, self.metric_store = acquisition_runtime, metric_store

    def execute(self, *, run: RuntimeRun, pipeline_run_id: str, input_key: BusinessKey, generated_at: str) -> PipelineExecutionResult:
        try:
            if input_key.workflow_run_id != run.context.workflow_run_id or input_key.dataset_id != self.dataset_id or input_key.period_role != "current":
                raise Stage3AError("STAGE3C_INPUT_KEY_INVALID", "DAU input binding is invalid")
            entry = run.run_input_manifest.get_entry(input_key)
            rows = _rows(self.acquisition_runtime.consume_bound_input(run, input_key))
            dates = {str(row.get("日期", "")) for row in rows}
            start, end = run.context.values["current_period_start_date"], run.context.values["current_period_end_date"]
            if len(rows) != 7 or len(dates) != 7 or min(dates, default="") != start or max(dates, default="") != end:
                raise DatasetValidationError("DAU_PERIOD_COVERAGE_INVALID", "DAU requires one row for each explicit weekly date")
            values = [_decimal(row.get("全平台日活跃用户数"), "dau_count") for row in rows]
            if any(value <= 0 for value in values):
                raise DatasetValidationError("DAU_VALUE_INVALID", "DAU values must be positive")
            average = sum(values, Decimal(0)) / Decimal(7)
            lineage = (entry.local_input_reference, "mapping-consumed://MAP_USER_ANALYTICS_NOVABI_PLATFORM_DAU_V1")
            prior = _exact_prior(self.metric_store, store_id=DAU_STORE_ID, asset_id="STORE_ASSET_WEEKLY_PLATFORM_DAU", variant_id="MV_USER_ANALYTICS_PLATFORM_WEEKLY_AVERAGE_DAU_V1", context_id=self.context_id, run=run)
            wow = None if prior is None or prior <= 0 else average / prior - 1
            fields = (_field("weekly_average_dau", "MV_USER_ANALYTICS_PLATFORM_WEEKLY_AVERAGE_DAU_V1", average, "user", lineage), _field("weekly_average_dau_wow", "MV_USER_ANALYTICS_PLATFORM_WEEKLY_AVERAGE_DAU_WOW_V1", wow, "decimal_ratio", lineage, status=ResultValueStatus.VALID_VALUE if wow is not None else ResultValueStatus.MISSING))
            daily = tuple({"activity_date": str(row["日期"]), "platform_scope": "full_platform", "dau_count": value} for row, value in sorted(zip(rows, values, strict=True), key=lambda item: str(item[0]["日期"])))
            result = _contract(contract_id="RC_USER_ANALYTICS_PLATFORM_DAU_WEEKLY", result_id=f"{pipeline_run_id}:RC_USER_ANALYTICS_PLATFORM_DAU_WEEKLY", run=run, pipeline_run_id=pipeline_run_id, context_id=self.context_id, fields=fields, generated_at=generated_at, inputs=(entry.local_input_reference,), mappings={"MAP_USER_ANALYTICS_NOVABI_PLATFORM_DAU_V1": "1.0.0"}, record_set=daily)
            daily_records = tuple(MetricStoreRecord(result_id=f"{result.result_id}:DAU:{item['activity_date']}", workflow_id=WORKFLOW_ID, workflow_run_id=result.workflow_run_id, pipeline_id=self.pipeline_id, pipeline_run_id=result.pipeline_run_id, store_id=DAU_STORE_ID, store_asset_id="STORE_ASSET_WEEKLY_PLATFORM_DAU", metric_variant_id="MV_USER_ANALYTICS_PLATFORM_DAILY_DAU_V1", metric_variant_version="1.0.0", workflow_reporting_date=result.workflow_reporting_date, current_revenue_cutoff_date=result.workflow_reporting_date, business_context_id=self.context_id, reporting_period=result.reporting_period, value=item["dau_count"], value_status="valid_value", numeric_semantics="integer_count", unit="user", precision="integer", validation_status="passed", generated_at=generated_at, lineage_references=lineage, canonical_dimensions={"activity_date": item["activity_date"], "platform_scope": "full_platform"}) for item in daily)
            warnings = _persist(result, pipeline_id=self.pipeline_id, store_id=DAU_STORE_ID, asset_id="STORE_ASSET_WEEKLY_PLATFORM_DAU", store=self.metric_store, extra_records=daily_records)
            return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id}, (entry.local_input_reference,), PipelineExecutionStatus.COMPLETED_WITH_WARNING if warnings else PipelineExecutionStatus.COMPLETED, warnings, produced_result_contract_reference=f"result-contract://{result.result_id}", lineage_references=lineage, result_contract=result)
        except (Stage3AError, AcquisitionError, KeyError) as exc:
            return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id}, (), PipelineExecutionStatus.BLOCKED, error_code=getattr(exc, "code", "STAGE3C_EXECUTION_BLOCKED"), error_message=str(exc))


def _upstream(contract: Stage3CResultContractInstance, contract_id: str, context_id: str, period: str, product: str | None = None) -> None:
    if any((contract.result_contract_id != contract_id, contract.business_context_id != context_id, contract.reporting_period != period, contract.validation_status != "passed", product is not None and contract.product_parameter != product)):
        raise ResultContractError("STAGE3C_UPSTREAM_CONTRACT_MISMATCH", "Upstream Result Contract is not an exact registered match")


class BrandMomentSellThroughExecutor:
    pipeline_id = "PL_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY"
    context_id = "CTX_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY"

    def __init__(self, metric_store: MetricStorePort) -> None: self.metric_store = metric_store

    def execute(self, *, run: RuntimeRun, pipeline_run_id: str, delivery: Stage3CResultContractInstance | None, inventory: Stage3CResultContractInstance | None, generated_at: str) -> PipelineExecutionResult:
        try:
            if delivery is None or inventory is None: raise ResultContractError("STAGE3C_UPSTREAM_CONTRACT_MISSING", "Both registered upstream Result Contracts are required")
            period = f"{run.context.values['reporting_period_start_date']}..{run.context.values['reporting_period_end_date']}"
            _upstream(delivery, "RC_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY", "CTX_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY", period)
            _upstream(inventory, "RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", "CTX_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", period)
            impression, available = delivery.field("impression_count").value, inventory.field("brand_moment_available_inventory_count").value
            if impression is None or available is None or impression <= 0 or available <= 0: raise ResultContractError("STAGE3C_SELL_THROUGH_INPUT_INVALID", "Brand Moment numerator and denominator must be positive valid values")
            lineage = (f"result-contract://{delivery.result_id}", f"result-contract://{inventory.result_id}")
            rate = impression / available
            prior_available = _exact_prior(self.metric_store, store_id=INVENTORY_STORE_ID, asset_id="STORE_ASSET_WEEKLY_INVENTORY_NON_PATCH_PRODUCT", variant_id="MV_INVENTORY_BRAND_MOMENT_AVAILABLE_VOLUME_V1", context_id="CTX_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", run=run, product=inventory.product_parameter)
            prior_rate = _exact_prior(self.metric_store, store_id=INVENTORY_STORE_ID, asset_id="STORE_ASSET_WEEKLY_INVENTORY_BRAND_MOMENT_SELL_THROUGH", variant_id="MV_INVENTORY_BRAND_MOMENT_SELL_THROUGH_RATE_V1", context_id=self.context_id, run=run)
            available_wow = None if prior_available is None or prior_available <= 0 else available / prior_available - 1
            rate_wow = None if prior_rate is None else (rate - prior_rate) * 100
            fields = (_field("available_inventory_wow", "MV_INVENTORY_BRAND_MOMENT_AVAILABLE_VOLUME_WOW_V1", available_wow, "decimal_ratio", lineage, status=ResultValueStatus.VALID_VALUE if available_wow is not None else ResultValueStatus.MISSING), _field("sell_through_rate", "MV_INVENTORY_BRAND_MOMENT_SELL_THROUGH_RATE_V1", rate, "decimal_ratio", lineage), _field("sell_through_wow_change_pp", "MV_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WOW_CHANGE_V1", rate_wow, "percentage_point", lineage, status=ResultValueStatus.VALID_VALUE if rate_wow is not None else ResultValueStatus.MISSING))
            result = _contract(contract_id="RC_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY", result_id=f"{pipeline_run_id}:RC_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY", run=run, pipeline_run_id=pipeline_run_id, context_id=self.context_id, fields=fields, generated_at=generated_at, inputs=lineage, mappings={})
            warnings = _persist(result, pipeline_id=self.pipeline_id, store_id=INVENTORY_STORE_ID, asset_id="STORE_ASSET_WEEKLY_INVENTORY_BRAND_MOMENT_SELL_THROUGH", store=self.metric_store)
            return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id}, lineage, PipelineExecutionStatus.COMPLETED_WITH_WARNING if warnings else PipelineExecutionStatus.COMPLETED, warnings, produced_result_contract_reference=f"result-contract://{result.result_id}", lineage_references=lineage, result_contract=result)
        except Stage3AError as exc:
            return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id}, (), PipelineExecutionStatus.BLOCKED, error_code=exc.code, error_message=str(exc))


class ProductSellThroughExecutor:
    pipeline_id = "PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY"
    context_id = "CTX_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY"

    def __init__(self, metric_store: MetricStorePort) -> None: self.metric_store = metric_store

    def execute(self, *, run: RuntimeRun, pipeline_run_id: str, product: str, rules: LocalRuleBindings, upstream: Stage3CResultContractInstance | None, generated_at: str) -> PipelineExecutionResult:
        try:
            if not product or product == "not_applicable": raise Stage3AError("STAGE3C_PRODUCT_REQUIRED", "Explicit product is required")
            rules.require("BR_APOLLO_PRODUCT_FILTER_MAPPING")
            route = rules.product_route_by_name.get(product)
            if route not in {"patch", "non_patch"}: raise Stage3AError("STAGE3C_PRODUCT_ROUTE_AMBIGUOUS", "Product has no unique registered sell-through route")
            if upstream is None: raise ResultContractError("STAGE3C_UPSTREAM_CONTRACT_MISSING", "Registered upstream Result Contract is required")
            period = f"{run.context.values['reporting_period_start_date']}..{run.context.values['reporting_period_end_date']}"
            contract_id, context_id = ("RC_INVENTORY_PATCH_WEEKLY", "CTX_INVENTORY_PATCH_WEEKLY") if route == "patch" else ("RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY", "CTX_INVENTORY_NON_PATCH_PRODUCT_WEEKLY")
            _upstream(upstream, contract_id, context_id, period, product if route == "non_patch" else None)
            numerator, denominator = upstream.field("brand_delivery_inventory_count").value, upstream.field("available_inventory_count").value
            if numerator is None or denominator is None or numerator < 0 or denominator <= 0: raise ResultContractError("STAGE3C_SELL_THROUGH_INPUT_INVALID", "Registered upstream values are ineligible")
            lineage = (f"result-contract://{upstream.result_id}", "rule-evaluated://BR_APOLLO_PRODUCT_FILTER_MAPPING")
            rate = numerator / denominator
            patch = route == "patch"
            rate_variant = "MV_INVENTORY_PATCH_BRAND_SELL_THROUGH_RATE_V1" if patch else "MV_INVENTORY_NON_PATCH_PRODUCT_BRAND_SELL_THROUGH_RATE_V1"
            prior_rate = _exact_prior(self.metric_store, store_id=INVENTORY_STORE_ID, asset_id="STORE_ASSET_WEEKLY_INVENTORY_PRODUCT_SELL_THROUGH", variant_id=rate_variant, context_id=self.context_id, run=run, product=product)
            change = None if prior_rate is None else (rate - prior_rate) * 100
            fields = (_field("patch_brand_sell_through_rate", "MV_INVENTORY_PATCH_BRAND_SELL_THROUGH_RATE_V1", rate if patch else None, "decimal_ratio", lineage, status=ResultValueStatus.VALID_VALUE if patch else ResultValueStatus.NOT_APPLICABLE), _field("patch_brand_sell_through_wow_change_pp", "MV_INVENTORY_PATCH_BRAND_SELL_THROUGH_WOW_CHANGE_V1", change if patch else None, "percentage_point", lineage, status=ResultValueStatus.VALID_VALUE if patch and change is not None else ResultValueStatus.MISSING if patch else ResultValueStatus.NOT_APPLICABLE), _field("non_patch_product_brand_sell_through_rate", "MV_INVENTORY_NON_PATCH_PRODUCT_BRAND_SELL_THROUGH_RATE_V1", rate if not patch else None, "decimal_ratio", lineage, status=ResultValueStatus.NOT_APPLICABLE if patch else ResultValueStatus.VALID_VALUE), _field("non_patch_product_brand_sell_through_wow_change_pp", "MV_INVENTORY_NON_PATCH_PRODUCT_BRAND_SELL_THROUGH_WOW_CHANGE_V1", change if not patch else None, "percentage_point", lineage, status=ResultValueStatus.NOT_APPLICABLE if patch else ResultValueStatus.VALID_VALUE if change is not None else ResultValueStatus.MISSING))
            result = _contract(contract_id="RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY", result_id=f"{pipeline_run_id}:RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY", run=run, pipeline_run_id=pipeline_run_id, context_id=self.context_id, fields=fields, generated_at=generated_at, inputs=lineage, mappings={}, rules={"BR_APOLLO_PRODUCT_FILTER_MAPPING": "1.0.0"}, product=product)
            warnings = _persist(result, pipeline_id=self.pipeline_id, store_id=INVENTORY_STORE_ID, asset_id="STORE_ASSET_WEEKLY_INVENTORY_PRODUCT_SELL_THROUGH", store=self.metric_store, product=product)
            return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id, "product": product}, lineage, PipelineExecutionStatus.COMPLETED_WITH_WARNING if warnings else PipelineExecutionStatus.COMPLETED, warnings, produced_result_contract_reference=f"result-contract://{result.result_id}", lineage_references=lineage, result_contract=result)
        except Stage3AError as exc:
            return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id}, (), PipelineExecutionStatus.BLOCKED, error_code=exc.code, error_message=str(exc))


class CustomerChangeAnalysisExecutor:
    """Conditional explanation executor; it never changes an upstream sell-through result."""

    pipeline_id = "PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"
    dataset_id = "DS_AD_PRODUCT_CUSTOMER_DELIVERY_CHANGE_ANALYSIS"
    context_id = "CTX_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"

    def __init__(self, acquisition_runtime: AcquisitionRuntime) -> None: self.acquisition_runtime = acquisition_runtime

    def execute(self, *, run: RuntimeRun, pipeline_run_id: str, product: str, rules: LocalRuleBindings, trigger_contract: Stage3CResultContractInstance | None, current_key: BusinessKey | None, comparison_key: BusinessKey | None, generated_at: str) -> PipelineExecutionResult:
        try:
            if not product or product == "not_applicable": raise Stage3AError("STAGE3C_PRODUCT_REQUIRED", "Explicit product is required")
            rules.require("BR_APOLLO_PRODUCT_FILTER_MAPPING")
            route = rules.product_route_by_name.get(product)
            config = rules.customer_analysis_by_product.get(product)
            if route not in {"patch", "non_patch", "brand_moment"} or not isinstance(config, Mapping) or not isinstance(config.get("output_limit"), int) or config["output_limit"] < 1:
                return self._omitted(run, pipeline_run_id, product, "STAGE3C_CUSTOMER_MAPPING_INVALID")
            threshold = Decimal(str(config.get("trigger_threshold_pp", "10")))
            if trigger_contract is None: raise ResultContractError("STAGE3C_TRIGGER_CONTRACT_MISSING", "Validated sell-through trigger Contract is required")
            period = f"{run.context.values['reporting_period_start_date']}..{run.context.values['reporting_period_end_date']}"
            if route == "brand_moment":
                _upstream(trigger_contract, "RC_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY", "CTX_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY", period)
                field_id = "sell_through_wow_change_pp"
            else:
                _upstream(trigger_contract, "RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY", "CTX_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY", period, product)
                field_id = "patch_brand_sell_through_wow_change_pp" if route == "patch" else "non_patch_product_brand_sell_through_wow_change_pp"
            trigger = trigger_contract.field(field_id)
            if trigger.value is None or trigger.value_status is not ResultValueStatus.VALID_VALUE:
                return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id, "product": product}, (), PipelineExecutionStatus.COMPLETED, produced_result_contract_reference="normal-omission://trigger-not-met")
            if abs(trigger.value) < threshold:
                return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id, "product": product}, (), PipelineExecutionStatus.COMPLETED, produced_result_contract_reference="normal-omission://trigger-not-met")
            if current_key is None or comparison_key is None: raise Stage3AError("STAGE3C_CUSTOMER_INPUT_MISSING", "Triggered analysis requires both explicit period inputs")
            for key, role in ((current_key, "current"), (comparison_key, "comparison")):
                if key.workflow_run_id != run.context.workflow_run_id or key.dataset_id != self.dataset_id or key.period_role != role or key.product_parameter != product: raise Stage3AError("STAGE3C_CUSTOMER_INPUT_KEY_INVALID", "Customer input key is not exact")
            current_entry, comparison_entry = run.run_input_manifest.get_entry(current_key), run.run_input_manifest.get_entry(comparison_key)
            current, comparison = _rows(self.acquisition_runtime.consume_bound_input(run, current_key)), _rows(self.acquisition_runtime.consume_bound_input(run, comparison_key))
            if not current or not comparison:
                return self._omitted(run, pipeline_run_id, product, "STAGE3C_CUSTOMER_RAW_QUERY_ZERO_ROWS")
            scenario = "positive_sell_through_change" if trigger.value > 0 else "negative_sell_through_change"
            records = self._compare(current, comparison, scenario, int(config["output_limit"]))
            lineage = (f"result-contract://{trigger_contract.result_id}", current_entry.local_input_reference, comparison_entry.local_input_reference, "policy-evaluated://POLICY_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS_V1")
            context = {"target_ad_product_name": product, "analysis_scenario": scenario, "current_period_start_date": str(run.context.values["current_period_start_date"]), "current_period_end_date": str(run.context.values["current_period_end_date"]), "comparison_period_start_date": str(run.context.values["comparison_period_start_date"]), "comparison_period_end_date": str(run.context.values["comparison_period_end_date"]), "trigger_sell_through_wow_change_pp": trigger.value, "applied_trigger_threshold_pp": threshold, "selection_basis": "current_period_impression_count" if scenario.startswith("positive") else "comparison_period_minus_current_period_impression_count", "applied_materiality_threshold_count": Decimal("1000000"), "applied_output_limit": int(config["output_limit"])}
            result = _contract(contract_id="RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS", result_id=f"{pipeline_run_id}:RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS", run=run, pipeline_run_id=pipeline_run_id, context_id=self.context_id, fields=(), generated_at=generated_at, inputs=lineage, mappings={"MAP_ADVERTISING_APOLLO_PRODUCT_CUSTOMER_DELIVERY_CHANGE_V1": "1.0.0"}, rules={"POLICY_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS_V1": "1.0.0"}, record_set=tuple(records), product=product, context_values=context)
            return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id, "product": product}, (current_entry.local_input_reference, comparison_entry.local_input_reference), PipelineExecutionStatus.COMPLETED, produced_result_contract_reference=f"result-contract://{result.result_id}", lineage_references=lineage, result_contract=result)
        except AcquisitionError:
            return self._omitted(run, pipeline_run_id, product, "STAGE3C_CUSTOMER_QUERY_FAILURE")
        except (Stage3AError, KeyError) as exc:
            return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id, "product": product}, (), PipelineExecutionStatus.BLOCKED, error_code=getattr(exc, "code", "STAGE3C_EXECUTION_BLOCKED"), error_message=str(exc))

    def _omitted(self, run: RuntimeRun, pipeline_run_id: str, product: str, code: str) -> PipelineExecutionResult:
        return PipelineExecutionResult(run.context.workflow_run_id, self.pipeline_id, pipeline_run_id, {"business_context_id": self.context_id, "product": product}, (), PipelineExecutionStatus.COMPLETED_WITH_WARNING, (ExecutionWarning(code, "Target product customer analysis omitted; owner notification required"),), produced_result_contract_reference="omitted://source-or-routing-exception")

    @staticmethod
    def _compare(current: list[dict[str, Any]], comparison: list[dict[str, Any]], scenario: str, output_limit: int) -> list[Mapping[str, Any]]:
        def eligible(rows: list[dict[str, Any]]) -> dict[str, tuple[str, Decimal]]:
            values: dict[str, tuple[str, Decimal]] = {}
            duplicates: set[str] = set()
            for row in rows:
                customer_id, name = str(row.get("客户ID", "")).strip(), str(row.get("客户名", "")).strip()
                if not customer_id or not name: continue
                try: amount = _decimal(row.get("曝光量"), "impression_count")
                except DatasetValidationError: continue
                if amount < 0: continue
                if customer_id in values: duplicates.add(customer_id)
                values[customer_id] = (name, amount)
            return {key: value for key, value in values.items() if key not in duplicates}
        before, after = eligible(comparison), eligible(current)
        records = []
        for customer_id in sorted(set(before) | set(after)):
            current_item = after.get(customer_id)
            previous_item = before.get(customer_id)
            name = (current_item or previous_item or ("", Decimal(0)))[0]
            current_value = current_item[1] if current_item is not None else Decimal(0)
            previous_value = previous_item[1] if previous_item is not None else Decimal(0)
            if current_item is not None and previous_item is not None and current_item[0] != previous_item[0]:
                continue
            ranking = current_value if scenario == "positive_sell_through_change" else previous_value - current_value
            if (scenario == "negative_sell_through_change" and previous_value <= current_value) or ranking < Decimal("1000000"):
                continue
            records.append({"customer_id": customer_id, "customer_name": name, "current_period_impression_count": current_value, "comparison_period_impression_count": previous_value, "impression_change_count": current_value - previous_value, "ranking_measure": ranking})
        records.sort(key=lambda item: (-item["ranking_measure"], item["customer_id"]))
        return [{**item, "customer_rank": index} for index, item in enumerate(records[:output_limit], start=1)]
