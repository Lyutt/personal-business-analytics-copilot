"""Assemble and validate RC_REVENUE_TECHNICAL_WEEKLY instances."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Mapping

from .ctv_metrics import RevenueExecutionContext
from .errors import ResultContractError
from .models import CtvResultContractInstance, ResultFieldValue, ResultValueStatus
from .technical_assets import RESULT_CONTRACT_ID, TechnicalAssetBundle
from .technical_metrics import TechnicalMetricCalculation


class TechnicalResultContractAssembler:
    def __init__(self, assets: TechnicalAssetBundle) -> None:
        self.assets = assets

    def assemble(
        self,
        *,
        workflow_run_id: str,
        pipeline_run_id: str,
        context: RevenueExecutionContext,
        dataset_instance_ids: tuple[str, ...],
        evaluated_rule_ids: tuple[str, ...],
        field_lineage_references: tuple[str, ...],
        calculation: TechnicalMetricCalculation,
        generated_at: str,
    ) -> CtvResultContractInstance:
        try:
            parsed = datetime.fromisoformat(generated_at)
        except ValueError as exc:
            raise ResultContractError(
                "TECHNICAL_RESULT_GENERATED_AT_INVALID", "generated_at must be ISO-8601"
            ) from exc
        if parsed.tzinfo is None:
            raise ResultContractError(
                "TECHNICAL_RESULT_GENERATED_AT_INVALID", "generated_at requires timezone"
            )
        fields = tuple(
            ResultFieldValue(
                field_id=field["field_id"],
                metric_variant_id=field["source_metric_variant_id"],
                value=calculation.values[field["source_metric_variant_id"]].value,
                value_status=calculation.values[field["source_metric_variant_id"]].value_status,
                unit=field["numeric_constraints"]["unit"],
                lineage_references=field_lineage_references,
            )
            for field in self.assets.result_contract["contract_fields"]
        )
        result_id = self._result_id(
            workflow_run_id, pipeline_run_id, context.target_report_period, fields
        )
        instance = CtvResultContractInstance(
            result_contract_id=RESULT_CONTRACT_ID,
            result_contract_version=str(self.assets.result_contract["contract_version"]),
            result_id=result_id,
            workflow_run_id=workflow_run_id,
            pipeline_run_id=pipeline_run_id,
            reporting_period=context.target_report_period,
            workflow_reporting_date=context.workflow_reporting_date.isoformat(),
            current_revenue_cutoff_date=context.current_revenue_cutoff_date.isoformat(),
            report_mode=context.report_mode,
            dataset_instance_ids=dataset_instance_ids,
            mapping_profile_versions={
                str(self.assets.mapping["mapping_profile_id"]): str(
                    self.assets.mapping["version"]
                )
            },
            business_rule_versions={
                rule_id: str(self.assets.business_rules[rule_id]["version"])
                for rule_id in evaluated_rule_ids
            },
            metric_variant_versions={
                variant_id: str(self.assets.metric_variants[variant_id]["version"])
                for variant_id in calculation.values
            },
            generated_at=generated_at,
            validation_status="passed",
            approval_status="not_applicable",
            fields=fields,
        )
        self.validate(instance)
        return instance

    def validate(self, instance: CtvResultContractInstance) -> None:
        contract = self.assets.result_contract
        if (
            instance.result_contract_id != contract["result_contract_id"]
            or instance.result_contract_version != str(contract["contract_version"])
            or instance.validation_status != "passed"
        ):
            raise ResultContractError(
                "TECHNICAL_RESULT_CONTRACT_MISMATCH", "Result Contract identity/status mismatch"
            )
        contracts = {field["field_id"]: field for field in contract["contract_fields"]}
        fields = {field.field_id: field for field in instance.fields}
        if len(fields) != len(instance.fields) or set(fields) != set(contracts):
            raise ResultContractError(
                "TECHNICAL_RESULT_FIELD_SET_MISMATCH", "Result field set must exactly match the contract"
            )
        for field_id, value in fields.items():
            expected = contracts[field_id]
            applicable = instance.report_mode in expected["applicable_report_modes"]
            if value.metric_variant_id != expected["source_metric_variant_id"]:
                raise ResultContractError(
                    "TECHNICAL_RESULT_METRIC_BINDING_MISMATCH", f"{field_id} Metric Variant mismatch"
                )
            if value.value_status.value not in expected["value_status_allowed"]:
                raise ResultContractError(
                    "TECHNICAL_RESULT_VALUE_STATUS_INVALID", f"{field_id} value_status is not allowed"
                )
            if not applicable and (
                value.value is not None
                or value.value_status is not ResultValueStatus.NOT_APPLICABLE
            ):
                raise ResultContractError(
                    "TECHNICAL_RESULT_APPLICABILITY_INVALID", f"{field_id} must be blank/not_applicable"
                )
            if applicable and value.value is None and not expected["nullable"]:
                raise ResultContractError(
                    "TECHNICAL_RESULT_NULL_INVALID", f"{field_id} is not nullable"
                )
            if value.value is not None and not isinstance(value.value, Decimal):
                raise ResultContractError(
                    "TECHNICAL_RESULT_TYPE_INVALID", f"{field_id} must contain a Decimal"
                )
            if value.unit != expected["numeric_constraints"]["unit"]:
                raise ResultContractError(
                    "TECHNICAL_RESULT_UNIT_MISMATCH", f"{field_id} unit mismatch"
                )
            if not value.lineage_references:
                raise ResultContractError(
                    "TECHNICAL_RESULT_LINEAGE_MISSING", f"{field_id} requires lineage"
                )

    @staticmethod
    def _result_id(
        workflow_run_id: str,
        pipeline_run_id: str,
        reporting_period: str,
        fields: tuple[ResultFieldValue, ...],
    ) -> str:
        payload: Mapping[str, object] = {
            "workflow_run_id": workflow_run_id,
            "pipeline_run_id": pipeline_run_id,
            "reporting_period": reporting_period,
            "fields": [
                {
                    "field_id": field.field_id,
                    "value": None if field.value is None else str(field.value),
                    "value_status": field.value_status.value,
                }
                for field in fields
            ],
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"RESULT_TECHNICAL_{digest[:24]}"
