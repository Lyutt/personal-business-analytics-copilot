"""Outlook Draft activation/deployment gate; Send is never available."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ContractViolation

COMPLETION_STATUSES = {"complete_draft", "partial_draft", "blocked"}


@dataclass(frozen=True)
class DraftDecision:
    create_draft: bool
    warning_preface_required: bool
    reason: str
    auto_send: bool = False


def evaluate_draft_gate(
    *,
    workflow_status: str,
    runtime_acceptance_passed: bool,
    human_confirmation: bool,
    post_acceptance_auto_draft_owner_approved: bool,
) -> DraftDecision:
    if workflow_status not in COMPLETION_STATUSES:
        raise ContractViolation("Unknown Workflow completion status")
    if workflow_status == "blocked":
        return DraftDecision(False, False, "blocked_workflow")
    if not runtime_acceptance_passed and not human_confirmation:
        return DraftDecision(False, False, "pre_acceptance_human_confirmation_required")
    if (
        runtime_acceptance_passed
        and not human_confirmation
        and not post_acceptance_auto_draft_owner_approved
    ):
        return DraftDecision(False, False, "post_acceptance_auto_draft_not_authorized")
    return DraftDecision(
        True,
        workflow_status == "partial_draft",
        "explicit_gate_satisfied",
    )
