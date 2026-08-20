"""Manifest-bound Technical QTD current and prior-year Dataset loading."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError, SchemaErrors

from .errors import DatasetValidationError
from .models import ExecutionWarning
from .technical_assets import MAPPING_ID, TechnicalAssetBundle

_REQUIRED_STANDARD_FIELDS = (
    "business_line_level_1",
    "fiscal_quarter",
    "performance_revenue_amount",
    "executed_revenue_amount",
)


@dataclass(frozen=True)
class LoadedTechnicalDataset:
    frame: pd.DataFrame
    warnings: tuple[ExecutionWarning, ...]
    input_reference: str
    input_role: str
    target_fiscal_quarter: str

    @property
    def performance(self) -> Decimal:
        return sum(self.frame["performance_revenue_amount"], Decimal("0"))

    @property
    def executed(self) -> Decimal:
        return sum(self.frame["executed_revenue_amount"], Decimal("0"))


def _is_blank(value: object) -> bool:
    return value is None or pd.isna(value) or (
        isinstance(value, str) and not value.strip()
    )


def _decimal_or_zero(value: object, raw_field_name: str) -> Decimal:
    if _is_blank(value):
        return Decimal("0")
    if isinstance(value, bool):
        raise DatasetValidationError(
            "TECHNICAL_NUMERIC_VALUE_INVALID",
            f"{raw_field_name} contains a boolean value",
        )
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(
            "TECHNICAL_NUMERIC_VALUE_INVALID",
            f"{raw_field_name} contains a non-empty non-numeric value",
        ) from exc


class TechnicalDatasetLoader:
    """Apply the registered detailed-QTD Mapping and exact Technical eligibility."""

    def __init__(self, assets: TechnicalAssetBundle) -> None:
        self.assets = assets

    def load(
        self,
        path: Path,
        *,
        target_fiscal_quarter: str,
        input_role: str,
        input_reference: str,
    ) -> LoadedTechnicalDataset:
        if input_role not in {"current", "prior_year_comparable"}:
            raise DatasetValidationError(
                "TECHNICAL_INPUT_ROLE_INVALID", "Technical QTD role is not registered"
            )
        if len(target_fiscal_quarter) != 6 or target_fiscal_quarter[4] != "Q":
            raise DatasetValidationError(
                "TECHNICAL_TARGET_QUARTER_INVALID",
                "Technical target fiscal quarter must be YYYYQ[1-4]",
            )
        sheet_name = f"{target_fiscal_quarter[:4]}年执行单类型"
        mapping_entries = {
            entry.get("standard_field_id"): entry
            for entry in self.assets.mapping.get("field_mappings", [])
            if isinstance(entry, dict)
            and entry.get("standard_field_id") in _REQUIRED_STANDARD_FIELDS
        }
        if set(mapping_entries) != set(_REQUIRED_STANDARD_FIELDS):
            raise DatasetValidationError(
                "TECHNICAL_MAPPING_INCOMPLETE",
                f"{MAPPING_ID} does not contain the four Technical QTD fields",
            )
        raw_to_standard = {
            entry["raw_field_name"]: standard
            for standard, entry in mapping_entries.items()
        }
        if len(raw_to_standard) != len(_REQUIRED_STANDARD_FIELDS):
            raise DatasetValidationError(
                "TECHNICAL_MAPPING_CONFLICT",
                "Technical QTD fields must resolve from distinct raw fields",
            )
        try:
            header = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=1)
            raw = pd.read_excel(path, sheet_name=sheet_name, header=0, dtype=object)
        except (OSError, ValueError, ImportError) as exc:
            raise DatasetValidationError(
                "TECHNICAL_DATASET_UNREADABLE",
                f"Cannot read exact worksheet {sheet_name}",
            ) from exc
        if header.empty:
            raise DatasetValidationError(
                "TECHNICAL_HEADER_MISSING", "Technical Dataset has no header row"
            )
        normalized_headers = [
            str(value).strip() for value in header.iloc[0] if not _is_blank(value)
        ]
        duplicates = sorted(
            {value for value in normalized_headers if normalized_headers.count(value) > 1}
        )
        if duplicates:
            raise DatasetValidationError(
                "TECHNICAL_DUPLICATE_SOURCE_HEADER",
                f"Duplicate source headers: {duplicates}",
            )
        missing = sorted(set(raw_to_standard) - set(raw.columns))
        if missing:
            raise DatasetValidationError(
                "TECHNICAL_REQUIRED_MAPPING_MISSING",
                f"Required Technical source headers are missing: {missing}",
            )
        inventory_fields = {
            item.get("raw_field_name")
            for item in self.assets.mapping.get("raw_field_inventory", [])
            if isinstance(item, dict)
        }
        unknown = sorted(
            str(column) for column in raw.columns if column not in inventory_fields
        )
        warnings: list[ExecutionWarning] = []
        if unknown:
            warnings.append(
                ExecutionWarning(
                    "TECHNICAL_SCHEMA_DRIFT_UNKNOWN_FIELD",
                    f"Unregistered source fields were ignored pending Owner registration: {unknown}",
                )
            )
        mapped = raw[list(raw_to_standard)].rename(columns=raw_to_standard).copy()
        for field in ("business_line_level_1", "fiscal_quarter"):
            mapped[field] = mapped[field].map(
                lambda value: value.strip() if isinstance(value, str) else ""
            )
        for standard in ("performance_revenue_amount", "executed_revenue_amount"):
            raw_name = mapping_entries[standard]["raw_field_name"]
            mapped[standard] = mapped[standard].map(
                lambda value, name=raw_name: _decimal_or_zero(value, name)
            )
        invalid_business_line = mapped["business_line_level_1"] == ""
        invalid_quarter = mapped["fiscal_quarter"] == ""
        if int(invalid_business_line.sum()):
            warnings.append(
                ExecutionWarning(
                    "TECHNICAL_BUSINESS_LINE_INVALID_EXCLUDED",
                    f"{int(invalid_business_line.sum())} blank business-line records were excluded",
                )
            )
        if int(invalid_quarter.sum()):
            warnings.append(
                ExecutionWarning(
                    "TECHNICAL_FISCAL_QUARTER_INVALID_EXCLUDED",
                    f"{int(invalid_quarter.sum())} blank fiscal-quarter records were excluded",
                )
            )
        eligible = mapped.loc[
            (mapped["business_line_level_1"] == "硬广")
            & (mapped["fiscal_quarter"] == target_fiscal_quarter)
        ].copy()
        self._validate_boundary(eligible, target_fiscal_quarter)
        loaded = LoadedTechnicalDataset(
            eligible,
            tuple(warnings),
            input_reference,
            input_role,
            target_fiscal_quarter,
        )
        if input_role == "prior_year_comparable" and (
            loaded.performance <= 0 or loaded.executed <= 0
        ):
            raise DatasetValidationError(
                "TECHNICAL_PRIOR_YEAR_RESULT_INVALID",
                "Prior-year QTD performance and executed results must each be greater than zero",
            )
        return loaded

    @staticmethod
    def _validate_boundary(frame: pd.DataFrame, target_quarter: str) -> None:
        decimal_check = pa.Check(
            lambda series: series.map(lambda value: isinstance(value, Decimal))
        )
        schema = pa.DataFrameSchema(
            {
                "business_line_level_1": pa.Column(
                    str, checks=pa.Check.eq("硬广"), nullable=False
                ),
                "fiscal_quarter": pa.Column(
                    str, checks=pa.Check.eq(target_quarter), nullable=False
                ),
                "performance_revenue_amount": pa.Column(
                    object, checks=decimal_check, nullable=False
                ),
                "executed_revenue_amount": pa.Column(
                    object, checks=decimal_check, nullable=False
                ),
            },
            strict=True,
            coerce=False,
        )
        try:
            schema.validate(frame, lazy=True)
        except (SchemaError, SchemaErrors) as exc:
            raise DatasetValidationError(
                "TECHNICAL_PANDERA_BOUNDARY_INVALID",
                "Technical standardized QTD DataFrame failed validation",
            ) from exc
