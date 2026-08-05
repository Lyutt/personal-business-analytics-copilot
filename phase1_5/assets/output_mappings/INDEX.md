# Output Mapping Assets

## Registered assets

- `OM_WEEKLY_BUSINESS_REPORT_V1.yaml` — Weekly Business Report email-body mapping.
- `OM_WEEKLY_BUSINESS_REPORT_OUTLOOK_DRAFT_V1.yaml` — Weekly Report Outlook Draft mapping.
- `weekly_report_output_mapping_readiness_gate.yaml` — Weekly Report Output Mapping Readiness Gate.
- `OM_CUSTOMER_REVENUE_DETAIL_EXCEL_V1.yaml` — Customer Revenue Detail local Excel mapping.

## Scope rules

- Output Mapping references validated Result Contract fields explicitly and does not select Metric Variants.
- Output Assembly performs presentation assembly only; it does not calculate or apply business rules.
- `SLOT_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE` references the independent
  `POLICY_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE_DISPLAY_V1`; resolving and
  reusing that configured value is not Metric calculation and cannot feed other
  business metrics.
- Weekly Report has no file-level dependency on Customer Revenue Excel.
- Outlook remains `auto_send: false`.
- `.msg` templates, recipient identities, raw business data, and generated reports remain local-only.

## Stage status

- Current focus: Weekly Business Report Output Mapping set is complete and closed.
- Weekly Business Report Output Mapping: complete, Readiness Gate passed.
- Inventory and Revenue section mapping: included in `OM_WEEKLY_BUSINESS_REPORT_V1.yaml`.
- Outlook Draft Mapping: complete, Draft-only and `auto_send=false`.
- Customer Revenue Detail Excel Output Mapping: deferred by owner; resume after Weekly Business Report Workflow completion.
- Code Implementation remains blocked until the Owner explicitly approves the frozen Implementation Baseline ID and version.
