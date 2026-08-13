"""Minimal Metric Store port and deterministic in-memory Stage 3A implementation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Protocol

from .errors import MetricStoreError


@dataclass(frozen=True)
class StoreReadKey:
    store_id: str
    store_asset_id: str
    metric_variant_id: str
    workflow_reporting_date: str
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
            store_id=self.store_id,
            store_asset_id=self.store_asset_id,
            metric_variant_id=self.metric_variant_id,
            workflow_reporting_date=self.workflow_reporting_date,
            business_context_id=self.business_context_id,
        )


@dataclass(frozen=True)
class StoreWriteReceipt:
    read_key: StoreReadKey
    result_id: str
    record_digest: str
    idempotent_replay: bool


class MetricStorePort(Protocol):
    """Physical-store-agnostic operations required by the CTV Pipeline."""

    def read_exact(self, key: StoreReadKey) -> MetricStoreRecord: ...

    def write_validated(self, record: MetricStoreRecord) -> StoreWriteReceipt: ...

    def verify_write(self, receipt: StoreWriteReceipt) -> bool: ...


def _record_digest(record: MetricStoreRecord) -> str:
    payload = asdict(record)
    payload["value"] = str(record.value)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class InMemoryMetricStore:
    """Exact-key Test Store; it has no Excel, SQLite, or path dependency."""

    def __init__(self) -> None:
        self._records: dict[StoreReadKey, list[MetricStoreRecord]] = {}
        self._verification_failures: set[StoreReadKey] = set()

    def seed_historical(self, *records: MetricStoreRecord) -> None:
        """Seed synthetic history, including deliberate ambiguity for negative tests."""

        for record in records:
            self._records.setdefault(record.read_key, []).append(record)

    def force_verification_failure(self, key: StoreReadKey) -> None:
        """Test-only fault injection without changing the Store Port contract."""

        self._verification_failures.add(key)

    def read_exact(self, key: StoreReadKey) -> MetricStoreRecord:
        matches = self._records.get(key, [])
        if not matches:
            raise MetricStoreError(
                "STORE_EXACT_KEY_NOT_FOUND",
                f"No validated Metric Result exists for exact key {key}",
            )
        if len(matches) != 1:
            raise MetricStoreError(
                "STORE_EXACT_KEY_AMBIGUOUS",
                f"More than one Metric Result exists for exact key {key}",
            )
        record = matches[0]
        if record.validation_status != "passed":
            raise MetricStoreError(
                "STORE_RESULT_NOT_VALIDATED",
                "Historical Metric Result validation_status must be passed",
            )
        return record

    def write_validated(self, record: MetricStoreRecord) -> StoreWriteReceipt:
        if record.validation_status != "passed" or record.value_status != "valid_value":
            raise MetricStoreError(
                "STORE_WRITE_REQUIRES_VALIDATED_RESULT",
                "Only passed, valid-value Metric Results may be written",
            )
        key = record.read_key
        existing = self._records.get(key, [])
        digest = _record_digest(record)
        if existing:
            if len(existing) == 1 and _record_digest(existing[0]) == digest:
                return StoreWriteReceipt(key, record.result_id, digest, True)
            raise MetricStoreError(
                "STORE_DUPLICATE_CONFLICT",
                "Exact business key already contains a different or ambiguous result",
            )
        self._records[key] = [record]
        return StoreWriteReceipt(key, record.result_id, digest, False)

    def verify_write(self, receipt: StoreWriteReceipt) -> bool:
        if receipt.read_key in self._verification_failures:
            return False
        matches = self._records.get(receipt.read_key, [])
        return (
            len(matches) == 1
            and matches[0].result_id == receipt.result_id
            and _record_digest(matches[0]) == receipt.record_digest
        )

    def records_for(self, key: StoreReadKey) -> tuple[MetricStoreRecord, ...]:
        return tuple(self._records.get(key, ()))
