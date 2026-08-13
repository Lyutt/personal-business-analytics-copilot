"""Strict Pydantic mirrors for PBAC-owned Runtime Contract validation.

The YAML Contracts and legacy validators remain authoritative. These models
only provide a lower-boilerplate validation implementation that runs in
parallel with the existing validators until parity is proven and approved.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from .errors import ValidatorParityError


NOT_APPLICABLE = "not_applicable"
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)\Z")
_VERSION_CLAUSE_PATTERN = re.compile(r"(>=|<=|==|>|<)(\d+\.\d+\.\d+)\Z")


class PBACStrictModel(BaseModel):
    """Common non-coercing configuration for the Pydantic integration."""

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )


def _required_text(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _iso_date(value: str, field_name: str, *, allow_not_applicable: bool) -> str:
    _required_text(value, field_name)
    if value == NOT_APPLICABLE:
        if allow_not_applicable:
            return value
        raise ValueError(f"{field_name} cannot be not_applicable")
    if not _DATE_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must use YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid calendar date") from exc
    return value


def _iso_datetime(value: str, field_name: str) -> str:
    _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return value


def _version_tuple(value: str, field_name: str) -> tuple[int, int, int]:
    _required_text(value, field_name)
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"{field_name} must use MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in match.groups())


def _version_satisfies(version: str, constraint: str) -> bool:
    candidate = _version_tuple(version, "dataset_version")
    for raw_clause in constraint.split(","):
        clause = raw_clause.strip()
        match = _VERSION_CLAUSE_PATTERN.fullmatch(clause)
        if match is None:
            raise ValueError(f"Unsupported Pipeline dataset_version_constraint: {constraint}")
        operator, required_text = match.groups()
        required = _version_tuple(required_text, "dataset_version_constraint")
        comparisons = {
            ">=": candidate >= required,
            "<=": candidate <= required,
            "==": candidate == required,
            ">": candidate > required,
            "<": candidate < required,
        }
        if not comparisons[operator]:
            return False
    return True


def validate_in_parallel(
    *,
    scope: str,
    legacy_validator: Callable[[], None],
    pydantic_validator: Callable[[], None],
) -> None:
    """Run both validators and keep the legacy PBAC outcome authoritative."""

    legacy_error: Exception | None = None
    pydantic_error: Exception | None = None
    try:
        legacy_validator()
    except Exception as exc:  # PBAC outcome comparison intentionally includes all blocks.
        legacy_error = exc
    try:
        pydantic_validator()
    except Exception as exc:  # Pydantic ValidationError and integration failures both block.
        pydantic_error = exc

    if (legacy_error is None) != (pydantic_error is None):
        legacy_outcome = "PASS" if legacy_error is None else "BLOCK"
        pydantic_outcome = "PASS" if pydantic_error is None else "BLOCK"
        raise ValidatorParityError(
            f"{scope} validator parity mismatch: legacy={legacy_outcome}, "
            f"pydantic={pydantic_outcome}; PBAC legacy outcome remains authoritative"
        )
    if legacy_error is not None:
        raise legacy_error


class BusinessKeyModel(PBACStrictModel):
    workflow_run_id: str
    dataset_id: str
    period_role: str
    product_parameter: str

    @field_validator("workflow_run_id", "dataset_id", "period_role", "product_parameter")
    @classmethod
    def validate_required_text(cls, value: str, info: ValidationInfo) -> str:
        return _required_text(value, info.field_name)

    @model_validator(mode="after")
    def validate_period_role(self, info: ValidationInfo) -> Self:
        period_roles = set((info.context or {}).get("period_roles", ()))
        if self.period_role not in period_roles:
            raise ValueError("period_role is outside the PBAC Contract enum")
        return self


class LockedRunContextModel(PBACStrictModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="allow",
        arbitrary_types_allowed=True,
    )

    workflow_run_id: str
    run_type: str
    workflow_execution_date: str
    workflow_reporting_date: str
    reporting_period_id: str
    reporting_period_start_date: str
    reporting_period_end_date: str
    current_period_start_date: str
    current_period_end_date: str
    comparison_period_start_date: str
    comparison_period_end_date: str
    cutoff_date: str
    timezone: str

    @field_validator("workflow_run_id", "run_type", "reporting_period_id", "timezone")
    @classmethod
    def validate_required_text(cls, value: str, info: ValidationInfo) -> str:
        return _required_text(value, info.field_name)

    @field_validator(
        "workflow_execution_date",
        "workflow_reporting_date",
        "reporting_period_start_date",
        "reporting_period_end_date",
        "current_period_start_date",
        "current_period_end_date",
        "comparison_period_start_date",
        "comparison_period_end_date",
        "cutoff_date",
    )
    @classmethod
    def validate_date(cls, value: str, info: ValidationInfo) -> str:
        return _iso_date(value, info.field_name, allow_not_applicable=False)

    @model_validator(mode="after")
    def validate_contract_relations(self, info: ValidationInfo) -> Self:
        context = info.context or {}
        if self.run_type not in set(context.get("run_types", ())):
            raise ValueError("run_type is outside the PBAC Contract enum")
        if self.timezone != context.get("timezone"):
            raise ValueError("timezone does not match the PBAC Contract")
        for start_name, end_name in (
            ("reporting_period_start_date", "reporting_period_end_date"),
            ("current_period_start_date", "current_period_end_date"),
            ("comparison_period_start_date", "comparison_period_end_date"),
        ):
            if getattr(self, start_name) > getattr(self, end_name):
                raise ValueError(f"{start_name} cannot be after {end_name}")
        return self


class AcquisitionAttemptBindingModel(PBACStrictModel):
    acquisition_attempt_id: str
    attempt_manifest_reference: str

    @field_validator("acquisition_attempt_id", "attempt_manifest_reference")
    @classmethod
    def validate_required_text(cls, value: str, info: ValidationInfo) -> str:
        return _required_text(value, info.field_name)


class AttemptManifestModel(PBACStrictModel):
    business_key: Any
    acquisition_attempt_id: str
    acquisition_mode: Any
    adapter_id: str
    adapter_version: str
    provider_id: str
    query_asset_id_or_not_applicable: str
    normalized_parameter_readback: Mapping[str, Any]
    started_at: str
    completed_at: str
    duration_ms: int = Field(ge=0)
    session_status_code: str
    local_input_opaque_reference: str
    sha256: str
    row_count_or_not_applicable: Any
    schema_fingerprint_or_not_applicable: str
    page_contract_version_or_not_applicable: str
    validation_status: str
    error_code_or_not_applicable: str = NOT_APPLICABLE

    @field_validator(
        "acquisition_attempt_id",
        "adapter_id",
        "adapter_version",
        "provider_id",
        "query_asset_id_or_not_applicable",
        "session_status_code",
        "local_input_opaque_reference",
        "sha256",
        "schema_fingerprint_or_not_applicable",
        "page_contract_version_or_not_applicable",
        "validation_status",
        "error_code_or_not_applicable",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: ValidationInfo) -> str:
        return _required_text(value, info.field_name)

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_datetime(cls, value: str, info: ValidationInfo) -> str:
        return _iso_datetime(value, info.field_name)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if not _SHA256_PATTERN.fullmatch(value):
            raise ValueError("sha256 must be exactly 64 hexadecimal characters")
        return value

    @model_validator(mode="after")
    def validate_acquisition_mode(self, info: ValidationInfo) -> Self:
        acquisition_mode_type = (info.context or {}).get("acquisition_mode_type")
        if acquisition_mode_type is None or not isinstance(
            self.acquisition_mode, acquisition_mode_type
        ):
            raise ValueError("acquisition_mode must be the PBAC AcquisitionMode enum")
        return self


class RunInputEntryModel(PBACStrictModel):
    business_key: Any
    dataset_version: str
    query_asset_binding: Mapping[str, Any]
    local_input_reference: str
    source_report_date: str
    source_business_data_cutoff_date: str
    acquisition_mode: Any
    acquisition_attempt_binding: Any = None

    @field_validator("dataset_version", "local_input_reference")
    @classmethod
    def validate_required_text(cls, value: str, info: ValidationInfo) -> str:
        return _required_text(value, info.field_name)

    @field_validator("source_report_date", "source_business_data_cutoff_date")
    @classmethod
    def validate_source_date(cls, value: str, info: ValidationInfo) -> str:
        return _iso_date(value, info.field_name, allow_not_applicable=True)

    @model_validator(mode="after")
    def validate_contract_relations(self, info: ValidationInfo) -> Self:
        context = info.context or {}
        not_applicable = context.get("not_applicable", NOT_APPLICABLE)
        query_statuses = set(context.get("query_binding_statuses", ()))
        status = self.query_asset_binding.get("binding_status")
        if status not in query_statuses:
            raise ValueError("query_asset_binding.binding_status is invalid")
        query_asset_id = self.query_asset_binding.get("query_asset_id", not_applicable)
        if status == "bound":
            if not isinstance(query_asset_id, str) or not query_asset_id.strip():
                raise ValueError("bound query_asset_binding requires query_asset_id")
            if query_asset_id == not_applicable:
                raise ValueError("bound query_asset_binding requires query_asset_id")
        elif query_asset_id not in (None, not_applicable):
            raise ValueError("not_applicable query binding cannot name a Query Asset")

        registry = context.get("registry")
        if registry is not None:
            dataset_id = self.business_key.dataset_id
            registered = registry.bindings.get(dataset_id)
            if registered is None:
                raise ValueError("Dataset is not registered for acquisition")
            _version_tuple(self.dataset_version, "dataset_version")
            for constraint in registered.dataset_version_constraints:
                if not _version_satisfies(self.dataset_version, constraint):
                    raise ValueError("dataset_version does not satisfy the Pipeline constraint")
            if query_asset_id != registered.query_asset_id_or_not_applicable:
                raise ValueError("Query Asset does not match the registered Dataset binding")
            if registered.product_scoped and self.business_key.product_parameter == not_applicable:
                raise ValueError("product-scoped input requires product_parameter")

        automated_mode = context.get("automated_mode")
        manual_fallback_mode = context.get("manual_fallback_mode")
        legacy_mode = context.get("legacy_mode")
        require_attempt_binding = context.get("require_attempt_binding", True)
        if (
            require_attempt_binding
            and self.acquisition_mode in {automated_mode, manual_fallback_mode}
            and self.acquisition_attempt_binding is None
        ):
            raise ValueError("automated and manual_fallback require an Attempt binding")
        if self.acquisition_mode is legacy_mode and self.acquisition_attempt_binding is not None:
            raise ValueError("legacy input must use not_applicable Attempt binding")
        return self


def validate_business_key(value: Mapping[str, Any], *, period_roles: set[str]) -> None:
    BusinessKeyModel.model_validate(value, context={"period_roles": period_roles})


def validate_locked_run_context(
    value: Mapping[str, Any], *, run_types: set[str], timezone: str
) -> None:
    LockedRunContextModel.model_validate(
        value,
        context={"run_types": run_types, "timezone": timezone},
    )


def validate_acquisition_attempt_binding(value: Mapping[str, Any]) -> None:
    AcquisitionAttemptBindingModel.model_validate(value)


def validate_attempt_manifest(
    value: Mapping[str, Any], *, acquisition_mode_type: type
) -> None:
    AttemptManifestModel.model_validate(
        value,
        context={"acquisition_mode_type": acquisition_mode_type},
    )


def validate_run_input_entry(
    value: Mapping[str, Any],
    *,
    registry: Any,
    require_attempt_binding: bool,
    query_binding_statuses: set[str],
    not_applicable: str,
    automated_mode: Any,
    manual_fallback_mode: Any,
    legacy_mode: Any,
) -> None:
    RunInputEntryModel.model_validate(
        value,
        context={
            "registry": registry,
            "require_attempt_binding": require_attempt_binding,
            "query_binding_statuses": query_binding_statuses,
            "not_applicable": not_applicable,
            "automated_mode": automated_mode,
            "manual_fallback_mode": manual_fallback_mode,
            "legacy_mode": legacy_mode,
        },
    )
