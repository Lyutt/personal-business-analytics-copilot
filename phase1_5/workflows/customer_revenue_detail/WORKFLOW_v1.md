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
2. The prior-year full-quarter Technical history used only for C, plus the
   separately selected exact prior-year comparable QTD snapshot used only for K/L.
3. The quarter baseline workbook referenced by
   `TEMPLATE_CUSTOMER_REVENUE_DETAIL_LATEST_LOCAL_ONLY`.
4. The validated output for the immediately previous reporting period. It is
   optional in quarter week one only when the quarter template is available;
   without that template it is required for layout, customer list and industry.

The quarter baseline and prior output are read-only local inputs ingested by the
pipeline. Output Mapping must not read a preceding workbook directly.

Every run locks `CUSTOMER_REVENUE_DETAIL_RUN_CONTEXT_V1` before data collection.
Business dates, source roles, template/D availability, mapping version and the
selected previous-period output cannot change within that run. A same-week rerun
must reuse the locked previous reporting period selection; an earlier attempt
from the same reporting period can never become M/N/Q/R history.

The Calculation Engine Result Contract is authoritative. Excel formula cells
E/G/H/I are display-and-audit mirrors only. Compare unrounded numeric values with
relative tolerance `1e-12` and absolute tolerance `1e-9`; display rounding never
enters the comparison. A mismatch outside tolerance blocks output. Formula cells
do not grant Output Mapping authority to originate or change business values.

## Confirmed business semantics

- Filter the standardized business line to Technical through
  `BR_CUSTOMER_REVENUE_TECHNICAL_ELIGIBILITY_ADAPTER_V1`. The frozen Weekly
  technical rule is unchanged and is not a Customer runtime dependency.
  Preserve negative values, reversals and exact duplicate source records; no
  additional deduplication is allowed.
- Map advertiser to the approved customer/group by exact mapping only. An
  unmatched advertiser uses the raw advertiser name as the temporary customer,
  generates a warning, and does not block. Mapping changes rerun the current
  week only.
- One customer/group produces exactly one detail row. Duplicate output rows are
  a Workflow error and block output.
- F/J are current QTD grouped performance/executed revenue. A customer retained
  in the union but absent from the validated current snapshot receives F=0 and
  J=0, never blank. K/L use the exact
  prior-year comparable archive with identical filtering, mapping and grouping.
  M/N are the preceding output F/J; Q/R are its K/L. In quarter week one,
  M/N/Q/R are blank and I equals F.
- Customer universe is the union of quarter baseline, prior output, current F/J
  and prior-year C/K/L. Retain existing quarter customers even when all values
  become zero. New customers default C=0 and O/P blank. D is zero only when the
  quarter forecast is available; while D is unavailable it remains blank.
- C archive supplementation uses a separate prior-year full-quarter history and
  must never use the K/L comparable QTD snapshot.
- C uses the quarter baseline value when populated; otherwise it is supplemented
  from the prior-year archive, with a missing customer set to zero. D/O/P are
  owner-supplied quarter values and are never inferred.
- A uses the quarter baseline industry when present. Otherwise choose the source
  industry by descending signed current performance, then executed revenue,
  then preceding-week industry, then industry name ascending; notify when the
  final fallback is needed.
- E strictly equals `IFERROR(D/C-1,"")`; Excel numeric semantics treat blank D
  as zero. G=`IF(D="","",IFERROR(F/D,""))`;
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
- A valid Top20 membership already present in the quarter template is preserved
  and frozen without reranking. Otherwise, when D is available, prior-year
  membership ranks by C descending, D descending, customer name ascending and
  forecast membership ranks by D descending, C descending, customer name
  ascending. When D is unavailable, prior-year membership ranks by C descending
  then customer name ascending, while forecast Top20 remains header-only. After
  freeze, newly eligible customers remain in Other. The locked D-availability
  state distinguishes explicit zero from blank.
- If no quarter baseline exists, use the previous output only for layout,
  customer list and A. Do not inherit prior-quarter C/D/O/P. Rebuild C from the
  archive, leave D/O/P blank, generate/freeze prior-year Top20, and retain only
  the forecast Top20 header until D becomes available. If D remains absent all
  quarter, the empty forecast Top20 is non-blocking. If both the quarter template
  and previous-period validated output are unavailable, block the Workflow.
- A quarter template is eligible only when its explicit metadata matches locked
  `current_year`, `quarter`, selected template version and passed structure
  validation. Otherwise it is unavailable; prior-quarter C/D/O/P and both Top20
  memberships must not be inherited.
- Previous-output selection uses local metadata fields `workflow_run_id`,
  `result_id`, `reporting_period_id`, `output_version`, `output_file_reference`,
  `validation_status` and `completed_at`. Period or version selection must never
  parse the filename.

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
