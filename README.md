# personal-business-analytics-copilot

Personal Business Analytics Copilot currently uses the versioned Phase 1.5
design assets for two independent Workflows as its formal implementation
baseline: Weekly Business Report and Customer Revenue Detail. The Phase 1.5
design-contract closure is merged to `main`. Phase 1 documents are retained as
historical design material and must not be used as the current executable
contract.

## Current authority

1. [Weekly Phase 1.5 Status Index](phase1_5/assets/readiness/status_index.yaml)
2. [Customer Revenue Detail Status Index](phase1_5/assets/readiness/status_index_customer_revenue_detail.yaml)
3. [Weekly Implementation Baseline](phase1_5/assets/readiness/implementation_baseline.yaml)
4. [Customer Revenue Detail Implementation Baseline](phase1_5/assets/readiness/implementation_baseline_customer_revenue_detail.yaml)
5. [Phase 1.5 Asset Index](phase1_5/INDEX.md)
6. [Weekly Business Report Workflow v2](phase1_5/workflows/weekly_business_report/WORKFLOW_v2.md)
7. [Customer Revenue Detail Workflow v1](phase1_5/workflows/customer_revenue_detail/WORKFLOW_v1.md)

The Weekly Acquisition Runtime Foundation completed Stage 2 in PR #8. Pydantic
V2 validation parity and Ruff Phases 1, 2A, and 2B were merged in PRs #9-#12,
and the Stage 2.5 governance synchronization was merged in PR #13. Stage 3A's
CTV vertical slice was completed in PR #14, followed by the CTV prior-year
source-authority correction in PR #15. The exact PR #15 head passed all 47
Stage 3A CTV tests.

Runtime Contract v1.2 is the active execution and validation candidate only.
It has not been promoted or refrozen as a new formal Runtime Baseline. The
local-only real-data calculation qualification has passed for the CTV
calculation scope in ordinary-week and quarter-transition scenarios. This does
not qualify a production Excel Store Adapter or any Provider, Store-write,
Scheduler, Draft, or Runtime Acceptance lifecycle. Stage 3A governance closure
remains current; Stage 3B has no registered scope contract or authorization,
and Runtime Acceptance has not started. Automated checks, readiness Gates,
qualification evidence, and merge status do not grant any later-stage approval.

The two Workflows remain isolated: they may reuse registered source and Mapping
assets, but neither consumes the other Workflow's files or outputs. The initial
MVP remains sequential. The Acquisition Runtime Foundation exists, while the
the Stage 3A CTV Data Engine and Business Execution slice is implemented. Other
business Pipelines, the Output Engine, and the end-to-end Workflow runtime are
not implied complete by that vertical slice.

Local business data, customer rows, emails, templates, recipients, credentials,
paths, resolved External Asset versions, and content fingerprints must remain
outside Git. Outlook output remains Draft-only with `auto_send=false`.
