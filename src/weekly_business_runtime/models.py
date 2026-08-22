"""Small shared execution, result, and lineage models for Stage 3A."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping


class PipelineExecutionStatus(str, Enum):
    COMPLETED = "completed"
    COMPLETED_WITH_WARNING = "completed_with_warning"
    BLOCKED = "blocked"


class ResultValueStatus(str, Enum):
    VALID_VALUE = "valid_value"
    MISSING = "missing"
    CALCULATION_FAILED = "calculation_failed"
    NOT_APPLICABLE = "not_applicable"
    PENDING_CONFIRMATION = "pending_confirmation"


@dataclass(frozen=True)
class ExecutionWarning:
    code: str
    message: str


@dataclass(frozen=True)
class ResultFieldValue:
    field_id: str
    metric_variant_id: str
    value: Decimal | None
    value_status: ResultValueStatus
    unit: str
    lineage_references: tuple[str, ...]


@dataclass(frozen=True)
class CtvResultContractInstance:
    result_contract_id: str
    result_contract_version: str
    result_id: str
    workflow_run_id: str
    pipeline_run_id: str
    reporting_period: str
    workflow_reporting_date: str
    current_revenue_cutoff_date: str
    report_mode: str
    dataset_instance_ids: tuple[str, ...]
    mapping_profile_versions: Mapping[str, str]
    business_rule_versions: Mapping[str, str]
    metric_variant_versions: Mapping[str, str]
    generated_at: str
    validation_status: str
    approval_status: str
    fields: tuple[ResultFieldValue, ...]

    def field(self, field_id: str) -> ResultFieldValue:
        matches = [field for field in self.fields if field.field_id == field_id]
        if len(matches) != 1:
            raise KeyError(field_id)
        return matches[0]


@dataclass(frozen=True)
class Stage3CResultContractInstance:
    """Validated non-Revenue Result Contract produced by a Stage 3C executor."""

    result_contract_id: str
    result_contract_version: str
    result_id: str
    workflow_run_id: str
    pipeline_run_id: str
    reporting_period: str
    business_context_id: str
    dataset_instance_ids: tuple[str, ...]
    mapping_profile_versions: Mapping[str, str]
    business_rule_versions: Mapping[str, str]
    metric_variant_versions: Mapping[str, str]
    generated_at: str
    validation_status: str
    approval_status: str
    fields: tuple[ResultFieldValue, ...]
    record_set: tuple[Mapping[str, Any], ...] = ()
    product_parameter: str = "not_applicable"
    workflow_reporting_date: str = "not_applicable"
    context_values: Mapping[str, Any] = field(default_factory=dict)

    def field(self, field_id: str) -> ResultFieldValue:
        matches = [field for field in self.fields if field.field_id == field_id]
        if len(matches) != 1:
            raise KeyError(field_id)
        return matches[0]


@dataclass(frozen=True)
class PipelineExecutionResult:
    workflow_run_id: str
    pipeline_id: str
    pipeline_run_id: str
    business_context: Mapping[str, Any]
    input_binding_references: tuple[str, ...]
    execution_status: PipelineExecutionStatus
    warnings: tuple[ExecutionWarning, ...] = ()
    error_code: str = "not_applicable"
    error_message: str = "not_applicable"
    produced_result_contract_reference: str = "not_applicable"
    lineage_references: tuple[str, ...] = ()
    result_contract: CtvResultContractInstance | Stage3CResultContractInstance | None = field(
        default=None, compare=True
    )
