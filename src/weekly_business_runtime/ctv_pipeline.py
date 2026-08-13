"""Minimal deterministic executor for PL_REVENUE_CTV_WEEKLY only."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from weekly_acquisition_runtime.contracts import BusinessKey
from weekly_acquisition_runtime.errors import AcquisitionError
from weekly_acquisition_runtime.runtime import AcquisitionRuntime, RuntimeRun

from .assets import (
    BUSINESS_CONTEXT_ID,
    CTV_VARIANT_IDS,
    CURRENT_DATASET_ID,
    CURRENT_MAPPING_ID,
    PIPELINE_ID,
    PREVIOUS_QUARTER_FALLBACK_DATASET_ID,
    PREVIOUS_QUARTER_FALLBACK_MAPPING_ID,
    PREVIOUS_QUARTER_RULE_ID,
    PRIOR_DATASET_ID,
    PRIOR_MAPPING_ID,
    STORE_ASSET_ID,
    STORE_ID,
    WORKFLOW_ID,
    CtvAssetBundle,
)
from .ctv_dataset import CtvDatasetLoader
from .ctv_metrics import (
    calculate_ctv_metrics,
    derive_prior_year_date,
    validate_revenue_context,
)
from .ctv_result import CtvResultContractAssembler
from .errors import DatasetValidationError, MetricStoreError, Stage3AError
from .models import (
    ExecutionWarning,
    PipelineExecutionResult,
    PipelineExecutionStatus,
    ResultFieldValue,
    ResultValueStatus,
)
from .store import MetricStorePort, MetricStoreRecord, StoreReadKey

REPORT_MODE_RULE_ID = "BR_WEEKLY_REVENUE_REPORT_MODE_SELECTION_V1"
PRIOR_YEAR_RULE_ID = "BR_REVENUE_PRIOR_YEAR_COMPARABLE_SOURCE_SELECTION_V1"


class CtvPipelineExecutor:
    """Execute one explicit CTV Pipeline Run over already-bound local inputs."""

    def __init__(
        self,
        *,
        acquisition_runtime: AcquisitionRuntime,
        assets: CtvAssetBundle,
        metric_store: MetricStorePort,
    ) -> None:
        self.acquisition_runtime = acquisition_runtime
        self.assets = assets
        self.metric_store = metric_store
        self.loader = CtvDatasetLoader(assets)
        self.assembler = CtvResultContractAssembler(assets)

    def execute(
        self,
        *,
        run: RuntimeRun,
        pipeline_run_id: str,
        current_input_key: BusinessKey,
        prior_year_input_key: BusinessKey,
        generated_at: str,
        previous_quarter_primary_input_key: BusinessKey | None = None,
        previous_quarter_fallback_input_key: BusinessKey | None = None,
    ) -> PipelineExecutionResult:
        input_references: tuple[str, ...] = ()
        try:
            self._validate_identity(run, pipeline_run_id, current_input_key, prior_year_input_key)
            context = validate_revenue_context(run.context.values)
            current_entry = run.run_input_manifest.get_entry(current_input_key)
            self._validate_current_manifest_context(current_entry, context)
            current_path = self.acquisition_runtime.consume_bound_input(run, current_input_key)
            input_references = (current_entry.local_input_reference,)
            current = self.loader.load_current(current_path, current_entry.local_input_reference)
            consumed_mapping_ids = [CURRENT_MAPPING_ID]
            evaluated_rule_ids = [REPORT_MODE_RULE_ID, PRIOR_YEAR_RULE_ID]
            rule_lineage = self._rule_authority_lineage()
            rule_lineage.append(f"rule-evaluated://{REPORT_MODE_RULE_ID}")
            if context.report_mode == "quarter_transition_week":
                selected = self._select_previous_quarter_source(
                    run,
                    context.target_previous_calendar_quarter,
                    previous_quarter_primary_input_key,
                    previous_quarter_fallback_input_key,
                )
                input_references = (*input_references, selected.input_reference)
                consumed_mapping_ids.append(
                    PRIOR_MAPPING_ID
                    if selected.selected_source_role == "primary"
                    else PREVIOUS_QUARTER_FALLBACK_MAPPING_ID
                )
                evaluated_rule_ids.append(PREVIOUS_QUARTER_RULE_ID)
                rule_lineage.extend(
                    (
                        f"rule-evaluated://{PREVIOUS_QUARTER_RULE_ID}",
                        f"source-consumed://previous_quarter_{selected.selected_source_role}/"
                        f"{selected.input_reference}",
                    )
                )
            target_prior_date = derive_prior_year_date(context)
            prior_value = Decimal("0")
            prior_business_cutoff: date | None = None
            prior_warnings: list[ExecutionWarning] = []
            rule_lineage.append(f"rule-evaluated://{PRIOR_YEAR_RULE_ID}")
            try:
                prior_entry = run.run_input_manifest.get_entry(prior_year_input_key)
                prior_path = self.acquisition_runtime.consume_bound_input(run, prior_year_input_key)
                if prior_entry.source_report_date != target_prior_date.isoformat():
                    raise DatasetValidationError(
                        "CTV_PRIOR_INPUT_DATE_MISMATCH",
                        "Prior input source_report_date does not match the exact frozen source date",
                    )
                try:
                    prior_business_cutoff = date.fromisoformat(
                        prior_entry.source_business_data_cutoff_date
                    )
                except ValueError as exc:
                    raise DatasetValidationError(
                        "CTV_PRIOR_BUSINESS_CUTOFF_INVALID",
                        "Prior input business cutoff must be a valid date",
                    ) from exc
                prior = self.loader.load_prior_comparable(
                    prior_path,
                    target_quarter=f"{context.workflow_year - 1}{context.target_fiscal_quarter[4:]}",
                    input_reference=prior_entry.local_input_reference,
                )
                prior_value = prior.value
                input_references = (*input_references, prior_entry.local_input_reference)
                consumed_mapping_ids.append(PRIOR_MAPPING_ID)
                rule_lineage.append(
                    f"source-consumed://prior_year_comparable/{prior_entry.local_input_reference}"
                )
            except (AcquisitionError, DatasetValidationError) as exc:
                prior_warnings.append(
                    ExecutionWarning(
                        "CTV_PRIOR_YEAR_INPUT_UNAVAILABLE",
                        f"Prior-year input was not eligible for YoY: {exc}",
                    )
                )
            historical_lineage = self._read_required_history(context)
            calculation = calculate_ctv_metrics(
                current.frame,
                prior_year_performance=prior_value,
                prior_year_business_cutoff_date=prior_business_cutoff,
                context=context,
            )
            field_lineage = (*input_references, *historical_lineage, *rule_lineage)
            result = self.assembler.assemble(
                workflow_run_id=run.context.workflow_run_id,
                pipeline_run_id=pipeline_run_id,
                context=context,
                dataset_instance_ids=input_references,
                consumed_mapping_profile_ids=tuple(dict.fromkeys(consumed_mapping_ids)),
                evaluated_rule_ids=tuple(dict.fromkeys(evaluated_rule_ids)),
                field_lineage_references=field_lineage,
                calculation=calculation,
                generated_at=generated_at,
            )
            warnings = list(current.warnings) + prior_warnings + list(calculation.warnings)
            store_lineage = self._persist_validated_result(result.fields, result, warnings)
            lineage = (*field_lineage, *store_lineage)
            status = (
                PipelineExecutionStatus.COMPLETED_WITH_WARNING
                if warnings
                else PipelineExecutionStatus.COMPLETED
            )
            return PipelineExecutionResult(
                workflow_run_id=run.context.workflow_run_id,
                pipeline_id=PIPELINE_ID,
                pipeline_run_id=pipeline_run_id,
                business_context=self._business_context(context),
                input_binding_references=input_references,
                execution_status=status,
                warnings=tuple(warnings),
                produced_result_contract_reference=f"result-contract://{result.result_id}",
                lineage_references=lineage,
                result_contract=result,
            )
        except (Stage3AError, AcquisitionError) as exc:
            code = exc.code if isinstance(exc, Stage3AError) else "CTV_INPUT_BINDING_INVALID"
            return PipelineExecutionResult(
                workflow_run_id=run.context.workflow_run_id,
                pipeline_id=PIPELINE_ID,
                pipeline_run_id=pipeline_run_id,
                business_context={"business_context_id": BUSINESS_CONTEXT_ID},
                input_binding_references=input_references,
                execution_status=PipelineExecutionStatus.BLOCKED,
                error_code=code,
                error_message=str(exc),
                lineage_references=input_references,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed Pipeline boundary
            return PipelineExecutionResult(
                workflow_run_id=run.context.workflow_run_id,
                pipeline_id=PIPELINE_ID,
                pipeline_run_id=pipeline_run_id,
                business_context={"business_context_id": BUSINESS_CONTEXT_ID},
                input_binding_references=input_references,
                execution_status=PipelineExecutionStatus.BLOCKED,
                error_code="CTV_UNEXPECTED_EXECUTION_ERROR",
                error_message=str(exc),
                lineage_references=input_references,
            )

    @staticmethod
    def _validate_identity(
        run: RuntimeRun,
        pipeline_run_id: str,
        current_key: BusinessKey,
        prior_key: BusinessKey,
    ) -> None:
        if not pipeline_run_id.strip():
            raise Stage3AError("CTV_PIPELINE_RUN_ID_INVALID", "pipeline_run_id is required")
        if current_key.workflow_run_id != run.context.workflow_run_id:
            raise Stage3AError("CTV_CONTEXT_MISMATCH", "Current input belongs to another Run")
        if prior_key.workflow_run_id != run.context.workflow_run_id:
            raise Stage3AError("CTV_CONTEXT_MISMATCH", "Prior input belongs to another Run")
        if current_key.dataset_id != CURRENT_DATASET_ID or current_key.period_role != "current":
            raise Stage3AError("CTV_CURRENT_INPUT_KEY_INVALID", "Current CTV business key mismatch")
        if prior_key.dataset_id != PRIOR_DATASET_ID or prior_key.period_role != "prior_year_comparable":
            raise Stage3AError("CTV_PRIOR_INPUT_KEY_INVALID", "Prior-year business key mismatch")
        if current_key.product_parameter != "not_applicable":
            raise Stage3AError("CTV_PRODUCT_CONTEXT_INVALID", "CTV input is not product-scoped")
        if prior_key.product_parameter != "not_applicable":
            raise Stage3AError("CTV_PRODUCT_CONTEXT_INVALID", "CTV prior input is not product-scoped")

    @staticmethod
    def _validate_current_manifest_context(current_entry, context) -> None:
        if current_entry.source_report_date != context.workflow_reporting_date.isoformat():
            raise Stage3AError(
                "CTV_CURRENT_SOURCE_REPORT_DATE_MISMATCH",
                "Current CTV source_report_date must equal workflow_reporting_date",
            )
        if current_entry.source_business_data_cutoff_date != context.current_revenue_cutoff_date.isoformat():
            raise Stage3AError(
                "CTV_CURRENT_CUTOFF_MISMATCH",
                "Current input cutoff must equal locked current_revenue_cutoff_date",
            )

    def _read_required_history(self, context) -> tuple[str, ...]:
        if context.report_mode != "regular_week":
            return ()
        lineage: list[str] = []
        for variant_id in CTV_VARIANT_IDS:
            key = StoreReadKey(
                STORE_ID,
                STORE_ASSET_ID,
                variant_id,
                context.expected_previous_revenue_workflow_reporting_date.isoformat(),
                BUSINESS_CONTEXT_ID,
            )
            record = self.metric_store.read_exact(key)
            self._validate_historical_record(record, key)
            lineage.append(f"metric-store://{record.result_id}")
        return tuple(lineage)

    def _validate_historical_record(
        self, record: MetricStoreRecord, key: StoreReadKey
    ) -> None:
        field_contracts = {
            item["source_metric_variant_id"]: item
            for item in self.assets.result_contract["contract_fields"]
        }
        expected = field_contracts[key.metric_variant_id]
        constraints = expected["numeric_constraints"]
        if any(
            (
                record.read_key != key,
                record.store_id != STORE_ID,
                record.store_asset_id != STORE_ASSET_ID,
                record.business_context_id != BUSINESS_CONTEXT_ID,
                record.metric_variant_id != key.metric_variant_id,
                record.validation_status != "passed",
                record.value_status != "valid_value",
                not isinstance(record.value, Decimal),
                record.numeric_semantics != constraints["numeric_semantics"],
                record.unit != constraints["unit"],
                record.precision != constraints["precision"],
                not record.lineage_references,
            )
        ):
            raise MetricStoreError(
                "STORE_HISTORICAL_RESULT_INELIGIBLE",
                "Exact historical Metric Result does not satisfy frozen consumption semantics",
            )

    def _persist_validated_result(
        self,
        fields: tuple[ResultFieldValue, ...],
        result,
        warnings: list[ExecutionWarning],
    ) -> tuple[str, ...]:
        contracts = {
            field["field_id"]: field for field in self.assets.result_contract["contract_fields"]
        }
        if any(
            field.value is None or field.value_status is not ResultValueStatus.VALID_VALUE
            for field in fields
        ):
            warnings.append(
                ExecutionWarning(
                    "STORE_WRITE_SET_INCOMPLETE",
                    "Revenue Store completeness gate blocked partial Result Contract persistence",
                )
            )
            return ()
        records: list[MetricStoreRecord] = []
        for field in fields:
            contract = contracts[field.field_id]
            records.append(MetricStoreRecord(
                result_id=f"{result.result_id}:{field.metric_variant_id}",
                workflow_id=WORKFLOW_ID,
                workflow_run_id=result.workflow_run_id,
                pipeline_id=PIPELINE_ID,
                pipeline_run_id=result.pipeline_run_id,
                store_id=STORE_ID,
                store_asset_id=STORE_ASSET_ID,
                metric_variant_id=field.metric_variant_id,
                metric_variant_version=result.metric_variant_versions[field.metric_variant_id],
                workflow_reporting_date=result.workflow_reporting_date,
                current_revenue_cutoff_date=result.current_revenue_cutoff_date,
                business_context_id=BUSINESS_CONTEXT_ID,
                reporting_period=result.reporting_period,
                value=field.value,
                value_status=field.value_status.value,
                numeric_semantics=contract["numeric_constraints"]["numeric_semantics"],
                unit=field.unit,
                precision=contract["numeric_constraints"]["precision"],
                validation_status=result.validation_status,
                generated_at=result.generated_at,
                lineage_references=field.lineage_references,
            ))
        try:
            plan = self.metric_store.preflight_write(tuple(records))
            receipt = self.metric_store.write_validated(plan)
            if not self.metric_store.verify_write(receipt):
                raise MetricStoreError(
                    "STORE_WRITE_VERIFICATION_FAILED",
                    "Post-write verification did not verify the complete Result Contract set",
                )
            return tuple(f"metric-store://{result_id}" for result_id in receipt.result_ids)
        except MetricStoreError as exc:
            warnings.append(ExecutionWarning(exc.code, str(exc)))
            return ()

    def _select_previous_quarter_source(
        self,
        run: RuntimeRun,
        target_quarter: str,
        primary_key: BusinessKey | None,
        fallback_key: BusinessKey | None,
    ):
        primary_error: Exception | None = None
        if primary_key is not None:
            self._validate_previous_quarter_key(
                run, primary_key, PRIOR_DATASET_ID, "primary"
            )
            try:
                entry = run.run_input_manifest.get_entry(primary_key)
                path = self.acquisition_runtime.consume_bound_input(run, primary_key)
                return self.loader.load_previous_quarter_primary(
                    path,
                    target_quarter=target_quarter,
                    input_reference=entry.local_input_reference,
                )
            except (AcquisitionError, DatasetValidationError) as exc:
                primary_error = exc
        if fallback_key is not None:
            self._validate_previous_quarter_key(
                run, fallback_key, PREVIOUS_QUARTER_FALLBACK_DATASET_ID, "fallback"
            )
            try:
                entry = run.run_input_manifest.get_entry(fallback_key)
                path = self.acquisition_runtime.consume_bound_input(run, fallback_key)
                return self.loader.load_previous_quarter_fallback(
                    path,
                    target_quarter=target_quarter,
                    input_reference=entry.local_input_reference,
                )
            except (AcquisitionError, DatasetValidationError) as exc:
                raise Stage3AError(
                    "CTV_PREVIOUS_QUARTER_SOURCE_UNAVAILABLE",
                    "Neither explicitly bound previous-quarter source is eligible",
                ) from exc
        raise Stage3AError(
            "CTV_PREVIOUS_QUARTER_SOURCE_UNAVAILABLE",
            "No eligible explicitly bound previous-quarter source exists"
            + (f": {primary_error}" if primary_error else ""),
        )

    @staticmethod
    def _validate_previous_quarter_key(
        run: RuntimeRun, key: BusinessKey, dataset_id: str, role: str
    ) -> None:
        if key.workflow_run_id != run.context.workflow_run_id:
            raise Stage3AError("CTV_CONTEXT_MISMATCH", f"{role} input belongs to another Run")
        if (
            key.dataset_id != dataset_id
            or key.period_role != "previous_quarter_complete"
            or key.product_parameter != "not_applicable"
        ):
            raise Stage3AError(
                "CTV_PREVIOUS_QUARTER_INPUT_KEY_INVALID",
                f"Previous-quarter {role} business key mismatch",
            )

    def _rule_authority_lineage(self) -> list[str]:
        return [
            f"rule-authority://{rule_id}@{self.assets.business_rules[rule_id]['version']}"
            for rule_id in self.assets.pipeline["execution"]["ordered_rule_set_ids"]
        ]

    @staticmethod
    def _business_context(context) -> dict[str, object]:
        return {
            "business_context_id": BUSINESS_CONTEXT_ID,
            "target_business_line": "CTV",
            "target_report_period": context.target_report_period,
            "target_fiscal_quarter": context.target_fiscal_quarter,
            "workflow_reporting_date": context.workflow_reporting_date.isoformat(),
            "current_revenue_cutoff_date": context.current_revenue_cutoff_date.isoformat(),
            "report_mode": context.report_mode,
        }
