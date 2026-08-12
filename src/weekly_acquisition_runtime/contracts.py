"""Immutable models for the 1.1.0 Candidate Manifest bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .errors import AmbiguousBindingError, ContractViolation, UnboundInputError


NOT_APPLICABLE = "not_applicable"
PASSED = "passed"


class AcquisitionMode(str, Enum):
    AUTOMATED = "automated"
    MANUAL_FALLBACK = "manual_fallback"
    LEGACY_PREPARED_LOCAL_INPUT = "legacy_prepared_local_input"


def _required_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractViolation(f"{field_name} must be a non-empty string")
    return value


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

    @classmethod
    def lock(cls, values: Mapping[str, Any]) -> "LockedRunContext":
        missing = sorted(cls.REQUIRED_FIELDS - set(values))
        if missing:
            raise ContractViolation(f"Run Context missing required fields: {missing}")
        if values["run_type"] not in cls.RUN_TYPES:
            raise ContractViolation("run_type must be scheduled, manual, or backfill")
        if values["timezone"] != "Asia/Shanghai":
            raise ContractViolation("timezone must be Asia/Shanghai")
        return cls(MappingProxyType(dict(values)))

    @property
    def workflow_run_id(self) -> str:
        return str(self.values["workflow_run_id"])


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
            _required_text(str(getattr(self, name)), name)
        if self.duration_ms < 0:
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

    def validate(self) -> None:
        _required_text(self.dataset_version, "dataset_version")
        _required_text(self.local_input_reference, "local_input_reference")
        if self.acquisition_mode in {
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
    _entries: dict[tuple[str, str, str, str], RunInputEntry] = field(default_factory=dict)

    def add_entry(self, entry: RunInputEntry) -> None:
        if entry.business_key.workflow_run_id != self.workflow_run_id:
            raise ContractViolation("Entry workflow_run_id does not match the Run Input Manifest")
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
        manifest.require_passed()
        if manifest.business_key != business_key:
            raise ContractViolation("Attempt Manifest association key does not match the Run Input entry")
        if manifest.acquisition_mode != entry.acquisition_mode:
            raise ContractViolation("Attempt acquisition_mode does not match the Run Input entry")
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
        entries = [self._entries[key].as_dict() for key in sorted(self._entries)]
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
