from __future__ import annotations

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

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
from weekly_business_runtime.business_line_pipeline import (
    DATASET_ID,
    FAST_VERSION_PROFILE,
    MAPPING_ID,
    SMART_SPEAKER_PROFILE,
    STORE_ID,
    WORKFLOW_ID,
    BusinessLineAssetBundle,
    BusinessLineRevenuePipelineExecutor,
)
from weekly_business_runtime.models import PipelineExecutionStatus, ResultValueStatus
from weekly_business_runtime.store import (
    InMemoryMetricStore,
    MetricStoreRecord,
    StoreReadKey,
)

PROFILES = (SMART_SPEAKER_PROFILE, FAST_VERSION_PROFILE)


def locked_context(run_id: str, report_mode: str) -> dict[str, object]:
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
        "current_period_start_date": "2026-07-17" if not transition else "2026-06-26",
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


def historical_record(profile, variant_id: str, value: str, suffix: str) -> MetricStoreRecord:
    return MetricStoreRecord(
        result_id=f"HIST_{profile.pipeline_id}_{suffix}",
        workflow_id=WORKFLOW_ID,
        workflow_run_id="RUN_HISTORY",
        pipeline_id=profile.pipeline_id,
        pipeline_run_id="PIPELINE_HISTORY",
        store_id=STORE_ID,
        store_asset_id=profile.store_asset_id,
        metric_variant_id=variant_id,
        metric_variant_version="1.0.0-draft",
        workflow_reporting_date="2026-07-16",
        current_revenue_cutoff_date="2026-07-15",
        business_context_id=profile.business_context_id,
        reporting_period="2026-W29",
        value=Decimal(value),
        value_status="valid_value",
        numeric_semantics="monetary_amount",
        unit="CNY_yuan",
        precision="preserve_source_precision",
        validation_status="passed",
        generated_at="2026-07-16T17:30:00+08:00",
        lineage_references=("synthetic://history",),
    )


class BusinessLineScenario:
    def __init__(
        self,
        root: Path,
        profile,
        *,
        report_mode: str = "regular_week",
        raw_value: object = "1,000.5",
        seed_history: bool = True,
    ) -> None:
        self.profile = profile
        self.assets = BusinessLineAssetBundle.load(ROOT, profile)
        scenario_id = (
            f"{report_mode}-{seed_history}-{raw_value}"
            .replace(",", "_")
            .replace("-", "negative")
        )
        self.storage = LocalRuntimeStorage(root / profile.pipeline_id / scenario_id, ROOT)
        registry = InputBindingRegistry(
            workflow_id=WORKFLOW_ID,
            bindings={
                DATASET_ID: RegisteredInputBinding(
                    dataset_id=DATASET_ID,
                    query_asset_id_or_not_applicable="QRY_APOLLO_QISHENG_EXECUTION_REVENUE",
                    adapter_id="ADP_INTERNAL_APOLLO_QUERY_V1",
                    source_id="SRC_INTERNAL_PLATFORM_APOLLO",
                    provider_id="PRV_INTERNAL_APOLLO_PLAYWRIGHT_V1",
                    dataset_version_constraints=(">=0.1.0,<0.2.0",),
                )
            },
        )
        self.runtime = AcquisitionRuntime(self.storage, registry)
        self.run = self.runtime.start_run(
            locked_context(f"RUN_SYNTH_{profile.pipeline_id}", report_mode)
        )
        self.key = BusinessKey(
            self.run.context.workflow_run_id,
            DATASET_ID,
            "current",
            self.assets.query_template,
        )
        input_dir = self.storage.root / "runs" / self.run.context.workflow_run_id / "legacy_inputs"
        input_dir.mkdir(parents=True)
        self.path = input_dir / "business_line.csv"
        raw_fields = [item["raw_field_name"] for item in self.assets.mapping["raw_field_inventory"]]
        row = dict.fromkeys(raw_fields, "")
        mapped_raw = self.assets.mapping["field_mappings"][0]["raw_field_name"]
        row[mapped_raw] = raw_value
        pd.DataFrame([row], columns=raw_fields).to_csv(self.path, index=False, encoding="utf-8-sig")
        values = self.run.context.values
        self.runtime.declare_input(
            self.run,
            RunInputEntry(
                business_key=self.key,
                dataset_version="0.1.0",
                query_asset_binding={
                    "binding_status": "bound",
                    "query_asset_id": "QRY_APOLLO_QISHENG_EXECUTION_REVENUE",
                },
                local_input_reference=self.storage.opaque_reference(self.path),
                source_report_date=str(values["workflow_reporting_date"]),
                source_business_data_cutoff_date=str(values["current_revenue_cutoff_date"]),
                acquisition_mode=AcquisitionMode.LEGACY_PREPARED_LOCAL_INPUT,
            ),
        )
        self.store = InMemoryMetricStore()
        if report_mode == "regular_week" and seed_history:
            self.store.seed_historical(
                historical_record(profile, profile.qtd_variant_id, "300", "QTD"),
                historical_record(profile, profile.weekly_variant_id, "100", "WEEKLY"),
            )
        self.executor = BusinessLineRevenuePipelineExecutor(
            acquisition_runtime=self.runtime,
            assets=self.assets,
            metric_store=self.store,
        )

    def execute(self):
        return self.executor.execute(
            run=self.run,
            pipeline_run_id=f"PIPELINE_RUN_{self.profile.pipeline_id}",
            current_input_key=self.key,
            generated_at="2026-07-23T17:31:00+08:00",
        )


class Stage3BBusinessLineVerticalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_both_authority_compositions_are_exact(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile.pipeline_id):
                assets = BusinessLineAssetBundle.load(ROOT, profile)
                self.assertEqual(assets.mapping["mapping_profile_id"], MAPPING_ID)
                self.assertEqual(
                    tuple(assets.pipeline["execution"]["metric_variant_ids"]),
                    profile.variant_ids,
                )

    def test_regular_week_executes_and_persists_only_physical_store_metrics(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile.pipeline_id):
                scenario = BusinessLineScenario(self.root, profile)
                result = scenario.execute()
                self.assertEqual(result.execution_status, PipelineExecutionStatus.COMPLETED)
                contract = result.result_contract
                assert contract is not None
                self.assertEqual(contract.field("weekly_executed_revenue").value, Decimal("1001"))
                self.assertEqual(contract.field("qtd_executed_revenue").value, Decimal("1301"))
                self.assertEqual(
                    contract.field("weekly_executed_revenue_wow").value, Decimal("9.01")
                )
                for variant_id in (profile.weekly_variant_id, profile.qtd_variant_id):
                    key = StoreReadKey(
                        STORE_ID,
                        profile.store_asset_id,
                        variant_id,
                        "2026-07-23",
                        profile.business_context_id,
                    )
                    self.assertEqual(len(scenario.store.records_for(key)), 1)
                wow_key = StoreReadKey(
                    STORE_ID,
                    profile.store_asset_id,
                    profile.wow_variant_id,
                    "2026-07-23",
                    profile.business_context_id,
                )
                self.assertEqual(scenario.store.records_for(wow_key), ())

    def test_quarter_transition_resets_qtd_and_leaves_weekly_fields_not_applicable(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile.pipeline_id):
                scenario = BusinessLineScenario(
                    self.root, profile, report_mode="quarter_transition_week"
                )
                result = scenario.execute()
                self.assertEqual(result.execution_status, PipelineExecutionStatus.COMPLETED)
                contract = result.result_contract
                assert contract is not None
                self.assertEqual(contract.field("qtd_executed_revenue").value, Decimal("1001"))
                for field_id in ("weekly_executed_revenue", "weekly_executed_revenue_wow"):
                    field = contract.field(field_id)
                    self.assertIsNone(field.value)
                    self.assertIs(field.value_status, ResultValueStatus.NOT_APPLICABLE)

    def test_invalid_value_and_missing_history_block_without_provider_repair(self) -> None:
        for profile in PROFILES:
            with self.subTest(profile=profile.pipeline_id, case="invalid_value"):
                result = BusinessLineScenario(
                    self.root, profile, raw_value="-1"
                ).execute()
                self.assertEqual(result.execution_status, PipelineExecutionStatus.BLOCKED)
                self.assertEqual(result.error_code, "BUSINESS_LINE_REVENUE_VALUE_INVALID")
            with self.subTest(profile=profile.pipeline_id, case="missing_history"):
                result = BusinessLineScenario(
                    self.root, profile, seed_history=False
                ).execute()
                self.assertEqual(result.execution_status, PipelineExecutionStatus.BLOCKED)
                self.assertEqual(result.error_code, "STORE_EXACT_KEY_NOT_FOUND")
                self.assertNotIn("repair", result.lineage_references)


if __name__ == "__main__":
    unittest.main()
