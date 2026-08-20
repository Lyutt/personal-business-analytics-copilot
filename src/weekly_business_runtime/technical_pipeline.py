"""Deterministic Stage 3B executor for PL_REVENUE_TECHNICAL_WEEKLY."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from weekly_acquisition_runtime.contracts import BusinessKey
from weekly_acquisition_runtime.errors import AcquisitionError
from weekly_acquisition_runtime.runtime import AcquisitionRuntime, RuntimeRun

from .ctv_metrics import derive_prior_year_date, validate_revenue_context
from .errors import DatasetValidationError, MetricStoreError, Stage3AError
from .models import (
    ExecutionWarning,
    PipelineExecutionResult,
    PipelineExecutionStatus,
    ResultValueStatus,
)
from .store import (
    MetricStorePort,
    MetricStoreRecord,
    StoreBusinessDateReadKey,
    StorePhysicalSnapshot,
    StorePhysicalSnapshotReadKey,
    StorePhysicalValue,
    StoreReadKey,
    StoreWriteContext,
)
from .technical_assets import (
    BUSINESS_CONTEXT_ID,
    DATASET_ID,
    MAPPING_ID,
    PIPELINE_ID,
    PRIOR_YEAR_RULE_ID,
    REPORT_MODE_RULE_ID,
    STORE_ASSET_ID,
    STORE_ID,
    VARIANT_IDS,
    WORKFLOW_ID,
    TechnicalAssetBundle,
)
from .technical_dataset import TechnicalDatasetLoader
from .technical_metrics import calculate_technical_metrics
from .technical_result import TechnicalResultContractAssembler


class TechnicalPipelineExecutor:
    def __init__(
        self,
        *,
        acquisition_runtime: AcquisitionRuntime,
        assets: TechnicalAssetBundle,
        metric_store: MetricStorePort,
    ) -> None:
        self.acquisition_runtime = acquisition_runtime
        self.assets = assets
        self.metric_store = metric_store
        self.loader = TechnicalDatasetLoader(assets)
        self.assembler = TechnicalResultContractAssembler(assets)

    def execute(
        self,
        *,
        run: RuntimeRun,
        pipeline_run_id: str,
        current_input_key: BusinessKey,
        prior_year_input_key: BusinessKey | None,
        generated_at: str,
    ) -> PipelineExecutionResult:
        input_references: tuple[str, ...] = ()
        try:
            self._validate_key(run, pipeline_run_id, current_input_key, "current")
            if prior_year_input_key is not None:
                self._validate_key(
                    run, pipeline_run_id, prior_year_input_key, "prior_year_comparable"
                )
            context = validate_revenue_context(run.context.values)
            current_entry = run.run_input_manifest.get_entry(current_input_key)
            target_prior_date = derive_prior_year_date(context)
            self._validate_current_manifest_context(current_entry, context)
            current_path = self.acquisition_runtime.consume_bound_input(
                run, current_input_key
            )
            input_references = (current_entry.local_input_reference,)
            current = self.loader.load(
                current_path,
                target_fiscal_quarter=context.target_fiscal_quarter,
                input_role="current",
                input_reference=input_references[0],
            )
            prior, prior_input_references, prior_input_warnings = (
                self._load_prior_year_input(
                    run, prior_year_input_key, target_prior_date, context
                )
            )
            input_references = (*input_references, *prior_input_references)
            previous_qtd_record, previous_incremental, history_lineage = (
                self._read_previous_week_history(context)
            )
            prior_incremental, prior_lineage, prior_warnings = (
                self._read_prior_year_incremental(context, prior, previous_qtd_record)
            )
            calculation = calculate_technical_metrics(
                current,
                prior,
                previous_qtd_executed=(
                    None if previous_qtd_record is None else previous_qtd_record.value
                ),
                previous_weekly_incremental=previous_incremental,
                prior_year_weekly_incremental=prior_incremental,
                context=context,
            )
            evaluated_rules = tuple(
                dict.fromkeys(
                    (*self.assets.pipeline["execution"]["ordered_rule_set_ids"], PRIOR_YEAR_RULE_ID)
                )
            )
            rule_lineage = tuple(
                f"rule-evaluated://{rule_id}" for rule_id in evaluated_rules
            )
            lineage = (
                *input_references,
                *history_lineage,
                *prior_lineage,
                *rule_lineage,
                f"mapping-consumed://{MAPPING_ID}",
            )
            result = self.assembler.assemble(
                workflow_run_id=run.context.workflow_run_id,
                pipeline_run_id=pipeline_run_id,
                context=context,
                dataset_instance_ids=input_references,
                evaluated_rule_ids=evaluated_rules,
                field_lineage_references=lineage,
                calculation=calculation,
                generated_at=generated_at,
            )
            warnings = [
                *current.warnings,
                *(() if prior is None else prior.warnings),
                *prior_input_warnings,
                *prior_warnings,
                *calculation.warnings,
            ]
            store_lineage = self._persist_validated_result(
                result,
                None if prior is None else prior.performance,
                None if prior is None else prior.executed,
                warnings,
            )
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
                lineage_references=(*lineage, *store_lineage),
                result_contract=result,
            )
        except (Stage3AError, AcquisitionError) as exc:
            code = exc.code if isinstance(exc, Stage3AError) else "TECHNICAL_INPUT_BINDING_INVALID"
            return self._blocked(run, pipeline_run_id, input_references, code, str(exc))
        except Exception as exc:  # noqa: BLE001 - fail-closed Pipeline boundary
            return self._blocked(
                run,
                pipeline_run_id,
                input_references,
                "TECHNICAL_UNEXPECTED_EXECUTION_ERROR",
                str(exc),
            )

    @staticmethod
    def _validate_key(
        run: RuntimeRun, pipeline_run_id: str, key: BusinessKey, role: str
    ) -> None:
        if not pipeline_run_id.strip():
            raise Stage3AError(
                "TECHNICAL_PIPELINE_RUN_ID_INVALID", "pipeline_run_id is required"
            )
        if key.workflow_run_id != run.context.workflow_run_id:
            raise Stage3AError(
                "TECHNICAL_CONTEXT_MISMATCH", f"{role} input belongs to another Run"
            )
        if (
            key.dataset_id != DATASET_ID
            or key.period_role != role
            or key.product_parameter != "not_applicable"
        ):
            raise Stage3AError(
                "TECHNICAL_INPUT_KEY_INVALID", f"Technical {role} business key mismatch"
            )

    @staticmethod
    def _validate_current_manifest_context(current_entry, context) -> None:
        if current_entry.source_report_date != context.workflow_reporting_date.isoformat():
            raise Stage3AError(
                "TECHNICAL_CURRENT_SOURCE_REPORT_DATE_MISMATCH",
                "Current source_report_date must equal workflow_reporting_date",
            )
        if (
            current_entry.source_business_data_cutoff_date
            != context.current_revenue_cutoff_date.isoformat()
        ):
            raise Stage3AError(
                "TECHNICAL_CURRENT_CUTOFF_MISMATCH",
                "Current source cutoff must equal current_revenue_cutoff_date",
            )

    def _load_prior_year_input(self, run, key, target_date, context):
        if key is None:
            return (
                None,
                (),
                [
                    ExecutionWarning(
                        "TECHNICAL_PRIOR_YEAR_INPUT_UNAVAILABLE",
                        "No prior-year comparable input is bound; dependent YoY remains missing",
                    )
                ],
            )
        references: tuple[str, ...] = ()
        try:
            entry = run.run_input_manifest.get_entry(key)
            references = (entry.local_input_reference,)
            if entry.source_business_data_cutoff_date != target_date.isoformat():
                raise DatasetValidationError(
                    "TECHNICAL_PRIOR_SOURCE_DATE_MISMATCH",
                    "Prior-year input business cutoff is not bound to the rule-derived physical date",
                )
            path = self.acquisition_runtime.consume_bound_input(run, key)
            prior_quarter = (
                f"{target_date.year}Q{((target_date.month - 1) // 3) + 1}"
            )
            loaded = self.loader.load(
                path,
                target_fiscal_quarter=prior_quarter,
                input_role="prior_year_comparable",
                input_reference=entry.local_input_reference,
            )
            return loaded, references, []
        except (AcquisitionError, DatasetValidationError) as exc:
            return (
                None,
                references,
                [
                    ExecutionWarning(
                        "TECHNICAL_PRIOR_YEAR_INPUT_UNAVAILABLE",
                        f"Prior-year comparable input is ineligible; dependent YoY remains missing: {exc}",
                    )
                ],
            )

    def _read_previous_week_history(self, context):
        if context.report_mode != "regular_week":
            return None, None, ()
        records: list[MetricStoreRecord] = []
        lineage: list[str] = []
        for variant_id in (VARIANT_IDS[2], VARIANT_IDS[3]):
            key = StoreReadKey(
                STORE_ID,
                STORE_ASSET_ID,
                variant_id,
                context.expected_previous_revenue_workflow_reporting_date.isoformat(),
                BUSINESS_CONTEXT_ID,
            )
            record = self.metric_store.read_exact(key)
            self._validate_record(record, variant_id)
            records.append(record)
            lineage.append(f"metric-store://{record.result_id}")
        return records[0], records[1].value, tuple(lineage)

    def _read_prior_year_incremental(self, context, prior, previous_qtd_record):
        if context.report_mode != "regular_week":
            return None, (), []
        target_date = derive_prior_year_date(context)
        key = StoreBusinessDateReadKey(
            STORE_ID,
            STORE_ASSET_ID,
            VARIANT_IDS[3],
            target_date.isoformat(),
            BUSINESS_CONTEXT_ID,
        )
        try:
            record = self.metric_store.read_exact_business_date(key)
            self._validate_record(record, VARIANT_IDS[3])
            return (
                record.value,
                (f"metric-store-business-date://{record.result_id}",),
                [],
            )
        except MetricStoreError as exc:
            if exc.code in {
                "STORE_EXACT_BUSINESS_DATE_NOT_FOUND",
                "STORE_EXCEL_LINEAGE_BUSINESS_DATE_KEY_NOT_FOUND",
                "STORE_EXCEL_LINEAGE_METADATA_MISSING",
            }:
                return self._reconstruct_prior_year_incremental(
                    context, prior, previous_qtd_record, exc
                )
            return (
                None,
                (),
                [
                    ExecutionWarning(
                        "TECHNICAL_PRIOR_YEAR_INCREMENTAL_UNAVAILABLE",
                        f"Prior-year incremental Store Result was not eligible: {exc}",
                    )
                ],
            )

    def _reconstruct_prior_year_incremental(
        self, context, prior, previous_qtd_record, primary_error
    ):
        if prior is None or previous_qtd_record is None:
            return (
                None,
                (),
                [
                    ExecutionWarning(
                        "TECHNICAL_PRIOR_YEAR_INCREMENTAL_RECONSTRUCTION_UNAVAILABLE",
                        "Exact prior-year Store Result was absent and both exact QTD Executed snapshots were not available",
                    )
                ],
            )
        key = StorePhysicalSnapshotReadKey(
            STORE_ID,
            STORE_ASSET_ID,
            "E",
            context.expected_previous_revenue_workflow_reporting_date.isoformat(),
            BUSINESS_CONTEXT_ID,
        )
        try:
            snapshot = self.metric_store.read_exact_physical_snapshot(key)
            self._validate_prior_year_qtd_snapshot(snapshot)
            previous_context = replace(
                context,
                current_revenue_cutoff_date=date.fromisoformat(
                    previous_qtd_record.current_revenue_cutoff_date
                ),
            )
            expected_previous_prior_date = derive_prior_year_date(
                previous_context
            ).isoformat()
            if snapshot.represented_business_date != expected_previous_prior_date:
                raise MetricStoreError(
                    "STORE_PRIOR_YEAR_QTD_SNAPSHOT_DATE_MISMATCH",
                    "Previous prior-year QTD snapshot does not match the exact comparable date",
                )
            reconstructed = prior.executed - snapshot.value
            return (
                reconstructed,
                (
                    f"metric-store-physical-snapshot://{STORE_ASSET_ID}/"
                    f"{snapshot.read_key.workflow_reporting_date}/E",
                    "deterministic-reconstruction://MV_REVENUE_TECHNICAL_WEEKLY_INCREMENTAL_EXECUTED_V1",
                ),
                [
                    ExecutionWarning(
                        "TECHNICAL_PRIOR_YEAR_INCREMENTAL_RECONSTRUCTED",
                        "Exact prior-year weekly incremental Store Result was absent; denominator was deterministically reconstructed from two exact authoritative QTD Executed snapshots",
                    )
                ],
            )
        except MetricStoreError as exc:
            return (
                None,
                (),
                [
                    ExecutionWarning(
                        "TECHNICAL_PRIOR_YEAR_INCREMENTAL_RECONSTRUCTION_UNAVAILABLE",
                        "Exact prior-year Store Result was absent and deterministic dual-QTD reconstruction was unavailable: "
                        f"primary={primary_error}; fallback={exc}",
                    )
                ],
            )

    @staticmethod
    def _validate_prior_year_qtd_snapshot(snapshot: StorePhysicalSnapshot) -> None:
        if any(
            (
                snapshot.read_key.store_id != STORE_ID,
                snapshot.read_key.store_asset_id != STORE_ASSET_ID,
                snapshot.read_key.field_id != "E",
                snapshot.read_key.business_context_id != BUSINESS_CONTEXT_ID,
                snapshot.metric_variant_id != VARIANT_IDS[2],
                snapshot.period_role != "prior_year_comparable",
                snapshot.validation_status != "passed",
                not isinstance(snapshot.value, Decimal),
                not snapshot.value.is_finite(),
                snapshot.numeric_semantics != "monetary_amount",
                snapshot.unit != "CNY_yuan",
                not snapshot.lineage_references,
            )
        ):
            raise MetricStoreError(
                "STORE_PRIOR_YEAR_QTD_SNAPSHOT_INELIGIBLE",
                "Exact prior-year QTD Executed snapshot is not contract-eligible",
            )

    def _validate_record(self, record: MetricStoreRecord, variant_id: str) -> None:
        contract = next(
            item
            for item in self.assets.result_contract["contract_fields"]
            if item["source_metric_variant_id"] == variant_id
        )
        constraints = contract["numeric_constraints"]
        if any(
            (
                record.store_id != STORE_ID,
                record.store_asset_id != STORE_ASSET_ID,
                record.business_context_id != BUSINESS_CONTEXT_ID,
                record.metric_variant_id != variant_id,
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
                "Exact Technical historical Result is not contract-eligible",
            )

    def _persist_validated_result(
        self,
        result,
        prior_performance: Decimal | None,
        prior_executed: Decimal | None,
        warnings: list[ExecutionWarning],
    ) -> tuple[str, ...]:
        applicable = tuple(
            field
            for field in result.fields
            if field.value_status is not ResultValueStatus.NOT_APPLICABLE
        )
        if prior_performance is None or prior_executed is None:
            warnings.append(
                ExecutionWarning(
                    "STORE_WRITE_SET_INCOMPLETE",
                    "Technical Store D/E physical values are unavailable; persistence is skipped",
                )
            )
            return ()
        if any(
            field.value is None or field.value_status is not ResultValueStatus.VALID_VALUE
            for field in applicable
        ):
            warnings.append(
                ExecutionWarning(
                    "STORE_WRITE_SET_INCOMPLETE",
                    "Revenue Store completeness gate blocked partial Result Contract persistence",
                )
            )
            return ()
        contracts = {
            field["field_id"]: field
            for field in self.assets.result_contract["contract_fields"]
        }
        records = tuple(
            MetricStoreRecord(
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
                numeric_semantics=contracts[field.field_id]["numeric_constraints"][
                    "numeric_semantics"
                ],
                unit=field.unit,
                precision=contracts[field.field_id]["numeric_constraints"]["precision"],
                validation_status=result.validation_status,
                generated_at=result.generated_at,
                lineage_references=field.lineage_references,
            )
            for field in applicable
        )
        try:
            plan = self.metric_store.preflight_write(
                records,
                StoreWriteContext(
                    report_mode=result.report_mode,
                    physical_values=(
                        StorePhysicalValue("D", prior_performance),
                        StorePhysicalValue("E", prior_executed),
                    ),
                ),
            )
            receipt = self.metric_store.write_validated(plan)
            if not self.metric_store.verify_write(receipt):
                raise MetricStoreError(
                    "STORE_WRITE_VERIFICATION_FAILED",
                    "Post-write verification did not verify the complete Technical Result set",
                )
            return tuple(f"metric-store://{item}" for item in receipt.result_ids)
        except MetricStoreError as exc:
            warnings.append(ExecutionWarning(exc.code, str(exc)))
            return ()

    @staticmethod
    def _business_context(context) -> dict[str, object]:
        return {
            "business_context_id": BUSINESS_CONTEXT_ID,
            "target_business_line": "Technical",
            "target_report_period": context.target_report_period,
            "target_fiscal_quarter": context.target_fiscal_quarter,
            "workflow_reporting_date": context.workflow_reporting_date.isoformat(),
            "current_revenue_cutoff_date": context.current_revenue_cutoff_date.isoformat(),
            "report_mode": context.report_mode,
        }

    @staticmethod
    def _blocked(run, pipeline_run_id, references, code, message):
        return PipelineExecutionResult(
            workflow_run_id=run.context.workflow_run_id,
            pipeline_id=PIPELINE_ID,
            pipeline_run_id=pipeline_run_id,
            business_context={"business_context_id": BUSINESS_CONTEXT_ID},
            input_binding_references=references,
            execution_status=PipelineExecutionStatus.BLOCKED,
            error_code=code,
            error_message=message,
            lineage_references=references,
        )
