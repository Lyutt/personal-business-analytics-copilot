"""Minimal deterministic executor for PL_REVENUE_CTV_WEEKLY only."""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from weekly_acquisition_runtime.contracts import BusinessKey
from weekly_acquisition_runtime.errors import AcquisitionError
from weekly_acquisition_runtime.runtime import AcquisitionRuntime, RuntimeRun

from .assets import (
    BUSINESS_CONTEXT_ID,
    CTV_VARIANT_IDS,
    CURRENT_DATASET_ID,
    PIPELINE_ID,
    PRIOR_DATASET_ID,
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
)
from .store import MetricStorePort, MetricStoreRecord, StoreReadKey


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
            target_prior_date = derive_prior_year_date(context)
            prior_value = Decimal("0")
            prior_warnings: list[ExecutionWarning] = []
            try:
                prior_entry = run.run_input_manifest.get_entry(prior_year_input_key)
                prior_path = self.acquisition_runtime.consume_bound_input(run, prior_year_input_key)
                if prior_entry.source_business_data_cutoff_date != target_prior_date.isoformat():
                    raise DatasetValidationError(
                        "CTV_PRIOR_INPUT_DATE_MISMATCH",
                        "Prior input cutoff does not match the exact frozen prior-year date",
                    )
                prior = self.loader.load_prior_comparable(
                    prior_path,
                    target_quarter=f"{context.workflow_year - 1}{context.target_fiscal_quarter[4:]}",
                    input_reference=prior_entry.local_input_reference,
                )
                prior_value = prior.value
                input_references = (*input_references, prior_entry.local_input_reference)
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
                context=context,
            )
            result = self.assembler.assemble(
                workflow_run_id=run.context.workflow_run_id,
                pipeline_run_id=pipeline_run_id,
                context=context,
                dataset_instance_ids=input_references,
                calculation=calculation,
                generated_at=generated_at,
            )
            warnings = list(current.warnings) + prior_warnings + list(calculation.warnings)
            store_lineage = self._persist_validated_result(result.fields, result, warnings)
            lineage = (*input_references, *historical_lineage, *store_lineage)
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
            lineage.append(f"metric-store://{record.result_id}")
        return tuple(lineage)

    def _persist_validated_result(
        self,
        fields: Iterable[ResultFieldValue],
        result,
        warnings: list[ExecutionWarning],
    ) -> tuple[str, ...]:
        references: list[str] = []
        contracts = {
            field["field_id"]: field for field in self.assets.result_contract["contract_fields"]
        }
        for field in fields:
            if field.value is None or field.value_status.value != "valid_value":
                continue
            contract = contracts[field.field_id]
            record = MetricStoreRecord(
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
            )
            try:
                receipt = self.metric_store.write_validated(record)
                if not self.metric_store.verify_write(receipt):
                    raise MetricStoreError(
                        "STORE_WRITE_VERIFICATION_FAILED",
                        "Post-write verification did not match the validated result",
                    )
                references.append(f"metric-store://{record.result_id}")
            except MetricStoreError as exc:
                warnings.append(ExecutionWarning(exc.code, str(exc)))
        return tuple(references)

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
