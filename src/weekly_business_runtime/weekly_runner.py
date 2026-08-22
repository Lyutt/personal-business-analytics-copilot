"""Minimal Registry-driven sequential Runner for the Weekly Workflow."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from weekly_acquisition_runtime.contracts import BusinessKey
from weekly_acquisition_runtime.runtime import RuntimeRun

from .ctv_metrics import validate_revenue_context
from .errors import Stage3AError
from .models import (
    ExecutionWarning,
    PipelineExecutionResult,
    PipelineExecutionStatus,
    ResultValueStatus,
    Stage3CResultContractInstance,
)
from .stage3c import LocalRuleBindings

WORKFLOW_ID = "WF_WEEKLY_BUSINESS_REPORT"
PRODUCT_PIPELINE_ID = "PL_INVENTORY_PRODUCT_SELL_THROUGH_WEEKLY"
CUSTOMER_PIPELINE_ID = "PL_ADVERTISING_PRODUCT_CUSTOMER_CHANGE_ANALYSIS"
BRAND_MOMENT_PIPELINE_ID = "PL_INVENTORY_BRAND_MOMENT_SELL_THROUGH_WEEKLY"
DAU_PIPELINE_ID = "PL_USER_ANALYTICS_PLATFORM_DAU_WEEKLY"
NON_PATCH_PIPELINE_ID = "PL_INVENTORY_NON_PATCH_PRODUCT_WEEKLY"
REVENUE_PIPELINE_IDS = frozenset(
    {
        "PL_REVENUE_TECHNICAL_WEEKLY",
        "PL_REVENUE_CTV_WEEKLY",
        "PL_REVENUE_SMART_SPEAKER_WEEKLY",
        "PL_REVENUE_FAST_VERSION_WEEKLY",
    }
)


class WeeklyWorkflowRunner:
    """Resolve and run the current Weekly Pipeline Runs without a generic DAG engine."""

    def __init__(
        self,
        *,
        repository_root: Path,
        executors: Mapping[str, object],
        rules: LocalRuleBindings,
        registry: Mapping[str, Any] | None = None,
    ) -> None:
        self.repository_root = repository_root
        self.executors = dict(executors)
        self.rules = rules
        self.registry = dict(registry or self._load_registry())
        self.pipelines = {
            item["pipeline_id"]: item for item in self.registry.get("pipelines", ())
        }
        execution = self.registry.get("constraints", {}).get("mvp_pipeline_execution", {})
        self.registry_order = tuple(execution.get("pipeline_ids", ()))
        self._validate_authority(execution)

    def execute(
        self,
        *,
        run: RuntimeRun,
        generated_at: str,
        pipeline_run_ids: Mapping[object, str],
    ) -> Mapping[str, Any]:
        """Execute one deterministic pass from the locked Context and Manifest."""

        manifest = run.run_input_manifest.finalize()
        if manifest.get("workflow_run_id") != run.context.workflow_run_id:
            raise Stage3AError(
                "STAGE3D_MANIFEST_RUN_MISMATCH",
                "Run Input Manifest does not belong to the locked Workflow Run Context",
            )
        entries = tuple(manifest.get("entries", ()))
        entry_index = self._entry_index(entries)
        try:
            validate_revenue_context(run.context.values)
            revenue_context_error = None
        except Stage3AError as exc:
            revenue_context_error = exc
        nodes, omissions = self._resolve_pipeline_runs(
            run=run,
            entry_index=entry_index,
            pipeline_run_ids=pipeline_run_ids,
        )
        order = self._topological_order(nodes)

        results: dict[tuple[str, str], PipelineExecutionResult] = {}
        dependency_status: dict[str, tuple[str, ...]] = {}
        blocked_scopes: list[Mapping[str, str]] = []
        normal_omissions = list(omissions)
        result_references: list[str] = []
        lineage: list[str] = []

        for node_key in order:
            node = nodes[node_key]
            dependency_results = {
                dependency: results[dependency] for dependency in node["dependencies"]
            }
            dependency_status[self._node_label(node_key)] = tuple(
                f"{self._node_label(key)}:{value.execution_status.value}"
                for key, value in dependency_results.items()
            )

            blocked_dependency = next(
                (
                    value
                    for value in dependency_results.values()
                    if value.execution_status is PipelineExecutionStatus.BLOCKED
                    or not self._is_validated_result(value)
                ),
                None,
            )
            if node["pipeline_id"] in REVENUE_PIPELINE_IDS and revenue_context_error is not None:
                result = self._blocked_result(
                    run,
                    node,
                    revenue_context_error.code,
                    str(revenue_context_error),
                )
            elif blocked_dependency is not None:
                result = self._blocked_result(
                    run,
                    node,
                    "STAGE3D_REQUIRED_UPSTREAM_UNAVAILABLE",
                    "A required registered upstream Result Contract is unavailable or invalid",
                )
            elif node["pipeline_id"] == CUSTOMER_PIPELINE_ID and not self._trigger_met(
                node["product"], dependency_results
            ):
                result = self._normal_omission(run, node)
                normal_omissions.append(
                    MappingProxyType(
                        {
                            "pipeline_id": CUSTOMER_PIPELINE_ID,
                            "product": node["product"],
                            "reason": "trigger_not_met",
                        }
                    )
                )
            else:
                result = self._invoke(
                    run=run,
                    node=node,
                    entry_index=entry_index,
                    dependency_results=dependency_results,
                    generated_at=generated_at,
                )

            self._validate_execution_identity(run, node, result)
            if self._is_validated_result(result):
                try:
                    self._validate_result_handoff(run, node, result)
                except Stage3AError as exc:
                    result = self._blocked_result(
                        run,
                        node,
                        exc.code,
                        str(exc),
                    )
            results[node_key] = result
            if result.execution_status is PipelineExecutionStatus.BLOCKED:
                blocked_scopes.append(
                    MappingProxyType(
                        {
                            "pipeline_id": result.pipeline_id,
                            "pipeline_run_id": result.pipeline_run_id,
                            "product": node["product"],
                            "error_code": result.error_code,
                        }
                    )
                )
            if self._is_validated_result(result):
                result_references.append(result.produced_result_contract_reference)
                lineage.extend(result.lineage_references)

        warnings = tuple(warning for result in results.values() for warning in result.warnings)
        errors = tuple(
            MappingProxyType(
                {
                    "pipeline_id": result.pipeline_id,
                    "pipeline_run_id": result.pipeline_run_id,
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                }
            )
            for result in results.values()
            if result.execution_status is PipelineExecutionStatus.BLOCKED
        )
        return MappingProxyType(
            {
                "workflow_run_id": run.context.workflow_run_id,
                "pipeline_run_results": tuple(results[key] for key in order),
                "execution_order": tuple(self._node_label(key) for key in order),
                "dependency_status": MappingProxyType(dependency_status),
                "warnings": warnings,
                "errors": errors,
                "blocked_scopes": tuple(blocked_scopes),
                "normal_omissions": tuple(normal_omissions),
                "result_contract_references": tuple(result_references),
                "lineage": tuple(lineage),
            }
        )

    def _load_registry(self) -> Mapping[str, Any]:
        path = self.repository_root / "phase1_5/assets/pipelines/pipeline_registry.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, Mapping):
            raise Stage3AError("STAGE3D_REGISTRY_INVALID", "Pipeline Registry is invalid")
        return document

    def _validate_authority(self, execution: Mapping[str, Any]) -> None:
        if (
            len(self.registry_order) != 12
            or len(set(self.registry_order)) != 12
            or execution.get("first_phase_scheduling_mode") != "sequential"
            or execution.get("registered_pipeline_order_required") is not True
            or execution.get("parallel_scheduler_required") is not False
            or set(self.registry_order) - set(self.pipelines)
        ):
            raise Stage3AError(
                "STAGE3D_REGISTRY_EXECUTION_AUTHORITY_INVALID",
                "The frozen 12-Pipeline sequential execution authority is incomplete",
            )
        for pipeline_id in self.registry_order:
            bindings = self.pipelines[pipeline_id].get("workflow_bindings", ())
            if len(bindings) != 1 or bindings[0].get("workflow_id") != WORKFLOW_ID:
                raise Stage3AError(
                    "STAGE3D_WORKFLOW_BINDING_INVALID",
                    f"{pipeline_id} has no unique Weekly Workflow binding",
                )

    @staticmethod
    def _entry_index(entries: tuple[Mapping[str, Any], ...]) -> Mapping[tuple[str, str, str], BusinessKey]:
        index: dict[tuple[str, str, str], BusinessKey] = {}
        for entry in entries:
            key = BusinessKey(
                workflow_run_id=str(entry["workflow_run_id"]),
                dataset_id=str(entry["dataset_id"]),
                period_role=str(entry["period_role"]),
                product_parameter=str(entry["product_parameter"]),
            )
            business_key = (key.dataset_id, key.period_role, key.product_parameter)
            if business_key in index:
                raise Stage3AError(
                    "STAGE3D_MANIFEST_BINDING_AMBIGUOUS",
                    "Run Input Manifest contains an ambiguous business key",
                )
            index[business_key] = key
        return MappingProxyType(index)

    def _resolve_pipeline_runs(
        self,
        *,
        run: RuntimeRun,
        entry_index: Mapping[tuple[str, str, str], BusinessKey],
        pipeline_run_ids: Mapping[object, str],
    ) -> tuple[dict[tuple[str, str], dict[str, Any]], tuple[Mapping[str, str], ...]]:
        nodes: dict[tuple[str, str], dict[str, Any]] = {}
        omissions: list[Mapping[str, str]] = []
        product_order = tuple(self.rules.product_route_by_name)
        non_patch_dataset = self._primary_dataset_id(self.pipelines[NON_PATCH_PIPELINE_ID])
        unregistered_manifest_products = {
            product
            for dataset, role, product in entry_index
            if dataset == non_patch_dataset
            and role == "current"
            and product not in self.rules.product_route_by_name
        }
        if unregistered_manifest_products:
            raise Stage3AError(
                "STAGE3D_PRODUCT_BINDING_UNREGISTERED",
                "Non-patch Manifest products have no exact configured route: "
                f"{sorted(unregistered_manifest_products)}",
            )

        for pipeline_id in self.registry_order:
            if pipeline_id in {PRODUCT_PIPELINE_ID, CUSTOMER_PIPELINE_ID}:
                continue
            pipeline = self.pipelines[pipeline_id]
            if pipeline_id == NON_PATCH_PIPELINE_ID:
                products = tuple(
                    product
                    for product in product_order
                    if (self._primary_dataset_id(pipeline), "current", product) in entry_index
                )
            else:
                products = ("not_applicable",)
            if pipeline_id == DAU_PIPELINE_ID and not self._has_primary_entry(
                pipeline, entry_index, "not_applicable"
            ):
                omissions.append(
                    MappingProxyType(
                        {
                            "pipeline_id": pipeline_id,
                            "product": "not_applicable",
                            "reason": "optional_input_not_bound",
                        }
                    )
                )
                continue
            for product in products:
                self._add_node(nodes, pipeline_id, product, pipeline_run_ids)

        for product in product_order:
            route = self.rules.product_route_by_name[product]
            if route not in {"patch", "non_patch", "brand_moment"}:
                raise Stage3AError(
                    "STAGE3D_PRODUCT_ROUTE_INVALID",
                    f"{product} has no exact registered product route",
                )
            if route in {"patch", "non_patch"}:
                self._add_node(nodes, PRODUCT_PIPELINE_ID, product, pipeline_run_ids)

        for product in self.rules.customer_analysis_by_product:
            if product not in self.rules.product_route_by_name:
                raise Stage3AError(
                    "STAGE3D_CUSTOMER_ROUTE_UNBOUND",
                    f"{product} customer analysis has no exact product route",
                )
            self._add_node(nodes, CUSTOMER_PIPELINE_ID, product, pipeline_run_ids)

        for key, node in nodes.items():
            pipeline_id, product = key
            dependencies: list[tuple[str, str]] = []
            pipeline = self.pipelines[pipeline_id]
            for dependency in pipeline.get("result_contract_dependencies", ()):
                dependency_product = "not_applicable"
                if (
                    pipeline_id == BRAND_MOMENT_PIPELINE_ID
                    and dependency["producer_pipeline_id"] == NON_PATCH_PIPELINE_ID
                ):
                    brand_products = [
                        configured_product
                        for configured_product, route in self.rules.product_route_by_name.items()
                        if route == "brand_moment"
                    ]
                    if len(brand_products) != 1:
                        raise Stage3AError(
                            "STAGE3D_BRAND_MOMENT_ROUTE_AMBIGUOUS",
                            "Brand Moment requires exactly one explicit configured product binding",
                        )
                    dependency_product = brand_products[0]
                dependencies.append(
                    (str(dependency["producer_pipeline_id"]), dependency_product)
                )
            if pipeline_id == PRODUCT_PIPELINE_ID:
                route_key = (
                    "regular_patch_products"
                    if self.rules.product_route_by_name[product] == "patch"
                    else "all_products_except_regular_patch_and_brand_moment"
                )
                route = pipeline["result_contract_dependency_routing"]["confirmed_routes"][route_key]
                upstream_product = "not_applicable" if route_key == "regular_patch_products" else product
                dependencies.append((str(route["producer_pipeline_id"]), upstream_product))
            if pipeline_id == CUSTOMER_PIPELINE_ID:
                route = self.rules.product_route_by_name[product]
                dependencies.append(
                    (BRAND_MOMENT_PIPELINE_ID, "not_applicable")
                    if route == "brand_moment"
                    else (PRODUCT_PIPELINE_ID, product)
                )
            node["dependencies"] = tuple(dependencies)
            for dependency in dependencies:
                if dependency not in nodes:
                    raise Stage3AError(
                        "STAGE3D_DEPENDENCY_REFERENCE_INVALID",
                        f"{self._node_label(key)} references unavailable {self._node_label(dependency)}",
                    )
        return nodes, tuple(omissions)

    def _add_node(
        self,
        nodes: dict[tuple[str, str], dict[str, Any]],
        pipeline_id: str,
        product: str,
        pipeline_run_ids: Mapping[object, str],
    ) -> None:
        key = (pipeline_id, product)
        if key in nodes:
            raise Stage3AError("STAGE3D_PIPELINE_RUN_DUPLICATE", self._node_label(key))
        run_id = pipeline_run_ids.get(key)
        if run_id is None and product == "not_applicable":
            run_id = pipeline_run_ids.get(pipeline_id)
        if not isinstance(run_id, str) or not run_id:
            raise Stage3AError(
                "STAGE3D_PIPELINE_RUN_ID_UNBOUND",
                f"{self._node_label(key)} requires an explicit Pipeline Run identity",
            )
        if pipeline_id not in self.executors:
            raise Stage3AError(
                "STAGE3D_EXECUTOR_UNBOUND",
                f"{pipeline_id} has no explicitly bound existing executor",
            )
        nodes[key] = {
            "pipeline_id": pipeline_id,
            "product": product,
            "pipeline_run_id": run_id,
            "dependencies": (),
        }

    def _topological_order(
        self, nodes: Mapping[tuple[str, str], Mapping[str, Any]]
    ) -> tuple[tuple[str, str], ...]:
        remaining = {key: set(node["dependencies"]) for key, node in nodes.items()}
        product_order = {product: index for index, product in enumerate(self.rules.product_route_by_name)}
        pipeline_order = {pipeline: index for index, pipeline in enumerate(self.registry_order)}
        ordered: list[tuple[str, str]] = []
        while remaining:
            ready = [key for key, dependencies in remaining.items() if not dependencies]
            if not ready:
                raise Stage3AError(
                    "STAGE3D_DEPENDENCY_CYCLE",
                    "Applicable Pipeline Run dependency graph contains a cycle",
                )
            ready.sort(
                key=lambda key: (
                    pipeline_order[key[0]],
                    product_order.get(key[1], -1),
                )
            )
            for key in ready:
                ordered.append(key)
                remaining.pop(key)
                for dependencies in remaining.values():
                    dependencies.discard(key)
        return tuple(ordered)

    def _invoke(
        self,
        *,
        run: RuntimeRun,
        node: Mapping[str, Any],
        entry_index: Mapping[tuple[str, str, str], BusinessKey],
        dependency_results: Mapping[tuple[str, str], PipelineExecutionResult],
        generated_at: str,
    ) -> PipelineExecutionResult:
        pipeline_id = str(node["pipeline_id"])
        product = str(node["product"])
        pipeline = self.pipelines[pipeline_id]
        executor = self.executors[pipeline_id]
        common = {
            "run": run,
            "pipeline_run_id": node["pipeline_run_id"],
            "generated_at": generated_at,
        }

        if pipeline_id == PRODUCT_PIPELINE_ID:
            upstream = next(iter(dependency_results.values())).result_contract
            kwargs = {**common, "product": product, "rules": self.rules, "upstream": upstream}
        elif pipeline_id == CUSTOMER_PIPELINE_ID:
            upstream = next(iter(dependency_results.values())).result_contract
            kwargs = {
                **common,
                "product": product,
                "rules": self.rules,
                "trigger_contract": upstream,
                "current_key": entry_index.get(
                    (self._primary_dataset_id(pipeline), "current", product)
                ),
                "comparison_key": entry_index.get(
                    (self._primary_dataset_id(pipeline), "comparison", product)
                ),
            }
        elif pipeline_id == BRAND_MOMENT_PIPELINE_ID:
            contracts = {
                result.result_contract.result_contract_id: result.result_contract
                for result in dependency_results.values()
                if isinstance(result.result_contract, Stage3CResultContractInstance)
            }
            kwargs = {
                **common,
                "delivery": contracts.get("RC_ADVERTISING_BRAND_MOMENT_DELIVERY_WEEKLY"),
                "inventory": contracts.get("RC_INVENTORY_NON_PATCH_PRODUCT_WEEKLY"),
            }
        else:
            current_key = entry_index.get((self._primary_dataset_id(pipeline), "current", product))
            if current_key is None:
                current_key = BusinessKey(
                    run.context.workflow_run_id,
                    self._primary_dataset_id(pipeline),
                    "current",
                    product,
                )
            if pipeline_id == "PL_REVENUE_TECHNICAL_WEEKLY":
                kwargs = {
                    **common,
                    "current_input_key": current_key,
                    "prior_year_input_key": entry_index.get(
                        (self._secondary_dataset_id(pipeline), "prior_year_comparable", product)
                    ),
                }
            elif pipeline_id == "PL_REVENUE_CTV_WEEKLY":
                kwargs = {
                    **common,
                    "current_input_key": current_key,
                    "previous_quarter_primary_input_key": entry_index.get(
                        (self._secondary_dataset_id(pipeline), "previous_quarter_complete", product)
                    ),
                    "previous_quarter_fallback_input_key": self._find_role(
                        entry_index, "previous_quarter_complete", product, exclude=current_key.dataset_id
                    ),
                }
            elif pipeline_id in {
                "PL_REVENUE_SMART_SPEAKER_WEEKLY",
                "PL_REVENUE_FAST_VERSION_WEEKLY",
            }:
                kwargs = {**common, "current_input_key": current_key}
            elif pipeline_id in {
                "PL_INVENTORY_FULL_SITE_WEEKLY",
                "PL_INVENTORY_PATCH_WEEKLY",
                NON_PATCH_PIPELINE_ID,
            }:
                kwargs = {**common, "input_key": current_key, "rules": self.rules}
            else:
                kwargs = {**common, "input_key": current_key}

        method = getattr(executor, "execute", executor if callable(executor) else None)
        if method is None:
            raise Stage3AError("STAGE3D_EXECUTOR_INVALID", f"{pipeline_id} executor is invalid")
        result = method(**kwargs)
        if not isinstance(result, PipelineExecutionResult):
            raise Stage3AError(
                "STAGE3D_EXECUTOR_RESULT_INVALID",
                f"{pipeline_id} did not return PipelineExecutionResult",
            )
        return result

    def _trigger_met(
        self,
        product: str,
        dependency_results: Mapping[tuple[str, str], PipelineExecutionResult],
    ) -> bool:
        result = next(iter(dependency_results.values()))
        contract = result.result_contract
        if not isinstance(contract, Stage3CResultContractInstance):
            return False
        route = self.rules.product_route_by_name[product]
        field_id = {
            "patch": "patch_brand_sell_through_wow_change_pp",
            "non_patch": "non_patch_product_brand_sell_through_wow_change_pp",
            "brand_moment": "sell_through_wow_change_pp",
        }[route]
        field = contract.field(field_id)
        config = self.rules.customer_analysis_by_product[product]
        threshold = config.get("trigger_threshold_pp", 10)
        return (
            field.value_status is ResultValueStatus.VALID_VALUE
            and field.value is not None
            and abs(field.value) >= abs(self._decimal_threshold(threshold))
        )

    @staticmethod
    def _decimal_threshold(value: object):
        from decimal import Decimal

        return Decimal(str(value))

    def _validate_execution_identity(
        self,
        run: RuntimeRun,
        node: Mapping[str, Any],
        result: PipelineExecutionResult,
    ) -> None:
        if (
            result.workflow_run_id != run.context.workflow_run_id
            or result.pipeline_id != node["pipeline_id"]
            or result.pipeline_run_id != node["pipeline_run_id"]
        ):
            raise Stage3AError(
                "STAGE3D_EXECUTION_IDENTITY_MISMATCH",
                "Executor result identity does not match the applicable Pipeline Run",
            )

    def _validate_result_handoff(
        self,
        run: RuntimeRun,
        node: Mapping[str, Any],
        result: PipelineExecutionResult,
    ) -> None:
        contract = result.result_contract
        if contract is None:
            raise Stage3AError(
                "STAGE3D_RESULT_CONTRACT_MISSING",
                "Completed Pipeline Run has no validated Result Contract",
            )
        expected_ids = self.pipelines[str(node["pipeline_id"])]["outputs"]["result_contract_ids"]
        expected_periods = {
            str(run.context.values["reporting_period_id"]),
            f"{run.context.values['reporting_period_start_date']}.."
            f"{run.context.values['reporting_period_end_date']}",
        }
        if (
            contract.result_contract_id not in expected_ids
            or contract.workflow_run_id != run.context.workflow_run_id
            or contract.pipeline_run_id != node["pipeline_run_id"]
            or contract.reporting_period not in expected_periods
            or contract.validation_status != "passed"
            or contract.workflow_reporting_date != run.context.values["workflow_reporting_date"]
        ):
            raise Stage3AError(
                "STAGE3D_RESULT_CONTRACT_HANDOFF_INVALID",
                "Result Contract identity, period, validation, or lineage is not exact",
            )
        if isinstance(contract, Stage3CResultContractInstance):
            if (
                contract.product_parameter != node["product"]
                or contract.cutoff_date != run.context.values["cutoff_date"]
                or contract.report_mode != run.context.values.get("report_mode")
            ):
                raise Stage3AError(
                    "STAGE3D_RESULT_CONTRACT_CONTEXT_MISMATCH",
                    "Stage 3C Result Contract context or product binding is not exact",
                )
        elif node["pipeline_id"] in REVENUE_PIPELINE_IDS and (
            contract.current_revenue_cutoff_date
            != run.context.values.get("current_revenue_cutoff_date")
            or contract.report_mode != run.context.values.get("report_mode")
        ):
            raise Stage3AError(
                "STAGE3D_REVENUE_LINEAGE_MISMATCH",
                "Revenue cutoff_date or report_mode lineage is not exact",
            )

    @staticmethod
    def _is_validated_result(result: PipelineExecutionResult) -> bool:
        return (
            result.execution_status is not PipelineExecutionStatus.BLOCKED
            and result.result_contract is not None
            and result.result_contract.validation_status == "passed"
        )

    @staticmethod
    def _blocked_result(
        run: RuntimeRun,
        node: Mapping[str, Any],
        code: str,
        message: str,
    ) -> PipelineExecutionResult:
        return PipelineExecutionResult(
            run.context.workflow_run_id,
            str(node["pipeline_id"]),
            str(node["pipeline_run_id"]),
            {"product": node["product"]},
            (),
            PipelineExecutionStatus.BLOCKED,
            error_code=code,
            error_message=message,
        )

    @staticmethod
    def _normal_omission(run: RuntimeRun, node: Mapping[str, Any]) -> PipelineExecutionResult:
        return PipelineExecutionResult(
            run.context.workflow_run_id,
            str(node["pipeline_id"]),
            str(node["pipeline_run_id"]),
            {"product": node["product"]},
            (),
            PipelineExecutionStatus.COMPLETED,
            produced_result_contract_reference="normal-omission://trigger-not-met",
        )

    @staticmethod
    def _primary_dataset_id(pipeline: Mapping[str, Any]) -> str:
        dependencies = pipeline.get("dataset_dependencies", ())
        if not dependencies:
            raise Stage3AError(
                "STAGE3D_DATASET_DEPENDENCY_MISSING",
                f"{pipeline.get('pipeline_id')} has no registered Dataset dependency",
            )
        return str(dependencies[0]["dataset_id"])

    @staticmethod
    def _secondary_dataset_id(pipeline: Mapping[str, Any]) -> str:
        dependencies = pipeline.get("dataset_dependencies", ())
        return str(dependencies[1]["dataset_id"]) if len(dependencies) > 1 else "not_applicable"

    def _has_primary_entry(
        self,
        pipeline: Mapping[str, Any],
        entry_index: Mapping[tuple[str, str, str], BusinessKey],
        product: str,
    ) -> bool:
        return (self._primary_dataset_id(pipeline), "current", product) in entry_index

    @staticmethod
    def _find_role(
        entry_index: Mapping[tuple[str, str, str], BusinessKey],
        role: str,
        product: str,
        *,
        exclude: str,
    ) -> BusinessKey | None:
        matches = [
            key
            for (dataset_id, period_role, entry_product), key in entry_index.items()
            if period_role == role and entry_product == product and dataset_id != exclude
        ]
        if len(matches) > 1:
            raise Stage3AError(
                "STAGE3D_INPUT_ROLE_AMBIGUOUS",
                f"More than one explicit {role} Manifest binding is available",
            )
        return matches[0] if matches else None

    @staticmethod
    def _node_label(key: tuple[str, str]) -> str:
        return key[0] if key[1] == "not_applicable" else f"{key[0]}[{key[1]}]"
