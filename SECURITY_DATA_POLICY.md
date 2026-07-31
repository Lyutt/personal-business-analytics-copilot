# Repository Data Safety Policy

## Scope

This repository stores architecture, sanitized configuration, reusable templates, and
reviewable business-asset definitions for Personal Business Analytics Copilot.
Operational data remains local and is never a Git dependency.

## Prohibited Git Content

The following content must not be committed or pushed:

- Personal information or directly identifying contact details.
- Passwords, tokens, cookies, secrets, private keys, or session material.
- Corporate mailbox credentials or mailbox addresses tied to a person.
- Apollo or NovaBI login information.
- Raw email messages or attachments.
- Unredacted customer or revenue detail.
- Recurring raw business Excel or CSV extracts.
- Generated weekly reports, Outlook messages, or other workflow outputs.
- Absolute local paths that disclose the user's workstation or storage layout.

Tracked configuration must use neutral role names and local-only placeholders such as
`${LOCAL_WORKFLOW_DATA_ROOT}`. Placeholder values are resolved outside Git.

## Allowed Content

- Architecture and workflow documentation.
- Sanitized YAML configuration without personal identifiers or credentials.
- Reusable empty templates.
- Synthetic examples that are explicitly labeled and contain no real business data.
- Validation scripts and security policy files.

Binary spreadsheets require an explicit allowlist in
`scripts/validate_repository_safety.py` and must pass a content inspection before
their first upload.

## Local-Only Discovery Boundary

`phase1_5/discovery/` is local-only except for its repository-safe `README.md`.
Discovery evidence, mapping samples, customer lists, mail extracts, and review
backups stay on the user's computer and are not tracked.

## Pull Request and Merge Gate

1. Run the repository safety validation before every push.
2. Push only to a short-lived `codex/*` branch.
3. Keep the pull request in Draft while business review is pending.
4. Automated checks are necessary but not sufficient for merge.
5. Auto-merge is prohibited.
6. Squash Merge is allowed only after the user explicitly confirms the business
   review for that pull request.
7. Regressions on `main` are reversed through a dedicated Revert PR.

Outlook output always keeps `auto_send=false`.
