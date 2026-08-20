from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from weekly_acquisition_runtime.contracts import (
    AcquisitionMode,
    BusinessKey,
    InputBindingRegistry,
    RegisteredInputBinding,
    RunInputEntry,
)
from weekly_acquisition_runtime.runtime import AcquisitionRuntime
from weekly_acquisition_runtime.storage import LocalRuntimeStorage
from weekly_business_runtime.errors import MetricStoreError
from weekly_business_runtime.models import PipelineExecutionStatus, ResultValueStatus
from weekly_business_runtime.store import (
    InMemoryMetricStore,
    MetricStoreRecord,
    StoreBusinessDateReadKey,
    StorePhysicalSnapshot,
    StorePhysicalSnapshotReadKey,
    StoreReadKey,
    StoreWriteContext,
)
from weekly_business_runtime.technical_assets import (
    BUSINESS_CONTEXT_ID,
    DATASET_ID,
    PIPELINE_ID,
    STORE_ASSET_ID,
    STORE_ID,
    VARIANT_IDS,
    WORKFLOW_ID,
    TechnicalAssetBundle,
)
from weekly_business_runtime.technical_pipeline import TechnicalPipelineExecutor


def locked_context(
    run_id: str = "RUN_SYNTH_TECHNICAL_001",
    *,
    report_mode: str = "regular_week",
) -> dict[str, object]:
    transition = report_mode == "quarter_transition_week"
    reporting = "2026-07-02" if transition else "2026-07-23"
    previous = "2026-06-25" if transition else "2026-07-16"
    cutoff = "2026-07-01" if transition else "2026-07-22"
    return {
        "workflow_run_id": run_id,
        "run_type": "manual",
        "workflow_execution_date": reporting,
        "workflow_reporting_date": reporting,
        "reporting_period_id": "2026-W30",
        "reporting_period_start_date": reporting,
        "reporting_period_end_date": reporting,
        "current_period_start_date": reporting,
        "current_period_end_date": reporting,
        "comparison_period_start_date": previous,
        "comparison_period_end_date": previous,
        "cutoff_date": cutoff,
        "timezone": "Asia/Shanghai",
        "current_revenue_cutoff_date": cutoff,
        "expected_previous_revenue_workflow_reporting_date": previous,
        "target_report_period": "2026-W30",
        "workflow_year": 2026,
        "target_fiscal_quarter": "2026Q3",
        "target_previous_calendar_quarter": "2026Q2",
        "report_mode": report_mode,
        "target_revenue_cutoff_date": cutoff,
    }


def store_record(
    variant_id: str,
    value: Decimal,
    *,
    reporting_date: str,
    cutoff_date: str,
    suffix: str,
) -> MetricStoreRecord:
    ratio = variant_id in {VARIANT_IDS[1], VARIANT_IDS[4], VARIANT_IDS[5]}
    return MetricStoreRecord(
        result_id=f"HIST_{suffix}",
        workflow_id=WORKFLOW_ID,
        workflow_run_id="RUN_SYNTH_HISTORY",
        pipeline_id=PIPELINE_ID,
        pipeline_run_id="PIPELINE_SYNTH_HISTORY",
        store_id=STORE_ID,
        store_asset_id=STORE_ASSET_ID,
        metric_variant_id=variant_id,
        metric_variant_version="1.0.0-draft",
        workflow_reporting_date=reporting_date,
        current_revenue_cutoff_date=cutoff_date,
        business_context_id=BUSINESS_CONTEXT_ID,
        reporting_period="2026-W29",
        value=value,
        value_status="valid_value",
        numeric_semantics="ratio" if ratio else "monetary_amount",
        unit="decimal_ratio" if ratio else "CNY_yuan",
        precision="preserve_source_precision",
        validation_status="passed",
        generated_at="2026-07-16T17:30:00+08:00",
        lineage_references=("synthetic://history",),
    )


class CapturingStore(InMemoryMetricStore):
    def __init__(self) -> None:
        super().__init__()
        self.last_physical_context: StoreWriteContext | None = None

    def preflight_write(self, records, physical_write_context=None):
        self.last_physical_context = physical_write_context
        return super().preflight_write(records, physical_write_context)


class TechnicalScenario:
    def __init__(
        self,
        root: Path,
        *,
        report_mode: str = "regular_week",
        seed_primary_prior_incremental: bool = True,
        seed_fallback_snapshot: bool = False,
    ) -> None:
        self.assets = TechnicalAssetBundle.load(ROOT)
        self.storage = LocalRuntimeStorage(root / "runtime", ROOT)
        registry = InputBindingRegistry(
            workflow_id=WORKFLOW_ID,
            bindings={
                DATASET_ID: RegisteredInputBinding(
                    dataset_id=DATASET_ID,
                    query_asset_id_or_not_applicable="not_applicable",
                    adapter_id="ADP_LOCAL_DATASET_V1",
                    source_id="SRC_LOCAL_DATASET",
                    provider_id="PRV_LOCAL_DATASET_V1",
                    dataset_version_constraints=(">=0.1.0,<0.2.0",),
                )
            },
        )
        self.runtime = AcquisitionRuntime(self.storage, registry)
        self.run = self.runtime.start_run(locked_context(report_mode=report_mode))
        self.current_key = BusinessKey(
            self.run.context.workflow_run_id, DATASET_ID, "current", "not_applicable"
        )
        self.prior_key = BusinessKey(
            self.run.context.workflow_run_id,
            DATASET_ID,
            "prior_year_comparable",
            "not_applicable",
        )
        inputs = self.storage.root / "runs" / self.run.context.workflow_run_id / "legacy_inputs"
        inputs.mkdir(parents=True)
        self.current_path = inputs / "technical_current.xlsx"
        self.prior_path = inputs / "technical_prior.xlsx"
        self._write_dataset(
            self.current_path,
            year=2026,
            quarter="2026Q3",
            performance=(100, 50),
            executed=(80, 20),
        )
        self._write_dataset(
            self.prior_path,
            year=2025,
            quarter="2025Q3",
            performance=(60, 30),
            executed=(40, 10),
        )
        self._declare_inputs()
        self.store = CapturingStore()
        if report_mode == "regular_week":
            records = [
                store_record(
                    VARIANT_IDS[2],
                    Decimal("70"),
                    reporting_date="2026-07-16",
                    cutoff_date="2026-07-15",
                    suffix="PREVIOUS_QTD_EXECUTED",
                ),
                store_record(
                    VARIANT_IDS[3],
                    Decimal("20"),
                    reporting_date="2026-07-16",
                    cutoff_date="2026-07-15",
                    suffix="PREVIOUS_INCREMENTAL",
                ),
            ]
            if seed_primary_prior_incremental:
                records.append(store_record(
                    VARIANT_IDS[3],
                    Decimal("15"),
                    reporting_date="2025-07-23",
                    cutoff_date="2025-07-23",
                    suffix="PRIOR_INCREMENTAL",
                ))
            self.store.seed_historical(*records)
            if seed_fallback_snapshot:
                self.store.seed_physical_snapshot(
                    StorePhysicalSnapshot(
                        read_key=StorePhysicalSnapshotReadKey(
                            STORE_ID,
                            STORE_ASSET_ID,
                            "E",
                            "2026-07-16",
                            BUSINESS_CONTEXT_ID,
                        ),
                        metric_variant_id=VARIANT_IDS[2],
                        period_role="prior_year_comparable",
                        represented_business_date="2025-07-16",
                        value=Decimal("35"),
                        numeric_semantics="monetary_amount",
                        unit="CNY_yuan",
                        validation_status="passed",
                        lineage_references=("synthetic://previous-prior-qtd-executed",),
                    )
                )
        self.executor = TechnicalPipelineExecutor(
            acquisition_runtime=self.runtime,
            assets=self.assets,
            metric_store=self.store,
        )

    def _write_dataset(
        self,
        path: Path,
        *,
        year: int,
        quarter: str,
        performance: tuple[int, int],
        executed: tuple[int, int],
    ) -> None:
        raw_fields = [
            item["raw_field_name"] for item in self.assets.mapping["raw_field_inventory"]
        ]
        rows = []
        for index in range(2):
            row = dict.fromkeys(raw_fields, "")
            row.update(
                {
                    "\u4e1a\u52a1\u7ebf2": "\u786c\u5e7f",
                    "\u5f52\u5c5e\u5b63\u5ea6": quarter,
                    "\u4e1a\u7ee9\u91d1\u989d": performance[index],
                    "\u5df2\u6267\u884c\u6536\u5165": executed[index],
                }
            )
            rows.append(row)
        pd.DataFrame(rows, columns=raw_fields).to_excel(
            path, sheet_name=f"{year}\u5e74\u6267\u884c\u5355\u7c7b\u578b", index=False
        )

    def _declare_inputs(self) -> None:
        values = self.run.context.values
        current_reference = self.storage.opaque_reference(self.current_path)
        prior_reference = self.storage.opaque_reference(self.prior_path)
        prior_cutoff = (
            "2025-07-02"
            if values["report_mode"] == "quarter_transition_week"
            else "2025-07-23"
        )
        prior_report_date = (
            "2025-07-03"
            if values["report_mode"] == "quarter_transition_week"
            else "2025-07-24"
        )
        for key, reference, source_date, cutoff in (
            (
                self.current_key,
                current_reference,
                str(values["workflow_reporting_date"]),
                str(values["current_revenue_cutoff_date"]),
            ),
            (self.prior_key, prior_reference, prior_report_date, prior_cutoff),
        ):
            self.runtime.declare_input(
                self.run,
                RunInputEntry(
                    business_key=key,
                    dataset_version="0.1.0",
                    query_asset_binding={"binding_status": "not_applicable"},
                    local_input_reference=reference,
                    source_report_date=source_date,
                    source_business_data_cutoff_date=cutoff,
                    acquisition_mode=AcquisitionMode.LEGACY_PREPARED_LOCAL_INPUT,
                ),
            )

    def execute(self):
        return self.executor.execute(
            run=self.run,
            pipeline_run_id="PIPELINE_RUN_SYNTH_TECHNICAL_001",
            current_input_key=self.current_key,
            prior_year_input_key=self.prior_key,
            generated_at="2026-07-23T17:31:00+08:00",
        )


class Stage3BTechnicalVerticalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_authority_composes_with_independent_prior_qtd_sources(self) -> None:
        assets = TechnicalAssetBundle.load(ROOT)
        registration = assets.mapping["scope"]["prior_year_comparable_qtd_registration"]
        self.assertEqual(registration["performance_source_mapping_entry_id"], "FM016")
        self.assertEqual(registration["executed_source_mapping_entry_id"], "FM017")
        self.assertFalse(registration["complete_quarter_equivalence_allowed"])

    def test_regular_week_executes_and_preserves_distinct_store_d_e_values(self) -> None:
        scenario = TechnicalScenario(self.root)
        result = scenario.execute()
        self.assertEqual(result.execution_status, PipelineExecutionStatus.COMPLETED)
        contract = result.result_contract
        assert contract is not None
        self.assertEqual(contract.field("qtd_performance_revenue").value, Decimal("150"))
        self.assertEqual(contract.field("qtd_executed_revenue").value, Decimal("100"))
        self.assertEqual(
            contract.field("qtd_performance_revenue_yoy").value,
            (Decimal("150") / Decimal("22")) / (Decimal("90") / Decimal("23"))
            - Decimal("1"),
        )
        self.assertEqual(
            contract.field("weekly_incremental_executed_revenue").value, Decimal("30")
        )
        self.assertEqual(
            contract.field("weekly_incremental_executed_revenue_wow").value,
            Decimal("0.5"),
        )
        self.assertEqual(
            contract.field("weekly_incremental_executed_revenue_yoy").value,
            Decimal("1"),
        )
        context = scenario.store.last_physical_context
        assert context is not None
        self.assertEqual(
            {item.field_id: item.value for item in context.physical_values},
            {"D": Decimal("90"), "E": Decimal("50")},
        )

    def test_missing_primary_prior_incremental_uses_exact_dual_qtd_reconstruction(self) -> None:
        scenario = TechnicalScenario(
            self.root,
            seed_primary_prior_incremental=False,
            seed_fallback_snapshot=True,
        )
        result = scenario.execute()
        self.assertEqual(
            result.result_contract.field("weekly_incremental_executed_revenue_yoy").value,
            Decimal("1"),
        )
        self.assertIn(
            "TECHNICAL_PRIOR_YEAR_INCREMENTAL_RECONSTRUCTED",
            {warning.code for warning in result.warnings},
        )

    def test_ambiguous_primary_prior_incremental_does_not_use_qtd_fallback(self) -> None:
        scenario = TechnicalScenario(self.root, seed_fallback_snapshot=True)
        scenario.store.seed_historical(
            store_record(
                VARIANT_IDS[3],
                Decimal("16"),
                reporting_date="2025-07-23",
                cutoff_date="2025-07-23",
                suffix="PRIOR_INCREMENTAL_DUPLICATE",
            )
        )
        result = scenario.execute()
        field = result.result_contract.field("weekly_incremental_executed_revenue_yoy")
        self.assertIsNone(field.value)
        self.assertIs(field.value_status, ResultValueStatus.MISSING)
        self.assertNotIn(
            "TECHNICAL_PRIOR_YEAR_INCREMENTAL_RECONSTRUCTED",
            {warning.code for warning in result.warnings},
        )

    def test_metadata_missing_primary_does_not_use_qtd_fallback(self) -> None:
        scenario = TechnicalScenario(self.root, seed_fallback_snapshot=True)
        with patch.object(
            scenario.store,
            "read_exact_business_date",
            side_effect=MetricStoreError(
                "STORE_EXCEL_LINEAGE_METADATA_MISSING",
                "Synthetic physical row has no Adapter metadata",
            ),
        ):
            result = scenario.execute()
        field = result.result_contract.field("weekly_incremental_executed_revenue_yoy")
        self.assertIsNone(field.value)
        self.assertIs(field.value_status, ResultValueStatus.MISSING)
        self.assertNotIn(
            "TECHNICAL_PRIOR_YEAR_INCREMENTAL_RECONSTRUCTED",
            {warning.code for warning in result.warnings},
        )

    def test_unverified_primary_does_not_use_qtd_fallback(self) -> None:
        scenario = TechnicalScenario(self.root, seed_fallback_snapshot=True)
        scenario.store.force_verification_failure(
            StoreBusinessDateReadKey(
                STORE_ID,
                STORE_ASSET_ID,
                VARIANT_IDS[3],
                "2025-07-23",
                BUSINESS_CONTEXT_ID,
            )
        )
        result = scenario.execute()
        field = result.result_contract.field("weekly_incremental_executed_revenue_yoy")
        self.assertIsNone(field.value)
        self.assertIs(field.value_status, ResultValueStatus.MISSING)
        self.assertNotIn(
            "TECHNICAL_PRIOR_YEAR_INCREMENTAL_RECONSTRUCTED",
            {warning.code for warning in result.warnings},
        )

    def test_invalid_primary_does_not_use_qtd_fallback(self) -> None:
        scenario = TechnicalScenario(
            self.root,
            seed_primary_prior_incremental=False,
            seed_fallback_snapshot=True,
        )
        scenario.store.seed_historical(
            replace(
                store_record(
                    VARIANT_IDS[3],
                    Decimal("15"),
                    reporting_date="2025-07-23",
                    cutoff_date="2025-07-23",
                    suffix="PRIOR_INCREMENTAL_INVALID",
                ),
                validation_status="failed",
            )
        )
        result = scenario.execute()
        field = result.result_contract.field("weekly_incremental_executed_revenue_yoy")
        self.assertIsNone(field.value)
        self.assertIs(field.value_status, ResultValueStatus.MISSING)
        self.assertNotIn(
            "TECHNICAL_PRIOR_YEAR_INCREMENTAL_RECONSTRUCTED",
            {warning.code for warning in result.warnings},
        )

    def test_quarter_transition_keeps_h_k_l_blank_not_applicable(self) -> None:
        scenario = TechnicalScenario(self.root, report_mode="quarter_transition_week")
        result = scenario.execute()
        self.assertEqual(result.execution_status, PipelineExecutionStatus.COMPLETED)
        contract = result.result_contract
        assert contract is not None
        for field_id in (
            "weekly_incremental_executed_revenue",
            "weekly_incremental_executed_revenue_wow",
            "weekly_incremental_executed_revenue_yoy",
        ):
            field = contract.field(field_id)
            self.assertIsNone(field.value)
            self.assertIs(field.value_status, ResultValueStatus.NOT_APPLICABLE)
        for variant_id in VARIANT_IDS[:3]:
            key = StoreReadKey(
                STORE_ID,
                STORE_ASSET_ID,
                variant_id,
                "2026-07-02",
                BUSINESS_CONTEXT_ID,
            )
            self.assertEqual(len(scenario.store.records_for(key)), 1)

    def test_ineligible_prior_manifest_scopes_failure_to_dependent_yoy(self) -> None:
        scenario = TechnicalScenario(self.root)
        entry = scenario.run.run_input_manifest.get_entry(scenario.prior_key)
        scenario.run.run_input_manifest._entries[scenario.prior_key.as_tuple()] = RunInputEntry(
            business_key=entry.business_key,
            dataset_version=entry.dataset_version,
            query_asset_binding=entry.query_asset_binding,
            local_input_reference=entry.local_input_reference,
            source_report_date=entry.source_report_date,
            source_business_data_cutoff_date="2025-07-22",
            acquisition_mode=entry.acquisition_mode,
        )
        result = scenario.execute()
        self.assertEqual(
            result.execution_status, PipelineExecutionStatus.COMPLETED_WITH_WARNING
        )
        contract = result.result_contract
        assert contract is not None
        self.assertEqual(contract.field("qtd_performance_revenue").value, Decimal("150"))
        self.assertEqual(contract.field("qtd_executed_revenue").value, Decimal("100"))
        yoy = contract.field("qtd_performance_revenue_yoy")
        self.assertIsNone(yoy.value)
        self.assertIs(yoy.value_status, ResultValueStatus.MISSING)
        self.assertIsNone(scenario.store.last_physical_context)


if __name__ == "__main__":
    unittest.main()
