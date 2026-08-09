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

The Weekly and Customer Revenue Detail implementation baselines are frozen for
Owner review. Code Implementation has not started and must not start until the
Owner explicitly approves the applicable baseline ID and version. Automated
checks, readiness Gates, synthetic acceptance, and merge status do not grant
that approval.

The two Workflows remain isolated: they may reuse registered source and Mapping
assets, but neither consumes the other Workflow's files or outputs. The initial
MVP remains sequential and design-only; no runtime Workflow Engine has been
implemented.

Local business data, customer rows, emails, templates, recipients, credentials,
paths, resolved External Asset versions, and content fingerprints must remain
outside Git. Outlook output remains Draft-only with `auto_send=false`.
