"""Manifest-bound CTV and prior-year comparable dataset loading."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError

from .assets import CtvAssetBundle
from .errors import DatasetValidationError
from .models import ExecutionWarning

_SOURCE_QUARTER = re.compile(r"(?P<year>\d{2})Q(?P<quarter>[1-4])\Z")


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
        except SchemaError as exc:
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
            schema.validate(prior_frame, lazy=True)
        except SchemaError as exc:
            raise DatasetValidationError(
                "CTV_PRIOR_PANDERA_BOUNDARY_INVALID",
                "Prior comparable DataFrame failed validation",
            ) from exc
        return PriorComparableValue(value, target_quarter, input_reference)

    @staticmethod
    def _normalize_source_quarter(value: object) -> str | None:
        match = _SOURCE_QUARTER.fullmatch(str(value).strip())
        if match is None:
            return None
        return f"20{match.group('year')}Q{match.group('quarter')}"
