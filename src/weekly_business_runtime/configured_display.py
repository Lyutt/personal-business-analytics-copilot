"""Minimum configured display-value resolution required before Stage 3E assembly."""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .errors import Stage3AError

POLICY_ID = "POLICY_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE_DISPLAY_V1"
WORKFLOW_ID = "WF_WEEKLY_BUSINESS_REPORT"


class ConfiguredDisplayValueResolver:
    """Implement only the frozen configured-value Policy and its SQLite state."""

    def __init__(
        self,
        *,
        repository_root: Path,
        database_path: Path,
        choose: Callable[[Sequence[str]], str] | None = None,
        policy: Mapping[str, Any] | None = None,
    ) -> None:
        self.database_path = database_path
        self.choose = choose or random.SystemRandom().choice
        self.policy = dict(policy or self._load_policy(repository_root))
        self._validate_policy()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS configured_display_values (
                    policy_id TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    reporting_period_id TEXT NOT NULL,
                    configured_value TEXT NOT NULL
                        CHECK (configured_value IN ('92%','93%','94%','95%')),
                    selected_at TEXT NOT NULL,
                    workflow_run_id TEXT NOT NULL,
                    UNIQUE (policy_id, workflow_id, reporting_period_id)
                )
                """
            )

    @staticmethod
    def _load_policy(repository_root: Path) -> Mapping[str, Any]:
        path = (
            repository_root / "phase1_5/assets/policies/"
            "POLICY_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE_DISPLAY_V1.yaml"
        )
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, Mapping):
            raise Stage3AError("STAGE3E_CONFIGURED_POLICY_INVALID", "Policy is invalid")
        return document

    def _validate_policy(self) -> None:
        selection = self.policy.get("selection_policy", {})
        persistence = self.policy.get("persistence_policy", {})
        physical = persistence.get("physical_state_store", {})
        if (
            self.policy.get("policy_id") != POLICY_ID
            or self.policy.get("workflow_id") != WORKFLOW_ID
            or tuple(self.policy.get("allowed_display_values", ()))
            != ("92%", "93%", "94%", "95%")
            or selection.get("selection_method") != "random_choice"
            or selection.get("current_period_state_precedence")
            != "reuse_saved_value_without_reselection"
            or selection.get("when_previous_period_value_exists", {}).get(
                "previous_period_repeat_allowed"
            )
            is not False
            or persistence.get("same_period_reselection_allowed") is not False
            or physical.get("provider") != "SQLite"
            or physical.get("table_name") != "configured_display_values"
            or persistence.get("metric_result_store") is not False
        ):
            raise Stage3AError(
                "STAGE3E_CONFIGURED_POLICY_INVALID",
                "Configured display-value Policy is not the frozen Stage 3E authority",
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def read_exact(self, reporting_period_id: str) -> str | None:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT configured_value FROM configured_display_values
                WHERE policy_id = ? AND workflow_id = ? AND reporting_period_id = ?""",
                (POLICY_ID, WORKFLOW_ID, reporting_period_id),
            ).fetchall()
        if len(rows) > 1:
            raise Stage3AError(
                "STAGE3E_CONFIGURED_VALUE_AMBIGUOUS",
                "Configured display state exact key is ambiguous",
            )
        return str(rows[0]["configured_value"]) if rows else None

    def persist_selected(
        self,
        *,
        reporting_period_id: str,
        configured_value: str,
        selected_at: str,
        workflow_run_id: str,
    ) -> str:
        if configured_value not in self.policy["allowed_display_values"]:
            raise Stage3AError(
                "STAGE3E_CONFIGURED_VALUE_INVALID",
                "Selected value is outside the frozen Policy",
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT configured_value FROM configured_display_values
                WHERE policy_id = ? AND workflow_id = ? AND reporting_period_id = ?""",
                (POLICY_ID, WORKFLOW_ID, reporting_period_id),
            ).fetchone()
            if row is not None:
                existing = str(row["configured_value"])
                if existing != configured_value:
                    raise Stage3AError(
                        "STAGE3E_CONFIGURED_VALUE_CONFLICT",
                        "Same-period configured display value conflicts with persisted state",
                    )
                return existing
            connection.execute(
                """INSERT INTO configured_display_values
                (policy_id, workflow_id, reporting_period_id, configured_value,
                 selected_at, workflow_run_id) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    POLICY_ID,
                    WORKFLOW_ID,
                    reporting_period_id,
                    configured_value,
                    selected_at,
                    workflow_run_id,
                ),
            )
        verified = self.read_exact(reporting_period_id)
        if verified != configured_value:
            raise Stage3AError(
                "STAGE3E_CONFIGURED_VALUE_VERIFICATION_FAILED",
                "Configured display value could not be read back by its exact key",
            )
        return verified

    def resolve(
        self,
        *,
        reporting_period_id: str,
        previous_reporting_period_id: str | None,
        selected_at: str,
        workflow_run_id: str,
    ) -> str:
        current = self.read_exact(reporting_period_id)
        if current is not None:
            return current
        previous = (
            self.read_exact(previous_reporting_period_id)
            if previous_reporting_period_id is not None
            else None
        )
        allowed = tuple(str(value) for value in self.policy["allowed_display_values"])
        candidates = tuple(value for value in allowed if value != previous)
        selected = self.choose(candidates)
        if selected not in candidates:
            raise Stage3AError(
                "STAGE3E_CONFIGURED_SELECTION_INVALID",
                "Configured display selector returned a value outside the frozen candidates",
            )
        return self.persist_selected(
            reporting_period_id=reporting_period_id,
            configured_value=selected,
            selected_at=selected_at,
            workflow_run_id=workflow_run_id,
        )
