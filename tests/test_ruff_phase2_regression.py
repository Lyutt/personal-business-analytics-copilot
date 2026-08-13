from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "validate_asset_integrity.py"
SPEC = importlib.util.spec_from_file_location("validate_asset_integrity_phase2", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load validate_asset_integrity.py")
VALIDATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)


class RuffPhase2RegressionTests(unittest.TestCase):
    def test_loop_local_validators_preserve_file_attribution_and_order(self) -> None:
        documents = {
            "phase1_5/assets/a.yaml": {
                "config_type": "result_contract",
                "result_contract_id": "RC_A",
                "contract_dimensions": "invalid-a",
                "record_grain": [],
                "producer": {"producer_pipeline_id": "PL_MISSING_A"},
            },
            "phase1_5/assets/b.yaml": {
                "config_type": "result_contract",
                "result_contract_id": "RC_B",
                "contract_dimensions": "invalid-b",
                "record_grain": [],
                "producer": {"producer_pipeline_id": "PL_MISSING_B"},
            },
            "phase1_5/assets/out_a.yaml": {
                "config_type": "output_mapping",
                "display_fields": "TBD",
            },
            "phase1_5/assets/out_b.yaml": {
                "config_type": "output_mapping",
                "display_fields": "TBD",
            },
        }
        errors: list[str] = []

        counts = VALIDATOR.validate_result_contract_semantics(documents, {}, errors)

        markers = [
            error
            for error in errors
            if "expected list" in error
            or "expected non-empty list" in error
            or "TBD is prohibited" in error
        ]
        self.assertEqual(
            markers,
            [
                "phase1_5/assets/a.yaml:contract_dimensions: expected list",
                "phase1_5/assets/a.yaml:record_grain: expected non-empty list",
                "phase1_5/assets/b.yaml:contract_dimensions: expected list",
                "phase1_5/assets/b.yaml:record_grain: expected non-empty list",
                "phase1_5/assets/out_a.yaml:.display_fields: TBD is prohibited in all active MVP outputs",
                "phase1_5/assets/out_b.yaml:.display_fields: TBD is prohibited in all active MVP outputs",
            ],
        )
        self.assertEqual(counts["contracts"], 2)
        self.assertEqual(counts["record_grains"], 2)
        self.assertEqual(counts["display_tbd_exceptions"], 2)

    def test_git_diff_validation_call_is_preserved_on_success(self) -> None:
        base_sha = "a" * 40
        completed = subprocess.CompletedProcess([], 0, stdout="changed.py\n", stderr="")
        with (
            patch.dict(
                VALIDATOR.os.environ,
                {"ASSET_VALIDATION_BASE_SHA": base_sha},
                clear=False,
            ),
            patch.object(VALIDATOR.subprocess, "run", return_value=completed) as run,
        ):
            errors: list[str] = []
            checked = VALIDATOR.validate_implementation_baseline({}, errors)

        self.assertEqual(run.call_count, 1)
        self.assertEqual(
            run.call_args.args[0],
            ["git", "diff", "--name-only", base_sha, "--"],
        )
        self.assertEqual(errors, [])
        self.assertEqual(checked, 1)

    def test_git_diff_validation_failure_still_blocks(self) -> None:
        base_sha = "b" * 40
        completed = subprocess.CompletedProcess([], 1, stdout="", stderr="failure")
        with (
            patch.dict(
                VALIDATOR.os.environ,
                {"ASSET_VALIDATION_BASE_SHA": base_sha},
                clear=False,
            ),
            patch.object(VALIDATOR.subprocess, "run", return_value=completed) as run,
        ):
            errors: list[str] = []
            checked = VALIDATOR.validate_implementation_baseline({}, errors)

        self.assertEqual(run.call_count, 1)
        self.assertEqual(
            errors,
            [f"cannot enumerate assets changed from Base SHA {base_sha}"],
        )
        self.assertEqual(checked, 1)


if __name__ == "__main__":
    unittest.main()
