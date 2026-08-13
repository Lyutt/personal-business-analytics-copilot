from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from weekly_acquisition_runtime.contracts import (
    AcquisitionMode,
    BusinessKey,
    InputBindingRegistry,
    LockedRunContext,
    RegisteredInputBinding,
    RunInputEntry,
)
from weekly_acquisition_runtime.runtime import AcquisitionRuntime
from weekly_acquisition_runtime.storage import LocalRuntimeStorage
from weekly_business_runtime.assets import (
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
from weekly_business_runtime.ctv_dataset import CtvDatasetLoader
from weekly_business_runtime.ctv_pipeline import CtvPipelineExecutor
from weekly_business_runtime.errors import (
    DatasetValidationError,
    MetricStoreError,
    ResultContractError,
)
from weekly_business_runtime.models import PipelineExecutionStatus
from weekly_business_runtime.store import (
    InMemoryMetricStore,
    MetricStoreRecord,
    StoreReadKey,
)


def locked_context(run_id: str = "RUN_SYNTH_CTV_001") -> dict[str, object]:
    return {
        "workflow_run_id": run_id,
        "run_type": "manual",
        "workflow_execution_date": "2026-07-23",
        "workflow_reporting_date": "2026-07-23",
        "reporting_period_id": "2026-W30",
        "reporting_period_start_date": "2026-07-17",
        "reporting_period_end_date": "2026-07-23",
        "current_period_start_date": "2026-07-17",
        "current_period_end_date": "2026-07-23",
        "comparison_period_start_date": "2026-07-10",
        "comparison_period_end_date": "2026-07-16",
        "cutoff_date": "2026-07-22",
        "timezone": "Asia/Shanghai",
        "current_revenue_cutoff_date": "2026-07-22",
        "expected_previous_revenue_workflow_reporting_date": "2026-07-16",
        "target_report_period": "2026-W30",
        "workflow_year": 2026,
        "target_fiscal_quarter": "2026Q3",
        "target_previous_calendar_quarter": "2026Q2",
        "report_mode": "regular_week",
        "target_revenue_cutoff_date": "2026-07-22",
    }


def store_record(
    variant_id: str,
    *,
    workflow_reporting_date: str = "2026-07-16",
    value: Decimal = Decimal("1"),
    result_id_suffix: str = "A",
) -> MetricStoreRecord:
    return MetricStoreRecord(
        result_id=f"HIST_{variant_id}_{result_id_suffix}",
        workflow_id=WORKFLOW_ID,
        workflow_run_id="RUN_SYNTH_PRIOR",
        pipeline_id=PIPELINE_ID,
        pipeline_run_id="PIPELINE_SYNTH_PRIOR",
        store_id=STORE_ID,
        store_asset_id=STORE_ASSET_ID,
        metric_variant_id=variant_id,
        metric_variant_version="1.0.0-draft",
        workflow_reporting_date=workflow_reporting_date,
        current_revenue_cutoff_date="2026-07-15",
        business_context_id=BUSINESS_CONTEXT_ID,
        reporting_period="2026-W29",
        value=value,
        value_status="valid_value",
        numeric_semantics="monetary_amount",
        unit="CNY_yuan",
        precision="preserve_source_precision",
        validation_status="passed",
        generated_at="2026-07-16T17:30:00+08:00",
        lineage_references=("synthetic://history",),
    )


class CtvScenario:
    def __init__(self, root: Path, *, seed_history: bool = True) -> None:
        self.assets = CtvAssetBundle.load(ROOT)
        self.storage = LocalRuntimeStorage(root / "runtime", ROOT)
        registry = InputBindingRegistry(
            workflow_id=WORKFLOW_ID,
            bindings={
                CURRENT_DATASET_ID: RegisteredInputBinding(
                    dataset_id=CURRENT_DATASET_ID,
                    query_asset_id_or_not_applicable="not_applicable",
                    adapter_id="ADP_OUTLOOK_EMAIL_V1",
                    source_id="SRC_CORP_OUTLOOK_PRIMARY_MAILBOX",
                    provider_id="PRV_OUTLOOK_EMAIL_PRIMARY_V1",
                    dataset_version_constraints=(">=0.1.0,<0.2.0",),
                ),
                PRIOR_DATASET_ID: RegisteredInputBinding(
                    dataset_id=PRIOR_DATASET_ID,
                    query_asset_id_or_not_applicable="not_applicable",
                    adapter_id="ADP_OUTLOOK_EMAIL_V1",
                    source_id="SRC_CORP_OUTLOOK_PRIMARY_MAILBOX",
                    provider_id="PRV_OUTLOOK_EMAIL_PRIMARY_V1",
                    dataset_version_constraints=(">=0.1.0,<0.2.0",),
                ),
            },
        )
        self.runtime = AcquisitionRuntime(self.storage, registry)
        self.run = self.runtime.start_run(locked_context())
        self.current_key = BusinessKey(
            self.run.context.workflow_run_id,
            CURRENT_DATASET_ID,
            "current",
            "not_applicable",
        )
        self.prior_key = BusinessKey(
            self.run.context.workflow_run_id,
            PRIOR_DATASET_ID,
            "prior_year_comparable",
            "not_applicable",
        )
        inputs = self.storage.root / "runs" / self.run.context.workflow_run_id / "legacy_inputs"
        inputs.mkdir(parents=True)
        self.current_path = inputs / "ctv_current.xlsx"
        self.prior_path = inputs / "ctv_prior.xlsx"
        self.write_current()
        self.write_prior()
        self.declare_current()
        self.declare_prior()
        self.store = InMemoryMetricStore()
        if seed_history:
            self.store.seed_historical(*(store_record(variant_id) for variant_id in CTV_VARIANT_IDS))
        self.executor = CtvPipelineExecutor(
            acquisition_runtime=self.runtime,
            assets=self.assets,
            metric_store=self.store,
        )

    def write_current(
        self,
        *,
        order_ids: tuple[object, ...] = ("SYNTH_ORDER_A", "SYNTH_ORDER_B"),
        executed: tuple[object, ...] = (50, 30),
        signed: tuple[object, ...] = (100, 60),
        ctv_signed: tuple[object, ...] = (40, 20),
        drop_header: str | None = None,
        extra_column: bool = False,
    ) -> None:
        raw_fields = [item["raw_field_name"] for item in self.assets.current_mapping["raw_field_inventory"]]
        rows: list[dict[str, object]] = []
        for index, order_id in enumerate(order_ids):
            row = dict.fromkeys(raw_fields, "")
            row.update(
                {
                    "订单ID": order_id,
                    "已执行收入(季度)": executed[index],
                    "总收入(季度)": signed[index],
                    "TV总金额(季度)": ctv_signed[index],
                }
            )
            rows.append(row)
        frame = pd.DataFrame(rows, columns=raw_fields)
        if drop_header is not None:
            frame = frame.drop(columns=[drop_header])
        if extra_column:
            frame["SYNTH_NEW_FIELD"] = "synthetic"
        frame.to_excel(self.current_path, sheet_name="Sheet0", index=False)

    def write_prior(self, *, value: object = 30, duplicate_ctv: bool = False) -> None:
        lines = ["CTV", "CTV"] if duplicate_ctv else ["CTV"]
        values = [value, value] if duplicate_ctv else [value]
        pd.DataFrame({"业务线": lines, "25Q3": values}).to_excel(
            self.prior_path, sheet_name="业务线", index=False
        )

    def declare_current(self) -> None:
        self.runtime.declare_input(
            self.run,
            RunInputEntry(
                business_key=self.current_key,
                dataset_version="0.1.0",
                query_asset_binding={"binding_status": "not_applicable"},
                local_input_reference=self.storage.opaque_reference(self.current_path),
                source_report_date="2026-07-23",
                source_business_data_cutoff_date="2026-07-22",
                acquisition_mode=AcquisitionMode.LEGACY_PREPARED_LOCAL_INPUT,
            ),
        )

    def declare_prior(self, *, cutoff: str = "2025-07-23") -> None:
        self.runtime.declare_input(
            self.run,
            RunInputEntry(
                business_key=self.prior_key,
                dataset_version="0.1.0",
                query_asset_binding={"binding_status": "not_applicable"},
                local_input_reference=self.storage.opaque_reference(self.prior_path),
                source_report_date="2026-07-23",
                source_business_data_cutoff_date=cutoff,
                acquisition_mode=AcquisitionMode.LEGACY_PREPARED_LOCAL_INPUT,
            ),
        )

    def execute(self):
        return self.executor.execute(
            run=self.run,
            pipeline_run_id="PIPELINE_RUN_SYNTH_CTV_001",
            current_input_key=self.current_key,
            prior_year_input_key=self.prior_key,
            generated_at="2026-07-23T17:31:00+08:00",
        )


class Stage3ACtvVerticalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_authority_assets_compose_exactly(self) -> None:
        assets = CtvAssetBundle.load(ROOT)
        self.assertEqual(set(assets.metric_variants), set(CTV_VARIANT_IDS))
        self.assertEqual(assets.result_contract["result_contract_id"], "RC_REVENUE_CTV_WEEKLY")

    def test_golden_case_produces_expected_contract_store_and_lineage(self) -> None:
        scenario = CtvScenario(self.root)
        result = scenario.execute()
        self.assertEqual(result.execution_status, PipelineExecutionStatus.COMPLETED)
        contract = result.result_contract
        self.assertIsNotNone(contract)
        assert contract is not None
        self.assertEqual(contract.field("qtd_performance_revenue").value, Decimal("60"))
        self.assertEqual(contract.field("qtd_executed_revenue").value, Decimal("30.0"))
        self.assertEqual(
            contract.field("qtd_performance_revenue_yoy").value,
            (Decimal("60") / Decimal("22")) / (Decimal("30") / Decimal("23")) - 1,
        )
        self.assertEqual(contract.validation_status, "passed")
        self.assertEqual(len(result.lineage_references), 8)
        for variant_id in CTV_VARIANT_IDS:
            key = StoreReadKey(
                STORE_ID,
                STORE_ASSET_ID,
                variant_id,
                "2026-07-23",
                BUSINESS_CONTEXT_ID,
            )
            self.assertEqual(len(scenario.store.records_for(key)), 1)

    def test_deterministic_rerun_returns_same_result_and_idempotent_store(self) -> None:
        scenario = CtvScenario(self.root)
        first = scenario.execute()
        second = scenario.execute()
        self.assertEqual(first.result_contract, second.result_contract)
        self.assertEqual(first.execution_status, second.execution_status)
        for variant_id in CTV_VARIANT_IDS:
            key = StoreReadKey(
                STORE_ID, STORE_ASSET_ID, variant_id, "2026-07-23", BUSINESS_CONTEXT_ID
            )
            self.assertEqual(len(scenario.store.records_for(key)), 1)

    def test_unbound_required_current_input_blocks(self) -> None:
        scenario = CtvScenario(self.root)
        scenario.run.run_input_manifest._entries.pop(scenario.current_key.as_tuple())
        result = scenario.execute()
        self.assertEqual(result.execution_status, PipelineExecutionStatus.BLOCKED)
        self.assertEqual(result.error_code, "CTV_INPUT_BINDING_INVALID")

    def test_missing_required_mapping_header_blocks(self) -> None:
        scenario = CtvScenario(self.root)
        scenario.write_current(drop_header="TV总金额(季度)")
        result = scenario.execute()
        self.assertEqual(result.error_code, "CTV_REQUIRED_MAPPING_MISSING")

    def test_mapping_conflict_blocks_without_guessing(self) -> None:
        scenario = CtvScenario(self.root)
        mapping = copy.deepcopy(scenario.assets.current_mapping)
        mapping["field_mappings"][1]["standard_field_id"] = "order_id"
        bad_assets = replace(scenario.assets, current_mapping=mapping)
        loader = CtvDatasetLoader(bad_assets)
        with self.assertRaisesRegex(DatasetValidationError, "duplicate source or target"):
            loader.load_current(scenario.current_path, "runtime://synthetic")

    def test_duplicate_source_header_blocks(self) -> None:
        scenario = CtvScenario(self.root)
        frame = pd.read_excel(scenario.current_path, sheet_name="Sheet0")
        frame.columns = [*frame.columns[:-1], frame.columns[-2]]
        frame.to_excel(scenario.current_path, sheet_name="Sheet0", index=False)
        result = scenario.execute()
        self.assertEqual(result.error_code, "CTV_DUPLICATE_SOURCE_HEADER")

    def test_nonblank_non_numeric_amount_blocks(self) -> None:
        scenario = CtvScenario(self.root)
        scenario.write_current(executed=("INVALID", 30))
        result = scenario.execute()
        self.assertEqual(result.error_code, "CTV_NUMERIC_VALUE_INVALID")

    def test_blank_numeric_amount_normalizes_to_zero_per_mapping_contract(self) -> None:
        scenario = CtvScenario(self.root)
        scenario.write_current(executed=("", 30))
        result = scenario.execute()
        self.assertIn(
            result.execution_status,
            {PipelineExecutionStatus.COMPLETED, PipelineExecutionStatus.COMPLETED_WITH_WARNING},
        )
        assert result.result_contract is not None
        self.assertEqual(result.result_contract.field("qtd_executed_revenue").value, Decimal("10.0"))

    def test_blank_and_duplicate_order_ids_warn_and_are_retained(self) -> None:
        scenario = CtvScenario(self.root)
        scenario.write_current(order_ids=("", "SYNTH_DUP", "SYNTH_DUP"), executed=(10, 20, 30), signed=(20, 40, 60), ctv_signed=(8, 16, 24))
        result = scenario.execute()
        self.assertEqual(result.execution_status, PipelineExecutionStatus.COMPLETED_WITH_WARNING)
        codes = {warning.code for warning in result.warnings}
        self.assertIn("CTV_ORDER_ID_BLANK_RETAINED", codes)
        self.assertIn("CTV_ORDER_ID_DUPLICATE_RETAINED", codes)

    def test_unknown_source_field_warns_but_does_not_change_mapping(self) -> None:
        scenario = CtvScenario(self.root)
        scenario.write_current(extra_column=True)
        result = scenario.execute()
        self.assertEqual(result.execution_status, PipelineExecutionStatus.COMPLETED_WITH_WARNING)
        self.assertIn("CTV_SCHEMA_DRIFT_UNKNOWN_FIELD", {item.code for item in result.warnings})

    def test_missing_historical_exact_key_blocks(self) -> None:
        scenario = CtvScenario(self.root, seed_history=False)
        result = scenario.execute()
        self.assertEqual(result.error_code, "STORE_EXACT_KEY_NOT_FOUND")

    def test_ambiguous_historical_exact_key_blocks(self) -> None:
        scenario = CtvScenario(self.root)
        scenario.store.seed_historical(store_record(CTV_VARIANT_IDS[0], result_id_suffix="B"))
        result = scenario.execute()
        self.assertEqual(result.error_code, "STORE_EXACT_KEY_AMBIGUOUS")

    def test_prior_input_date_mismatch_only_marks_yoy_missing(self) -> None:
        scenario = CtvScenario(self.root)
        entry = scenario.run.run_input_manifest.get_entry(scenario.prior_key)
        entry.source_business_data_cutoff_date = "2025-07-22"
        result = scenario.execute()
        self.assertEqual(result.execution_status, PipelineExecutionStatus.COMPLETED_WITH_WARNING)
        assert result.result_contract is not None
        self.assertIsNone(result.result_contract.field("qtd_performance_revenue_yoy").value)
        self.assertEqual(
            result.result_contract.field("qtd_performance_revenue_yoy").value_status.value,
            "missing",
        )
        self.assertEqual(result.result_contract.field("qtd_performance_revenue").value, Decimal("60"))

    def test_missing_prior_input_only_marks_yoy_missing(self) -> None:
        scenario = CtvScenario(self.root)
        scenario.run.run_input_manifest._entries.pop(scenario.prior_key.as_tuple())
        result = scenario.execute()
        self.assertEqual(result.execution_status, PipelineExecutionStatus.COMPLETED_WITH_WARNING)
        assert result.result_contract is not None
        self.assertIsNone(result.result_contract.field("qtd_performance_revenue_yoy").value)
        self.assertEqual(result.result_contract.field("qtd_executed_revenue").value, Decimal("30.0"))

    def test_context_mismatch_blocks_before_calculation(self) -> None:
        scenario = CtvScenario(self.root)
        values = dict(scenario.run.context.values)
        values["target_revenue_cutoff_date"] = "2026-07-21"
        scenario.run.context = LockedRunContext.lock(values)
        result = scenario.execute()
        self.assertEqual(result.error_code, "CTV_REVENUE_CUTOFF_ALIAS_MISMATCH")

    def test_result_contract_mismatch_fails_closed(self) -> None:
        scenario = CtvScenario(self.root)
        result = scenario.execute()
        assert result.result_contract is not None
        invalid = replace(result.result_contract, result_contract_id="RC_SYNTH_INVALID")
        with self.assertRaises(ResultContractError):
            scenario.executor.assembler.validate(invalid)

    def test_invalid_metric_result_cannot_be_written(self) -> None:
        store = InMemoryMetricStore()
        invalid = replace(store_record(CTV_VARIANT_IDS[0]), validation_status="failed")
        with self.assertRaisesRegex(MetricStoreError, "Only passed"):
            store.write_validated(invalid)
        self.assertEqual(store.records_for(invalid.read_key), ())

    def test_non_idempotent_duplicate_write_is_protected(self) -> None:
        store = InMemoryMetricStore()
        original = store_record(CTV_VARIANT_IDS[0])
        store.write_validated(original)
        conflict = replace(original, value=Decimal("2"), result_id="CONFLICT")
        with self.assertRaisesRegex(MetricStoreError, "different or ambiguous"):
            store.write_validated(conflict)
        self.assertEqual(store.read_exact(original.read_key), original)

    def test_store_verification_failure_preserves_valid_result_with_warning(self) -> None:
        scenario = CtvScenario(self.root)
        key = StoreReadKey(
            STORE_ID,
            STORE_ASSET_ID,
            CTV_VARIANT_IDS[0],
            "2026-07-23",
            BUSINESS_CONTEXT_ID,
        )
        scenario.store.force_verification_failure(key)
        result = scenario.execute()
        self.assertEqual(result.execution_status, PipelineExecutionStatus.COMPLETED_WITH_WARNING)
        self.assertIsNotNone(result.result_contract)
        self.assertIn("STORE_WRITE_VERIFICATION_FAILED", {item.code for item in result.warnings})


if __name__ == "__main__":
    unittest.main()
