"""Load and cross-check the Technical Stage 3B authority composition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .assets import _read_yaml, _require_one
from .errors import AssetContractError

WORKFLOW_ID = "WF_WEEKLY_BUSINESS_REPORT"
PIPELINE_ID = "PL_REVENUE_TECHNICAL_WEEKLY"
DATASET_ID = "DS_REVENUE_SALES_ROLLING_DECK_QTD"
MAPPING_ID = "MAP_REVENUE_SALES_ROLLING_DECK_QTD_V1"
COMPLETE_QUARTER_MAPPING_ID = "MAP_REVENUE_SALES_ROLLING_DECK_QTD_BUSINESS_LINE_V1"
ELIGIBILITY_RULE_ID = "BR_REVENUE_TECHNICAL_SINGLE_COUNT_ELIGIBILITY_V1"
PRIOR_YEAR_RULE_ID = "BR_REVENUE_PRIOR_YEAR_COMPARABLE_SOURCE_SELECTION_V1"
REPORT_MODE_RULE_ID = "BR_WEEKLY_REVENUE_REPORT_MODE_SELECTION_V1"
RESULT_CONTRACT_ID = "RC_REVENUE_TECHNICAL_WEEKLY"
STORE_ID = "STORE_WEEKLY_REVENUE_HISTORICAL"
STORE_ASSET_ID = "STORE_ASSET_WEEKLY_REVENUE_TECHNICAL"
BUSINESS_CONTEXT_ID = "CTX_REVENUE_TECHNICAL_WEEKLY"
VARIANT_IDS = (
    "MV_REVENUE_TECHNICAL_QTD_PERFORMANCE_V1",
    "MV_REVENUE_TECHNICAL_QTD_PERFORMANCE_YOY_V1",
    "MV_REVENUE_TECHNICAL_QTD_EXECUTED_V1",
    "MV_REVENUE_TECHNICAL_WEEKLY_INCREMENTAL_EXECUTED_V1",
    "MV_REVENUE_TECHNICAL_WEEKLY_INCREMENTAL_EXECUTED_WOW_V1",
    "MV_REVENUE_TECHNICAL_WEEKLY_INCREMENTAL_EXECUTED_YOY_V1",
)


@dataclass(frozen=True)
class TechnicalAssetBundle:
    repository_root: Path
    dataset: Mapping[str, Any]
    pipeline: Mapping[str, Any]
    mapping: Mapping[str, Any]
    complete_quarter_mapping: Mapping[str, Any]
    metric_variants: Mapping[str, Mapping[str, Any]]
    result_contract: Mapping[str, Any]
    store: Mapping[str, Any]
    store_asset: Mapping[str, Any]
    business_rules: Mapping[str, Mapping[str, Any]]
    runtime_contract: Mapping[str, Any]

    @classmethod
    def load(cls, repository_root: Path) -> "TechnicalAssetBundle":
        root = repository_root.resolve()
        assets = root / "phase1_5" / "assets"
        datasets = _read_yaml(assets / "datasets" / "dataset_inventory.yaml")
        dataset = _require_one(
            datasets.get("datasets"), "dataset_id", DATASET_ID, "Dataset Inventory"
        )
        registry = _read_yaml(assets / "pipelines" / "pipeline_registry.yaml")
        pipeline = _require_one(
            registry.get("pipelines"), "pipeline_id", PIPELINE_ID, "Pipeline Registry"
        )
        mapping = _read_yaml(
            assets / "field_mappings" / "MAP_REVENUE_SALES_ROLLING_DECK_QTD_V1.yaml"
        )
        complete_mapping = _read_yaml(
            assets
            / "field_mappings"
            / "MAP_REVENUE_SALES_ROLLING_DECK_QTD_BUSINESS_LINE_V1.yaml"
        )
        library = _read_yaml(
            assets / "metrics" / "metric_library_revenue_technical_ctv_v1.yaml"
        )
        variants = {
            item["metric_variant_id"]: item
            for item in library.get("metric_variants", [])
            if isinstance(item, dict) and item.get("metric_variant_id") in VARIANT_IDS
        }
        contract = _read_yaml(
            assets / "result_contracts" / "RC_REVENUE_TECHNICAL_WEEKLY.yaml"
        )
        stores = _read_yaml(
            assets / "metric_stores" / "metric_result_store_registry.yaml"
        )
        store = _require_one(
            stores.get("metric_result_stores"), "store_id", STORE_ID, "Store Registry"
        )
        store_asset = _require_one(
            store.get("store_assets"), "store_asset_id", STORE_ASSET_ID, "Revenue Store"
        )
        rules: dict[str, Mapping[str, Any]] = {}
        for path in (assets / "business_rules").glob("*.yaml"):
            value = _read_yaml(path)
            rule_id = value.get("rule_id")
            if isinstance(rule_id, str):
                rules[rule_id] = value
        runtime = _read_yaml(
            assets / "execution" / "weekly_workflow_runtime_contracts_v1_2_candidate.yaml"
        )
        bundle = cls(
            root,
            dataset,
            pipeline,
            mapping,
            complete_mapping,
            variants,
            contract,
            store,
            store_asset,
            rules,
            runtime,
        )
        bundle.validate_composition()
        return bundle

    def validate_composition(self) -> None:
        if self.dataset.get("dataset_id") != DATASET_ID:
            raise AssetContractError("TECHNICAL_ASSET_MISMATCH", "Dataset identity mismatch")
        if self.mapping.get("mapping_profile_id") != MAPPING_ID:
            raise AssetContractError("TECHNICAL_ASSET_MISMATCH", "QTD Mapping mismatch")
        if self.mapping.get("dataset_id") != DATASET_ID:
            raise AssetContractError("TECHNICAL_ASSET_MISMATCH", "QTD Dataset binding mismatch")
        registration = self.mapping.get("scope", {}).get(
            "prior_year_comparable_qtd_registration", {}
        )
        if (
            registration.get("input_role") != "prior_year_comparable"
            or registration.get("performance_source_mapping_entry_id") != "FM016"
            or registration.get("executed_source_mapping_entry_id") != "FM017"
            or registration.get("source_raw_fields_must_be_distinct") is not True
            or registration.get("complete_quarter_equivalence_allowed") is not False
        ):
            raise AssetContractError(
                "TECHNICAL_PRIOR_AUTHORITY_MISMATCH",
                "Independent prior-year QTD performance/executed Mapping is not registered",
            )
        if (
            self.complete_quarter_mapping.get("mapping_profile_id")
            != COMPLETE_QUARTER_MAPPING_ID
            or "prior_year_comparable_qtd"
            not in self.complete_quarter_mapping.get("scope", {}).get(
                "excluded_usage_contexts", []
            )
        ):
            raise AssetContractError(
                "TECHNICAL_PRIOR_AUTHORITY_MISMATCH",
                "Complete-quarter equivalence is not excluded from prior-year QTD",
            )
        execution = self.pipeline.get("execution", {})
        outputs = self.pipeline.get("outputs", {})
        if (
            self.pipeline.get("business_context_id") != BUSINESS_CONTEXT_ID
            or execution.get("mapping_profile_ids") != [MAPPING_ID]
            or tuple(execution.get("metric_variant_ids", ())) != VARIANT_IDS
            or outputs.get("result_contract_ids") != [RESULT_CONTRACT_ID]
            or outputs.get("metric_result_store_id") != STORE_ID
            or outputs.get("metric_result_store_asset_id") != STORE_ASSET_ID
        ):
            raise AssetContractError(
                "TECHNICAL_ASSET_MISMATCH", "Pipeline output composition mismatch"
            )
        prior_dependencies = [
            item
            for item in self.pipeline.get("historical_input_dependencies", [])
            if isinstance(item, dict)
            and item.get("relationship_rule_id") == PRIOR_YEAR_RULE_ID
        ]
        if len(prior_dependencies) != 1:
            raise AssetContractError(
                "TECHNICAL_PRIOR_AUTHORITY_MISMATCH",
                "Technical prior-year Dataset dependency must be unique",
            )
        prior = prior_dependencies[0]
        physical = prior.get("physical_store_value_bindings", {})
        if (
            prior.get("dataset_id") != DATASET_ID
            or prior.get("run_input_manifest_required") is not True
            or prior.get("run_input_role") != "prior_year_comparable"
            or prior.get("mapping_profile_id") != MAPPING_ID
            or prior.get("complete_quarter_performance_executed_equivalence_allowed")
            is not False
            or physical.get("D", {}).get("standard_field_id")
            != "performance_revenue_amount"
            or physical.get("E", {}).get("standard_field_id")
            != "executed_revenue_amount"
        ):
            raise AssetContractError(
                "TECHNICAL_PRIOR_AUTHORITY_MISMATCH",
                "Technical Store D/E lineage does not resolve to independent QTD fields",
            )
        prior_rule = self.business_rules.get(PRIOR_YEAR_RULE_ID, {})
        prior_binding = prior_rule.get("technical_qtd_eligibility_binding", {})
        if (
            prior_rule.get("inputs", {}).get("required_mapping_profile_id") != MAPPING_ID
            or prior_binding.get("semantic_authority_rule_id") != ELIGIBILITY_RULE_ID
            or prior_binding.get("input_role") != "prior_year_comparable"
            or prior_binding.get("registration_status") != "registered"
        ):
            raise AssetContractError(
                "TECHNICAL_PRIOR_AUTHORITY_MISMATCH",
                "Technical prior-year eligibility adapter is not registered",
            )
        ordered_rules = tuple(execution.get("ordered_rule_set_ids", ()))
        if any(rule_id not in self.business_rules for rule_id in ordered_rules):
            raise AssetContractError(
                "TECHNICAL_ASSET_MISMATCH", "Pipeline Business Rule set is incomplete"
            )
        if set(self.metric_variants) != set(VARIANT_IDS):
            raise AssetContractError(
                "TECHNICAL_ASSET_MISMATCH", "Technical Metric Variant set is incomplete"
            )
        yoy_dependency = self.metric_variants[VARIANT_IDS[5]].get(
            "denominator_result_dependency", {}
        )
        fallback = yoy_dependency.get("fallback_reconstruction", {})
        previous_snapshot = fallback.get(
            "previous_prior_year_qtd_executed_snapshot", {}
        )
        if (
            yoy_dependency.get("owner_confirmation")
            != "confirmed_primary_store_result_with_exact_dual_qtd_reconstruction_fallback_2026-08-20"
            or fallback.get("allowed_when")
            != "primary_exact_store_result_not_found_only"
            or fallback.get("reconstruction_metric_variant_id") != VARIANT_IDS[3]
            or fallback.get("exact_date_and_validated_lineage_required") is not True
            or fallback.get("nearby_date_or_row_order_fallback_allowed") is not False
            or previous_snapshot.get("source")
            != "MetricStorePort exact physical snapshot read"
            or previous_snapshot.get("physical_field_id") != "E"
            or previous_snapshot.get("period_role") != "prior_year_comparable"
        ):
            raise AssetContractError(
                "TECHNICAL_PRIOR_AUTHORITY_MISMATCH",
                "Technical weekly YoY exact dual-QTD fallback is not registered",
            )
        if self.result_contract.get("result_contract_id") != RESULT_CONTRACT_ID:
            raise AssetContractError(
                "TECHNICAL_ASSET_MISMATCH", "Technical Result Contract mismatch"
            )
        field_variants = {
            item.get("source_metric_variant_id")
            for item in self.result_contract.get("contract_fields", [])
            if isinstance(item, dict)
        }
        if field_variants != set(VARIANT_IDS):
            raise AssetContractError(
                "TECHNICAL_ASSET_MISMATCH", "Result Contract Metric bindings are incomplete"
            )
        columns = self.store_asset.get("physical_column_bindings", {})
        if (
            columns.get("D", {}).get("metric_variant_id") != VARIANT_IDS[0]
            or columns.get("D", {}).get("period_role") != "prior_year_comparable"
            or columns.get("E", {}).get("metric_variant_id") != VARIANT_IDS[2]
            or columns.get("E", {}).get("period_role") != "prior_year_comparable"
        ):
            raise AssetContractError(
                "TECHNICAL_PRIOR_AUTHORITY_MISMATCH", "Store D/E Metric bindings mismatch"
            )
        snapshot_read = self.store_asset.get(
            "prior_year_qtd_executed_snapshot_read", {}
        )
        if (
            snapshot_read.get("adapter_operation")
            != "read_exact_physical_snapshot"
            or snapshot_read.get("metric_store_port_required") is not True
            or snapshot_read.get("physical_field_id") != "E"
            or snapshot_read.get("metric_variant_id") != VARIANT_IDS[2]
            or snapshot_read.get("period_role") != "prior_year_comparable"
            or snapshot_read.get("adapter_technical_read_only") is not True
            or snapshot_read.get("result_contract_field_created") is not False
        ):
            raise AssetContractError(
                "TECHNICAL_PRIOR_AUTHORITY_MISMATCH",
                "Store E exact prior-year QTD snapshot read is not registered",
            )
        if self.runtime_contract.get("run_input_manifest", {}).get("manifest_id") != (
            "RUN_INPUT_MANIFEST_WF_WEEKLY_BUSINESS_REPORT_V1"
        ):
            raise AssetContractError("TECHNICAL_ASSET_MISMATCH", "Manifest identity mismatch")
