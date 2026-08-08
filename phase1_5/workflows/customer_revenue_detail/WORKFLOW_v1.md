# WF_CUSTOMER_REVENUE_DETAIL — Phase 1.5 design contract

Status: design assets complete; code implementation not authorized.

## Boundary

`WF_CUSTOMER_REVENUE_DETAIL` is independent from `WF_WEEKLY_BUSINESS_REPORT`.
Its V1 output is one local-only XLSX workbook and it neither attaches the file
to the Weekly report nor publishes a shared Metric Store or cross-Workflow
Result Contract. A failure blocks only this Workflow.

The Workflow reuses the existing Outlook revenue source, Rolling Deck dataset,
business-line field mapping, technical-line eligibility rule, prior-year exact
source-selection rule, and advertiser ownership mapping. Raw emails, original
attachments, customer rows, mapping contents, generated workbooks and resolved
local paths remain outside Git.

## Trigger and parameters

- Scheduled trigger: Thursday 17:40, `Asia/Shanghai`.
- If the current revenue email is absent, notify once, then recheck every 30
  minutes without a fixed retry limit. Resume automatically when it arrives.
- Manual runs require `WorkflowExecutionDate`, `CurrentRevenueCutoffDate`,
  `CurrentYear`, and `Quarter`; missing values block the run.
- For a 2026 run, the prior-year comparable cutoff is the current cutoff shifted
  back one year and then forward one day. Any 2027-or-later run blocks until the
  owner reconfirms this date relationship.

## Inputs and authority

The pipeline reads four logical inputs before Output Mapping:

1. Current QTD revenue from `DS_REVENUE_SALES_ROLLING_DECK_QTD`.
2. The exact prior-year comparable Rolling Deck archive instance.
3. The quarter baseline workbook referenced by
   `TEMPLATE_CUSTOMER_REVENUE_DETAIL_LATEST_LOCAL_ONLY`.
4. The immediately preceding approved output of this Workflow, except in the
   first week of a quarter.

The quarter baseline and prior output are read-only local inputs ingested by the
pipeline. Output Mapping must not read a preceding workbook directly.

The Calculation Engine Result Contract is authoritative. Excel formula cells
E/G/H/I are display-and-audit mirrors only; a formula result mismatch blocks
output. Formula cells do not grant Output Mapping authority to originate or
change business values.

## Confirmed business semantics

- Filter the standardized business line to Technical and apply the existing
  technical single-count eligibility rule. Preserve negative values, reversals
  and exact duplicate source records; no additional deduplication is allowed.
- Map advertiser to the approved customer/group by exact mapping only. An
  unmatched advertiser uses the raw advertiser name as the temporary customer,
  generates a warning, and does not block. Mapping changes rerun the current
  week only.
- One customer/group produces exactly one detail row. Duplicate output rows are
  a Workflow error and block output.
- F/J are current QTD grouped performance/executed revenue. K/L use the exact
  prior-year comparable archive with identical filtering, mapping and grouping.
  M/N are the preceding output F/J; Q/R are its K/L. In quarter week one,
  M/N/Q/R are blank and I equals F.
- Customer universe is the union of quarter baseline, prior output, current F/J
  and prior-year C/K/L. Retain existing quarter customers even when all values
  become zero. New customers default C=0, D=0, O/P blank.
- C uses the quarter baseline value when populated; otherwise it is supplemented
  from the prior-year archive, with a missing customer set to zero. D/O/P are
  owner-supplied quarter values and are never inferred.
- A uses the quarter baseline industry when present. Otherwise choose the source
  industry by descending signed current performance, then executed revenue,
  then preceding-week industry, then industry name ascending; notify when the
  final fallback is needed.
- E=`IFERROR(D/C-1,"")`; G=`IFERROR(F/D,"")`;
  H=`IFERROR(F/K-1,"")`; I=`F-M`, except quarter week one where I=F.
  Calculation uses full precision. Money displays as integer with thousands
  separators and rates as integer percentages.
- Sort detail rows by C descending. Preserve prior order for ties; for new tied
  rows use D descending, F descending, then customer name ascending. New rows
  with blank/zero C stay at the bottom.
- Prior-year and forecast Top20 memberships are independent and frozen for the
  quarter. Each has exactly 20 members with no tie expansion; all remaining
  customers, including zero and negative values, aggregate into `Other`.
  Ratios are recomputed from aggregate amounts. Top20 sheets contain validated
  static A:G values only.
- If no quarter baseline exists, use the previous output only for layout,
  customer list and A. Do not inherit prior-quarter C/D/O/P. Rebuild C from the
  archive, leave D/O/P blank, generate/freeze prior-year Top20, and retain only
  the forecast Top20 header until D becomes available. If D remains absent all
  quarter, the empty forecast Top20 is non-blocking.

## Validation, warnings and output

Reconcile F, J, K and L separately to their filtered technical-line source totals
with absolute tolerance of CNY 1. Reconcile C as well whenever the archive
supplements it. Missing, unreadable or ambiguous required inputs; duplicate
customer rows; reconciliation failure; invalid required template structure; or
formula-mirror mismatch blocks output and prohibits a partial file.

Unmatched advertisers, negative money, negative I and absolute rates of at least
100% are warnings only. An unmatched-advertiser notification contains only raw
advertiser name, temporary customer, grouped F/J amounts and mapping version;
raw order rows must not be persisted in repository assets or run logs.

Output filename is `{YY}年Q{Quarter}硬广预算盘点-收入截止{YYYYMMDD}.xlsx`.
An existing filename is never overwritten: create the next `v2`, `v3`, ...
suffix and notify the owner. Clear inherited red manual cell fills while
preserving the normal template style, sheet order, filters, formulas and dynamic
row formatting. Notifications are limited to the Codex task result and a
local-only exception/run record; no email, Outlook Draft or external message is
created.
