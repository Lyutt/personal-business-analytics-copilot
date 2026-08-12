from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from weekly_acquisition_runtime.adapters import (  # noqa: E402
    ApolloQueryAdapter,
    QueryContract,
    SessionExpired,
    validate_outlook_provider,
)
from weekly_acquisition_runtime.browser_lock import BrowserAcquisitionLock  # noqa: E402
from weekly_acquisition_runtime.config_validation import load_yaml, validate_composition  # noqa: E402
from weekly_acquisition_runtime.contracts import (  # noqa: E402
    AcquisitionMode,
    AttemptManifest,
    BusinessKey,
    RunInputEntry,
)
from weekly_acquisition_runtime.draft_gate import evaluate_draft_gate  # noqa: E402
from weekly_acquisition_runtime.errors import (  # noqa: E402
    AmbiguousBindingError,
    BrowserLockOccupied,
    ImmutableArtifactError,
    PageContractDrift,
    ProviderCapabilityError,
    StorageBoundaryError,
    UnboundInputError,
)
from weekly_acquisition_runtime.runtime import AcquisitionRuntime  # noqa: E402
from weekly_acquisition_runtime.storage import LocalRuntimeStorage  # noqa: E402


def context(run_id: str = "RUN_SYNTH_001") -> dict[str, str]:
    return {
        "workflow_run_id": run_id,
        "run_type": "manual",
        "workflow_execution_date": "2026-01-08",
        "workflow_reporting_date": "2026-01-08",
        "reporting_period_id": "WEEK_SYNTH_001",
        "reporting_period_start_date": "2026-01-01",
        "reporting_period_end_date": "2026-01-07",
        "current_period_start_date": "2026-01-01",
        "current_period_end_date": "2026-01-07",
        "comparison_period_start_date": "2025-12-25",
        "comparison_period_end_date": "2025-12-31",
        "cutoff_date": "2026-01-07",
        "timezone": "Asia/Shanghai",
    }


def entry(key: BusinessKey, mode: AcquisitionMode) -> RunInputEntry:
    return RunInputEntry(
        business_key=key,
        dataset_version="0.1.0",
        query_asset_binding={"binding_status": "bound", "query_asset_id": "QRY_SYNTH_EXACT"},
        local_input_reference="not_applicable",
        source_report_date="not_applicable",
        source_business_data_cutoff_date="not_applicable",
        acquisition_mode=mode,
    )


def manifest_fields() -> dict[str, object]:
    return {
        "adapter_id": "ADP_SYNTH_EXACT_V1",
        "adapter_version": "1.0.0",
        "provider_id": "PRV_SYNTH_EXACT_V1",
        "query_asset_id_or_not_applicable": "QRY_SYNTH_EXACT",
        "normalized_parameter_readback": {"period": "SYNTH_PERIOD"},
        "started_at": "2026-01-08T10:00:00+08:00",
        "completed_at": "2026-01-08T10:00:01+08:00",
        "duration_ms": 1000,
        "session_status_code": "active",
        "row_count_or_not_applicable": 1,
        "schema_fingerprint_or_not_applicable": "SYNTH_SCHEMA_SHA256",
        "page_contract_version_or_not_applicable": "PAGE_SYNTH_V1",
        "validation_status": "passed",
    }


class RuntimeChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "runtime-data"
        self.storage = LocalRuntimeStorage(self.root, ROOT)
        self.runtime = AcquisitionRuntime(self.storage)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_manual_fallback_deterministic_chain(self) -> None:
        run = self.runtime.start_run(context())
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable")
        self.runtime.declare_input(run, entry(key, AcquisitionMode.MANUAL_FALLBACK))
        manifest, reference = self.runtime.ingest_manual_fallback(
            run=run,
            business_key=key,
            attempt_id="ATTEMPT_001",
            filename="input.bin",
            source=io.BytesIO(b"synthetic-input"),
            manifest_fields=manifest_fields(),
        )
        self.assertEqual(
            manifest.association_key,
            ("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable", "ATTEMPT_001"),
        )
        with self.assertRaises(UnboundInputError):
            self.runtime.consume_bound_input(run, key)
        self.runtime.bind_successful_attempt(run, key, manifest, reference)
        consumed = self.runtime.consume_bound_input(run, key)
        self.assertEqual(consumed.read_bytes(), b"synthetic-input")
        run_manifest_ref = self.runtime.finalize_run_input_manifest(run)
        run_manifest = json.loads(self.storage.resolve_opaque_reference(run_manifest_ref).read_text())
        bound = run_manifest["entries"][0]["acquisition_attempt_binding"]
        self.assertEqual(bound["acquisition_attempt_id"], "ATTEMPT_001")
        self.assertEqual(bound["attempt_manifest_reference"], reference)

    def test_attempt_and_manifest_are_immutable(self) -> None:
        run = self.runtime.start_run(context())
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable")
        self.runtime.declare_input(run, entry(key, AcquisitionMode.MANUAL_FALLBACK))
        self.runtime.create_attempt(run, key, "ATTEMPT_001")
        with self.assertRaises(ImmutableArtifactError):
            self.runtime.create_attempt(run, key, "ATTEMPT_001")

    def test_one_entry_cannot_bind_two_attempts(self) -> None:
        run = self.runtime.start_run(context())
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable")
        self.runtime.declare_input(run, entry(key, AcquisitionMode.MANUAL_FALLBACK))
        first, ref = self.runtime.ingest_manual_fallback(
            run=run,
            business_key=key,
            attempt_id="ATTEMPT_001",
            filename="one.bin",
            source=io.BytesIO(b"one"),
            manifest_fields=manifest_fields(),
        )
        self.runtime.bind_successful_attempt(run, key, first, ref)
        second, second_ref = self.runtime.ingest_manual_fallback(
            run=run,
            business_key=key,
            attempt_id="ATTEMPT_002",
            filename="two.bin",
            source=io.BytesIO(b"two"),
            manifest_fields=manifest_fields(),
        )
        with self.assertRaises(AmbiguousBindingError):
            self.runtime.bind_successful_attempt(run, key, second, second_ref)

    def test_failed_attempt_cannot_bind(self) -> None:
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable")
        failed = AttemptManifest(
            business_key=key,
            acquisition_attempt_id="ATTEMPT_FAILED",
            acquisition_mode=AcquisitionMode.AUTOMATED,
            local_input_opaque_reference="runs/RUN_SYNTH_001/attempts/ATTEMPT_FAILED/inputs/input.bin",
            sha256="0" * 64,
            **{**manifest_fields(), "validation_status": "failed"},
        )
        with self.assertRaisesRegex(Exception, "passed"):
            failed.require_passed()

    def test_legacy_input_uses_not_applicable_binding(self) -> None:
        run = self.runtime.start_run(context())
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "configured_history", "not_applicable")
        path = self.root / "runs" / "RUN_SYNTH_001" / "legacy.bin"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"legacy")
        legacy = entry(key, AcquisitionMode.LEGACY_PREPARED_LOCAL_INPUT)
        legacy.local_input_reference = self.storage.opaque_reference(path)
        self.runtime.declare_input(run, legacy)
        self.assertEqual(self.runtime.consume_bound_input(run, key).read_bytes(), b"legacy")


class BoundaryTests(unittest.TestCase):
    def test_storage_rejects_repository_and_onedrive(self) -> None:
        with self.assertRaises(StorageBoundaryError):
            LocalRuntimeStorage(ROOT / "runtime-data", ROOT)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(StorageBoundaryError):
                LocalRuntimeStorage(Path(temp) / "OneDrive" / "runtime-data", ROOT)

    def test_global_browser_lock_has_no_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            first = BrowserAcquisitionLock(state)
            second = BrowserAcquisitionLock(state)
            first.acquire({"workflow_run_id": "RUN_SYNTH_001", "acquisition_attempt_id": "ATTEMPT_001"})
            try:
                with self.assertRaises(BrowserLockOccupied):
                    second.acquire({"workflow_run_id": "RUN_SYNTH_002"})
            finally:
                first.release()
            self.assertTrue(first.path.exists(), "normal release must not delete lock metadata")

    def test_stale_browser_lock_requires_manual_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            lock = BrowserAcquisitionLock(state)
            lock.path.write_text('{"status":"held","workflow_run_id":"RUN_OLD"}', encoding="utf-8")
            with self.assertRaises(BrowserLockOccupied):
                lock.acquire({"workflow_run_id": "RUN_NEW"})
            self.assertEqual(json.loads(lock.path.read_text())["workflow_run_id"], "RUN_OLD")

    def test_draft_activation_gate(self) -> None:
        self.assertFalse(
            evaluate_draft_gate(
                workflow_status="complete_draft",
                runtime_acceptance_passed=False,
                human_confirmation=False,
                post_acceptance_auto_draft_owner_approved=False,
            ).create_draft
        )
        partial = evaluate_draft_gate(
            workflow_status="partial_draft",
            runtime_acceptance_passed=False,
            human_confirmation=True,
            post_acceptance_auto_draft_owner_approved=False,
        )
        self.assertTrue(partial.create_draft)
        self.assertTrue(partial.warning_preface_required)
        self.assertFalse(partial.auto_send)
        self.assertFalse(
            evaluate_draft_gate(
                workflow_status="blocked",
                runtime_acceptance_passed=True,
                human_confirmation=True,
                post_acceptance_auto_draft_owner_approved=True,
            ).create_draft
        )

    def test_outlook_capability_failure_has_no_fallback(self) -> None:
        class Provider:
            provider_id = "PRV_OUTLOOK_EMAIL_PRIMARY_V1"

            @staticmethod
            def capabilities() -> set[str]:
                return {"contract_scoped_mailbox_search"}

        with self.assertRaises(ProviderCapabilityError):
            validate_outlook_provider(Provider())


class FakePage:
    def __init__(self, expire_once: bool = False, wrong_schema: bool = False) -> None:
        self.expire_once = expire_once
        self.wrong_schema = wrong_schema
        self.refresh_count = 0
        self.query_count = 0
        self.parameters: dict[str, object] = {}

    def enter_exact_module(self, module_id: str) -> None:
        if module_id != "MODULE_EXACT":
            raise LookupError

    def select_exact_template(self, template_id: str) -> None:
        if template_id != "TEMPLATE_EXACT":
            raise LookupError

    def set_exact_parameters(self, parameters: dict[str, object]) -> None:
        self.parameters = dict(parameters)

    def read_parameter_values(self) -> dict[str, object]:
        return dict(self.parameters)

    def execute_query(self) -> None:
        self.query_count += 1
        if self.expire_once and self.query_count == 1:
            raise SessionExpired

    def read_result(self):
        columns = ("wrong",) if self.wrong_schema else ("date", "value")
        return columns, ({"date": "SYNTH_DATE", "value": 1},)

    def refresh(self) -> None:
        self.refresh_count += 1


class AdapterTests(unittest.TestCase):
    @staticmethod
    def contract() -> QueryContract:
        return QueryContract(
            adapter_id="ADP_INTERNAL_APOLLO_QUERY_V1",
            adapter_version="1.0.0",
            provider_id="PRV_INTERNAL_APOLLO_PLAYWRIGHT_V1",
            source_id="SRC_INTERNAL_PLATFORM_APOLLO",
            dataset_id="DS_SYNTH",
            query_asset_id="QRY_SYNTH_EXACT",
            module_id="MODULE_EXACT",
            template_id="TEMPLATE_EXACT",
            parameters={"period": "SYNTH_PERIOD"},
            expected_columns=("date", "value"),
            page_contract_version="PAGE_SYNTH_V1",
        )

    def test_single_refresh_single_recovery_query(self) -> None:
        page = FakePage(expire_once=True)
        result = ApolloQueryAdapter(page, self.contract()).acquire()
        self.assertEqual(page.refresh_count, 1)
        self.assertEqual(page.query_count, 2)
        self.assertEqual(result.recovery_refresh_count, 1)
        self.assertEqual(result.recovery_query_count, 1)

    def test_schema_drift_blocks(self) -> None:
        with self.assertRaises(PageContractDrift):
            ApolloQueryAdapter(FakePage(wrong_schema=True), self.contract()).acquire()


class CandidateCompositionTests(unittest.TestCase):
    def test_candidate_composition(self) -> None:
        runtime = load_yaml(ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1_1_candidate.yaml")
        extension = load_yaml(ROOT / "phase1_5/assets/execution/weekly_acquisition_automation_contracts_v1_1_candidate.yaml")
        validate_composition(runtime, extension)
        self.assertEqual(
            runtime["run_input_manifest"]["entry_business_key"],
            ["workflow_run_id", "dataset_id", "period_role", "product_parameter"],
        )
        self.assertEqual(
            runtime["workflow_run_context"]["required_fields"]["run_type"]["allowed_values"],
            ["scheduled", "manual", "backfill"],
        )
        self.assertFalse(runtime["governance"]["auto_send"])


if __name__ == "__main__":
    unittest.main()
