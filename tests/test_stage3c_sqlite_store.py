import sqlite3
from dataclasses import replace
from decimal import Decimal

import pytest

from weekly_business_runtime.store import (
    STAGE3C_SQLITE_STORE_ASSETS,
    MetricStoreError,
    MetricStoreRecord,
    SqliteMetricStore,
    Stage3CPhysicalReadKey,
)


def record(asset: str, *, value: Decimal = Decimal("1"), product: str = "not_applicable") -> MetricStoreRecord:
    return MetricStoreRecord(
        result_id=f"RESULT:{asset}:{product}", workflow_id="WF_WEEKLY_BUSINESS_REPORT",
        workflow_run_id="RUN", pipeline_id="PIPELINE", pipeline_run_id="PIPELINE_RUN",
        store_id="STORE", store_asset_id=asset, metric_variant_id="MV_TEST_V1",
        metric_variant_version="1.0.0", workflow_reporting_date="2026-08-21",
        current_revenue_cutoff_date="not_applicable", business_context_id="CTX_TEST",
        reporting_period="2026-08-17..2026-08-23", value=value, value_status="valid_value",
        numeric_semantics="integer_count", unit="inventory_count", precision="integer",
        validation_status="passed", generated_at="2026-08-22T00:00:00+08:00",
        lineage_references=("fixture://input",), product_parameter=product,
    )


@pytest.mark.parametrize("asset", sorted(STAGE3C_SQLITE_STORE_ASSETS))
def test_stage3c_assets_are_idempotent_and_readable(tmp_path, asset):
    store = SqliteMetricStore(tmp_path / "metric_results.sqlite")
    first = record(asset, product="Product A" if asset.endswith("PRODUCT_SELL_THROUGH") else "not_applicable")
    receipt = store.write_validated(store.preflight_write((first,)))
    assert store.verify_write(receipt)
    replay = store.write_validated(store.preflight_write((replace(first, result_id="REPLAY"),)))
    assert replay.idempotent_replay
    assert store.read_exact(first.read_key).value == Decimal("1")


def test_stage3c_store_rejects_conflict_and_unapproved_asset(tmp_path):
    store = SqliteMetricStore(tmp_path / "metric_results.sqlite")
    original = record("STORE_ASSET_WEEKLY_INVENTORY_FULL_SITE")
    assert store.verify_write(store.write_validated(store.preflight_write((original,))))
    with pytest.raises(MetricStoreError, match="conflicts"):
        store.preflight_write((replace(original, value=Decimal("2"), result_id="CONFLICT"),))
    with pytest.raises(MetricStoreError, match="outside Stage 3C"):
        store.preflight_write((replace(original, store_asset_id="STORE_ASSET_NOT_AUTHORIZED"),))


def test_sqlite_canonical_key_ignores_lineage_and_uses_version_period_and_dimensions(tmp_path):
    store = SqliteMetricStore(tmp_path / "metric_results.sqlite")
    original = record("STORE_ASSET_WEEKLY_PLATFORM_DAU", product="not_applicable")
    assert store.verify_write(store.write_validated(store.preflight_write((original,))))
    same = replace(original, result_id="OTHER", workflow_run_id="OTHER_RUN", pipeline_run_id="OTHER_PIPELINE", lineage_references=("fixture://other",))
    assert store.write_validated(store.preflight_write((same,))).idempotent_replay
    with pytest.raises(MetricStoreError, match="conflicts"):
        store.preflight_write((replace(same, value=Decimal("2")),))
    changed_version = replace(original, metric_variant_version="2.0.0")
    changed_version_receipt = store.write_validated(store.preflight_write((changed_version,)))
    assert store.verify_write(changed_version_receipt)
    changed_period = replace(original, reporting_period="2026-08-24..2026-08-30")
    changed_period_receipt = store.write_validated(store.preflight_write((changed_period,)))
    assert store.verify_write(changed_period_receipt)
    assert store.read_stage3c_exact(Stage3CPhysicalReadKey("STORE", original.store_asset_id, "MV_TEST_V1", "1.0.0", original.reporting_period, "CTX_TEST")).value == Decimal("1")
    assert store.read_stage3c_exact(Stage3CPhysicalReadKey("STORE", original.store_asset_id, "MV_TEST_V1", "2.0.0", original.reporting_period, "CTX_TEST")).value == Decimal("1")
    assert store.read_stage3c_exact(Stage3CPhysicalReadKey("STORE", original.store_asset_id, "MV_TEST_V1", "1.0.0", changed_period.reporting_period, "CTX_TEST")).value == Decimal("1")
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM metric_results").fetchone()[0] == 3


@pytest.mark.parametrize(("unit", "numeric_semantics", "precision", "integer_only"), [
    ("inventory_unit", "integer_count", "integer", 1),
    ("user", "average_count", "preserve_source_precision", 0),
    ("percentage_point", "percentage_point_change", "preserve_source_precision", 0),
])
def test_sqlite_preserves_registered_non_revenue_metadata(tmp_path, unit, numeric_semantics, precision, integer_only):
    store = SqliteMetricStore(tmp_path / "metric_results.sqlite")
    item = replace(record("STORE_ASSET_WEEKLY_PLATFORM_DAU"), unit=unit, numeric_semantics=numeric_semantics, precision=precision)
    store.write_validated(store.preflight_write((item,)))
    with sqlite3.connect(store.database_path) as connection:
        row = connection.execute("SELECT current_revenue_cutoff_date, integer_only, precision FROM metric_results").fetchone()
    assert row == ("not_applicable", integer_only, precision)
