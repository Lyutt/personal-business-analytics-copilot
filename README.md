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
3. [Stage 3E Weekly Output Assembly and Review Preview Exact Scope](phase1_5/assets/readiness/stage3e_weekly_output_assembly_review_preview_exact_scope.yaml)
4. [Stage 3D Weekly Workflow Runner Exact Scope](phase1_5/assets/readiness/stage3d_weekly_workflow_runner_exact_scope.yaml)
5. [Stage 3C Weekly Executor Completion Retrospective Scope](phase1_5/assets/readiness/stage3c_weekly_executor_completion_retrospective_scope.yaml)
6. [Stage 3B Revenue Expansion Exact Scope Contract](phase1_5/assets/readiness/stage3b_revenue_expansion_exact_scope.yaml)
7. [Weekly Implementation Baseline](phase1_5/assets/readiness/implementation_baseline.yaml)
8. [Customer Revenue Detail Implementation Baseline](phase1_5/assets/readiness/implementation_baseline_customer_revenue_detail.yaml)
9. [Phase 1.5 Asset Index](phase1_5/INDEX.md)
10. [Weekly Business Report Workflow v2](phase1_5/workflows/weekly_business_report/WORKFLOW_v2.md)
11. [Customer Revenue Detail Workflow v1](phase1_5/workflows/customer_revenue_detail/WORKFLOW_v1.md)

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
Scheduler, Draft, or Runtime Acceptance lifecycle. The exact Stage 3B Revenue
Expansion scope contract is registered and Owner-authorized. Store column and
formula evidence has been reconciled, and the Excel Adapter physical lineage
binding is registered with a very-hidden technical metadata worksheet that
stores no business values. The static-value Excel `MetricStorePort` increment
for Smart Speaker and Fast Version and the Technical/CTV formula-capable write
path are implemented and validated. Stage 3B implementation is completed and
its exit qualification has passed. Technical local real-data calculation
qualification is PASS; CTV reuses the Stage 3A PASS evidence. Smart Speaker and
Fast Version real-data Source-to-Result qualification was not executed and is
not a Stage 3B exit requirement. Stage 3C completed the remaining eight Weekly
Pipeline executors and seven registered shared-SQLite Store Assets in PR #18.
Its scope is retrospectively registered from the exact merged implementation
and does not claim pre-implementation scope authorization. Stage 3D implements
the deterministic sequential Weekly Workflow Runner over the existing 12
Pipeline executors and has passed its scoped exit qualification. Runtime
Acceptance, Stage 3E implementation, Stage 3F, Provider, Scheduler/Queue,
Outlook Draft/Send, Cutover, and Baseline promotion/refreeze remain
unauthorized. Automated checks, readiness Gates,
qualification evidence, and merge status do not grant any later-stage approval.

The Owner-confirmed Stage 3E exact scope is Weekly Output Assembly and Review
Preview, including only the minimum pre-assembly configured display-value
resolver and its registered `configured_display_values` SQLite state path.
This scope registration does not authorize implementation. Outlook Draft/Send,
Provider, Scheduler/Queue, Stage 3F, Runtime Acceptance, Cutover, and Baseline
promotion/refreeze remain excluded and unauthorized.

The two Workflows remain isolated: they may reuse registered source and Mapping
assets, but neither consumes the other Workflow's files or outputs. The initial
MVP remains sequential. The Acquisition Runtime Foundation, all 12 Weekly
Pipeline executors, their registered minimum Store paths, and the Stage 3D
deterministic Runner are implemented. The Output Engine, Provider acquisition,
Scheduler, Draft/Send, and production Runtime Acceptance remain unimplemented
and unauthorized.

Local business data, customer rows, emails, templates, recipients, credentials,
paths, resolved External Asset versions, and content fingerprints must remain
outside Git. Outlook output remains Draft-only with `auto_send=false`.
