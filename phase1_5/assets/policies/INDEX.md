# Inventory and Advertising Policy Assets

## Authority boundary

- This directory indexes and gates existing Inventory and Advertising Policy
  semantics; it does not create new formulas, thresholds, product mappings, or
  failure behavior.
- Inventory calculation policy remains defined by the approved Inventory Metric
  Library, Pipeline Registry, Field Mappings, and versioned local External Asset
  references.
- Advertising customer-analysis policy remains defined by
  `POLICY_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS_V1` and its approved
  upstream assets.
- The configured display value for `订单整体曝光完成率` is governed independently
  by `POLICY_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE_DISPLAY_V1`. It is an
  Owner-approved display configuration, not a Metric, Dataset result, Result
  Contract, or Metric Result Store value.
- Actual product rules, mapping rows, customer data, templates, recipients,
  local paths, resolved local versions, and fingerprints remain local-only.

## Gate

- `GATE_INVENTORY_ADVERTISING_POLICY_WF_WEEKLY_BUSINESS_REPORT_V1`
- Gate asset: `inventory_advertising_policy_readiness_gate.yaml`
- Configured Display Value Policy asset:
  `POLICY_ORDER_OVERALL_IMPRESSION_COMPLETION_RATE_DISPLAY_V1.yaml`
- The Gate confirms policy completeness only. It does not authorize Code
  Implementation, runtime activation, or a change to Owner approval status.
