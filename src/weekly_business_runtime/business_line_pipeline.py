"""Smart Speaker and Fast Version Revenue business-execution slices."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from weekly_acquisition_runtime.contracts import BusinessKey
from weekly_acquisition_runtime.errors import AcquisitionError
from weekly_acquisition_runtime.runtime import AcquisitionRuntime, RuntimeRun

from .assets import _read_yaml, _require_one
from .ctv_metrics import RevenueExecutionContext, validate_revenue_context
from .errors import (
    AssetContractError,
    DatasetValidationError,
    MetricStoreError,
    ResultContractError,
    Stage3AError,
)
from .models import (
    CtvResultContractInstance,
    ExecutionWarning,
    PipelineExecutionResult,
    PipelineExecutionStatus,
    ResultFieldValue,
    ResultValueStatus,
)
from .store import MetricStorePort, MetricStoreRecord, StoreReadKey

WORKFLOW_ID = "WF_WEEKLY_BUSINESS_REPORT"
DATASET_ID = "DS_REVENUE_APOLLO_BUSINESS_LINE_SUMMARY"
MAPPING_ID = "MAP_REVENUE_APOLLO_BUSINESS_LINE_SUMMARY_V1"
STORE_ID = "STORE_WEEKLY_REVENUE_HISTORICAL"
REPORT_MODE_RULE_ID = "BR_WEEKLY_REVENUE_REPORT_MODE_SELECTION_V1"
QTD_RULE_ID = "BR_REVENUE_QTD_HISTORY_CARRY_FORWARD_ELIGIBILITY_V1"


@dataclass(frozen=True)
class BusinessLineProfile:
    pipeline_id: str
    business_line: str
    business_context_id: str
    result_contract_id: str
    store_asset_id: str
    weekly_variant_id: str
    qtd_variant_id: str
    wow_variant_id: str

    @property
    def variant_ids(self) -> tuple[str, str, str]:
        return self.weekly_variant_id, self.qtd_variant_id, self.wow_variant_id


SMART_SPEAKER_PROFILE = BusinessLineProfile(
    "PL_REVENUE_SMART_SPEAKER_WEEKLY",
    "Smart Speaker",
    "CTX_REVENUE_SMART_SPEAKER_WEEKLY",
    "RC_REVENUE_SMART_SPEAKER_WEEKLY",
    "STORE_ASSET_WEEKLY_REVENUE_SMART_SPEAKER",
    "MV_REVENUE_SMART_SPEAKER_WEEKLY_EXECUTED_V1",
    "MV_REVENUE_SMART_SPEAKER_QTD_EXECUTED_V1",
    "MV_REVENUE_SMART_SPEAKER_WEEKLY_EXECUTED_WOW_V1",
)
FAST_VERSION_PROFILE = BusinessLineProfile(
    "PL_REVENUE_FAST_VERSION_WEEKLY",
    "Fast Version",
    "CTX_REVENUE_FAST_VERSION_WEEKLY",
    "RC_REVENUE_FAST_VERSION_WEEKLY",
    "STORE_ASSET_WEEKLY_REVENUE_FAST_VERSION",
    "MV_REVENUE_FAST_VERSION_WEEKLY_EXECUTED_V1",
    "MV_REVENUE_FAST_VERSION_QTD_EXECUTED_V1",
    "MV_REVENUE_FAST_VERSION_WEEKLY_EXECUTED_WOW_V1",
)


@dataclass(frozen=True)
class BusinessLineAssetBundle:
    repository_root: Path
    profile: BusinessLineProfile
    dataset: Mapping[str, Any]
    mapping: Mapping[str, Any]
    pipeline: Mapping[str, Any]
    result_contract: Mapping[str, Any]
    metric_variants: Mapping[str, Mapping[str, Any]]
    business_rules: Mapping[str, Mapping[str, Any]]

    @classmethod
    def load(
        cls, repository_root: Path, profile: BusinessLineProfile
    ) -> "BusinessLineAssetBundle":
        root = repository_root.resolve()
        assets = root / "phase1_5" / "assets"
        inventory = _read_yaml(assets / "datasets" / "dataset_inventory.yaml")
        dataset = _require_one(
            inventory.get("datasets"), "dataset_id", DATASET_ID, "Dataset Inventory"
        )
        mapping = _read_yaml(
            assets / "field_mappings" / "MAP_REVENUE_APOLLO_BUSINESS_LINE_SUMMARY_V1.yaml"
        )
        registry = _read_yaml(assets / "pipelines" / "pipeline_registry.yaml")
        pipeline = _require_one(
            registry.get("pipelines"), "pipeline_id", profile.pipeline_id, "Pipeline Registry"
        )
        result_contract = _read_yaml(
            assets / "result_contracts" / f"{profile.result_contract_id}.yaml"
        )
        library = _read_yaml(
            assets / "metrics" / "metric_library_revenue_technical_ctv_v1.yaml"
        )
        variants = {
            item["metric_variant_id"]: item
            for item in library.get("metric_variants", [])
            if isinstance(item, dict)
            and item.get("metric_variant_id") in profile.variant_ids
        }
        rules = {
            rule_id: _read_yaml(assets / "business_rules" / f"{rule_id}.yaml")
            for rule_id in (REPORT_MODE_RULE_ID, QTD_RULE_ID)
        }
        bundle = cls(
            root, profile, dataset, mapping, pipeline, result_contract, variants, rules
        )
        bundle.validate_composition()
        return bundle

    def validate_composition(self) -> None:
        execution = self.pipeline.get("execution", {})
        outputs = self.pipeline.get("outputs", {})
        if (
            self.dataset.get("dataset_id") != DATASET_ID
            or self.mapping.get("mapping_profile_id") != MAPPING_ID
            or self.mapping.get("dataset_id") != DATASET_ID
            or self.pipeline.get("business_line") != self.profile.business_line
            or self.pipeline.get("business_context_id") != self.profile.business_context_id
            or execution.get("mapping_profile_ids") != [MAPPING_ID]
            or tuple(execution.get("ordered_rule_set_ids", ()))
            != (REPORT_MODE_RULE_ID, QTD_RULE_ID)
            or tuple(execution.get("metric_variant_ids", ())) != self.profile.variant_ids
            or outputs.get("result_contract_ids") != [self.profile.result_contract_id]
            or outputs.get("metric_result_store_id") != STORE_ID
            or outputs.get("metric_result_store_asset_id") != self.profile.store_asset_id
        ):
            raise AssetContractError(
                "BUSINESS_LINE_ASSET_MISMATCH", "Revenue business-line composition mismatch"
            )
        if (
            self.result_contract.get("result_contract_id")
            != self.profile.result_contract_id
            or set(self.metric_variants) != set(self.profile.variant_ids)
        ):
            raise AssetContractError(
                "BUSINESS_LINE_ASSET_MISMATCH", "Metric/Result Contract composition mismatch"
            )
        bindings = {
            item.get("source_metric_variant_id")
            for item in self.result_contract.get("contract_fields", [])
            if isinstance(item, dict)
        }
        if bindings != set(self.profile.variant_ids):
            raise AssetContractError(
                "BUSINESS_LINE_ASSET_MISMATCH", "Result field bindings are incomplete"
            )
        new_field_policy = self.mapping.get("validation", {}).get(
            "new_raw_field_policy", {}
        )
        if (
            self.mapping.get("source_schema", {}).get("unknown_source_field_policy")
            != "notify_and_request_owner_confirmation_without_blocking"
            or self.mapping.get("validation", {}).get(
                "unknown_field_validation_required"
            )
            is not True
            or new_field_policy.get("notify_owner") is not True
            or new_field_policy.get("automatic_registration_allowed") is not False
            or new_field_policy.get("automatic_mapping_allowed") is not False
            or new_field_policy.get("block_confirmed_field_processing") is not False
        ):
            raise AssetContractError(
                "BUSINESS_LINE_UNKNOWN_FIELD_POLICY_MISMATCH",
                "Unknown source field handling does not match the registered Mapping",
            )

    @property
    def query_template(self) -> str:
        return str(self.pipeline["dataset_dependencies"][0]["query_template_parameter"])


def _load_weekly_amount(
    path: Path, assets: BusinessLineAssetBundle
) -> tuple[Decimal, tuple[ExecutionWarning, ...]]:
    entry = assets.mapping["field_mappings"][0]
    raw_field = entry["raw_field_name"]
    try:
        if path.suffix.lower() == ".csv":
            frame = pd.read_csv(path, dtype=object)
        elif path.suffix.lower() in {".xlsx", ".xls"}:
            frame = pd.read_excel(path, dtype=object)
        else:
            raise DatasetValidationError(
                "BUSINESS_LINE_DATASET_FORMAT_INVALID", "Input must be CSV or Excel"
            )
    except (OSError, ValueError, ImportError) as exc:
        raise DatasetValidationError(
            "BUSINESS_LINE_DATASET_UNREADABLE", "Cannot read bound local Dataset input"
        ) from exc
    frame.columns = [str(column).strip() for column in frame.columns]
    if len(frame.index) != 1:
        raise DatasetValidationError(
            "BUSINESS_LINE_RESULT_SHAPE_INVALID", "Business-line query Result must have exactly one row"
        )
    if raw_field not in frame.columns:
        raise DatasetValidationError(
            "BUSINESS_LINE_REQUIRED_MAPPING_MISSING", "Mapped calibrated revenue field is missing"
        )
    registered_raw_fields = {
        str(item["raw_field_name"]).strip()
        for item in assets.mapping["raw_field_inventory"]
    }
    unknown_fields = tuple(
        sorted(set(frame.columns).difference(registered_raw_fields))
    )
    warnings = (
        (
            ExecutionWarning(
                "BUSINESS_LINE_UNKNOWN_SOURCE_FIELDS",
                "Owner notification required; unregistered source fields were ignored "
                "while confirmed fields continued: "
                + ", ".join(unknown_fields),
            ),
        )
        if unknown_fields
        else ()
    )
    raw_value = frame.iloc[0][raw_field]
    if raw_value is None or pd.isna(raw_value) or not str(raw_value).strip():
        raise DatasetValidationError(
            "BUSINESS_LINE_REVENUE_VALUE_INVALID", "Calibrated executed revenue is blank"
        )
    try:
        value = Decimal(str(raw_value).strip().replace(",", "")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(
            "BUSINESS_LINE_REVENUE_VALUE_INVALID", "Calibrated executed revenue is non-numeric"
        ) from exc
    if value <= 0:
        raise DatasetValidationError(
            "BUSINESS_LINE_REVENUE_VALUE_INVALID",
            "Metric and Result Contract require revenue greater than zero",
        )
    return value, warnings


class BusinessLineRevenuePipelineExecutor:
    """Execute either registered Apollo business-line Revenue slice."""

    def __init__(
        self,
        *,
        acquisition_runtime: AcquisitionRuntime,
        assets: BusinessLineAssetBundle,
        metric_store: MetricStorePort,
    ) -> None:
        self.acquisition_runtime = acquisition_runtime
        self.assets = assets
        self.profile = assets.profile
        self.metric_store = metric_store

    def execute(
        self,
        *,
        run: RuntimeRun,
        pipeline_run_id: str,
        current_input_key: BusinessKey,
        generated_at: str,
    ) -> PipelineExecutionResult:
        references: tuple[str, ...] = ()
        try:
            context = validate_revenue_context(run.context.values)
            self._validate_query_context(run.context.values)
            self._validate_identity(run, pipeline_run_id, current_input_key)
            entry = run.run_input_manifest.get_entry(current_input_key)
            self._validate_manifest(entry, context)
            path = self.acquisition_runtime.consume_bound_input(run, current_input_key)
            references = (entry.local_input_reference,)
            weekly, input_warnings = _load_weekly_amount(path, self.assets)
            previous_qtd, previous_weekly, history_lineage = self._read_history(context)
            if context.report_mode == "quarter_transition_week":
                qtd = weekly
                weekly_value = wow = None
                weekly_status = wow_status = ResultValueStatus.NOT_APPLICABLE
                warnings = list(input_warnings)
            else:
                if previous_qtd is None:
                    raise Stage3AError(
                        "BUSINESS_LINE_QTD_HISTORY_REQUIRED",
                        "Regular-week QTD calculation requires exact previous-week QTD history",
                    )
                qtd = previous_qtd + weekly
                weekly_value = weekly
                weekly_status = ResultValueStatus.VALID_VALUE
                warnings = list(input_warnings)
                if previous_weekly is None or previous_weekly <= 0:
                    wow = None
                    wow_status = ResultValueStatus.MISSING
                    warnings.append(
                        ExecutionWarning(
                            "BUSINESS_LINE_WOW_DENOMINATOR_INVALID",
                            "Previous-week Revenue is unavailable or non-positive; WoW remains missing",
                        )
                    )
                else:
                    wow = weekly / previous_weekly - Decimal("1")
                    wow_status = ResultValueStatus.VALID_VALUE
            if qtd <= 0:
                raise Stage3AError(
                    "BUSINESS_LINE_QTD_RESULT_INVALID", "QTD Revenue must be greater than zero"
                )
            lineage = (
                *references,
                *history_lineage,
                f"mapping-consumed://{MAPPING_ID}",
                f"rule-evaluated://{REPORT_MODE_RULE_ID}",
                f"rule-evaluated://{QTD_RULE_ID}",
            )
            values = {
                self.profile.qtd_variant_id: (qtd, ResultValueStatus.VALID_VALUE),
                self.profile.weekly_variant_id: (weekly_value, weekly_status),
                self.profile.wow_variant_id: (wow, wow_status),
            }
            result = self._assemble_result(
                run, pipeline_run_id, context, references, lineage, values, generated_at
            )
            store_lineage = self._persist(result, warnings)
            status = (
                PipelineExecutionStatus.COMPLETED_WITH_WARNING
                if warnings
                else PipelineExecutionStatus.COMPLETED
            )
            return PipelineExecutionResult(
                workflow_run_id=run.context.workflow_run_id,
                pipeline_id=self.profile.pipeline_id,
                pipeline_run_id=pipeline_run_id,
                business_context=self._business_context(context),
                input_binding_references=references,
                execution_status=status,
                warnings=tuple(warnings),
                produced_result_contract_reference=f"result-contract://{result.result_id}",
                lineage_references=(*lineage, *store_lineage),
                result_contract=result,
            )
        except (Stage3AError, AcquisitionError) as exc:
            code = exc.code if isinstance(exc, Stage3AError) else "BUSINESS_LINE_INPUT_BINDING_INVALID"
            return self._blocked(run, pipeline_run_id, references, code, str(exc))
        except Exception as exc:  # noqa: BLE001 - fail-closed Pipeline boundary
            return self._blocked(
                run, pipeline_run_id, references, "BUSINESS_LINE_UNEXPECTED_EXECUTION_ERROR", str(exc)
            )

    def _validate_identity(self, run, pipeline_run_id, key) -> None:
        if not pipeline_run_id.strip():
            raise Stage3AError("BUSINESS_LINE_PIPELINE_RUN_ID_INVALID", "pipeline_run_id is required")
        if (
            key.workflow_run_id != run.context.workflow_run_id
            or key.dataset_id != DATASET_ID
            or key.period_role != "current"
            or key.product_parameter != self.assets.query_template
        ):
            raise Stage3AError(
                "BUSINESS_LINE_INPUT_KEY_INVALID", "Manifest business key/query template mismatch"
            )

    @staticmethod
    def _validate_manifest(entry, context) -> None:
        if entry.source_report_date != context.workflow_reporting_date.isoformat():
            raise Stage3AError(
                "BUSINESS_LINE_SOURCE_REPORT_DATE_MISMATCH",
                "source_report_date must equal workflow_reporting_date",
            )
        if (
            entry.source_business_data_cutoff_date
            != context.current_revenue_cutoff_date.isoformat()
        ):
            raise Stage3AError(
                "BUSINESS_LINE_SOURCE_CUTOFF_MISMATCH",
                "source business cutoff must equal current_revenue_cutoff_date",
            )

    @staticmethod
    def _validate_query_context(values: Mapping[str, object]) -> None:
        try:
            period_start = date.fromisoformat(str(values["current_period_start_date"]))
            period_end = date.fromisoformat(str(values["current_period_end_date"]))
        except (KeyError, ValueError) as exc:
            raise Stage3AError(
                "BUSINESS_LINE_QUERY_PERIOD_INVALID",
                "Explicit current-period query dates are required",
            ) from exc
        if period_start > period_end:
            raise Stage3AError(
                "BUSINESS_LINE_QUERY_PERIOD_INVALID",
                "Explicit query period start must not be after period end",
            )

    def _read_history(self, context):
        if context.report_mode == "quarter_transition_week":
            return None, None, ()
        records = []
        lineage = []
        for variant_id in (
            self.profile.qtd_variant_id,
            self.profile.weekly_variant_id,
        ):
            key = StoreReadKey(
                STORE_ID,
                self.profile.store_asset_id,
                variant_id,
                context.expected_previous_revenue_workflow_reporting_date.isoformat(),
                self.profile.business_context_id,
            )
            record = self.metric_store.read_exact(key)
            self._validate_history_record(record, variant_id, context)
            records.append(record.value)
            lineage.append(f"metric-store://{record.result_id}")
        return records[0], records[1], tuple(lineage)

    def _validate_history_record(self, record, variant_id, context) -> None:
        if (
            record.store_id != STORE_ID
            or record.store_asset_id != self.profile.store_asset_id
            or record.business_context_id != self.profile.business_context_id
            or record.metric_variant_id != variant_id
            or record.validation_status != "passed"
            or record.value_status != "valid_value"
            or not isinstance(record.value, Decimal)
            or record.unit != "CNY_yuan"
            or record.numeric_semantics != "monetary_amount"
            or not record.lineage_references
        ):
            raise MetricStoreError(
                "STORE_HISTORICAL_RESULT_INELIGIBLE", "Historical business-line Result is ineligible"
            )
        cutoff = date.fromisoformat(record.current_revenue_cutoff_date)
        quarter = f"{cutoff.year}Q{((cutoff.month - 1) // 3) + 1}"
        if quarter != context.target_fiscal_quarter:
            raise MetricStoreError(
                "STORE_HISTORICAL_QUARTER_MISMATCH", "Cross-quarter QTD carry-forward is prohibited"
            )

    def _assemble_result(
        self, run, pipeline_run_id, context, references, lineage, values, generated_at
    ) -> CtvResultContractInstance:
        try:
            parsed = datetime.fromisoformat(generated_at)
        except ValueError as exc:
            raise ResultContractError(
                "BUSINESS_LINE_GENERATED_AT_INVALID", "generated_at must be ISO-8601"
            ) from exc
        if parsed.tzinfo is None:
            raise ResultContractError(
                "BUSINESS_LINE_GENERATED_AT_INVALID", "generated_at requires timezone"
            )
        fields = tuple(
            ResultFieldValue(
                field_id=contract["field_id"],
                metric_variant_id=contract["source_metric_variant_id"],
                value=values[contract["source_metric_variant_id"]][0],
                value_status=values[contract["source_metric_variant_id"]][1],
                unit=contract["numeric_constraints"]["unit"],
                lineage_references=lineage,
            )
            for contract in self.assets.result_contract["contract_fields"]
        )
        payload = [
            (field.field_id, None if field.value is None else str(field.value), field.value_status.value)
            for field in fields
        ]
        digest = hashlib.sha256(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        result = CtvResultContractInstance(
            result_contract_id=self.profile.result_contract_id,
            result_contract_version=str(self.assets.result_contract["contract_version"]),
            result_id=f"RESULT_{self.profile.pipeline_id}_{digest[:20]}",
            workflow_run_id=run.context.workflow_run_id,
            pipeline_run_id=pipeline_run_id,
            reporting_period=context.target_report_period,
            workflow_reporting_date=context.workflow_reporting_date.isoformat(),
            current_revenue_cutoff_date=context.current_revenue_cutoff_date.isoformat(),
            report_mode=context.report_mode,
            dataset_instance_ids=references,
            mapping_profile_versions={MAPPING_ID: str(self.assets.mapping["version"])},
            business_rule_versions={
                rule_id: str(self.assets.business_rules[rule_id]["version"])
                for rule_id in (REPORT_MODE_RULE_ID, QTD_RULE_ID)
            },
            metric_variant_versions={
                variant_id: str(self.assets.metric_variants[variant_id]["version"])
                for variant_id in self.profile.variant_ids
            },
            generated_at=generated_at,
            validation_status="passed",
            approval_status="not_applicable",
            fields=fields,
        )
        self._validate_result(result)
        return result

    def _validate_result(self, result) -> None:
        contracts = {
            item["field_id"]: item for item in self.assets.result_contract["contract_fields"]
        }
        if {field.field_id for field in result.fields} != set(contracts):
            raise ResultContractError(
                "BUSINESS_LINE_RESULT_FIELD_SET_MISMATCH", "Result field set mismatch"
            )
        for field in result.fields:
            contract = contracts[field.field_id]
            applicable = result.report_mode in contract["applicable_report_modes"]
            if field.value_status.value not in contract["value_status_allowed"]:
                raise ResultContractError(
                    "BUSINESS_LINE_RESULT_STATUS_INVALID", f"{field.field_id} value status invalid"
                )
            if not applicable and (
                field.value is not None
                or field.value_status is not ResultValueStatus.NOT_APPLICABLE
            ):
                raise ResultContractError(
                    "BUSINESS_LINE_RESULT_APPLICABILITY_INVALID",
                    f"{field.field_id} must be blank/not_applicable",
                )
            if applicable and field.value is None and not contract["nullable"]:
                raise ResultContractError(
                    "BUSINESS_LINE_RESULT_NULL_INVALID", f"{field.field_id} is not nullable"
                )

    def _persist(self, result, warnings) -> tuple[str, ...]:
        persisted_ids = {self.profile.qtd_variant_id}
        if result.report_mode == "regular_week":
            persisted_ids.add(self.profile.weekly_variant_id)
        contracts = {
            item["source_metric_variant_id"]: item
            for item in self.assets.result_contract["contract_fields"]
        }
        fields = [field for field in result.fields if field.metric_variant_id in persisted_ids]
        records = tuple(
            MetricStoreRecord(
                result_id=f"{result.result_id}:{field.metric_variant_id}",
                workflow_id=WORKFLOW_ID,
                workflow_run_id=result.workflow_run_id,
                pipeline_id=self.profile.pipeline_id,
                pipeline_run_id=result.pipeline_run_id,
                store_id=STORE_ID,
                store_asset_id=self.profile.store_asset_id,
                metric_variant_id=field.metric_variant_id,
                metric_variant_version=result.metric_variant_versions[field.metric_variant_id],
                workflow_reporting_date=result.workflow_reporting_date,
                current_revenue_cutoff_date=result.current_revenue_cutoff_date,
                business_context_id=self.profile.business_context_id,
                reporting_period=result.reporting_period,
                value=field.value,
                value_status=field.value_status.value,
                numeric_semantics=contracts[field.metric_variant_id]["numeric_constraints"][
                    "numeric_semantics"
                ],
                unit=field.unit,
                precision=contracts[field.metric_variant_id]["numeric_constraints"]["precision"],
                validation_status="passed",
                generated_at=result.generated_at,
                lineage_references=field.lineage_references,
            )
            for field in fields
        )
        try:
            plan = self.metric_store.preflight_write(records)
            receipt = self.metric_store.write_validated(plan)
            if not self.metric_store.verify_write(receipt):
                raise MetricStoreError(
                    "STORE_WRITE_VERIFICATION_FAILED", "Business-line Store write was not verified"
                )
            return tuple(f"metric-store://{item}" for item in receipt.result_ids)
        except MetricStoreError as exc:
            warnings.append(ExecutionWarning(exc.code, str(exc)))
            return ()

    def _business_context(self, context: RevenueExecutionContext) -> dict[str, object]:
        return {
            "business_context_id": self.profile.business_context_id,
            "target_business_line": self.profile.business_line,
            "target_report_period": context.target_report_period,
            "target_fiscal_quarter": context.target_fiscal_quarter,
            "workflow_reporting_date": context.workflow_reporting_date.isoformat(),
            "current_revenue_cutoff_date": context.current_revenue_cutoff_date.isoformat(),
            "report_mode": context.report_mode,
        }

    def _blocked(self, run, pipeline_run_id, references, code, message):
        return PipelineExecutionResult(
            workflow_run_id=run.context.workflow_run_id,
            pipeline_id=self.profile.pipeline_id,
            pipeline_run_id=pipeline_run_id,
            business_context={"business_context_id": self.profile.business_context_id},
            input_binding_references=references,
            execution_status=PipelineExecutionStatus.BLOCKED,
            error_code=code,
            error_message=message,
            lineage_references=references,
        )
