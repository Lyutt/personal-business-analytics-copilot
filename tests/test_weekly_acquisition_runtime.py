from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from weekly_acquisition_runtime.adapters import (  # noqa: E402
    ApolloQueryAdapter,
    AdapterRegistry,
    QueryContract,
    SessionExpired,
    validate_outlook_provider,
)
from weekly_acquisition_runtime.browser_lock import BrowserAcquisitionLock  # noqa: E402
from weekly_acquisition_runtime.config_validation import load_yaml, validate_composition  # noqa: E402
from weekly_acquisition_runtime.cli import build_parser, main as cli_main  # noqa: E402
from weekly_acquisition_runtime.contracts import (  # noqa: E402
    AcquisitionMode,
    AttemptManifest,
    BusinessKey,
    InputBindingRegistry,
    LockedRunContext,
    RegisteredInputBinding,
    RunInputEntry,
)
from weekly_acquisition_runtime.draft_gate import evaluate_draft_gate  # noqa: E402
from weekly_acquisition_runtime.errors import (  # noqa: E402
    AmbiguousBindingError,
    BrowserLockOccupied,
    ContractViolation,
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


def synthetic_registry() -> InputBindingRegistry:
    return InputBindingRegistry(
        workflow_id="WF_WEEKLY_BUSINESS_REPORT",
        bindings={
            "DS_SYNTH": RegisteredInputBinding(
                dataset_id="DS_SYNTH",
                query_asset_id_or_not_applicable="QRY_SYNTH_EXACT",
                adapter_id="ADP_SYNTH_EXACT_V1",
                source_id="SRC_SYNTH",
            ),
            "DS_PRODUCT_SYNTH": RegisteredInputBinding(
                dataset_id="DS_PRODUCT_SYNTH",
                query_asset_id_or_not_applicable="QRY_PRODUCT_SYNTH",
                adapter_id="ADP_SYNTH_EXACT_V1",
                source_id="SRC_SYNTH",
                product_scoped=True,
            ),
            "DS_OUTLOOK_SYNTH": RegisteredInputBinding(
                dataset_id="DS_OUTLOOK_SYNTH",
                query_asset_id_or_not_applicable="not_applicable",
                adapter_id="ADP_OUTLOOK_EMAIL_INPUT_V1",
                source_id="SRC_CORP_OUTLOOK_PRIMARY_MAILBOX",
            ),
        },
    )


class RuntimeChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "runtime-data"
        self.storage = LocalRuntimeStorage(self.root, ROOT)
        self.runtime = AcquisitionRuntime(self.storage, synthetic_registry())

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

    def test_manifest_file_mutation_after_binding_blocks_consumption(self) -> None:
        run = self.runtime.start_run(context())
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable")
        self.runtime.declare_input(run, entry(key, AcquisitionMode.MANUAL_FALLBACK))
        manifest, reference = self.runtime.ingest_manual_fallback(
            run=run,
            business_key=key,
            attempt_id="ATTEMPT_MUTATION",
            filename="input.bin",
            source=io.BytesIO(b"original"),
            manifest_fields=manifest_fields(),
        )
        self.runtime.bind_successful_attempt(run, key, manifest, reference)
        input_path = self.storage.resolve_opaque_reference(manifest.local_input_opaque_reference)
        input_path.write_bytes(b"modified-after-manifest")
        with self.assertRaisesRegex(ContractViolation, "SHA-256"):
            self.runtime.consume_bound_input(run, key)

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

    def test_attempt_and_run_business_key_mismatch_blocks(self) -> None:
        run = self.runtime.start_run(context())
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable")
        self.runtime.declare_input(run, entry(key, AcquisitionMode.MANUAL_FALLBACK))
        other_run_key = BusinessKey("RUN_OTHER", "DS_SYNTH", "current", "not_applicable")
        with self.assertRaisesRegex(ContractViolation, "locked Run Context"):
            self.runtime.create_attempt(run, other_run_key, "ATTEMPT_WRONG_RUN")

    def test_outlook_source_date_binding_mismatch_blocks(self) -> None:
        run = self.runtime.start_run(context())
        key = BusinessKey(
            "RUN_SYNTH_001", "DS_OUTLOOK_SYNTH", "current", "not_applicable"
        )
        outlook_entry = RunInputEntry(
            business_key=key,
            dataset_version="1.0.0",
            query_asset_binding={"binding_status": "not_applicable"},
            local_input_reference="not_applicable",
            source_report_date="2026-01-07",
            source_business_data_cutoff_date="2026-01-07",
            acquisition_mode=AcquisitionMode.MANUAL_FALLBACK,
        )
        with self.assertRaisesRegex(ContractViolation, "workflow_reporting_date"):
            self.runtime.declare_input(run, outlook_entry)


class ContractEnforcementTests(unittest.TestCase):
    def test_invalid_and_null_run_context_dates_block(self) -> None:
        for invalid in (None, "2026-02-30", "2026/01/08"):
            with self.subTest(invalid=invalid):
                values = context()
                values["workflow_reporting_date"] = invalid  # type: ignore[assignment]
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaises(ContractViolation):
                        AcquisitionRuntime(
                            LocalRuntimeStorage(Path(temp) / "runtime", ROOT),
                            synthetic_registry(),
                        ).start_run(values)

    def test_invalid_run_type_and_timezone_block(self) -> None:
        for field_name, invalid in (("run_type", "automatic"), ("timezone", None)):
            with self.subTest(field_name=field_name):
                values = context()
                values[field_name] = invalid  # type: ignore[assignment]
                with self.assertRaises(ContractViolation):
                    LockedRunContext.lock(values)

    def test_invalid_period_role_blocks(self) -> None:
        with self.assertRaisesRegex(ContractViolation, "period_role"):
            BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "latest", "not_applicable")

    def test_invalid_query_binding_blocks(self) -> None:
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable")
        invalid_status = entry(key, AcquisitionMode.MANUAL_FALLBACK)
        invalid_status.query_asset_binding = {"binding_status": "unknown"}
        with self.assertRaisesRegex(ContractViolation, "binding_status"):
            invalid_status.validate(synthetic_registry())
        missing_query = entry(key, AcquisitionMode.MANUAL_FALLBACK)
        missing_query.query_asset_binding = {"binding_status": "bound"}
        with self.assertRaisesRegex(ContractViolation, "query_asset_id"):
            missing_query.validate(synthetic_registry())
        wrong_query = entry(key, AcquisitionMode.MANUAL_FALLBACK)
        wrong_query.query_asset_binding = {
            "binding_status": "bound",
            "query_asset_id": "QRY_UNREGISTERED",
        }
        with self.assertRaisesRegex(ContractViolation, "registered Dataset"):
            wrong_query.validate(synthetic_registry())

    def test_product_scoped_input_requires_product_parameter(self) -> None:
        key = BusinessKey(
            "RUN_SYNTH_001", "DS_PRODUCT_SYNTH", "current", "not_applicable"
        )
        product_entry = entry(key, AcquisitionMode.MANUAL_FALLBACK)
        product_entry.query_asset_binding = {
            "binding_status": "bound",
            "query_asset_id": "QRY_PRODUCT_SYNTH",
        }
        with self.assertRaisesRegex(ContractViolation, "product-scoped"):
            product_entry.validate(synthetic_registry())

    def test_source_date_format_blocks(self) -> None:
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable")
        invalid = entry(key, AcquisitionMode.MANUAL_FALLBACK)
        invalid.source_report_date = "2026-13-40"
        with self.assertRaises(ContractViolation):
            invalid.validate(synthetic_registry())

    def test_none_adapter_and_provider_ids_block(self) -> None:
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable")
        base = dict(
            business_key=key,
            acquisition_attempt_id="ATTEMPT_NONE_ID",
            acquisition_mode=AcquisitionMode.AUTOMATED,
            local_input_opaque_reference="runs/RUN_SYNTH_001/attempts/ATTEMPT_NONE_ID/inputs/input.bin",
            sha256="0" * 64,
            **manifest_fields(),
        )
        for field_name in ("adapter_id", "provider_id"):
            with self.subTest(field_name=field_name):
                values = {**base, field_name: None}
                with self.assertRaises(ContractViolation):
                    AttemptManifest(**values)

    def test_malformed_sha256_blocks(self) -> None:
        key = BusinessKey("RUN_SYNTH_001", "DS_SYNTH", "current", "not_applicable")
        with self.assertRaisesRegex(ContractViolation, "sha256"):
            AttemptManifest(
                business_key=key,
                acquisition_attempt_id="ATTEMPT_BAD_HASH",
                acquisition_mode=AcquisitionMode.AUTOMATED,
                local_input_opaque_reference="runs/RUN_SYNTH_001/attempts/ATTEMPT_BAD_HASH/inputs/input.bin",
                sha256="not-a-sha256",
                **manifest_fields(),
            )

    def test_runtime_has_no_implicit_latest_selection(self) -> None:
        source = (ROOT / "src/weekly_acquisition_runtime/runtime.py").read_text(encoding="utf-8")
        storage = (ROOT / "src/weekly_acquisition_runtime/storage.py").read_text(encoding="utf-8")
        for prohibited in ("getmtime(", ".st_mtime", ".glob(", ".rglob(", ".iterdir("):
            self.assertNotIn(prohibited, source + storage)


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


class ExplicitBindingCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp.name) / "runtime-data"
        config = self.runtime_root / "runtime-config" / "explicit.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "run_context": context(),
                    "period_role": "current",
                    "product_parameter": "not_applicable",
                    "dataset_version": "1.0.0",
                    "source_report_date": "not_applicable",
                    "source_business_data_cutoff_date": "2026-01-07",
                }
            ),
            encoding="utf-8",
        )
        self.args = [
            "acquire-one-explicit-binding",
            "--workflow-id",
            "WF_WEEKLY_BUSINESS_REPORT",
            "--workflow-run-id",
            "RUN_SYNTH_001",
            "--acquisition-attempt-id",
            "ATTEMPT_CLI_001",
            "--adapter-id",
            "ADP_INTERNAL_APOLLO_QUERY_V1",
            "--dataset-id",
            "DS_REVENUE_APOLLO_BUSINESS_LINE_SUMMARY",
            "--query-asset-id-or-not-applicable",
            "QRY_APOLLO_QISHENG_EXECUTION_REVENUE",
            "--local-runtime-config-reference",
            "runtime-config/explicit.json",
        ]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def invoke_with_output(
        self, args: list[str], registry: AdapterRegistry | None = None
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.dict(
            os.environ,
            {"LOCAL_WORKFLOW_DATA_ROOT_LOCAL_ONLY": str(self.runtime_root)},
            clear=False,
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli_main(args, registry)
        return result, stdout.getvalue(), stderr.getvalue()

    def invoke(self, args: list[str], registry: AdapterRegistry | None = None) -> int:
        return self.invoke_with_output(args, registry)[0]

    def test_all_explicit_fields_validate_before_provider_block(self) -> None:
        result, _, error = self.invoke_with_output(self.args)
        self.assertEqual(result, 2)
        self.assertIn("No Stage 5 Provider is configured", error)
        self.assertIn("fallback is prohibited", error)

    def test_cli_required_fields_match_candidate_contract(self) -> None:
        parser = build_parser()
        acquire_parser = next(
            action.choices["acquire-one-explicit-binding"]
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        required = {
            action.dest.replace("_", "-")
            for action in acquire_parser._actions
            if action.required
        }
        self.assertEqual(
            required,
            {
                "workflow-id",
                "workflow-run-id",
                "acquisition-attempt-id",
                "adapter-id",
                "dataset-id",
                "query-asset-id-or-not-applicable",
                "local-runtime-config-reference",
            },
        )

    def test_invalid_binding_never_calls_adapter_boundary(self) -> None:
        class FakeAdapter:
            called = False

            def acquire(self) -> None:
                self.called = True

        adapter = FakeAdapter()
        registry = AdapterRegistry()
        registry.register("ADP_INTERNAL_APOLLO_QUERY_V1", adapter)
        args = list(self.args)
        args[args.index("QRY_APOLLO_QISHENG_EXECUTION_REVENUE")] = "QRY_WRONG"
        self.assertEqual(self.invoke(args, registry), 2)
        self.assertFalse(adapter.called)

    def test_valid_explicit_binding_calls_exact_adapter_boundary(self) -> None:
        class FakeAdapter:
            called = False

            def acquire(self) -> None:
                self.called = True

        adapter = FakeAdapter()
        registry = AdapterRegistry()
        registry.register("ADP_INTERNAL_APOLLO_QUERY_V1", adapter)
        self.assertEqual(self.invoke(self.args, registry), 0)
        self.assertTrue(adapter.called)


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

    def test_candidate_composition_exact_identity_mismatches_block(self) -> None:
        runtime = load_yaml(ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1_1_candidate.yaml")
        extension = load_yaml(ROOT / "phase1_5/assets/execution/weekly_acquisition_automation_contracts_v1_1_candidate.yaml")
        for field_name, bad_value in (
            ("extension_contract_id", "ACQUISITION_WRONG"),
            ("extension_contract_version", "1.1.1"),
        ):
            with self.subTest(field_name=field_name):
                changed = json.loads(json.dumps(runtime))
                changed["acquisition_automation_contract_binding"][field_name] = bad_value
                with self.assertRaises(ContractViolation):
                    validate_composition(changed, extension)
        changed_extension = json.loads(json.dumps(extension))
        changed_extension["workflow_id"] = "WF_WRONG"
        with self.assertRaises(ContractViolation):
            validate_composition(runtime, changed_extension)


if __name__ == "__main__":
    unittest.main()
