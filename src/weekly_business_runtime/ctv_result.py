"""Assemble and fail-closed validate RC_REVENUE_CTV_WEEKLY instances."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Mapping

from .assets import RESULT_CONTRACT_ID, CtvAssetBundle
from .ctv_metrics import CtvMetricCalculation, RevenueExecutionContext
from .errors import ResultContractError
from .models import CtvResultContractInstance, ResultFieldValue


class CtvResultContractAssembler:
    def __init__(self, assets: CtvAssetBundle) -> None:
        self.assets = assets

    def assemble(
        self,
        *,
        workflow_run_id: str,
        pipeline_run_id: str,
        context: RevenueExecutionContext,
        dataset_instance_ids: tuple[str, ...],
        consumed_mapping_profile_ids: tuple[str, ...],
        evaluated_rule_ids: tuple[str, ...],
        field_lineage_references: tuple[str, ...],
        calculation: CtvMetricCalculation,
        generated_at: str,
    ) -> CtvResultContractInstance:
        try:
            parsed = datetime.fromisoformat(generated_at)
        except ValueError as exc:
            raise ResultContractError(
                "CTV_RESULT_GENERATED_AT_INVALID", "generated_at must be ISO-8601"
            ) from exc
        if parsed.tzinfo is None:
            raise ResultContractError(
                "CTV_RESULT_GENERATED_AT_INVALID", "generated_at requires timezone"
            )
        contract = self.assets.result_contract
        fields: list[ResultFieldValue] = []
        for field_contract in contract["contract_fields"]:
            variant_id = field_contract["source_metric_variant_id"]
            metric = calculation.values[variant_id]
            fields.append(
                ResultFieldValue(
                    field_id=field_contract["field_id"],
                    metric_variant_id=variant_id,
                    value=metric.value,
                    value_status=metric.value_status,
                    unit=field_contract["numeric_constraints"]["unit"],
                    lineage_references=field_lineage_references,
                )
            )
        result_id = self._result_id(
            workflow_run_id,
            pipeline_run_id,
            context.target_report_period,
            tuple(fields),
        )
        rule_versions = {
            rule_id: str(self.assets.business_rules[rule_id]["version"])
            for rule_id in evaluated_rule_ids
        }
        variant_versions = {
            variant_id: str(self.assets.metric_variants[variant_id]["version"])
            for variant_id in calculation.values
        }
        instance = CtvResultContractInstance(
            result_contract_id=RESULT_CONTRACT_ID,
            result_contract_version=str(contract["contract_version"]),
            result_id=result_id,
            workflow_run_id=workflow_run_id,
            pipeline_run_id=pipeline_run_id,
            reporting_period=context.target_report_period,
            workflow_reporting_date=context.workflow_reporting_date.isoformat(),
            current_revenue_cutoff_date=context.current_revenue_cutoff_date.isoformat(),
            report_mode=context.report_mode,
            dataset_instance_ids=dataset_instance_ids,
            mapping_profile_versions={
                mapping_id: str(self._mapping_by_id(mapping_id)["version"])
                for mapping_id in consumed_mapping_profile_ids
            },
            business_rule_versions=rule_versions,
            metric_variant_versions=variant_versions,
            generated_at=generated_at,
            validation_status="passed",
            approval_status="not_applicable",
            fields=tuple(fields),
        )
        self.validate(instance)
        return instance

    def _mapping_by_id(self, mapping_id: str) -> Mapping[str, object]:
        mappings = (
            self.assets.current_mapping,
            self.assets.prior_mapping,
            self.assets.previous_quarter_fallback_mapping,
        )
        matches = [item for item in mappings if item.get("mapping_profile_id") == mapping_id]
        if len(matches) != 1:
            raise ResultContractError(
                "CTV_RESULT_MAPPING_LINEAGE_INVALID",
                f"Mapping Profile {mapping_id} is not an exact loaded authority",
            )
        return matches[0]

    def validate(self, instance: CtvResultContractInstance) -> None:
        contract = self.assets.result_contract
        if instance.result_contract_id != contract["result_contract_id"]:
            raise ResultContractError("CTV_RESULT_CONTRACT_MISMATCH", "Result Contract ID mismatch")
        if instance.result_contract_version != str(contract["contract_version"]):
            raise ResultContractError(
                "CTV_RESULT_CONTRACT_MISMATCH", "Result Contract version mismatch"
            )
        if instance.validation_status != "passed":
            raise ResultContractError(
                "CTV_RESULT_NOT_VALIDATED", "Result validation_status must be passed"
            )
        field_contracts = {field["field_id"]: field for field in contract["contract_fields"]}
        fields = {field.field_id: field for field in instance.fields}
        if len(fields) != len(instance.fields) or set(fields) != set(field_contracts):
            raise ResultContractError(
                "CTV_RESULT_FIELD_SET_MISMATCH", "Result field set must exactly match the contract"
            )
        for field_id, value in fields.items():
            expected = field_contracts[field_id]
            if value.metric_variant_id != expected["source_metric_variant_id"]:
                raise ResultContractError(
                    "CTV_RESULT_METRIC_BINDING_MISMATCH", f"{field_id} Metric Variant mismatch"
                )
            if value.value_status.value not in expected["value_status_allowed"]:
                raise ResultContractError(
                    "CTV_RESULT_VALUE_STATUS_INVALID", f"{field_id} value_status is not allowed"
                )
            if value.value is None and not expected["nullable"]:
                raise ResultContractError(
                    "CTV_RESULT_NULL_INVALID", f"{field_id} is not nullable"
                )
            if value.value is not None and not isinstance(value.value, Decimal):
                raise ResultContractError(
                    "CTV_RESULT_TYPE_INVALID", f"{field_id} must contain a Decimal"
                )
            if value.unit != expected["numeric_constraints"]["unit"]:
                raise ResultContractError(
                    "CTV_RESULT_UNIT_MISMATCH", f"{field_id} unit mismatch"
                )
            if not value.lineage_references:
                raise ResultContractError(
                    "CTV_RESULT_LINEAGE_MISSING", f"{field_id} requires lineage"
                )
        required_lineage = set(contract["lineage"]["required_instance_fields"])
        available_lineage = {
            "result_id",
            "workflow_run_id",
            "pipeline_run_id",
            "reporting_period",
            "workflow_reporting_date",
            "current_revenue_cutoff_date",
            "report_mode",
            "dataset_instance_ids",
            "mapping_profile_versions",
            "business_rule_versions",
            "metric_variant_versions",
            "generated_at",
            "validation_status",
            "approval_status",
        }
        if not required_lineage.issubset(available_lineage):
            raise ResultContractError(
                "CTV_RESULT_LINEAGE_SCHEMA_MISMATCH", "Required lineage fields are not implemented"
            )
        for name in (
            "result_id",
            "workflow_run_id",
            "pipeline_run_id",
            "reporting_period",
            "workflow_reporting_date",
            "current_revenue_cutoff_date",
            "report_mode",
            "generated_at",
            "approval_status",
        ):
            if not str(getattr(instance, name)).strip():
                raise ResultContractError(
                    "CTV_RESULT_LINEAGE_VALUE_MISSING", f"{name} is required"
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
                    "metric_variant_id": field.metric_variant_id,
                    "value": None if field.value is None else str(field.value),
                    "value_status": field.value_status.value,
                    "unit": field.unit,
                }
                for field in fields
            ],
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return f"RESULT_CTV_{digest[:24]}"
