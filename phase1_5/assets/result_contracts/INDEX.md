# Result Contract Assets

## Current status

- Workflow: `WF_WEEKLY_BUSINESS_REPORT`
- Result Contract count: 12
- Producer Pipeline count: 12
- Metric Variant output bindings: 48
- Result Contract field definitions: 67, including record-set context and record fields
- Readiness Gate: `GATE_RESULT_CONTRACT_WF_WEEKLY_BUSINESS_REPORT_V1`
- Outlook remains Draft-only with `auto_send=false`
- This asset layer does not authorize code implementation or change Owner approval status.
- Independent Customer Revenue Detail scope: 1 Contract, 3 record sets, 14 Metric Variant bindings; governed by its own Readiness Gate and Baseline.

## Contracts

| Result Contract | Producer Pipeline | Shape |
|---|---|---|
| `RC_REVENUE_TECHNICAL_WEEKLY` | `PL_REVENUE_TECHNICAL_WEEKLY` | single record |
| `RC_REVENUE_CTV_WEEKLY` | `PL_REVENUE_CTV_WEEKLY` | single record |
| `RC_REVENUE_SMART_SPEAKER_WEEKLY` | `PL_REVENUE_SMART_SPEAKER_WEEKLY` | single record |
| `RC_REVENUE_FAST_VERSION_WEEKLY` | `PL_REVENUE_FAST_VERSION_WEEKLY` | single record |
| `RC_INVENTORY_FULL_SITE_WEEKLY` | `PL_INVENTORY_FULL_SITE_WEEKLY` | single record |
| `RC_INVENTORY_PATCH_WEEKLY` | `PL_INVENTORY_PATCH_WEEKLY` | single record |
| `RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY` | `PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY` | parameterized single record |
| `RC_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY` | `PL_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY` | single record |
| `RC_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY` | `PL_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY` | single record |
| `RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY` | `PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY` | parameterized single record |
| `RC_USER_ANALYTICS_PLATFORM_DAU_WEEKLY` | `PL_USER_ANALYTICS_PLATFORM_DAU_WEEKLY` | composite with daily record set |
| `RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS` | `PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS` | analysis-only record set |
| `RC_CUSTOMER_REVENUE_DETAIL_WEEKLY` | `PL_CUSTOMER_REVENUE_DETAIL_WEEKLY` | three local-only record sets |

## Contract rules

- Every Metric Variant has one explicit `output_binding` to one Contract Field.
- Metric Library `metric_variants[].input_contract_fields` is the unique
  authority for field-level Input Contract Dependencies. Result Contracts keep
  only a validated derived lineage summary, and Pipeline Registry declarations
  are orchestration routing only.
- Every `record_grain` item must resolve to a field or an explicitly declared
  dimension in the same Contract or record set.
- Numeric fields use `data_type: number` plus explicit numeric semantics, unit,
  integer-only behavior, precision and bounds.
- DAU daily detail uses `activity_date x full_platform` records and keeps
  `dau_count` as a single numeric field.
- Customer analysis is analysis-only and may source fields from standardized
  fields, upstream Contract Fields and approved policy derivations without a
  fabricated Metric Variant.
- Customer-analysis upstream Contract Field routes are mutually explicit by
  regular patch, other non-patch, and Brand Moment route conditions.
- Output Mapping consumes explicit Contract Fields and does not select Metric Variants.
