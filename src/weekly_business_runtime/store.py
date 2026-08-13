"""Minimal Metric Store port and deterministic in-memory Stage 3A implementation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from .errors import MetricStoreError


@dataclass(frozen=True)
class StoreReadKey:
    """Frozen historical lookup identity: reporting period, not business cutoff."""

    store_id: str
    store_asset_id: str
    metric_variant_id: str
    workflow_reporting_date: str
    business_context_id: str


@dataclass(frozen=True)
class StoreWriteIdentity:
    """Frozen Revenue duplicate identity based on the Store business date."""

    store_id: str
    store_asset_id: str
    current_revenue_cutoff_date: str
    business_context_id: str


@dataclass(frozen=True)
class MetricStoreRecord:
    result_id: str
    workflow_id: str
    workflow_run_id: str
    pipeline_id: str
    pipeline_run_id: str
    store_id: str
    store_asset_id: str
    metric_variant_id: str
    metric_variant_version: str
    workflow_reporting_date: str
    current_revenue_cutoff_date: str
    business_context_id: str
    reporting_period: str
    value: Decimal
    value_status: str
    numeric_semantics: str
    unit: str
    precision: str
    validation_status: str
    generated_at: str
    lineage_references: tuple[str, ...]

    @property
    def read_key(self) -> StoreReadKey:
        return StoreReadKey(
            self.store_id,
            self.store_asset_id,
            self.metric_variant_id,
            self.workflow_reporting_date,
            self.business_context_id,
        )

    @property
    def write_identity(self) -> StoreWriteIdentity:
        return StoreWriteIdentity(
            self.store_id,
            self.store_asset_id,
            self.current_revenue_cutoff_date,
            self.business_context_id,
        )


@dataclass(frozen=True)
class StoreWritePlan:
    write_identity: StoreWriteIdentity
    records: tuple[MetricStoreRecord, ...]
    business_digest: str
    idempotent_replay: bool


@dataclass(frozen=True)
class StoreWriteReceipt:
    write_identity: StoreWriteIdentity
    read_keys: tuple[StoreReadKey, ...]
    result_ids: tuple[str, ...]
    business_digest: str
    idempotent_replay: bool


class MetricStorePort(Protocol):
    """Physical-store-agnostic, result-set atomic operations used by CTV."""

    def read_exact(self, key: StoreReadKey) -> MetricStoreRecord: ...

    def preflight_write(self, records: tuple[MetricStoreRecord, ...]) -> StoreWritePlan: ...

    def write_validated(self, plan: StoreWritePlan) -> StoreWriteReceipt: ...

    def verify_write(self, receipt: StoreWriteReceipt) -> bool: ...


def _business_payload(records: tuple[MetricStoreRecord, ...]) -> list[dict[str, str]]:
    """Only frozen business value semantics participate in idempotency."""

    return [
        {
            "metric_variant_id": record.metric_variant_id,
            "value": str(record.value),
            "value_status": record.value_status,
            "numeric_semantics": record.numeric_semantics,
            "unit": record.unit,
            "precision": record.precision,
            "validation_status": record.validation_status,
        }
        for record in sorted(records, key=lambda item: item.metric_variant_id)
    ]


def _business_digest(records: tuple[MetricStoreRecord, ...]) -> str:
    encoded = json.dumps(
        _business_payload(records), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class InMemoryMetricStore:
    """Atomic exact-key Test Store with separate pending and verified states."""

    def __init__(self) -> None:
        self._verified: dict[StoreReadKey, list[MetricStoreRecord]] = {}
        self._pending: dict[StoreWriteIdentity, tuple[MetricStoreRecord, ...]] = {}
        self._verification_failures: set[StoreReadKey | StoreWriteIdentity] = set()

    def seed_historical(self, *records: MetricStoreRecord) -> None:
        """Seed explicitly verified synthetic history, including deliberate ambiguity."""

        for record in records:
            self._verified.setdefault(record.read_key, []).append(record)

    def force_verification_failure(self, key: StoreReadKey | StoreWriteIdentity) -> None:
        """Test-only fault injection without expanding the Store Port."""

        self._verification_failures.add(key)

    def read_exact(self, key: StoreReadKey) -> MetricStoreRecord:
        matches = self._verified.get(key, [])
        if not matches:
            raise MetricStoreError(
                "STORE_EXACT_KEY_NOT_FOUND",
                f"No verified Metric Result exists for exact key {key}",
            )
        if len(matches) != 1:
            raise MetricStoreError(
                "STORE_EXACT_KEY_AMBIGUOUS",
                f"More than one verified Metric Result exists for exact key {key}",
            )
        record = matches[0]
        if record.validation_status != "passed" or record.value_status != "valid_value":
            raise MetricStoreError(
                "STORE_RESULT_NOT_CONSUMABLE",
                "Historical Metric Result is not validation/value-status eligible",
            )
        return record

    def preflight_write(self, records: tuple[MetricStoreRecord, ...]) -> StoreWritePlan:
        if not records:
            raise MetricStoreError(
                "STORE_WRITE_SET_INCOMPLETE", "Validated Result Contract write set is empty"
            )
        identities = {record.write_identity for record in records}
        if len(identities) != 1:
            raise MetricStoreError(
                "STORE_WRITE_IDENTITY_MISMATCH",
                "Every record in a Result Contract write set must share one business identity",
            )
        if len({record.metric_variant_id for record in records}) != len(records):
            raise MetricStoreError(
                "STORE_WRITE_SET_DUPLICATE_METRIC",
                "Result Contract write set contains duplicate Metric identities",
            )
        for record in records:
            if record.validation_status != "passed" or record.value_status != "valid_value":
                raise MetricStoreError(
                    "STORE_WRITE_REQUIRES_VALIDATED_RESULT",
                    "Only passed, valid-value Metric Results may be written",
                )
        identity = next(iter(identities))
        digest = _business_digest(records)
        if any(self._verified.get(record.read_key) for record in records):
            exact_read_records = tuple(
                existing
                for record in records
                for existing in self._verified.get(record.read_key, ())
            )
            if (
                any(item.write_identity != identity for item in exact_read_records)
                or _business_digest(exact_read_records) != digest
            ):
                raise MetricStoreError(
                    "STORE_DUPLICATE_CONFLICT",
                    "Historical read identity already contains a conflicting result set",
                )
        existing = self._records_for_write_identity(identity)
        if existing:
            existing_records = tuple(existing)
            if _business_digest(existing_records) == digest:
                return StoreWritePlan(identity, records, digest, True)
            raise MetricStoreError(
                "STORE_DUPLICATE_CONFLICT",
                "Revenue business date already contains a different validated result set",
            )
        pending = self._pending.get(identity)
        if pending is not None and _business_digest(pending) != digest:
            raise MetricStoreError(
                "STORE_DUPLICATE_CONFLICT",
                "Revenue business date already contains a different unverified candidate set",
            )
        return StoreWritePlan(identity, records, digest, False)

    def write_validated(self, plan: StoreWritePlan) -> StoreWriteReceipt:
        if _business_digest(plan.records) != plan.business_digest:
            raise MetricStoreError(
                "STORE_WRITE_PLAN_INVALID", "Store write plan business semantics changed"
            )
        receipt_records = plan.records
        if plan.idempotent_replay:
            receipt_records = tuple(self._records_for_write_identity(plan.write_identity))
        else:
            # One assignment is the in-memory equivalent of an atomic result-set write.
            self._pending[plan.write_identity] = plan.records
        return StoreWriteReceipt(
            plan.write_identity,
            tuple(record.read_key for record in receipt_records),
            tuple(record.result_id for record in receipt_records),
            plan.business_digest,
            plan.idempotent_replay,
        )

    def verify_write(self, receipt: StoreWriteReceipt) -> bool:
        if receipt.write_identity in self._verification_failures or any(
            key in self._verification_failures for key in receipt.read_keys
        ):
            return False
        if receipt.idempotent_replay:
            existing = tuple(self._records_for_write_identity(receipt.write_identity))
            return bool(existing) and _business_digest(existing) == receipt.business_digest
        pending = self._pending.get(receipt.write_identity)
        if pending is None or _business_digest(pending) != receipt.business_digest:
            return False
        if tuple(record.read_key for record in pending) != receipt.read_keys:
            return False
        # Verification promotes the complete set together; pending candidates are never readable.
        for record in pending:
            self._verified.setdefault(record.read_key, []).append(record)
        del self._pending[receipt.write_identity]
        return True

    def _records_for_write_identity(
        self, identity: StoreWriteIdentity
    ) -> list[MetricStoreRecord]:
        return [
            record
            for records in self._verified.values()
            for record in records
            if record.write_identity == identity
        ]

    def records_for(self, key: StoreReadKey) -> tuple[MetricStoreRecord, ...]:
        """Return only verified, historically consumable records."""

        return tuple(self._verified.get(key, ()))

    def pending_records_for(
        self, identity: StoreWriteIdentity
    ) -> tuple[MetricStoreRecord, ...]:
        return self._pending.get(identity, ())
