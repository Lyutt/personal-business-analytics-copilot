"""Fixed Stage 3E Weekly Output Assembly and review-only Preview."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from .errors import Stage3AError
from .models import PipelineExecutionResult, PipelineExecutionStatus, ResultValueStatus

OUTPUT_MAPPING_ID = "OM_WEEKLY_BUSINESS_REPORT_V1"
CUSTOMER_CONTRACT_ID = "RC_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"
PRODUCT_CONTRACTS = {
    "RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY",
    "RC_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY",
    CUSTOMER_CONTRACT_ID,
}


@dataclass(frozen=True)
class PreviewProductBinding:
    product: str
    inventory_route_type: str
    commentary_output_field_id: str


@dataclass(frozen=True)
class TemplateAnchorOccurrence:
    output_field_id: str
    anchor_reference: str
    placeholder_reference: str


@dataclass(frozen=True)
class ResolvedWeeklyTemplate:
    asset_id: str
    reference_contract_version: str
    content_fingerprint: str
    body: str
    anchor_occurrences: tuple[TemplateAnchorOccurrence, ...]


class WeeklyOutputAssembler:
    """Consume Stage 3D results through the one frozen Weekly Mapping."""

    def __init__(self, *, repository_root: Path) -> None:
        self.root = repository_root
        self.mapping = self._yaml(
            "phase1_5/assets/output_mappings/OM_WEEKLY_BUSINESS_REPORT_V1.yaml"
        )
        registry = self._yaml("phase1_5/assets/pipelines/pipeline_registry.yaml")
        if self.mapping.get("output_mapping_id") != OUTPUT_MAPPING_ID:
            raise Stage3AError(
                "STAGE3E_OUTPUT_MAPPING_INVALID", "Output Mapping is invalid"
            )
        self.requirement = {
            item["pipeline_id"]: item["workflow_bindings"][0]["required_or_optional"]
            for item in registry["pipelines"]
            if item.get("workflow_bindings")
            and item["workflow_bindings"][0].get("workflow_id")
            == "WF_WEEKLY_BUSINESS_REPORT"
        }

    def _yaml(self, relative: str) -> Mapping[str, Any]:
        value = yaml.safe_load((self.root / relative).read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise Stage3AError("STAGE3E_AUTHORITY_INVALID", f"{relative} is invalid")
        return value

    def assemble(
        self,
        *,
        context: Mapping[str, Any],
        execution_summary: Mapping[str, Any],
        configured_display_value: str,
        template: ResolvedWeeklyTemplate,
        product_bindings: tuple[PreviewProductBinding, ...],
    ) -> Mapping[str, Any]:
        run_id = str(context.get("workflow_run_id", ""))
        if not run_id or execution_summary.get("workflow_run_id") != run_id:
            raise Stage3AError(
                "STAGE3E_WORKFLOW_RUN_MISMATCH", "Summary run is not current"
            )
        report_mode = context.get("report_mode")
        expected_template = self._template_for(report_mode)
        self._validate_template(template, expected_template)
        if configured_display_value not in {"92%", "93%", "94%", "95%"}:
            raise Stage3AError(
                "STAGE3E_CONFIGURED_VALUE_UNRESOLVED", "Configured value is unresolved"
            )
        results = tuple(execution_summary.get("pipeline_run_results", ()))
        if any(not isinstance(item, PipelineExecutionResult) for item in results):
            raise Stage3AError("STAGE3E_SUMMARY_INVALID", "Summary result is invalid")
        contracts = self._contracts(run_id, results)
        bindings = self._bindings(product_bindings)
        narratives = self._narratives(contracts, bindings)
        template_body = template.body
        for occurrence in template.anchor_occurrences:
            template_body = template_body.replace(
                occurrence.placeholder_reference,
                narratives.get(occurrence.output_field_id, ""),
                1,
            )
        sections = tuple(
            self._section(section, contracts, bindings, configured_display_value)
            for section in sorted(
                self.mapping["section_order"], key=lambda x: x["display_order"]
            )
        )
        status = self._completion(results)
        warnings = self._warnings(execution_summary, results)
        return MappingProxyType(
            {
                "workflow_run_id": run_id,
                "output_mapping_id": OUTPUT_MAPPING_ID,
                "template_asset_id": template.asset_id,
                "report_mode": report_mode,
                "completion_status": status,
                "review_only": True,
                "outlook_draft_created": False,
                "configured_display_value": configured_display_value,
                "sections": sections,
                "rendered_body": self._body(template_body, sections, warnings),
                "warnings": warnings,
                "normal_omissions": tuple(
                    execution_summary.get("normal_omissions", ())
                ),
                "result_contract_references": tuple(
                    execution_summary.get("result_contract_references", ())
                ),
                "lineage": tuple(execution_summary.get("lineage", ())),
                "validation_summary": MappingProxyType(
                    {
                        "template": "passed",
                        "result_contracts": "passed",
                        "mapping": "passed",
                    }
                ),
            }
        )

    def _template_for(self, mode: object) -> str:
        refs = self.mapping["output_target"]["source_template_references"]
        if mode == "regular_week":
            return str(refs["regular_week"])
        if mode == "quarter_transition_week":
            return str(refs["quarter_transition_week_revenue"])
        raise Stage3AError("STAGE3E_REPORT_MODE_INVALID", "Report Mode is invalid")

    def _validate_template(
        self, template: ResolvedWeeklyTemplate, expected: str
    ) -> None:
        if (
            template.asset_id != expected
            or template.reference_contract_version != "1.0.0"
            or hashlib.sha256(template.body.encode()).hexdigest()
            != template.content_fingerprint
        ):
            raise Stage3AError(
                "STAGE3E_TEMPLATE_IDENTITY_INVALID", "Template identity is invalid"
            )
        required = {
            item["output_field_id"]
            for item in self.mapping["output_target"][
                "local_inventory_commentary_anchor_bindings"
            ]["anchors"]
        }
        for output_id in required:
            occurrences = [
                x for x in template.anchor_occurrences if x.output_field_id == output_id
            ]
            if (
                len(occurrences) != 1
                or not occurrences[0].anchor_reference
                or template.body.count(occurrences[0].placeholder_reference) != 1
            ):
                raise Stage3AError(
                    "STAGE3E_TEMPLATE_BINDING_INVALID",
                    f"{output_id} must bind exactly once",
                )

    @staticmethod
    def _bindings(
        items: tuple[PreviewProductBinding, ...],
    ) -> Mapping[str, PreviewProductBinding]:
        result: dict[str, PreviewProductBinding] = {}
        for item in items:
            if (
                item.product in result
                or item.inventory_route_type
                not in {"patch", "non_patch", "brand_moment"}
                or item.commentary_output_field_id
                not in {
                    "patch_and_similar_resource_commentary",
                    "page_resource_commentary",
                }
            ):
                raise Stage3AError(
                    "STAGE3E_PRODUCT_BINDING_INVALID", "Product binding is invalid"
                )
            result[item.product] = item
        return MappingProxyType(result)

    @staticmethod
    def _contracts(
        run_id: str, results: tuple[PipelineExecutionResult, ...]
    ) -> Mapping[tuple[str, str], object]:
        index: dict[tuple[str, str], object] = {}
        for result in results:
            contract = result.result_contract
            if (
                result.execution_status is PipelineExecutionStatus.BLOCKED
                or contract is None
            ):
                continue
            if (
                contract.workflow_run_id != run_id
                or contract.validation_status != "passed"
                or result.produced_result_contract_reference
                != f"result-contract://{contract.result_id}"
            ):
                raise Stage3AError(
                    "STAGE3E_RESULT_CONTRACT_NOT_CONSUMABLE",
                    "Contract is not consumable",
                )
            key = (
                contract.result_contract_id,
                getattr(contract, "product_parameter", "not_applicable"),
            )
            if key in index:
                raise Stage3AError(
                    "STAGE3E_RESULT_CONTRACT_AMBIGUOUS", "Contract is ambiguous"
                )
            index[key] = contract
        return MappingProxyType(index)

    def _section(
        self,
        section: Mapping[str, Any],
        contracts: Mapping[tuple[str, str], object],
        bindings: Mapping[str, PreviewProductBinding],
        configured: str,
    ) -> Mapping[str, Any]:
        rows = []
        for entry in sorted(
            section.get("mapping_entries", ()), key=lambda x: x["display_order"]
        ):
            if entry.get("source_type") == "Configured display value":
                rows.append(
                    self._row(entry, "not_applicable", {"configured_value": configured})
                )
                continue
            for product in self._products(entry["output_slot_id"], bindings):
                values = {}
                for output in entry.get("output_fields", ()):
                    field_binding = output["result_field_binding"]
                    contract_id = field_binding["result_contract_id"]
                    key_product = (
                        product
                        if contract_id in PRODUCT_CONTRACTS
                        else "not_applicable"
                    )
                    values[output["output_field_id"]] = self._field(
                        contracts.get((contract_id, key_product)),
                        field_binding["result_field_id"],
                        output["output_field_id"],
                    )
                rows.append(self._row(entry, product, values))
        return MappingProxyType(
            {
                "section_id": section["section_id"],
                "display_title": section["display_title"],
                "rows": tuple(rows),
            }
        )

    @staticmethod
    def _products(
        slot: str, bindings: Mapping[str, PreviewProductBinding]
    ) -> tuple[str, ...]:
        route = {
            "SLOT_INVENTORY_PATCH": "patch",
            "SLOT_INVENTORY_NON_PATCH_PRODUCTS": "non_patch",
            "SLOT_INVENTORY_BRAND_MOMENT": "brand_moment",
        }.get(slot)
        if route is None:
            return ("not_applicable",)
        return tuple(
            name
            for name, item in bindings.items()
            if item.inventory_route_type == route
        )

    @staticmethod
    def _row(
        entry: Mapping[str, Any], product: str, values: Mapping[str, str]
    ) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "output_slot_id": entry["output_slot_id"],
                "display_label": entry["display_label"],
                "product": product,
                "values": MappingProxyType(dict(values)),
            }
        )

    def _field(self, contract: object | None, field_id: str, output_id: str) -> str:
        if contract is None:
            return ""
        try:
            field = contract.field(field_id)  # type: ignore[attr-defined]
        except KeyError as exc:
            raise Stage3AError("STAGE3E_OUTPUT_FIELD_UNBOUND", field_id) from exc
        if (
            field.value_status is not ResultValueStatus.VALID_VALUE
            or field.value is None
        ):
            return ""
        return self._format(field.value, field.unit, output_id)

    @staticmethod
    def _format(value: Decimal, unit: str, output_id: str) -> str:
        if unit in {"yuan", "CNY yuan"}:
            if abs(value) >= Decimal("100000000"):
                return f"{(value / Decimal('100000000')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}亿"
            return f"{(value / Decimal('10000')).quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}万"
        if unit in {"ratio", "Decimal ratio"}:
            places = Decimal("0.1") if "dau" in output_id else Decimal("1")
            return f"{(value * 100).quantize(places, rounding=ROUND_HALF_UP)}%"
        if unit == "percentage_point":
            return f"{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):+}pp"
        if output_id == "full_site_available_inventory_cpm":
            return f"{(value / Decimal('10000')).quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}万CPM"
        return f"{int(value):,}" if value == value.to_integral_value() else str(value)

    def _narratives(
        self,
        contracts: Mapping[tuple[str, str], object],
        bindings: Mapping[str, PreviewProductBinding],
    ) -> Mapping[str, str]:
        config = next(
            section["customer_analysis_narrative_mapping"]
            for section in self.mapping["section_order"]
            if "customer_analysis_narrative_mapping" in section
        )["fixed_templates"]
        grouped = {
            "patch_and_similar_resource_commentary": [],
            "page_resource_commentary": [],
        }
        for product, binding in bindings.items():
            contract = contracts.get((CUSTOMER_CONTRACT_ID, product))
            if contract is None:
                continue
            context = contract.context_values  # type: ignore[attr-defined]
            positive = context["analysis_scenario"] == "positive_sell_through_change"
            customers = []
            for record in contract.record_set:  # type: ignore[attr-defined]
                key = (
                    "positive_customer_template"
                    if positive
                    else "negative_customer_template"
                )
                customers.append(
                    config[key].format(
                        customer_name=record["customer_name"],
                        current_period_impression_count=record[
                            "current_period_impression_count"
                        ],
                        absolute_impression_change_count=abs(
                            record["impression_change_count"]
                        ),
                    )
                )
            key = (
                (
                    "positive_product_template"
                    if positive
                    else "negative_product_template"
                )
                if customers
                else (
                    "positive_product_change_only_template"
                    if positive
                    else "negative_product_change_only_template"
                )
            )
            grouped[binding.commentary_output_field_id].append(
                config[key].format(
                    product_name=context["target_ad_product_name"],
                    absolute_change_pp=abs(
                        context["trigger_sell_through_wow_change_pp"]
                    ),
                    ranked_customer_text="、".join(customers),
                )
            )
        return MappingProxyType({key: "".join(value) for key, value in grouped.items()})

    def _completion(self, results: tuple[PipelineExecutionResult, ...]) -> str:
        completed = any(
            x.result_contract is not None
            and x.execution_status is not PipelineExecutionStatus.BLOCKED
            for x in results
        )
        if not completed:
            return "blocked"
        required_blocked = any(
            x.execution_status is PipelineExecutionStatus.BLOCKED
            and self.requirement.get(x.pipeline_id, "required").startswith("required")
            for x in results
        )
        return "partial_draft" if required_blocked else "complete_draft"

    @staticmethod
    def _warnings(
        summary: Mapping[str, Any], results: tuple[PipelineExecutionResult, ...]
    ) -> tuple[Mapping[str, str], ...]:
        warnings = [
            MappingProxyType({"code": x.code, "message": x.message})
            for x in summary.get("warnings", ())
        ]
        warnings.extend(
            MappingProxyType(
                {
                    "pipeline_id": x.pipeline_id,
                    "affected_report_scope": str(x.business_context),
                    "fallback_used": "approved_partial_draft_fallback",
                    "failure_summary": x.error_message,
                }
            )
            for x in results
            if x.execution_status is PipelineExecutionStatus.BLOCKED
        )
        return tuple(warnings)

    @staticmethod
    def _body(
        template: str,
        sections: tuple[Mapping[str, Any], ...],
        warnings: tuple[Mapping[str, str], ...],
    ) -> str:
        lines = [template]
        for section in sections:
            lines.append(str(section["display_title"]))
            for row in section["rows"]:
                product = (
                    "" if row["product"] == "not_applicable" else f"[{row['product']}]"
                )
                values = "；".join(
                    f"{key}={value}" for key, value in row["values"].items()
                )
                lines.append(f"{row['display_label']}{product}：{values}")
        if warnings:
            lines.append("数据质量提醒")
            lines.extend(str(dict(item)) for item in warnings)
        return "\n".join(lines)
