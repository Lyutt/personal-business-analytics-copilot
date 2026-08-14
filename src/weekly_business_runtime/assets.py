"""Load and cross-check the checked-in CTV authority assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import AssetContractError

WORKFLOW_ID = "WF_WEEKLY_BUSINESS_REPORT"
PIPELINE_ID = "PL_REVENUE_CTV_WEEKLY"
CURRENT_DATASET_ID = "DS_REVENUE_CTV_EXCL_PLACEMENT_QTD"
PRIOR_DATASET_ID = "DS_REVENUE_SALES_ROLLING_DECK_QTD"
PREVIOUS_QUARTER_FALLBACK_DATASET_ID = (
    "DS_REVENUE_SALES_ROLLING_DECK_QUARTER_CLOSE_CONFIRMATION"
)
CURRENT_MAPPING_ID = "MAP_REVENUE_CTV_EXCL_PLACEMENT_QTD_V1"
PRIOR_MAPPING_ID = "MAP_REVENUE_SALES_ROLLING_DECK_QTD_BUSINESS_LINE_V1"
PREVIOUS_QUARTER_FALLBACK_MAPPING_ID = (
    "MAP_REVENUE_SALES_ROLLING_DECK_QUARTER_CLOSE_BUSINESS_LINE_V1"
)
PREVIOUS_QUARTER_RULE_ID = "BR_REVENUE_PREVIOUS_QUARTER_RESULT_SOURCE_SELECTION_V1"
CTV_PRIOR_YEAR_STORE_RULE_ID = (
    "BR_REVENUE_CTV_PRIOR_YEAR_HISTORICAL_STORE_SELECTION_V1"
)
RESULT_CONTRACT_ID = "RC_REVENUE_CTV_WEEKLY"
STORE_ID = "STORE_WEEKLY_REVENUE_HISTORICAL"
STORE_ASSET_ID = "STORE_ASSET_WEEKLY_REVENUE_CTV"
BUSINESS_CONTEXT_ID = "CTX_REVENUE_CTV_WEEKLY"
CTV_VARIANT_IDS = (
    "MV_REVENUE_CTV_QTD_PERFORMANCE_V1",
    "MV_REVENUE_CTV_QTD_PERFORMANCE_YOY_V1",
    "MV_REVENUE_CTV_QTD_EXECUTED_V1",
)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise AssetContractError("CTV_ASSET_INVALID", f"{path.name} must contain an object")
    return value


def _require_one(items: object, key: str, expected: str, scope: str) -> dict[str, Any]:
    matches = [item for item in items or [] if isinstance(item, dict) and item.get(key) == expected]
    if len(matches) != 1:
        raise AssetContractError(
            "CTV_ASSET_IDENTITY_MISMATCH",
            f"{scope} must contain exactly one {key}={expected}",
        )
    return matches[0]


@dataclass(frozen=True)
class CtvAssetBundle:
    repository_root: Path
    current_dataset: Mapping[str, Any]
    prior_dataset: Mapping[str, Any]
    previous_quarter_fallback_dataset: Mapping[str, Any]
    pipeline: Mapping[str, Any]
    current_mapping: Mapping[str, Any]
    prior_mapping: Mapping[str, Any]
    previous_quarter_fallback_mapping: Mapping[str, Any]
    metric_variants: Mapping[str, Mapping[str, Any]]
    result_contract: Mapping[str, Any]
    store: Mapping[str, Any]
    store_asset: Mapping[str, Any]
    business_rules: Mapping[str, Mapping[str, Any]]
    runtime_contract_v1: Mapping[str, Any]
    runtime_contract_candidate: Mapping[str, Any]
    acquisition_contract: Mapping[str, Any]

    @classmethod
    def load(cls, repository_root: Path) -> "CtvAssetBundle":
        root = repository_root.resolve()
        assets = root / "phase1_5" / "assets"
        dataset_inventory = _read_yaml(assets / "datasets" / "dataset_inventory.yaml")
        current_dataset = _require_one(
            dataset_inventory.get("datasets"), "dataset_id", CURRENT_DATASET_ID, "Dataset Inventory"
        )
        prior_dataset = _require_one(
            dataset_inventory.get("datasets"), "dataset_id", PRIOR_DATASET_ID, "Dataset Inventory"
        )
        previous_quarter_fallback_dataset = _require_one(
            dataset_inventory.get("datasets"),
            "dataset_id",
            PREVIOUS_QUARTER_FALLBACK_DATASET_ID,
            "Dataset Inventory",
        )
        pipeline_registry = _read_yaml(assets / "pipelines" / "pipeline_registry.yaml")
        pipeline = _require_one(
            pipeline_registry.get("pipelines"), "pipeline_id", PIPELINE_ID, "Pipeline Registry"
        )
        current_mapping = _read_yaml(
            assets / "field_mappings" / "MAP_REVENUE_CTV_EXCL_PLACEMENT_QTD_V1.yaml"
        )
        prior_mapping = _read_yaml(
            assets
            / "field_mappings"
            / "MAP_REVENUE_SALES_ROLLING_DECK_QTD_BUSINESS_LINE_V1.yaml"
        )
        previous_quarter_fallback_mapping = _read_yaml(
            assets
            / "field_mappings"
            / "MAP_REVENUE_SALES_ROLLING_DECK_QUARTER_CLOSE_BUSINESS_LINE_V1.yaml"
        )
        metric_library = _read_yaml(
            assets / "metrics" / "metric_library_revenue_technical_ctv_v1.yaml"
        )
        variants = {
            item["metric_variant_id"]: item
            for item in metric_library.get("metric_variants", [])
            if isinstance(item, dict) and item.get("metric_variant_id") in CTV_VARIANT_IDS
        }
        result_contract = _read_yaml(
            assets / "result_contracts" / "RC_REVENUE_CTV_WEEKLY.yaml"
        )
        store_registry = _read_yaml(
            assets / "metric_stores" / "metric_result_store_registry.yaml"
        )
        store = _require_one(
            store_registry.get("metric_result_stores"), "store_id", STORE_ID, "Store Registry"
        )
        store_asset = _require_one(
            store.get("store_assets"), "store_asset_id", STORE_ASSET_ID, "Revenue Store"
        )
        rule_assets: dict[str, Mapping[str, Any]] = {}
        for path in (assets / "business_rules").glob("*.yaml"):
            value = _read_yaml(path)
            rule_id = value.get("rule_id")
            if isinstance(rule_id, str):
                rule_assets[rule_id] = value
        runtime_contract_v1 = _read_yaml(
            assets / "execution" / "weekly_workflow_runtime_contracts_v1.yaml"
        )
        runtime_contract_candidate = _read_yaml(
            assets / "execution" / "weekly_workflow_runtime_contracts_v1_2_candidate.yaml"
        )
        acquisition_contract = _read_yaml(
            assets / "execution" / "weekly_acquisition_automation_contracts_v1_1_candidate.yaml"
        )
        bundle = cls(
            root,
            current_dataset,
            prior_dataset,
            previous_quarter_fallback_dataset,
            pipeline,
            current_mapping,
            prior_mapping,
            previous_quarter_fallback_mapping,
            variants,
            result_contract,
            store,
            store_asset,
            rule_assets,
            runtime_contract_v1,
            runtime_contract_candidate,
            acquisition_contract,
        )
        bundle.validate_composition()
        return bundle

    def validate_composition(self) -> None:
        if self.current_dataset.get("dataset_id") != CURRENT_DATASET_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Current Dataset mismatch")
        if self.current_dataset.get("data_contract", {}).get("mapping_profile_id") != CURRENT_MAPPING_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Dataset Mapping binding mismatch")
        if self.prior_dataset.get("dataset_id") != PRIOR_DATASET_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Prior Dataset mismatch")
        if (
            self.previous_quarter_fallback_dataset.get("dataset_id")
            != PREVIOUS_QUARTER_FALLBACK_DATASET_ID
        ):
            raise AssetContractError(
                "CTV_ASSET_IDENTITY_MISMATCH", "Previous-quarter fallback Dataset mismatch"
            )
        if self.pipeline.get("business_context_id") != BUSINESS_CONTEXT_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "CTV business context mismatch")
        execution = self.pipeline.get("execution", {})
        if tuple(execution.get("metric_variant_ids", ())) != CTV_VARIANT_IDS:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "CTV Metric Variant set mismatch")
        if execution.get("mapping_profile_ids") != [CURRENT_MAPPING_ID]:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "CTV Mapping binding mismatch")
        outputs = self.pipeline.get("outputs", {})
        if outputs.get("result_contract_ids") != [RESULT_CONTRACT_ID]:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "CTV Result Contract binding mismatch")
        if outputs.get("metric_result_store_id") != STORE_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "CTV Store binding mismatch")
        if outputs.get("metric_result_store_asset_id") != STORE_ASSET_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "CTV Store Asset binding mismatch")
        prior_year_exact_read = self.store_asset.get("prior_year_yoy_exact_read", {})
        if (
            self.store_asset.get("workbook_name") != "CTV线历史整体数据存档.xlsx"
            or self.store_asset.get("business_date_field") != "数据当周最后一天"
            or prior_year_exact_read.get("rule_id") != CTV_PRIOR_YEAR_STORE_RULE_ID
            or prior_year_exact_read.get("value_field") != "QTD签单执行金额"
            or prior_year_exact_read.get("source_metric_variant_id") != CTV_VARIANT_IDS[0]
            or prior_year_exact_read.get("exact_single_row_required") is not True
        ):
            raise AssetContractError(
                "CTV_ASSET_IDENTITY_MISMATCH",
                "CTV Store exact business-date read contract mismatch",
            )
        if self.current_mapping.get("mapping_profile_id") != CURRENT_MAPPING_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Current Mapping identity mismatch")
        if self.current_mapping.get("dataset_id") != CURRENT_DATASET_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Current Dataset binding mismatch")
        if self.prior_mapping.get("mapping_profile_id") != PRIOR_MAPPING_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Prior Mapping identity mismatch")
        if self.prior_mapping.get("dataset_id") != PRIOR_DATASET_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Prior Dataset binding mismatch")
        if (
            self.previous_quarter_fallback_mapping.get("mapping_profile_id")
            != PREVIOUS_QUARTER_FALLBACK_MAPPING_ID
            or self.previous_quarter_fallback_mapping.get("dataset_id")
            != PREVIOUS_QUARTER_FALLBACK_DATASET_ID
        ):
            raise AssetContractError(
                "CTV_ASSET_IDENTITY_MISMATCH",
                "Previous-quarter fallback Mapping binding mismatch",
            )
        if set(self.metric_variants) != set(CTV_VARIANT_IDS):
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "CTV Metric Variants incomplete")
        if self.result_contract.get("result_contract_id") != RESULT_CONTRACT_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Result Contract identity mismatch")
        if self.result_contract.get("workflow_id") != WORKFLOW_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Result Contract Workflow mismatch")
        ordered_rules = tuple(execution.get("ordered_rule_set_ids", ()))
        if any(rule_id not in self.business_rules for rule_id in ordered_rules):
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "CTV Business Rule set incomplete")
        for rule_id in ordered_rules:
            rule = self.business_rules[rule_id]
            if WORKFLOW_ID not in rule.get("applicable_workflow_ids", []):
                raise AssetContractError(
                    "CTV_ASSET_IDENTITY_MISMATCH", f"{rule_id} Workflow binding mismatch"
                )
            if PIPELINE_ID not in rule.get("applicable_pipeline_ids", []):
                raise AssetContractError(
                    "CTV_ASSET_IDENTITY_MISMATCH", f"{rule_id} Pipeline binding mismatch"
                )
        conditional_mappings = {
            item.get("mapping_profile_id")
            for item in execution.get("conditional_mapping_profile_ids", ())
            if isinstance(item, dict)
        }
        if conditional_mappings != {
            PRIOR_MAPPING_ID,
            PREVIOUS_QUARTER_FALLBACK_MAPPING_ID,
        }:
            raise AssetContractError(
                "CTV_ASSET_IDENTITY_MISMATCH",
                "CTV conditional Mapping Profile set mismatch",
            )
        previous_quarter_rule = self.business_rules.get(PREVIOUS_QUARTER_RULE_ID, {})
        source_priority = previous_quarter_rule.get("source_priority", {})
        if (
            source_priority.get("primary", {}).get("dataset_id") != PRIOR_DATASET_ID
            or source_priority.get("fallback", {}).get("dataset_id")
            != PREVIOUS_QUARTER_FALLBACK_DATASET_ID
        ):
            raise AssetContractError(
                "CTV_ASSET_IDENTITY_MISMATCH",
                "Previous-quarter primary/fallback Dataset authority mismatch",
            )
        historical = self.pipeline.get("historical_input_dependencies", ())
        previous_quarter_dependencies = [
            item
            for item in historical
            if isinstance(item, dict)
            and item.get("dataset_id") == PRIOR_DATASET_ID
            and item.get("role") == "Previous-quarter complete CTV primary input"
        ]
        regular_store_dependencies = [
            item
            for item in historical
            if isinstance(item, dict)
            and item.get("store_id") == STORE_ID
            and item.get("store_asset_id") == STORE_ASSET_ID
            and item.get("read_key_semantics") == "exact_workflow_reporting_date"
        ]
        prior_year_store_dependencies = [
            item
            for item in historical
            if isinstance(item, dict)
            and item.get("store_id") == STORE_ID
            and item.get("store_asset_id") == STORE_ASSET_ID
            and item.get("read_key_semantics")
            == "exact_current_revenue_cutoff_business_date"
            and item.get("target_business_date_rule_id") == CTV_PRIOR_YEAR_STORE_RULE_ID
            and item.get("source_metric_variant_id") == CTV_VARIANT_IDS[0]
            and item.get("run_input_manifest_required") is False
            and item.get("prior_year_source_report_date_required") is False
        ]
        if (
            len(previous_quarter_dependencies) != 1
            or len(regular_store_dependencies) != 1
            or len(prior_year_store_dependencies) != 1
        ):
            raise AssetContractError(
                "CTV_ASSET_IDENTITY_MISMATCH",
                "CTV previous-quarter or Historical Store dependency mismatch",
            )
        yoy_variant = self.metric_variants.get(CTV_VARIANT_IDS[1], {})
        yoy_store = yoy_variant.get("prior_year_store_dependency", {})
        if (
            yoy_variant.get("prior_year_source_rule_id") != CTV_PRIOR_YEAR_STORE_RULE_ID
            or yoy_store.get("store_id") != STORE_ID
            or yoy_store.get("store_asset_id") != STORE_ASSET_ID
            or yoy_store.get("source_metric_variant_id") != CTV_VARIANT_IDS[0]
            or yoy_store.get("run_input_manifest_required") is not False
            or yoy_store.get("prior_year_source_report_date_required") is not False
        ):
            raise AssetContractError(
                "CTV_ASSET_IDENTITY_MISMATCH",
                "CTV prior-year Metric Store authority mismatch",
            )
        technical_prior_rule = self.business_rules.get(
            "BR_REVENUE_PRIOR_YEAR_COMPARABLE_SOURCE_SELECTION_V1", {}
        )
        ctv_prior_rule = self.business_rules.get(CTV_PRIOR_YEAR_STORE_RULE_ID, {})
        if (
            PIPELINE_ID in technical_prior_rule.get("applicable_pipeline_ids", [])
            or "PL_REVENUE_TECHNICAL_WEEKLY"
            not in technical_prior_rule.get("applicable_pipeline_ids", [])
            or ctv_prior_rule.get("applicable_pipeline_ids") != [PIPELINE_ID]
        ):
            raise AssetContractError(
                "CTV_ASSET_IDENTITY_MISMATCH",
                "CTV and Technical prior-year authority separation mismatch",
            )
        for runtime in (self.runtime_contract_v1, self.runtime_contract_candidate):
            if runtime.get("workflow_id") != WORKFLOW_ID:
                raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Runtime Workflow mismatch")
            if runtime.get("run_input_manifest", {}).get("manifest_id") != (
                "RUN_INPUT_MANIFEST_WF_WEEKLY_BUSINESS_REPORT_V1"
            ):
                raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Manifest identity mismatch")
        runtime_pipeline = self.runtime_contract_candidate.get(
            "pipeline_scoped_rule_context_bindings", {}
        ).get(PIPELINE_ID, {})
        if runtime_pipeline.get("target_business_line", {}).get("value") != "CTV":
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Runtime CTV identity mismatch")
        if self.acquisition_contract.get("workflow_id") != WORKFLOW_ID:
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Acquisition Workflow mismatch")
        if self.acquisition_contract.get("contract_id") != (
            "ACQUISITION_AUTOMATION_WF_WEEKLY_BUSINESS_REPORT_V1_1"
        ):
            raise AssetContractError("CTV_ASSET_IDENTITY_MISMATCH", "Acquisition identity mismatch")

        contract_fields = self.result_contract.get("contract_fields", [])
        field_variants = {
            field.get("source_metric_variant_id")
            for field in contract_fields
            if isinstance(field, dict)
        }
        if field_variants != set(CTV_VARIANT_IDS):
            raise AssetContractError(
                "CTV_ASSET_IDENTITY_MISMATCH", "Result fields do not bind the CTV Variant set"
            )
        for variant_id, variant in self.metric_variants.items():
            binding = variant.get("output_binding", {})
            if binding.get("result_contract_id") != RESULT_CONTRACT_ID:
                raise AssetContractError(
                    "CTV_ASSET_IDENTITY_MISMATCH", f"{variant_id} Result Contract mismatch"
                )
