from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pydantic
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from weekly_acquisition_runtime.contracts import (
    AcquisitionAttemptBinding,
    AcquisitionMode,
    AttemptManifest,
    BusinessKey,
    InputBindingRegistry,
    LockedRunContext,
    RegisteredInputBinding,
    RunInputEntry,
)
from weekly_acquisition_runtime.errors import (
    ContractViolation,
    ValidatorParityError,
)
from weekly_acquisition_runtime.pydantic_models import (
    AcquisitionAttemptBindingModel,
    AttemptManifestModel,
    BusinessKeyModel,
    LockedRunContextModel,
    RunInputEntryModel,
)


def valid_context() -> dict[str, str]:
    return {
        "workflow_run_id": "RUN_SYNTH_PYDANTIC",
        "run_type": "manual",
        "workflow_execution_date": "2026-01-08",
        "workflow_reporting_date": "2026-01-08",
        "reporting_period_id": "WEEK_SYNTH_PYDANTIC",
        "reporting_period_start_date": "2026-01-01",
        "reporting_period_end_date": "2026-01-07",
        "current_period_start_date": "2026-01-01",
        "current_period_end_date": "2026-01-07",
        "comparison_period_start_date": "2025-12-25",
        "comparison_period_end_date": "2025-12-31",
        "cutoff_date": "2026-01-07",
        "timezone": "Asia/Shanghai",
    }


def registry() -> InputBindingRegistry:
    return InputBindingRegistry(
        workflow_id="WF_WEEKLY_BUSINESS_REPORT",
        bindings={
            "DS_SYNTH": RegisteredInputBinding(
                dataset_id="DS_SYNTH",
                query_asset_id_or_not_applicable="QRY_SYNTH_EXACT",
                adapter_id="ADP_SYNTH_EXACT_V1",
                source_id="SRC_SYNTH",
                dataset_version_constraints=(">=0.1.0,<0.2.0",),
            )
        },
    )


def valid_key() -> BusinessKey:
    return BusinessKey(
        "RUN_SYNTH_PYDANTIC",
        "DS_SYNTH",
        "current",
        "not_applicable",
    )


def valid_manifest(key: BusinessKey) -> AttemptManifest:
    return AttemptManifest(
        business_key=key,
        acquisition_attempt_id="ATTEMPT_PYDANTIC_001",
        acquisition_mode=AcquisitionMode.AUTOMATED,
        adapter_id="ADP_SYNTH_EXACT_V1",
        adapter_version="1.0.0",
        provider_id="PRV_SYNTH_EXACT_V1",
        query_asset_id_or_not_applicable="QRY_SYNTH_EXACT",
        normalized_parameter_readback={"period": "SYNTH_PERIOD"},
        started_at="2026-01-08T10:00:00+08:00",
        completed_at="2026-01-08T10:00:01+08:00",
        duration_ms=1000,
        session_status_code="active",
        local_input_opaque_reference=(
            "runs/RUN_SYNTH_PYDANTIC/attempts/ATTEMPT_PYDANTIC_001/"
            "inputs/automated_result.json"
        ),
        sha256="0" * 64,
        row_count_or_not_applicable=1,
        schema_fingerprint_or_not_applicable="SYNTH_SCHEMA_SHA256",
        page_contract_version_or_not_applicable="PAGE_SYNTH_V1",
        validation_status="passed",
    )


class PydanticIntegrationTests(unittest.TestCase):
    def test_exact_dependency_and_strict_v2_configuration(self) -> None:
        self.assertEqual(pydantic.__version__, "2.13.4")
        for model in (
            BusinessKeyModel,
            LockedRunContextModel,
            AcquisitionAttemptBindingModel,
            AttemptManifestModel,
            RunInputEntryModel,
        ):
            with self.subTest(model=model.__name__):
                self.assertIs(model.model_config.get("strict"), True)
                self.assertIs(model.model_config.get("frozen"), True)

    def test_all_first_batch_public_models_accept_contract_valid_input(self) -> None:
        key = valid_key()
        locked = LockedRunContext.lock(
            {**valid_context(), "approved_extension_field": "preserved"}
        )
        binding = AcquisitionAttemptBinding(
            "ATTEMPT_PYDANTIC_001",
            "runs/RUN_SYNTH_PYDANTIC/attempts/ATTEMPT_PYDANTIC_001/"
            "manifests/attempt_manifest.json",
        )
        manifest = valid_manifest(key)
        run_input = RunInputEntry(
            business_key=key,
            dataset_version="0.1.0",
            query_asset_binding={
                "binding_status": "bound",
                "query_asset_id": "QRY_SYNTH_EXACT",
            },
            local_input_reference=manifest.local_input_opaque_reference,
            source_report_date="not_applicable",
            source_business_data_cutoff_date="not_applicable",
            acquisition_mode=AcquisitionMode.AUTOMATED,
            acquisition_attempt_binding=binding,
        )
        run_input.validate(registry())

        self.assertEqual(locked.workflow_run_id, "RUN_SYNTH_PYDANTIC")
        self.assertEqual(key.as_tuple(), key.as_tuple())
        self.assertEqual(
            manifest.association_key,
            (
                "RUN_SYNTH_PYDANTIC",
                "DS_SYNTH",
                "current",
                "not_applicable",
                "ATTEMPT_PYDANTIC_001",
            ),
        )

    def test_strict_models_do_not_coerce_business_key_values(self) -> None:
        with self.assertRaises(ValidationError):
            BusinessKeyModel.model_validate(
                {
                    "workflow_run_id": 123,
                    "dataset_id": "DS_SYNTH",
                    "period_role": "current",
                    "product_parameter": "not_applicable",
                },
                context={"period_roles": {"current"}},
            )
        with self.assertRaises(ContractViolation) as captured:
            BusinessKey(123, "DS_SYNTH", "current", "not_applicable")  # type: ignore[arg-type]
        self.assertNotIsInstance(captured.exception, ValidatorParityError)

    def test_legacy_and_pydantic_both_block_invalid_first_batch_inputs(self) -> None:
        cases = []
        cases.append(lambda: BusinessKey("RUN", "DS", "latest", "not_applicable"))
        invalid_context = valid_context()
        invalid_context["workflow_reporting_date"] = "2026-02-30"
        cases.append(lambda: LockedRunContext.lock(invalid_context))
        cases.append(lambda: AcquisitionAttemptBinding("", "manifest.json"))

        def invalid_manifest() -> None:
            key = valid_key()
            values = valid_manifest(key)._validation_payload()
            values["duration_ms"] = True
            AttemptManifest(**values)

        cases.append(invalid_manifest)

        def invalid_entry() -> None:
            value = RunInputEntry(
                business_key=valid_key(),
                dataset_version="0.1.0",
                query_asset_binding={"binding_status": "unknown"},
                local_input_reference="not_applicable",
                source_report_date="not_applicable",
                source_business_data_cutoff_date="not_applicable",
                acquisition_mode=AcquisitionMode.MANUAL_FALLBACK,
            )
            value.validate(registry())

        cases.append(invalid_entry)
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ContractViolation) as captured:
                    case()
                self.assertNotIsInstance(captured.exception, ValidatorParityError)

    def test_pydantic_only_block_is_detected_before_runtime_continues(self) -> None:
        with patch(
            "weekly_acquisition_runtime.contracts.validate_business_key",
            side_effect=ValueError("synthetic Pydantic-only block"),
        ):
            with self.assertRaisesRegex(ValidatorParityError, "legacy=PASS, pydantic=BLOCK"):
                valid_key()

    def test_legacy_only_block_is_detected_and_not_adapted_away(self) -> None:
        with patch(
            "weekly_acquisition_runtime.contracts.validate_business_key",
            return_value=None,
        ):
            with self.assertRaisesRegex(ValidatorParityError, "legacy=BLOCK, pydantic=PASS"):
                BusinessKey("RUN", "DS", "latest", "not_applicable")

    def test_dependency_pin_and_ci_parity_command_are_explicit(self) -> None:
        requirements = (ROOT / "requirements-validation.txt").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/validate-assets.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pydantic==2.13.4", requirements.splitlines())
        self.assertIn(
            "python -m unittest tests.test_pydantic_integration",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
