"""Direct implementation of the three frozen CTV Metric Variants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Mapping

import pandas as pd

from .assets import CTV_VARIANT_IDS
from .errors import Stage3AError
from .models import ExecutionWarning, ResultValueStatus


@dataclass(frozen=True)
class RevenueExecutionContext:
    workflow_reporting_date: date
    expected_previous_revenue_workflow_reporting_date: date
    current_revenue_cutoff_date: date
    target_report_period: str
    workflow_year: int
    target_fiscal_quarter: str
    target_previous_calendar_quarter: str
    report_mode: str


@dataclass(frozen=True)
class CtvMetricValue:
    metric_variant_id: str
    value: Decimal | None
    value_status: ResultValueStatus
    unit: str


@dataclass(frozen=True)
class CtvMetricCalculation:
    values: Mapping[str, CtvMetricValue]
    warnings: tuple[ExecutionWarning, ...]
    target_prior_year_date: date


def _quarter(value: date) -> str:
    return f"{value.year}Q{((value.month - 1) // 3) + 1}"


def _quarter_start(value: date) -> date:
    month = ((value.month - 1) // 3) * 3 + 1
    return date(value.year, month, 1)


def _inclusive_days(start: date, end: date) -> int:
    if end < start:
        raise Stage3AError("CTV_PERIOD_INVALID", "Revenue cutoff precedes quarter start")
    return (end - start).days + 1


def derive_prior_year_date(context: RevenueExecutionContext) -> date:
    if context.workflow_year != 2026:
        raise Stage3AError(
            "CTV_PRIOR_YEAR_RULE_RECONFIRMATION_REQUIRED",
            "The frozen prior-year matching rule requires Owner reconfirmation after 2026",
        )
    try:
        prior_year_base = context.current_revenue_cutoff_date.replace(
            year=context.current_revenue_cutoff_date.year - 1
        )
    except ValueError as exc:
        raise Stage3AError(
            "CTV_PRIOR_YEAR_DATE_INVALID", "Cannot derive prior-year comparable date"
        ) from exc
    target_prior_date = prior_year_base + timedelta(days=1)
    if _quarter(target_prior_date) != f"{context.workflow_year - 1}{context.target_fiscal_quarter[4:]}":
        raise Stage3AError(
            "CTV_PRIOR_YEAR_QUARTER_MISMATCH", "Prior-year comparable date changed quarter"
        )
    return target_prior_date


def validate_revenue_context(values: Mapping[str, object]) -> RevenueExecutionContext:
    required = (
        "workflow_reporting_date",
        "expected_previous_revenue_workflow_reporting_date",
        "current_revenue_cutoff_date",
        "target_report_period",
        "workflow_year",
        "target_fiscal_quarter",
        "target_previous_calendar_quarter",
        "report_mode",
        "target_revenue_cutoff_date",
        "reporting_period_id",
    )
    missing = [name for name in required if name not in values]
    if missing:
        raise Stage3AError(
            "CTV_REVENUE_CONTEXT_MISSING", f"Revenue Run Context fields are missing: {missing}"
        )
    try:
        reporting_date = date.fromisoformat(str(values["workflow_reporting_date"]))
        previous_reporting_date = date.fromisoformat(
            str(values["expected_previous_revenue_workflow_reporting_date"])
        )
        cutoff = date.fromisoformat(str(values["current_revenue_cutoff_date"]))
    except ValueError as exc:
        raise Stage3AError("CTV_REVENUE_CONTEXT_DATE_INVALID", "Revenue dates must be valid") from exc
    if previous_reporting_date != reporting_date - timedelta(days=7):
        raise Stage3AError(
            "CTV_PREVIOUS_REPORTING_DATE_MISMATCH",
            "Expected previous Revenue reporting date must be exactly seven days earlier",
        )
    report_mode = str(values["report_mode"])
    expected_mode = (
        "regular_week"
        if _quarter(reporting_date) == _quarter(previous_reporting_date)
        else "quarter_transition_week"
    )
    if report_mode != expected_mode:
        raise Stage3AError(
            "CTV_REPORT_MODE_MISMATCH", "report_mode does not match the frozen calendar rule"
        )
    target_quarter = str(values["target_fiscal_quarter"])
    if target_quarter != _quarter(cutoff):
        raise Stage3AError(
            "CTV_TARGET_QUARTER_MISMATCH", "Revenue cutoff is outside target_fiscal_quarter"
        )
    if int(values["workflow_year"]) != int(target_quarter[:4]):
        raise Stage3AError("CTV_WORKFLOW_YEAR_MISMATCH", "workflow_year mismatch")
    if values["target_report_period"] != values["reporting_period_id"]:
        raise Stage3AError("CTV_REPORT_PERIOD_MISMATCH", "target_report_period mismatch")
    if str(values["target_revenue_cutoff_date"]) != cutoff.isoformat():
        raise Stage3AError(
            "CTV_REVENUE_CUTOFF_ALIAS_MISMATCH",
            "target_revenue_cutoff_date must exactly equal current_revenue_cutoff_date",
        )
    return RevenueExecutionContext(
        reporting_date,
        previous_reporting_date,
        cutoff,
        str(values["target_report_period"]),
        int(values["workflow_year"]),
        target_quarter,
        str(values["target_previous_calendar_quarter"]),
        report_mode,
    )


def calculate_ctv_metrics(
    frame: pd.DataFrame,
    *,
    prior_year_performance: Decimal,
    context: RevenueExecutionContext,
) -> CtvMetricCalculation:
    """Execute only the three formulas registered for PL_REVENUE_CTV_WEEKLY."""

    performance = sum(frame["qtd_ctv_signed_amount"], Decimal("0"))
    executed = Decimal("0")
    for row in frame.itertuples(index=False):
        denominator = row.qtd_signed_amount
        if denominator == 0:
            continue
        executed += (
            row.qtd_executed_revenue_amount / denominator * row.qtd_ctv_signed_amount
        )

    target_prior_date = derive_prior_year_date(context)
    current_days = Decimal(
        _inclusive_days(_quarter_start(context.current_revenue_cutoff_date), context.current_revenue_cutoff_date)
    )
    prior_days = Decimal(_inclusive_days(_quarter_start(target_prior_date), target_prior_date))
    warnings: list[ExecutionWarning] = []
    if prior_year_performance <= 0:
        yoy: Decimal | None = None
        yoy_status = ResultValueStatus.MISSING
        warnings.append(
            ExecutionWarning(
                "CTV_PRIOR_YEAR_RESULT_INVALID",
                "Prior-year comparable result is unavailable; YoY remains missing",
            )
        )
    else:
        yoy = (performance / current_days) / (prior_year_performance / prior_days) - Decimal("1")
        yoy_status = ResultValueStatus.VALID_VALUE
    if performance <= 0:
        warnings.append(
            ExecutionWarning(
                "CTV_QTD_PERFORMANCE_NON_POSITIVE",
                "CTV QTD Performance is zero or negative and requires final-output notice",
            )
        )
    if executed <= 0:
        warnings.append(
            ExecutionWarning(
                "CTV_QTD_EXECUTED_NON_POSITIVE",
                "CTV QTD Executed Revenue is zero or negative and requires final-output notice",
            )
        )
    values = {
        CTV_VARIANT_IDS[0]: CtvMetricValue(
            CTV_VARIANT_IDS[0], performance, ResultValueStatus.VALID_VALUE, "CNY_yuan"
        ),
        CTV_VARIANT_IDS[1]: CtvMetricValue(
            CTV_VARIANT_IDS[1], yoy, yoy_status, "decimal_ratio"
        ),
        CTV_VARIANT_IDS[2]: CtvMetricValue(
            CTV_VARIANT_IDS[2], executed, ResultValueStatus.VALID_VALUE, "CNY_yuan"
        ),
    }
    return CtvMetricCalculation(values, tuple(warnings), target_prior_date)
