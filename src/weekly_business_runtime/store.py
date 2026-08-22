"""Minimal Metric Store port and deterministic in-memory Stage 3A implementation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Protocol

from .errors import MetricStoreError


@dataclass(frozen=True)
class StoreReadKey:
    """Frozen historical lookup identity: reporting period, not business cutoff."""

    store_id: str
    store_asset_id: str
    metric_variant_id: str
    workflow_reporting_date: str
    business_context_id: str
    product_parameter: str = "not_applicable"
    canonical_dimension_items: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Stage3CPhysicalReadKey:
    store_id: str
    store_asset_id: str
    metric_variant_id: str
    metric_variant_version: str
    reporting_period: str
    business_context_id: str
    product_parameter: str = "not_applicable"
    canonical_dimension_items: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class StoreBusinessDateReadKey:
    """Exact historical lookup identity keyed by the Store business date."""

    store_id: str
    store_asset_id: str
    metric_variant_id: str
    current_revenue_cutoff_date: str
    business_context_id: str


@dataclass(frozen=True)
class StorePhysicalSnapshotReadKey:
    """Exact technical read identity for a registered physical helper snapshot."""

    store_id: str
    store_asset_id: str
    field_id: str
    workflow_reporting_date: str
    business_context_id: str


@dataclass(frozen=True)
class StorePhysicalSnapshot:
    """Validated physical snapshot exposed through MetricStorePort without becoming a Result field."""

    read_key: StorePhysicalSnapshotReadKey
    metric_variant_id: str
    period_role: str
    represented_business_date: str
    value: Decimal
    numeric_semantics: str
    unit: str
    validation_status: str
    lineage_references: tuple[str, ...]


@dataclass(frozen=True)
class StoreWriteIdentity:
    """Frozen Revenue duplicate identity based on the Store business date."""

    store_id: str
    store_asset_id: str
    current_revenue_cutoff_date: str
    business_context_id: str
    product_parameter: str = "not_applicable"


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
    product_parameter: str = "not_applicable"
    canonical_dimensions: Mapping[str, str] = field(default_factory=dict)

    @property
    def read_key(self) -> StoreReadKey:
        return StoreReadKey(
            self.store_id,
            self.store_asset_id,
            self.metric_variant_id,
            self.workflow_reporting_date,
            self.business_context_id,
            self.product_parameter,
            tuple(sorted(self.canonical_dimensions.items())),
        )

    @property
    def write_identity(self) -> StoreWriteIdentity:
        return StoreWriteIdentity(
            self.store_id,
            self.store_asset_id,
            self.current_revenue_cutoff_date,
            self.business_context_id,
            self.product_parameter,
        )

    @property
    def business_date_read_key(self) -> StoreBusinessDateReadKey:
        return StoreBusinessDateReadKey(
            self.store_id,
            self.store_asset_id,
            self.metric_variant_id,
            self.current_revenue_cutoff_date,
            self.business_context_id,
        )


@dataclass(frozen=True)
class StorePhysicalValue:
    """Adapter-only validated value required by a physical Store schema."""

    field_id: str
    value: Decimal


@dataclass(frozen=True)
class StoreWriteContext:
    """Physical write context that never expands logical Result Contract semantics."""

    report_mode: str
    physical_values: tuple[StorePhysicalValue, ...] = ()


@dataclass(frozen=True)
class StoreWritePlan:
    write_identity: StoreWriteIdentity
    records: tuple[MetricStoreRecord, ...]
    business_digest: str
    idempotent_replay: bool
    physical_write_context: StoreWriteContext | None = None


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

    def read_exact_business_date(
        self, key: StoreBusinessDateReadKey
    ) -> MetricStoreRecord: ...

    def read_exact_physical_snapshot(
        self, key: StorePhysicalSnapshotReadKey
    ) -> StorePhysicalSnapshot: ...

    def preflight_write(
        self,
        records: tuple[MetricStoreRecord, ...],
        physical_write_context: StoreWriteContext | None = None,
    ) -> StoreWritePlan: ...

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
        self._physical_snapshots: dict[
            StorePhysicalSnapshotReadKey, list[StorePhysicalSnapshot]
        ] = {}
        self._pending: dict[StoreWriteIdentity, tuple[MetricStoreRecord, ...]] = {}
        self._verification_failures: set[
            StoreReadKey
            | StoreBusinessDateReadKey
            | StorePhysicalSnapshotReadKey
            | StoreWriteIdentity
        ] = set()

    def seed_historical(self, *records: MetricStoreRecord) -> None:
        """Seed explicitly verified synthetic history, including deliberate ambiguity."""

        for record in records:
            self._verified.setdefault(record.read_key, []).append(record)

    def seed_physical_snapshot(self, *snapshots: StorePhysicalSnapshot) -> None:
        """Seed explicitly validated synthetic physical snapshots for port tests."""

        for snapshot in snapshots:
            self._physical_snapshots.setdefault(snapshot.read_key, []).append(snapshot)

    def force_verification_failure(
        self,
        key: StoreReadKey
        | StoreBusinessDateReadKey
        | StorePhysicalSnapshotReadKey
        | StoreWriteIdentity,
    ) -> None:
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

    def read_exact_business_date(
        self, key: StoreBusinessDateReadKey
    ) -> MetricStoreRecord:
        if key in self._verification_failures:
            raise MetricStoreError(
                "STORE_EXACT_BUSINESS_DATE_NOT_VERIFIED",
                "Exact business-date Metric Result failed verification",
            )
        matches = [
            record
            for records in self._verified.values()
            for record in records
            if record.business_date_read_key == key
        ]
        if not matches:
            raise MetricStoreError(
                "STORE_EXACT_BUSINESS_DATE_NOT_FOUND",
                f"No verified Metric Result exists for exact business-date key {key}",
            )
        if len(matches) != 1:
            raise MetricStoreError(
                "STORE_EXACT_BUSINESS_DATE_AMBIGUOUS",
                "More than one verified Metric Result exists for the exact business-date key",
            )
        record = matches[0]
        if record.validation_status != "passed" or record.value_status != "valid_value":
            raise MetricStoreError(
                "STORE_RESULT_NOT_CONSUMABLE",
                "Historical Metric Result is not validation/value-status eligible",
            )
        return record

    def read_exact_physical_snapshot(
        self, key: StorePhysicalSnapshotReadKey
    ) -> StorePhysicalSnapshot:
        if key in self._verification_failures:
            raise MetricStoreError(
                "STORE_EXACT_PHYSICAL_SNAPSHOT_NOT_VERIFIED",
                "Exact physical snapshot failed verification",
            )
        matches = self._physical_snapshots.get(key, [])
        if not matches:
            raise MetricStoreError(
                "STORE_EXACT_PHYSICAL_SNAPSHOT_NOT_FOUND",
                f"No validated physical snapshot exists for exact key {key}",
            )
        if len(matches) != 1:
            raise MetricStoreError(
                "STORE_EXACT_PHYSICAL_SNAPSHOT_AMBIGUOUS",
                "More than one validated physical snapshot exists for the exact key",
            )
        snapshot = matches[0]
        if (
            snapshot.validation_status != "passed"
            or not isinstance(snapshot.value, Decimal)
            or not snapshot.value.is_finite()
            or not snapshot.lineage_references
        ):
            raise MetricStoreError(
                "STORE_PHYSICAL_SNAPSHOT_NOT_CONSUMABLE",
                "Historical physical snapshot is not validation/value eligible",
            )
        return snapshot

    def preflight_write(
        self,
        records: tuple[MetricStoreRecord, ...],
        physical_write_context: StoreWriteContext | None = None,
    ) -> StoreWritePlan:
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
        if (
            len({record.metric_variant_id for record in records}) != len(records)
            and len({record.read_key for record in records}) != len(records)
        ):
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
                return StoreWritePlan(
                    identity, records, digest, True, physical_write_context
                )
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
        return StoreWritePlan(identity, records, digest, False, physical_write_context)

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


STAGE3C_SQLITE_STORE_ASSETS = frozenset(
    {
        "STORE_ASSET_WEEKLY_INVENTORY_FULL_SITE",
        "STORE_ASSET_WEEKLY_INVENTORY_PATCH",
        "STORE_ASSET_WEEKLY_INVENTORY_NON_PATCH_PRODUCT",
        "STORE_ASSET_WEEKLY_INVENTORY_BRAND_MOMENT_SELL_THROUGH",
        "STORE_ASSET_WEEKLY_INVENTORY_PRODUCT_SELL_THROUGH",
        "STORE_ASSET_WEEKLY_BRAND_MOMENT_DELIVERY",
        "STORE_ASSET_WEEKLY_PLATFORM_DAU",
    }
)


class SqliteMetricStore:
    """Stage 3C's direct SQLite implementation of the existing MetricStorePort."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metric_results (
                    result_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workflow_run_id TEXT NOT NULL,
                    pipeline_id TEXT NOT NULL,
                    pipeline_run_id TEXT NOT NULL,
                    store_id TEXT NOT NULL,
                    store_asset_id TEXT NOT NULL,
                    metric_variant_id TEXT NOT NULL,
                    metric_variant_version TEXT NOT NULL,
                    reporting_period_start TEXT NOT NULL,
                    reporting_period_end TEXT NOT NULL,
                    workflow_reporting_date TEXT,
                    current_revenue_cutoff_date TEXT,
                    dimensions_json TEXT NOT NULL,
                    value_numeric NUMERIC NOT NULL,
                    numeric_semantics TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    integer_only INTEGER NOT NULL,
                    precision TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    CHECK (integer_only IN (0, 1)),
                    CHECK (validation_status = 'passed'),
                    UNIQUE (store_id, store_asset_id, metric_variant_id,
                        metric_variant_version, reporting_period_start,
                        reporting_period_end, dimensions_json)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _dimensions(record: MetricStoreRecord) -> str:
        return json.dumps(
            {
                "business_context_id": record.business_context_id,
                "product_parameter": record.product_parameter,
                **record.canonical_dimensions,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> MetricStoreRecord:
        dimensions = json.loads(row["dimensions_json"])
        return MetricStoreRecord(
            result_id=row["result_id"], workflow_id=row["workflow_id"],
            workflow_run_id=row["workflow_run_id"], pipeline_id=row["pipeline_id"],
            pipeline_run_id=row["pipeline_run_id"], store_id=row["store_id"],
            store_asset_id=row["store_asset_id"], metric_variant_id=row["metric_variant_id"],
            metric_variant_version=row["metric_variant_version"],
            workflow_reporting_date=row["workflow_reporting_date"],
            current_revenue_cutoff_date=row["current_revenue_cutoff_date"],
            business_context_id=dimensions["business_context_id"],
            reporting_period=f"{row['reporting_period_start']}..{row['reporting_period_end']}",
            value=Decimal(str(row["value_numeric"])), value_status="valid_value",
            numeric_semantics=row["numeric_semantics"], unit=row["unit"], precision=row["precision"],
            validation_status=row["validation_status"], generated_at=row["generated_at"],
            lineage_references=(),
            product_parameter=dimensions["product_parameter"],
            canonical_dimensions={
                key: value for key, value in dimensions.items()
                if key not in {"business_context_id", "product_parameter"}
            },
        )

    @staticmethod
    def _require_stage3c_asset(record: MetricStoreRecord) -> None:
        if record.store_asset_id not in STAGE3C_SQLITE_STORE_ASSETS:
            raise MetricStoreError("STORE_ASSET_NOT_AUTHORIZED", "Store Asset is outside Stage 3C SQLite scope")

    def _matching_records(self, key: StoreReadKey) -> tuple[MetricStoreRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM metric_results WHERE store_id = ? AND store_asset_id = ?
                AND metric_variant_id = ?""",
                (key.store_id, key.store_asset_id, key.metric_variant_id),
            ).fetchall()
        return tuple(
            record for row in rows if (record := self._record_from_row(row)).read_key == key
        )

    def _physical_matches(self, record: MetricStoreRecord) -> tuple[MetricStoreRecord, ...]:
        start, end = record.reporting_period.split("..", 1)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM metric_results WHERE store_id = ? AND store_asset_id = ?
                AND metric_variant_id = ? AND metric_variant_version = ?
                AND reporting_period_start = ? AND reporting_period_end = ? AND dimensions_json = ?""",
                (record.store_id, record.store_asset_id, record.metric_variant_id,
                 record.metric_variant_version, start, end, self._dimensions(record)),
            ).fetchall()
        return tuple(self._record_from_row(row) for row in rows)

    def read_exact(self, key: StoreReadKey) -> MetricStoreRecord:
        records = self._matching_records(key)
        if len(records) != 1:
            code = "STORE_EXACT_KEY_NOT_FOUND" if not records else "STORE_EXACT_KEY_AMBIGUOUS"
            raise MetricStoreError(code, "SQLite Metric Result exact key did not resolve uniquely")
        record = records[0]
        if record.validation_status != "passed" or record.value_status != "valid_value":
            raise MetricStoreError("STORE_RESULT_NOT_CONSUMABLE", "Historical result is not consumable")
        return record

    def read_stage3c_exact(self, key: Stage3CPhysicalReadKey) -> MetricStoreRecord:
        start, end = key.reporting_period.split("..", 1)
        dimensions = json.dumps({"business_context_id": key.business_context_id, "product_parameter": key.product_parameter, **dict(key.canonical_dimension_items)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as connection:
            rows = connection.execute("""SELECT * FROM metric_results WHERE store_id = ? AND store_asset_id = ? AND metric_variant_id = ? AND metric_variant_version = ? AND reporting_period_start = ? AND reporting_period_end = ? AND dimensions_json = ?""", (key.store_id, key.store_asset_id, key.metric_variant_id, key.metric_variant_version, start, end, dimensions)).fetchall()
        if len(rows) != 1:
            raise MetricStoreError("STORE_EXACT_KEY_NOT_FOUND" if not rows else "STORE_EXACT_KEY_AMBIGUOUS", "Stage 3C physical exact key did not resolve uniquely")
        return self._record_from_row(rows[0])

    def read_exact_business_date(self, key: StoreBusinessDateReadKey) -> MetricStoreRecord:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM metric_results WHERE store_id = ? AND store_asset_id = ?
                AND metric_variant_id = ? AND current_revenue_cutoff_date = ?""",
                (key.store_id, key.store_asset_id, key.metric_variant_id, key.current_revenue_cutoff_date),
            ).fetchall()
        records = tuple(
            record for row in rows
            if (record := self._record_from_row(row)).business_context_id == key.business_context_id
        )
        if len(records) != 1:
            code = "STORE_EXACT_BUSINESS_DATE_NOT_FOUND" if not records else "STORE_EXACT_BUSINESS_DATE_AMBIGUOUS"
            raise MetricStoreError(code, "SQLite business-date key did not resolve uniquely")
        return records[0]

    def read_exact_physical_snapshot(self, key: StorePhysicalSnapshotReadKey) -> StorePhysicalSnapshot:
        raise MetricStoreError(
            "STORE_PHYSICAL_SNAPSHOT_UNSUPPORTED",
            "Stage 3C SQLite contracts do not expose physical snapshots",
        )

    def preflight_write(
        self, records: tuple[MetricStoreRecord, ...], physical_write_context: StoreWriteContext | None = None
    ) -> StoreWritePlan:
        if not records:
            raise MetricStoreError("STORE_WRITE_SET_INCOMPLETE", "Validated write set is empty")
        identities = {record.write_identity for record in records}
        if len(identities) != 1:
            raise MetricStoreError("STORE_WRITE_IDENTITY_MISMATCH", "Write set identity is not unique")
        for record in records:
            self._require_stage3c_asset(record)
            if record.validation_status != "passed" or record.value_status != "valid_value":
                raise MetricStoreError("STORE_WRITE_REQUIRES_VALIDATED_RESULT", "Only valid results may persist")
        identity = next(iter(identities))
        digest = _business_digest(records)
        existing = tuple(item for record in records for item in self._physical_matches(record))
        if existing:
            if len(existing) != len(records) or _business_digest(existing) != digest:
                raise MetricStoreError("STORE_DUPLICATE_CONFLICT", "Existing SQLite Result set conflicts")
            return StoreWritePlan(identity, records, digest, True, physical_write_context)
        return StoreWritePlan(identity, records, digest, False, physical_write_context)

    def write_validated(self, plan: StoreWritePlan) -> StoreWriteReceipt:
        if _business_digest(plan.records) != plan.business_digest:
            raise MetricStoreError("STORE_WRITE_PLAN_INVALID", "Write plan changed after validation")
        if not plan.idempotent_replay:
            with self._connect() as connection:
                for record in plan.records:
                    self._require_stage3c_asset(record)
                    start, end = record.reporting_period.split("..", 1)
                    connection.execute(
                        """INSERT INTO metric_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (record.result_id, record.workflow_id, record.workflow_run_id, record.pipeline_id,
                         record.pipeline_run_id, record.store_id, record.store_asset_id,
                         record.metric_variant_id, record.metric_variant_version, start, end,
                         record.workflow_reporting_date, record.current_revenue_cutoff_date,
                         self._dimensions(record), str(record.value), record.numeric_semantics,
                         record.unit, int(record.precision == "integer"), record.precision,
                         record.validation_status, record.generated_at),
                    )
        return StoreWriteReceipt(
            plan.write_identity, tuple(record.read_key for record in plan.records),
            tuple(record.result_id for record in plan.records), plan.business_digest, plan.idempotent_replay
        )

    def verify_write(self, receipt: StoreWriteReceipt) -> bool:
        try:
            records = tuple(self.read_exact(key) for key in receipt.read_keys)
        except MetricStoreError:
            return False
        return _business_digest(records) == receipt.business_digest
