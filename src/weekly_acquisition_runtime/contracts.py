"""Immutable models for the 1.1.0 Candidate Manifest bridge."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .errors import AmbiguousBindingError, ContractViolation, UnboundInputError


NOT_APPLICABLE = "not_applicable"
PASSED = "passed"
PERIOD_ROLES = {
    "current",
    "comparison",
    "prior_year_comparable",
    "previous_quarter_complete",
    "configured_history",
}
QUERY_BINDING_STATUSES = {"bound", NOT_APPLICABLE}
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)\Z")
_VERSION_CLAUSE_PATTERN = re.compile(r"(>=|<=|==|>|<)(\d+\.\d+\.\d+)\Z")


class AcquisitionMode(str, Enum):
    AUTOMATED = "automated"
    MANUAL_FALLBACK = "manual_fallback"
    LEGACY_PREPARED_LOCAL_INPUT = "legacy_prepared_local_input"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractViolation(f"{field_name} must be a non-empty string")
    return value


def _iso_date_or_not_applicable(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    if text == NOT_APPLICABLE:
        return text
    if not _DATE_PATTERN.fullmatch(text):
        raise ContractViolation(f"{field_name} must use YYYY-MM-DD or not_applicable")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ContractViolation(f"{field_name} is not a valid calendar date") from exc
    return text


def _iso_datetime(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ContractViolation(f"{field_name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ContractViolation(f"{field_name} must include a timezone offset")
    return text


def _version_tuple(value: object, field_name: str) -> tuple[int, int, int]:
    text = _required_text(value, field_name)
    match = _VERSION_PATTERN.fullmatch(text)
    if match is None:
        raise ContractViolation(f"{field_name} must use MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in match.groups())


def _version_clauses(constraint: str) -> list[tuple[str, tuple[int, int, int]]]:
    clauses = []
    for raw_clause in constraint.split(","):
        clause = raw_clause.strip()
        match = _VERSION_CLAUSE_PATTERN.fullmatch(clause)
        if match is None:
            raise ContractViolation(
                f"Unsupported Pipeline dataset_version_constraint: {constraint}"
            )
        operator, required_text = match.groups()
        required = _version_tuple(required_text, "dataset_version_constraint")
        clauses.append((operator, required))
    return clauses


def _version_satisfies(version: str, constraint: str) -> bool:
    candidate = _version_tuple(version, "dataset_version")
    for operator, required in _version_clauses(constraint):
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


@dataclass(frozen=True)
class BusinessKey:
    """The unchanged four-field Run Input Manifest business key."""

    workflow_run_id: str
    dataset_id: str
    period_role: str
    product_parameter: str

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _required_text(getattr(self, name), name)
        if self.period_role not in PERIOD_ROLES:
            raise ContractViolation(f"period_role must be one of {sorted(PERIOD_ROLES)}")

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (
            self.workflow_run_id,
            self.dataset_id,
            self.period_role,
            self.product_parameter,
        )

    def as_dict(self) -> dict[str, str]:
        return dict(zip(self.__dataclass_fields__, self.as_tuple(), strict=True))


@dataclass(frozen=True)
class LockedRunContext:
    """An immutable Run Context locked before any Acquisition Attempt."""

    values: Mapping[str, Any]

    REQUIRED_FIELDS = {
        "workflow_run_id",
        "run_type",
        "workflow_execution_date",
        "workflow_reporting_date",
        "reporting_period_id",
        "reporting_period_start_date",
        "reporting_period_end_date",
        "current_period_start_date",
        "current_period_end_date",
        "comparison_period_start_date",
        "comparison_period_end_date",
        "cutoff_date",
        "timezone",
    }
    RUN_TYPES = {"scheduled", "manual", "backfill"}
    DATE_FIELDS = {
        "workflow_execution_date",
        "workflow_reporting_date",
        "reporting_period_start_date",
        "reporting_period_end_date",
        "current_period_start_date",
        "current_period_end_date",
        "comparison_period_start_date",
        "comparison_period_end_date",
        "cutoff_date",
    }

    @classmethod
    def lock(cls, values: Mapping[str, Any]) -> "LockedRunContext":
        missing = sorted(cls.REQUIRED_FIELDS - set(values))
        if missing:
            raise ContractViolation(f"Run Context missing required fields: {missing}")
        for name in cls.REQUIRED_FIELDS - cls.DATE_FIELDS:
            _required_text(values[name], f"Run Context {name}")
        for name in cls.DATE_FIELDS:
            _iso_date_or_not_applicable(values[name], f"Run Context {name}")
            if values[name] == NOT_APPLICABLE:
                raise ContractViolation(f"Run Context {name} cannot be not_applicable")
        if values["run_type"] not in cls.RUN_TYPES:
            raise ContractViolation("run_type must be scheduled, manual, or backfill")
        if values["timezone"] != "Asia/Shanghai":
            raise ContractViolation("timezone must be Asia/Shanghai")
        for start_name, end_name in (
            ("reporting_period_start_date", "reporting_period_end_date"),
            ("current_period_start_date", "current_period_end_date"),
            ("comparison_period_start_date", "comparison_period_end_date"),
        ):
            if values[start_name] > values[end_name]:
                raise ContractViolation(f"Run Context {start_name} cannot be after {end_name}")
        return cls(MappingProxyType(dict(values)))

    @property
    def workflow_run_id(self) -> str:
        return self.values["workflow_run_id"]


@dataclass(frozen=True)
class RegisteredInputBinding:
    dataset_id: str
    query_asset_id_or_not_applicable: str
    adapter_id: str
    source_id: str
    product_scoped: bool = False
    dataset_version_constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "dataset_id",
            "query_asset_id_or_not_applicable",
            "adapter_id",
            "source_id",
        ):
            _required_text(getattr(self, name), name)
        for constraint in self.dataset_version_constraints:
            _version_clauses(constraint)


@dataclass(frozen=True)
class InputBindingRegistry:
    workflow_id: str
    bindings: Mapping[str, RegisteredInputBinding]

    def require(self, dataset_id: object) -> RegisteredInputBinding:
        dataset = _required_text(dataset_id, "dataset_id")
        try:
            return self.bindings[dataset]
        except KeyError as exc:
            raise ContractViolation(f"Dataset is not registered for acquisition: {dataset}") from exc

    def validate_request(
        self,
        *,
        workflow_id: object,
        adapter_id: object,
        dataset_id: object,
        query_asset_id_or_not_applicable: object,
        product_parameter: object,
    ) -> RegisteredInputBinding:
        if _required_text(workflow_id, "workflow_id") != self.workflow_id:
            raise ContractViolation("workflow_id does not match the registered acquisition contract")
        binding = self.require(dataset_id)
        if _required_text(adapter_id, "adapter_id") != binding.adapter_id:
            raise ContractViolation("adapter_id does not match the registered Dataset binding")
        if (
            _required_text(query_asset_id_or_not_applicable, "query_asset_id_or_not_applicable")
            != binding.query_asset_id_or_not_applicable
        ):
            raise ContractViolation("Query Asset does not match the registered Dataset binding")
        product = _required_text(product_parameter, "product_parameter")
        if binding.product_scoped and product == NOT_APPLICABLE:
            raise ContractViolation("product-scoped input requires an explicit product_parameter")
        return binding

    def validate_dataset_version(self, dataset_id: object, dataset_version: object) -> None:
        binding = self.require(dataset_id)
        version = _required_text(dataset_version, "dataset_version")
        _version_tuple(version, "dataset_version")
        for constraint in binding.dataset_version_constraints:
            if not _version_satisfies(version, constraint):
                raise ContractViolation(
                    "Run Input dataset_version does not satisfy the Pipeline Registry "
                    f"constraint for {binding.dataset_id}: {constraint}"
                )


@dataclass(frozen=True)
class AcquisitionAttemptBinding:
    acquisition_attempt_id: str
    attempt_manifest_reference: str

    def __post_init__(self) -> None:
        _required_text(self.acquisition_attempt_id, "acquisition_attempt_id")
        _required_text(self.attempt_manifest_reference, "attempt_manifest_reference")

    def as_dict(self) -> dict[str, str]:
        return {
            "acquisition_attempt_id": self.acquisition_attempt_id,
            "attempt_manifest_reference": self.attempt_manifest_reference,
        }


@dataclass(frozen=True)
class AttemptManifest:
    """Immutable Attempt Manifest with the complete association key."""

    business_key: BusinessKey
    acquisition_attempt_id: str
    acquisition_mode: AcquisitionMode
    adapter_id: str
    adapter_version: str
    provider_id: str
    query_asset_id_or_not_applicable: str
    normalized_parameter_readback: Mapping[str, Any]
    started_at: str
    completed_at: str
    duration_ms: int
    session_status_code: str
    local_input_opaque_reference: str
    sha256: str
    row_count_or_not_applicable: int | str
    schema_fingerprint_or_not_applicable: str
    page_contract_version_or_not_applicable: str
    validation_status: str
    error_code_or_not_applicable: str = NOT_APPLICABLE

    def __post_init__(self) -> None:
        for name in (
            "acquisition_attempt_id",
            "adapter_id",
            "adapter_version",
            "provider_id",
            "query_asset_id_or_not_applicable",
            "started_at",
            "completed_at",
            "session_status_code",
            "local_input_opaque_reference",
            "sha256",
            "schema_fingerprint_or_not_applicable",
            "page_contract_version_or_not_applicable",
            "validation_status",
            "error_code_or_not_applicable",
        ):
            _required_text(getattr(self, name), name)
        if not isinstance(self.acquisition_mode, AcquisitionMode):
            raise ContractViolation("acquisition_mode must be a registered AcquisitionMode")
        _iso_datetime(self.started_at, "started_at")
        _iso_datetime(self.completed_at, "completed_at")
        if not _SHA256_PATTERN.fullmatch(self.sha256):
            raise ContractViolation("sha256 must be exactly 64 hexadecimal characters")
        if not isinstance(self.duration_ms, int) or isinstance(self.duration_ms, bool) or self.duration_ms < 0:
            raise ContractViolation("duration_ms cannot be negative")
        if not isinstance(self.normalized_parameter_readback, Mapping):
            raise ContractViolation("normalized_parameter_readback must be a mapping")

    @property
    def association_key(self) -> tuple[str, str, str, str, str]:
        return (*self.business_key.as_tuple(), self.acquisition_attempt_id)

    def require_passed(self) -> None:
        if self.validation_status != PASSED:
            raise ContractViolation("Only a passed Attempt Manifest may be bound")

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.business_key.as_dict(),
            "acquisition_attempt_id": self.acquisition_attempt_id,
            "acquisition_mode": self.acquisition_mode.value,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "provider_id": self.provider_id,
            "query_asset_id_or_not_applicable": self.query_asset_id_or_not_applicable,
            "normalized_parameter_readback": dict(self.normalized_parameter_readback),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "session_status_code": self.session_status_code,
            "local_input_opaque_reference": self.local_input_opaque_reference,
            "sha256": self.sha256,
            "row_count_or_not_applicable": self.row_count_or_not_applicable,
            "schema_fingerprint_or_not_applicable": self.schema_fingerprint_or_not_applicable,
            "page_contract_version_or_not_applicable": self.page_contract_version_or_not_applicable,
            "validation_status": self.validation_status,
            "error_code_or_not_applicable": self.error_code_or_not_applicable,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AttemptManifest":
        key = BusinessKey(
            workflow_run_id=value["workflow_run_id"],
            dataset_id=value["dataset_id"],
            period_role=value["period_role"],
            product_parameter=value["product_parameter"],
        )
        kwargs = dict(value)
        for name in key.__dataclass_fields__:
            kwargs.pop(name)
        kwargs["business_key"] = key
        kwargs["acquisition_mode"] = AcquisitionMode(kwargs["acquisition_mode"])
        return cls(**kwargs)


@dataclass
class RunInputEntry:
    business_key: BusinessKey
    dataset_version: str
    query_asset_binding: Mapping[str, Any]
    local_input_reference: str
    source_report_date: str
    source_business_data_cutoff_date: str
    acquisition_mode: AcquisitionMode
    acquisition_attempt_binding: AcquisitionAttemptBinding | None = None

    def validate(
        self,
        registry: InputBindingRegistry | None = None,
        *,
        require_attempt_binding: bool = True,
    ) -> None:
        _required_text(self.dataset_version, "dataset_version")
        _required_text(self.local_input_reference, "local_input_reference")
        if not isinstance(self.query_asset_binding, Mapping):
            raise ContractViolation("query_asset_binding must be an object")
        status = self.query_asset_binding.get("binding_status")
        if status not in QUERY_BINDING_STATUSES:
            raise ContractViolation("query_asset_binding.binding_status must be bound or not_applicable")
        query_asset_id = self.query_asset_binding.get("query_asset_id", NOT_APPLICABLE)
        if status == "bound":
            _required_text(query_asset_id, "query_asset_binding.query_asset_id")
            if query_asset_id == NOT_APPLICABLE:
                raise ContractViolation("bound query_asset_binding requires query_asset_id")
        elif query_asset_id not in (None, NOT_APPLICABLE):
            raise ContractViolation("not_applicable query binding cannot name a Query Asset")
        _iso_date_or_not_applicable(self.source_report_date, "source_report_date")
        _iso_date_or_not_applicable(
            self.source_business_data_cutoff_date,
            "source_business_data_cutoff_date",
        )
        if registry is not None:
            registered = registry.require(self.business_key.dataset_id)
            registry.validate_dataset_version(
                self.business_key.dataset_id, self.dataset_version
            )
            expected_query = registered.query_asset_id_or_not_applicable
            if query_asset_id != expected_query:
                raise ContractViolation("Query Asset does not match the registered Dataset binding")
            if registered.product_scoped and self.business_key.product_parameter == NOT_APPLICABLE:
                raise ContractViolation("product-scoped input requires an explicit product_parameter")
        if require_attempt_binding and self.acquisition_mode in {
            AcquisitionMode.AUTOMATED,
            AcquisitionMode.MANUAL_FALLBACK,
        } and self.acquisition_attempt_binding is None:
            raise UnboundInputError("Automated and manual_fallback entries require an explicit Attempt binding")
        if (
            self.acquisition_mode is AcquisitionMode.LEGACY_PREPARED_LOCAL_INPUT
            and self.acquisition_attempt_binding is not None
        ):
            raise ContractViolation("Legacy prepared local input must use not_applicable Attempt binding")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **self.business_key.as_dict(),
            "dataset_version": self.dataset_version,
            "query_asset_binding": dict(self.query_asset_binding),
            "local_input_reference": self.local_input_reference,
            "source_report_date": self.source_report_date,
            "source_business_data_cutoff_date": self.source_business_data_cutoff_date,
            "acquisition_mode": self.acquisition_mode.value,
            "acquisition_attempt_binding": (
                self.acquisition_attempt_binding.as_dict()
                if self.acquisition_attempt_binding
                else NOT_APPLICABLE
            ),
        }


@dataclass
class RunInputManifestBuilder:
    """One per Run; starts before acquisition and finalizes without implicit selection."""

    workflow_run_id: str
    input_binding_registry: InputBindingRegistry | None = None
    _entries: dict[tuple[str, str, str, str], RunInputEntry] = field(default_factory=dict)

    def add_entry(self, entry: RunInputEntry) -> None:
        if entry.business_key.workflow_run_id != self.workflow_run_id:
            raise ContractViolation("Entry workflow_run_id does not match the Run Input Manifest")
        entry.validate(self.input_binding_registry, require_attempt_binding=False)
        key = entry.business_key.as_tuple()
        if key in self._entries:
            raise AmbiguousBindingError("Duplicate Run Input Manifest business key")
        self._entries[key] = entry

    def bind_successful_attempt(
        self, business_key: BusinessKey, manifest: AttemptManifest, manifest_reference: str
    ) -> None:
        key = business_key.as_tuple()
        if key not in self._entries:
            raise UnboundInputError("Run Input Manifest entry does not exist")
        entry = self._entries[key]
        if entry.acquisition_attempt_binding is not None:
            raise AmbiguousBindingError("Run Input Manifest entry already has an explicit Attempt binding")
        entry.validate(self.input_binding_registry, require_attempt_binding=False)
        manifest.require_passed()
        if manifest.business_key != business_key:
            raise ContractViolation("Attempt Manifest association key does not match the Run Input entry")
        if manifest.acquisition_mode != entry.acquisition_mode:
            raise ContractViolation("Attempt acquisition_mode does not match the Run Input entry")
        expected_query = entry.query_asset_binding.get("query_asset_id", NOT_APPLICABLE)
        if manifest.query_asset_id_or_not_applicable != expected_query:
            raise ContractViolation("Attempt Query Asset does not match the Run Input entry")
        entry.acquisition_attempt_binding = AcquisitionAttemptBinding(
            manifest.acquisition_attempt_id, manifest_reference
        )
        entry.local_input_reference = manifest.local_input_opaque_reference

    def get_entry(self, business_key: BusinessKey) -> RunInputEntry:
        try:
            return self._entries[business_key.as_tuple()]
        except KeyError as exc:
            raise UnboundInputError("Run Input Manifest entry does not exist") from exc

    def finalize(self) -> dict[str, Any]:
        entries = []
        for key in sorted(self._entries):
            entry = self._entries[key]
            entry.validate(self.input_binding_registry)
            entries.append(entry.as_dict())
        return {
            "manifest_id": "RUN_INPUT_MANIFEST_WF_WEEKLY_BUSINESS_REPORT_V1",
            "workflow_run_id": self.workflow_run_id,
            "entry_business_key": [
                "workflow_run_id",
                "dataset_id",
                "period_role",
                "product_parameter",
            ],
            "entries": entries,
        }
