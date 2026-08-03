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
- Actual product rules, mapping rows, customer data, templates, recipients,
  local paths, resolved local versions, and fingerprints remain local-only.

## Gate

- `GATE_INVENTORY_ADVERTISING_POLICY_WF_WEEKLY_BUSINESS_REPORT_V1`
- Gate asset: `inventory_advertising_policy_readiness_gate.yaml`
- The Gate confirms policy completeness only. It does not authorize Code
  Implementation, runtime activation, or a change to Owner approval status.
