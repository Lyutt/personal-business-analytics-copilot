# Git Working Agreement

## Branch Model

- `main` is the single stable branch and contains only reviewed baselines.
- Work uses short-lived `codex/<stage-or-change>` branches.
- One pull request represents one coherent asset stage, gate, or corrective change.
- Pull requests start as Draft and should normally close within one working stage.
- Long-lived development, release, or environment branches are not used.

## Event-driven Commit Policy

Commits are created at meaningful recovery points, not on a timer:

1. An asset category or bounded rule set has been confirmed.
2. A readiness gate or stage archive has been completed.
3. A risky migration or bulk normalization is about to begin.
4. A coherent working session ends with validated, reviewable changes.

Do not commit every conversational answer. Do not combine unrelated stages merely to
reduce commit count.

After a commit, push the short-lived branch automatically only when the repository
safety check and relevant asset checks pass. A failed check blocks the push.

## Pull Request Gate

Before each push:

```text
python scripts/validate_repository_safety.py
```

The Draft PR must summarize:

- Scope and current stage.
- Changed assets and readiness status.
- Remaining TBD or blocking items.
- Data-safety validation result.

Automated checks do not authorize merge. Auto-merge is prohibited. The user must
complete business review and explicitly confirm that the exact PR may be merged.

## Merge Policy

- Merge method: Squash Merge only.
- Squash title: concise stage outcome in imperative form.
- Squash body: asset scope, gate result, and material open items.
- Delete the short-lived remote branch after the merge.
- Direct pushes and force pushes to `main` are prohibited.

## Rollback Policy

Rollback is performed through a new `codex/revert-<change>` branch and Revert PR:

1. Revert the single squash commit from `main`.
2. Run the same repository safety and asset checks.
3. Explain the affected business assets and why rollback is required.
4. Obtain explicit business confirmation.
5. Squash Merge the Revert PR.

Do not rewrite `main` history and do not use destructive resets for rollback.
