"""Frozen Technical Revenue Metric Variant execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping

from .ctv_metrics import RevenueExecutionContext, derive_prior_year_date
from .errors import Stage3AError
from .models import ExecutionWarning, ResultValueStatus
from .technical_assets import VARIANT_IDS
from .technical_dataset import LoadedTechnicalDataset


@dataclass(frozen=True)
class TechnicalMetricValue:
    metric_variant_id: str
    value: Decimal | None
    value_status: ResultValueStatus
    unit: str


@dataclass(frozen=True)
class TechnicalMetricCalculation:
    values: Mapping[str, TechnicalMetricValue]
    warnings: tuple[ExecutionWarning, ...]
    target_prior_year_date: date


def _quarter_start(value: date) -> date:
    return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)


def _days_through(value: date) -> Decimal:
    return Decimal((value - _quarter_start(value)).days + 1)


def _ratio(
    numerator: Decimal,
    denominator: Decimal | None,
    *,
    warning_code: str,
    warning_message: str,
    warnings: list[ExecutionWarning],
) -> tuple[Decimal | None, ResultValueStatus]:
    if denominator is None or denominator <= 0:
        warnings.append(ExecutionWarning(warning_code, warning_message))
        return None, ResultValueStatus.MISSING
    return numerator / denominator - Decimal("1"), ResultValueStatus.VALID_VALUE


def calculate_technical_metrics(
    current: LoadedTechnicalDataset,
    prior_year: LoadedTechnicalDataset | None,
    *,
    previous_qtd_executed: Decimal | None,
    previous_weekly_incremental: Decimal | None,
    prior_year_weekly_incremental: Decimal | None,
    context: RevenueExecutionContext,
) -> TechnicalMetricCalculation:
    """Calculate only the six registered Technical Revenue variants."""

    target_prior_date = derive_prior_year_date(context)
    warnings: list[ExecutionWarning] = []
    performance = current.performance
    executed = current.executed
    if prior_year is None:
        performance_yoy = None
        performance_yoy_status = ResultValueStatus.MISSING
        warnings.append(
            ExecutionWarning(
                "TECHNICAL_PRIOR_YEAR_QTD_UNAVAILABLE",
                "Prior-year QTD input is unavailable; QTD Performance YoY remains missing",
            )
        )
    else:
        performance_yoy = (
            (performance / _days_through(context.current_revenue_cutoff_date))
            / (prior_year.performance / _days_through(target_prior_date))
            - Decimal("1")
        )
        performance_yoy_status = ResultValueStatus.VALID_VALUE
    if performance <= 0:
        warnings.append(
            ExecutionWarning(
                "TECHNICAL_QTD_PERFORMANCE_NON_POSITIVE",
                "Technical QTD Performance is zero or negative and requires final-output notice",
            )
        )
    if executed <= 0:
        warnings.append(
            ExecutionWarning(
                "TECHNICAL_QTD_EXECUTED_NON_POSITIVE",
                "Technical QTD Executed Revenue is zero or negative and requires final-output notice",
            )
        )

    if context.report_mode == "quarter_transition_week":
        weekly = wow = weekly_yoy = None
        weekly_status = wow_status = weekly_yoy_status = ResultValueStatus.NOT_APPLICABLE
    else:
        if previous_qtd_executed is None:
            raise Stage3AError(
                "TECHNICAL_PREVIOUS_QTD_EXECUTED_REQUIRED",
                "Regular-week Technical execution requires exact previous-week QTD Executed Revenue",
            )
        weekly = executed - previous_qtd_executed
        if weekly < 0:
            raise Stage3AError(
                "TECHNICAL_WEEKLY_INCREMENTAL_NEGATIVE",
                "Current QTD Executed Revenue minus previous-week QTD Executed Revenue is negative",
            )
        weekly_status = ResultValueStatus.VALID_VALUE
        wow, wow_status = _ratio(
            weekly,
            previous_weekly_incremental,
            warning_code="TECHNICAL_PREVIOUS_WEEK_INCREMENTAL_INVALID",
            warning_message="Previous-week incremental result is unavailable or non-positive; WoW remains missing",
            warnings=warnings,
        )
        weekly_yoy, weekly_yoy_status = _ratio(
            weekly,
            prior_year_weekly_incremental,
            warning_code="TECHNICAL_PRIOR_YEAR_INCREMENTAL_INVALID",
            warning_message="Prior-year comparable incremental result is unavailable or non-positive; YoY remains missing",
            warnings=warnings,
        )

    values = {
        VARIANT_IDS[0]: TechnicalMetricValue(
            VARIANT_IDS[0], performance, ResultValueStatus.VALID_VALUE, "CNY_yuan"
        ),
        VARIANT_IDS[1]: TechnicalMetricValue(
            VARIANT_IDS[1], performance_yoy, performance_yoy_status, "decimal_ratio"
        ),
        VARIANT_IDS[2]: TechnicalMetricValue(
            VARIANT_IDS[2], executed, ResultValueStatus.VALID_VALUE, "CNY_yuan"
        ),
        VARIANT_IDS[3]: TechnicalMetricValue(
            VARIANT_IDS[3], weekly, weekly_status, "CNY_yuan"
        ),
        VARIANT_IDS[4]: TechnicalMetricValue(
            VARIANT_IDS[4], wow, wow_status, "decimal_ratio"
        ),
        VARIANT_IDS[5]: TechnicalMetricValue(
            VARIANT_IDS[5], weekly_yoy, weekly_yoy_status, "decimal_ratio"
        ),
    }
    return TechnicalMetricCalculation(values, tuple(warnings), target_prior_date)
