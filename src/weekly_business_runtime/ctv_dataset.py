"""Manifest-bound CTV and prior-year comparable dataset loading."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError, SchemaErrors

from .assets import CtvAssetBundle
from .errors import DatasetValidationError
from .models import ExecutionWarning

_SOURCE_QUARTER = re.compile(r"(?P<year>\d{2})Q(?P<quarter>[1-4])\Z")
_PERIOD_RANGE = re.compile(
    r"(?P<start>\d{4}/\d{1,2}/\d{1,2})\s*[—–-]\s*(?P<end>\d{4}/\d{1,2}/\d{1,2})\Z"
)


@dataclass(frozen=True)
class LoadedCtvDataset:
    frame: pd.DataFrame
    warnings: tuple[ExecutionWarning, ...]
    input_reference: str


@dataclass(frozen=True)
class PriorComparableValue:
    value: Decimal
    target_quarter: str
    input_reference: str


@dataclass(frozen=True)
class PreviousQuarterSourceValue:
    value: Decimal
    target_quarter: str
    selected_source_role: str
    input_reference: str


def _is_blank(value: object) -> bool:
    return value is None or pd.isna(value) or (isinstance(value, str) and not value.strip())


def _decimal_or_zero(value: object, raw_field_name: str) -> Decimal:
    if _is_blank(value):
        return Decimal("0")
    if isinstance(value, bool):
        raise DatasetValidationError(
            "CTV_NUMERIC_VALUE_INVALID", f"{raw_field_name} contains a boolean value"
        )
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise DatasetValidationError(
            "CTV_NUMERIC_VALUE_INVALID",
            f"{raw_field_name} contains a non-empty non-numeric value",
        ) from exc


def _read_header(path: Path, sheet_name: str) -> list[object]:
    try:
        header = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=1)
    except (OSError, ValueError, ImportError) as exc:
        raise DatasetValidationError(
            "CTV_DATASET_UNREADABLE", f"Cannot read required worksheet {sheet_name}"
        ) from exc
    if header.empty:
        raise DatasetValidationError("CTV_HEADER_MISSING", "Dataset has no header row")
    return list(header.iloc[0])


def _require_unique_headers(headers: list[object]) -> None:
    normalized = [str(value).strip() for value in headers if not _is_blank(value)]
    duplicates = sorted({value for value in normalized if normalized.count(value) > 1})
    if duplicates:
        raise DatasetValidationError(
            "CTV_DUPLICATE_SOURCE_HEADER", f"Duplicate source headers: {duplicates}"
        )


class CtvDatasetLoader:
    """Load only the current CTV source and its explicit prior-year comparable."""

    def __init__(self, assets: CtvAssetBundle) -> None:
        self.assets = assets

    def load_current(self, path: Path, input_reference: str) -> LoadedCtvDataset:
        mapping = self.assets.current_mapping
        sheet_name = str(mapping["scope"]["source_object_or_sheet"])
        headers = _read_header(path, sheet_name)
        _require_unique_headers(headers)
        try:
            raw = pd.read_excel(path, sheet_name=sheet_name, header=0, dtype=object)
        except (OSError, ValueError, ImportError) as exc:
            raise DatasetValidationError(
                "CTV_DATASET_UNREADABLE", "Cannot load the manifest-bound CTV workbook"
            ) from exc

        entries = mapping.get("field_mappings", [])
        raw_to_standard = {
            entry["raw_field_name"]: entry["standard_field_id"]
            for entry in entries
            if isinstance(entry, dict)
        }
        if len(raw_to_standard) != len(entries) or len(set(raw_to_standard.values())) != len(entries):
            raise DatasetValidationError(
                "CTV_MAPPING_CONFLICT", "CTV Mapping Profile contains duplicate source or target fields"
            )
        missing = sorted(set(raw_to_standard) - set(raw.columns))
        if missing:
            raise DatasetValidationError(
                "CTV_REQUIRED_MAPPING_MISSING", f"Required CTV source headers are missing: {missing}"
            )

        inventory_fields = {
            item["raw_field_name"]
            for item in mapping.get("raw_field_inventory", [])
            if isinstance(item, dict)
        }
        unknown = sorted(str(column) for column in raw.columns if column not in inventory_fields)
        warnings: list[ExecutionWarning] = []
        if unknown:
            warnings.append(
                ExecutionWarning(
                    "CTV_SCHEMA_DRIFT_UNKNOWN_FIELD",
                    f"Unregistered source fields were ignored pending Owner registration: {unknown}",
                )
            )

        mapped = raw[list(raw_to_standard)].rename(columns=raw_to_standard).copy()
        mapped["order_id"] = mapped["order_id"].map(self._normalize_order_id)
        for entry in entries:
            if entry.get("standard_data_type") == "decimal":
                standard = entry["standard_field_id"]
                raw_name = entry["raw_field_name"]
                mapped[standard] = mapped[standard].map(
                    lambda value, name=raw_name: _decimal_or_zero(value, name)
                )

        blank_count = int((mapped["order_id"] == "").sum())
        duplicate_count = int(mapped.loc[mapped["order_id"] != "", "order_id"].duplicated().sum())
        if blank_count:
            warnings.append(
                ExecutionWarning(
                    "CTV_ORDER_ID_BLANK_RETAINED",
                    f"{blank_count} blank order_id records were retained as required",
                )
            )
        if duplicate_count:
            warnings.append(
                ExecutionWarning(
                    "CTV_ORDER_ID_DUPLICATE_RETAINED",
                    f"{duplicate_count} duplicate order_id records were retained without deduplication",
                )
            )

        self._validate_current_boundary(mapped)
        return LoadedCtvDataset(mapped, tuple(warnings), input_reference)

    @staticmethod
    def _normalize_order_id(value: object) -> str:
        if _is_blank(value):
            return ""
        if not isinstance(value, str):
            raise DatasetValidationError(
                "CTV_ORDER_ID_TYPE_INVALID", "order_id must preserve source text and cannot be cast"
            )
        return value.strip(" \r\n")

    @staticmethod
    def _validate_current_boundary(frame: pd.DataFrame) -> None:
        decimal_check = pa.Check(lambda series: series.map(lambda value: isinstance(value, Decimal)))
        schema = pa.DataFrameSchema(
            {
                "order_id": pa.Column(
                    str,
                    checks=pa.Check(lambda series: series.map(lambda value: isinstance(value, str))),
                    nullable=False,
                ),
                "qtd_executed_revenue_amount": pa.Column(
                    object, checks=decimal_check, nullable=False
                ),
                "qtd_signed_amount": pa.Column(object, checks=decimal_check, nullable=False),
                "qtd_ctv_signed_amount": pa.Column(object, checks=decimal_check, nullable=False),
            },
            strict=True,
            coerce=False,
        )
        try:
            schema.validate(frame, lazy=True)
        except (SchemaError, SchemaErrors) as exc:
            raise DatasetValidationError(
                "CTV_PANDERA_BOUNDARY_INVALID", "CTV standardized DataFrame failed validation"
            ) from exc

    def load_prior_comparable(
        self,
        path: Path,
        *,
        target_quarter: str,
        input_reference: str,
    ) -> PriorComparableValue:
        mapping = self.assets.prior_mapping
        sheet_name = str(mapping["scope"]["source_object_or_sheet_pattern"])
        headers = _read_header(path, sheet_name)
        _require_unique_headers(headers)
        try:
            raw = pd.read_excel(path, sheet_name=sheet_name, header=0, dtype=object)
        except (OSError, ValueError, ImportError) as exc:
            raise DatasetValidationError(
                "CTV_PRIOR_DATASET_UNREADABLE", "Cannot load the bound prior-year workbook"
            ) from exc

        business_mapping = mapping["field_mappings"][0]["explicit_value_mapping"]
        source_labels = [source for source, standard in business_mapping.items() if standard == "CTV"]
        if len(source_labels) != 1:
            raise DatasetValidationError(
                "CTV_PRIOR_MAPPING_CONFLICT", "Prior Mapping must define exactly one CTV label"
            )
        source_label = source_labels[0]
        first_column = raw.columns[0]
        matches = raw[first_column].map(
            lambda value: isinstance(value, str) and value.strip() == source_label
        )
        if int(matches.sum()) != 1:
            raise DatasetValidationError(
                "CTV_PRIOR_BUSINESS_LINE_NOT_UNIQUE",
                "Prior workbook must contain exactly one mapped CTV row",
            )

        quarter_columns = [
            column
            for column in raw.columns[1:]
            if self._normalize_source_quarter(column) == target_quarter
        ]
        if len(quarter_columns) != 1:
            raise DatasetValidationError(
                "CTV_PRIOR_QUARTER_NOT_UNIQUE",
                "Prior workbook must contain exactly one target quarter column",
            )
        value = _decimal_or_zero(raw.loc[matches, quarter_columns[0]].iloc[0], str(quarter_columns[0]))
        if value <= 0:
            raise DatasetValidationError(
                "CTV_PRIOR_VALUE_INVALID", "Prior comparable CTV value must be greater than zero"
            )
        prior_frame = pd.DataFrame(
            {"revenue_business_line": ["CTV"], "fiscal_quarter": [target_quarter], "value": [value]}
        )
        self._validate_prior_boundary(prior_frame, target_quarter)
        return PriorComparableValue(value, target_quarter, input_reference)

    @staticmethod
    def _validate_prior_boundary(frame: pd.DataFrame, target_quarter: str) -> None:
        schema = pa.DataFrameSchema(
            {
                "revenue_business_line": pa.Column(str, checks=pa.Check.eq("CTV")),
                "fiscal_quarter": pa.Column(str, checks=pa.Check.eq(target_quarter)),
                "value": pa.Column(
                    object,
                    checks=pa.Check(
                        lambda series: series.map(
                            lambda item: isinstance(item, Decimal) and item > 0
                        )
                    ),
                ),
            },
            strict=True,
        )
        try:
            schema.validate(frame, lazy=True)
        except (SchemaError, SchemaErrors) as exc:
            raise DatasetValidationError(
                "CTV_PRIOR_PANDERA_BOUNDARY_INVALID",
                "Prior comparable DataFrame failed validation",
            ) from exc

    def load_previous_quarter_primary(
        self, path: Path, *, target_quarter: str, input_reference: str
    ) -> PreviousQuarterSourceValue:
        loaded = self.load_prior_comparable(
            path,
            target_quarter=target_quarter,
            input_reference=input_reference,
        )
        return PreviousQuarterSourceValue(
            loaded.value,
            target_quarter,
            "primary",
            input_reference,
        )

    def load_previous_quarter_fallback(
        self, path: Path, *, target_quarter: str, input_reference: str
    ) -> PreviousQuarterSourceValue:
        mapping = self.assets.previous_quarter_fallback_mapping
        sheet_name = str(mapping["scope"]["source_object_or_sheet_pattern"])
        try:
            raw = pd.read_excel(path, sheet_name=sheet_name, header=None, dtype=object)
        except (OSError, ValueError, ImportError) as exc:
            raise DatasetValidationError(
                "CTV_PREVIOUS_QUARTER_FALLBACK_UNREADABLE",
                "Cannot load the bound previous-quarter fallback workbook",
            ) from exc
        if raw.shape[0] < 8 or raw.shape[1] < 7:
            raise DatasetValidationError(
                "CTV_PREVIOUS_QUARTER_FALLBACK_SHAPE_INVALID",
                "Fallback workbook does not contain the registered B8/G layout",
            )
        period_value = raw.iat[7, 1]
        match = _PERIOD_RANGE.fullmatch(str(period_value).strip())
        if match is None:
            raise DatasetValidationError(
                "CTV_PREVIOUS_QUARTER_PERIOD_INVALID",
                "Fallback source period range is not an exact registered period",
            )
        try:
            period_start = datetime.strptime(match.group("start"), "%Y/%m/%d").date()
            period_end = datetime.strptime(match.group("end"), "%Y/%m/%d").date()
        except ValueError as exc:
            raise DatasetValidationError(
                "CTV_PREVIOUS_QUARTER_PERIOD_INVALID",
                "Fallback source period range contains an invalid date",
            ) from exc
        year = int(target_quarter[:4])
        quarter = int(target_quarter[-1])
        start_month = (quarter - 1) * 3 + 1
        expected_start = date(year, start_month, 1)
        if quarter == 4:
            expected_end = date(year, 12, 31)
        else:
            expected_end = date(year, start_month + 3, 1) - timedelta(days=1)
        if period_start != expected_start or period_end != expected_end:
            raise DatasetValidationError(
                "CTV_PREVIOUS_QUARTER_PERIOD_MISMATCH",
                "Fallback source must represent the complete target previous quarter",
            )
        labels = raw.iloc[8:, 1].map(
            lambda value: isinstance(value, str) and value.strip() == "CTV"
        )
        if int(labels.sum()) != 1:
            raise DatasetValidationError(
                "CTV_PREVIOUS_QUARTER_FALLBACK_CTV_NOT_UNIQUE",
                "Fallback workbook must contain exactly one CTV business-line result",
            )
        row_index = labels[labels].index[0]
        value = _decimal_or_zero(raw.loc[row_index, 6], "当季执行收入")
        if value <= 0:
            raise DatasetValidationError(
                "CTV_PREVIOUS_QUARTER_FALLBACK_VALUE_INVALID",
                "Fallback CTV complete-quarter value must be greater than zero",
            )
        frame = pd.DataFrame(
            {
                "source_period_range": [str(period_value).strip()],
                "period_start_date": [period_start],
                "period_end_date": [period_end],
                "revenue_business_line": ["CTV"],
                "performance_revenue_amount": [value],
                "executed_revenue_amount": [value],
            }
        )
        schema = pa.DataFrameSchema(
            {
                "source_period_range": pa.Column(str, nullable=False),
                "period_start_date": pa.Column(object, checks=pa.Check.eq(expected_start)),
                "period_end_date": pa.Column(object, checks=pa.Check.eq(expected_end)),
                "revenue_business_line": pa.Column(str, checks=pa.Check.eq("CTV")),
                "performance_revenue_amount": pa.Column(
                    object,
                    checks=pa.Check(
                        lambda series: series.map(
                            lambda item: isinstance(item, Decimal) and item > 0
                        )
                    ),
                ),
                "executed_revenue_amount": pa.Column(
                    object,
                    checks=pa.Check(
                        lambda series: series.map(
                            lambda item: isinstance(item, Decimal) and item > 0
                        )
                    ),
                ),
            },
            strict=True,
        )
        try:
            schema.validate(frame, lazy=True)
        except (SchemaError, SchemaErrors) as exc:
            raise DatasetValidationError(
                "CTV_PREVIOUS_QUARTER_FALLBACK_PANDERA_BOUNDARY_INVALID",
                "Previous-quarter fallback DataFrame failed validation",
            ) from exc
        return PreviousQuarterSourceValue(
            value,
            target_quarter,
            "fallback",
            input_reference,
        )

    @staticmethod
    def _normalize_source_quarter(value: object) -> str | None:
        match = _SOURCE_QUARTER.fullmatch(str(value).strip())
        if match is None:
            return None
        return f"20{match.group('year')}Q{match.group('quarter')}"
