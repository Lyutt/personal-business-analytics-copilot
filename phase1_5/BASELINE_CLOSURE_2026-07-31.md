# Phase 1.5 Baseline Closure — 2026-07-31

## Baseline Outcome

This baseline closes the repository state through the current Business Rule
Initialization checkpoint. It does not close Phase 1.5 and does not authorize code
implementation.

## Completed and Archived

- Personal Business Analytics Copilot long-term architecture direction.
- Weekly Business Report Workflow v2 architecture.
- Active Data Source Inventory: 3 sources.
- Dataset / Query Asset Inventory for the current Weekly Workflow scope.
- Weekly Business Report Pipeline Registry: 12 registered Pipelines.
- Field Mapping Initialization for the original 9 in-scope Datasets.
- Original Field Mapping Readiness Gate: passed.
- Business Rule Initialization: 3 approved Revenue rules.

## Open Items

- Add the “业务线” Sheet mapping delta for
  `DS_REVENUE_SALES_ROLLING_DECK_QTD`.
- Confirm the fallback activation policy for the quarter-close confirmation Dataset.
- Complete the remaining Business Rule categories.
- Initialize Metric Library and Metric Variants.
- Initialize Output Mapping.
- Re-run the relevant readiness gates after each approved scope change.
- Code implementation remains out of scope.

## Repository Safety Boundary

- Architecture, sanitized configuration, templates, gates, and stage archives may be
  tracked.
- Local discovery evidence and operational business data are excluded from Git.
- Personal identifiers, credentials, raw mail, raw business data, customer/revenue
  detail, and generated weekly outputs are prohibited.
- Local paths and identities use `${..._LOCAL_ONLY}` placeholders.
- Outlook always keeps `auto_send=false`.

## Source-of-Truth Order

1. Approved versioned assets under `phase1_5/assets/`.
2. Current stage index under `phase1_5/`.
3. Workflow v2 definition.
4. Historical Phase 1 files for reference only.
5. Local-only discovery evidence is supporting material, not a Git dependency.

## Merge Status

The baseline is ready for automated validation and business review. Squash Merge
requires a separate explicit user confirmation after the Draft PR is reviewed.
