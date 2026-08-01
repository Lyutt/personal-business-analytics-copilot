## Scope

- [ ] The PR has one clear stage or change objective.
- [ ] Architecture, asset, or workflow status indexes are synchronized.

## Data Safety Gate

- [ ] `python scripts/validate_repository_safety.py` passes.
- [ ] No personal information, credentials, mailbox identity, login information,
      raw attachments, customer/revenue detail, mutable raw Excel/CSV, or generated
      report output is included.
- [ ] Any new binary template has been explicitly inspected and allowlisted.
- [ ] Outlook remains `auto_send=false`.

## Business Review

- [ ] The PR remains Draft until business review is complete.
- [ ] The user has explicitly confirmed this exact PR may be Squash Merged.

Auto-merge is prohibited. Do not merge based only on automated checks.
