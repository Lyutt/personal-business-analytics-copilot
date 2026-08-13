from __future__ import annotations

import unittest
from dataclasses import fields

from src.weekly_acquisition_runtime.browser_lock import BrowserAcquisitionLock
from src.weekly_acquisition_runtime.contracts import LockedRunContext
from src.weekly_acquisition_runtime.errors import ValidatorParityError
from src.weekly_acquisition_runtime.pydantic_models import validate_in_parallel


class RuffPhase2BRegressionTests(unittest.TestCase):
    def test_class_constants_remain_shared_and_outside_dataclass_fields(self) -> None:
        self.assertEqual(
            BrowserAcquisitionLock.METADATA_ALLOWED,
            {
                "workflow_run_id",
                "acquisition_attempt_id",
                "adapter_id",
                "acquired_at",
                "process_reference",
            },
        )
        self.assertEqual([field.name for field in fields(LockedRunContext)], ["values"])
        self.assertEqual(
            LockedRunContext.RUN_TYPES,
            {"scheduled", "manual", "backfill"},
        )
        self.assertIn("workflow_run_id", LockedRunContext.REQUIRED_FIELDS)
        self.assertIn("cutoff_date", LockedRunContext.DATE_FIELDS)

    def test_parallel_validation_preserves_call_order_and_legacy_exception(self) -> None:
        calls: list[str] = []
        legacy_error = RuntimeError("legacy block")

        def legacy_validator() -> None:
            calls.append("legacy")
            raise legacy_error

        def pydantic_validator() -> None:
            calls.append("pydantic")
            raise ValueError("pydantic block")

        with self.assertRaises(RuntimeError) as captured:
            validate_in_parallel(
                scope="synthetic",
                legacy_validator=legacy_validator,
                pydantic_validator=pydantic_validator,
            )

        self.assertIs(captured.exception, legacy_error)
        self.assertEqual(calls, ["legacy", "pydantic"])

    def test_pydantic_integration_exception_still_fails_closed(self) -> None:
        calls: list[str] = []

        def legacy_validator() -> None:
            calls.append("legacy")

        def pydantic_validator() -> None:
            calls.append("pydantic")
            raise RuntimeError("integration failure")

        with self.assertRaisesRegex(
            ValidatorParityError,
            "legacy=PASS, pydantic=BLOCK",
        ):
            validate_in_parallel(
                scope="synthetic",
                legacy_validator=legacy_validator,
                pydantic_validator=pydantic_validator,
            )

        self.assertEqual(calls, ["legacy", "pydantic"])


if __name__ == "__main__":
    unittest.main()
