"""Spreadsheet calculation-engine boundary for formula-backed Excel Stores."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import MetricStoreError


class WorkbookCalculationEngine(Protocol):
    """Recalculate and save one already-written workbook in place."""

    def recalculate(self, workbook_path: Path) -> None: ...


@dataclass(frozen=True)
class PowerShellExcelCalculationEngine:
    """Invoke the checked-in invisible Excel COM calculation helper."""

    script_path: Path
    powershell_executable: str = "powershell.exe"
    timeout_seconds: int = 120

    def recalculate(self, workbook_path: Path) -> None:
        if not workbook_path.is_file():
            raise MetricStoreError(
                "STORE_EXCEL_WORKBOOK_UNAVAILABLE",
                "Workbook requested for calculation is unavailable",
            )
        if not self.script_path.is_file():
            raise MetricStoreError(
                "STORE_EXCEL_CALCULATION_ENGINE_UNAVAILABLE",
                "Excel calculation helper is unavailable",
            )
        try:
            completed = subprocess.run(
                (
                    self.powershell_executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(self.script_path.resolve()),
                    "-WorkbookPath",
                    str(workbook_path.resolve()),
                ),
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MetricStoreError(
                "STORE_EXCEL_CALCULATION_ENGINE_UNAVAILABLE",
                "Excel calculation engine could not be started or timed out",
            ) from exc
        if completed.returncode != 0:
            raise MetricStoreError(
                "STORE_EXCEL_CALCULATION_FAILED",
                "Excel calculation engine did not recalculate and save the workbook",
            )
