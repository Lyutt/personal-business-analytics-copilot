"""Local Adapter Runner CLI with no scheduler and no Send command."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping

from .adapters import AdapterRegistry, AdapterResult, QueryContract
from .config_validation import (
    build_input_binding_registry,
    load_yaml,
    validate_composition,
)
from .contracts import (
    AcquisitionMode,
    AttemptManifest,
    BusinessKey,
    LockedRunContext,
    RunInputEntry,
)
from .draft_gate import evaluate_draft_gate
from .errors import (
    AcquisitionError,
    BrowserLockOccupied,
    PageContractDrift,
    ProviderCapabilityError,
    SessionRecoveryFailed,
)
from .runtime import AcquisitionRuntime
from .storage import LocalRuntimeStorage

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_BUNDLE = (
    ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1_1_candidate.yaml"
)
DEFAULT_EXTENSION = (
    ROOT / "phase1_5/assets/execution/weekly_acquisition_automation_contracts_v1_1_candidate.yaml"
)
DEFAULT_DATASET_INVENTORY = ROOT / "phase1_5/assets/datasets/dataset_inventory.yaml"
DEFAULT_PIPELINE_REGISTRY = ROOT / "phase1_5/assets/pipelines/pipeline_registry.yaml"
BROWSER_ACQUISITION_SOURCE_IDS = {
    "SRC_INTERNAL_PLATFORM_APOLLO",
    "SRC_INTERNAL_PLATFORM_NOVABI",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weekly-acquisition-runner")
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("validate-config")
    config.add_argument("--runtime-bundle", type=Path, required=True)
    config.add_argument("--extension", type=Path, required=True)

    attempt = sub.add_parser("validate-attempt")
    attempt.add_argument("--manifest", type=Path, required=True)

    acquire = sub.add_parser("acquire-one-explicit-binding")
    acquire.add_argument("--workflow-id", required=True)
    acquire.add_argument("--workflow-run-id", required=True)
    acquire.add_argument("--acquisition-attempt-id", required=True)
    acquire.add_argument("--adapter-id", required=True)
    acquire.add_argument("--dataset-id", required=True)
    acquire.add_argument("--query-asset-id-or-not-applicable", required=True)
    acquire.add_argument("--local-runtime-config-reference", required=True)
    acquire.add_argument("--runtime-bundle", type=Path, default=DEFAULT_RUNTIME_BUNDLE)
    acquire.add_argument("--extension", type=Path, default=DEFAULT_EXTENSION)
    acquire.add_argument("--dataset-inventory", type=Path, default=DEFAULT_DATASET_INVENTORY)
    acquire.add_argument("--pipeline-registry", type=Path, default=DEFAULT_PIPELINE_REGISTRY)

    draft = sub.add_parser("request-draft-creation")
    draft.add_argument("--workflow-status", required=True)
    draft.add_argument("--runtime-acceptance-passed", action="store_true")
    draft.add_argument("--human-confirmation", action="store_true")
    draft.add_argument("--post-acceptance-auto-draft-owner-approved", action="store_true")
    return parser


def _authoritative_query_parameters(
    *,
    contract: QueryContract,
    context: LockedRunContext,
    entry: RunInputEntry,
    local_config: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve every query parameter from an explicit allowed authority."""

    bindings = local_config.get("query_parameter_bindings")
    if not isinstance(bindings, Mapping):
        raise ProviderCapabilityError(
            "query_parameter_bindings is required; implicit Adapter parameters are prohibited"
        )
    expected_names = set(contract.parameters)
    if set(bindings) != expected_names:
        raise ProviderCapabilityError(
            "query_parameter_bindings must exactly cover QueryContract.parameters"
        )
    manifest_values = {
        **entry.business_key.as_dict(),
        "dataset_version": entry.dataset_version,
        "query_asset_id_or_not_applicable": entry.query_asset_binding.get(
            "query_asset_id", "not_applicable"
        ),
        "source_report_date": entry.source_report_date,
        "source_business_data_cutoff_date": entry.source_business_data_cutoff_date,
    }
    if "query_parameter_values" in local_config:
        raise ProviderCapabilityError(
            "Local Runtime Config cannot provide Query parameter values"
        )
    authorities: dict[str, Mapping[str, Any]] = {
        "locked_run_context": context.values,
        "run_input_manifest": manifest_values,
    }
    resolved: dict[str, Any] = {}
    for parameter_name in sorted(expected_names):
        specification = bindings[parameter_name]
        if not isinstance(specification, Mapping) or set(specification) != {
            "authority",
            "field",
        }:
            raise ProviderCapabilityError(
                f"Query parameter {parameter_name} requires exact authority and field binding"
            )
        authority = specification["authority"]
        field = specification["field"]
        if not isinstance(authority, str) or authority not in authorities:
            raise ProviderCapabilityError(
                f"Query parameter {parameter_name} uses an unsupported authority"
            )
        if not isinstance(field, str) or field not in authorities[authority]:
            raise ProviderCapabilityError(
                f"Query parameter {parameter_name} field is not explicitly bound"
            )
        value = authorities[authority][field]
        if value is None:
            raise ProviderCapabilityError(
                f"Query parameter {parameter_name} cannot resolve to null"
            )
        resolved[parameter_name] = value
    if resolved != dict(contract.parameters):
        raise ProviderCapabilityError(
            "QueryContract.parameters do not match the locked Context and explicit Manifest/config binding"
        )
    return resolved


def _automated_failure_code(error: Exception, default: str) -> str:
    if isinstance(error, BrowserLockOccupied):
        return "BROWSER_ACQUISITION_LOCK_OCCUPIED"
    if isinstance(error, PageContractDrift):
        return "ADAPTER_PAGE_CONTRACT_DRIFT"
    if isinstance(error, SessionRecoveryFailed):
        return "SESSION_RECOVERY_FAILED"
    return default


def main(argv: list[str] | None = None, registry: AdapterRegistry | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate-config":
            validate_composition(load_yaml(args.runtime_bundle), load_yaml(args.extension))
            print(json.dumps({"status": "passed"}))
        elif args.command == "validate-attempt":
            value = json.loads(args.manifest.read_text(encoding="utf-8"))
            manifest = AttemptManifest.from_dict(value)
            manifest.require_passed()
            print(json.dumps({"status": "passed", "acquisition_attempt_id": manifest.acquisition_attempt_id}))
        elif args.command == "acquire-one-explicit-binding":
            runtime_bundle = load_yaml(args.runtime_bundle)
            extension = load_yaml(args.extension)
            validate_composition(runtime_bundle, extension)
            input_registry = build_input_binding_registry(
                extension,
                load_yaml(args.dataset_inventory),
                load_yaml(args.pipeline_registry),
            )
            local_root = os.environ.get("LOCAL_WORKFLOW_DATA_ROOT_LOCAL_ONLY")
            if not local_root:
                raise ProviderCapabilityError(
                    "LOCAL_WORKFLOW_DATA_ROOT_LOCAL_ONLY is not configured; fallback is prohibited"
                )
            storage = LocalRuntimeStorage(Path(local_root), ROOT)
            config_path = storage.resolve_opaque_reference(
                args.local_runtime_config_reference
            )
            if not config_path.is_file():
                raise ProviderCapabilityError(
                    "local_runtime_config_reference does not resolve to a local config file"
                )
            local_config = json.loads(config_path.read_text(encoding="utf-8"))
            runtime = AcquisitionRuntime(storage, input_registry)
            run = runtime.start_run(local_config["run_context"])
            if run.context.workflow_run_id != args.workflow_run_id:
                raise ProviderCapabilityError(
                    "workflow_run_id does not match the locked local Run Context"
                )
            key = BusinessKey(
                workflow_run_id=args.workflow_run_id,
                dataset_id=args.dataset_id,
                period_role=local_config["period_role"],
                product_parameter=local_config["product_parameter"],
            )
            binding = input_registry.validate_request(
                workflow_id=args.workflow_id,
                adapter_id=args.adapter_id,
                dataset_id=args.dataset_id,
                query_asset_id_or_not_applicable=(
                    args.query_asset_id_or_not_applicable
                ),
                product_parameter=key.product_parameter,
            )
            entry = RunInputEntry(
                business_key=key,
                dataset_version=local_config["dataset_version"],
                query_asset_binding=(
                    {
                        "binding_status": "not_applicable",
                    }
                    if binding.query_asset_id_or_not_applicable == "not_applicable"
                    else {
                        "binding_status": "bound",
                        "query_asset_id": binding.query_asset_id_or_not_applicable,
                    }
                ),
                local_input_reference="not_applicable",
                source_report_date=local_config["source_report_date"],
                source_business_data_cutoff_date=local_config[
                    "source_business_data_cutoff_date"
                ],
                acquisition_mode=AcquisitionMode.AUTOMATED,
            )
            entry.validate(input_registry, require_attempt_binding=False)
            runtime.declare_input(run, entry)
            LocalRuntimeStorage._safe_component(
                args.acquisition_attempt_id, "acquisition_attempt_id"
            )
            if registry is None:
                raise ProviderCapabilityError(
                    "No Stage 5 Provider is configured; fallback is prohibited"
                )
            adapter = registry.require(args.adapter_id)
            attempt_root = runtime.create_attempt(
                run, key, args.acquisition_attempt_id
            )
            started = datetime.now(timezone.utc)
            started_clock = perf_counter()
            adapter_contract = getattr(adapter, "contract", None)
            browser_lock = None
            failure_code = "ADAPTER_CONTRACT_VALIDATION_FAILED"
            try:
                if not isinstance(adapter_contract, QueryContract) or any(
                    (
                        adapter_contract.adapter_id != args.adapter_id,
                        adapter_contract.dataset_id != args.dataset_id,
                        adapter_contract.query_asset_id
                        != args.query_asset_id_or_not_applicable,
                        adapter_contract.source_id != binding.source_id,
                    )
                ):
                    raise ProviderCapabilityError(
                        "Configured Adapter contract does not match the explicit binding; fallback is prohibited"
                    )
                acquire_boundary = getattr(adapter, "acquire", None)
                if not callable(acquire_boundary):
                    raise ProviderCapabilityError(
                        "Configured Adapter does not expose the acquisition boundary; fallback is prohibited"
                    )
                failure_code = "QUERY_PARAMETER_AUTHORITY_VALIDATION_FAILED"
                _authoritative_query_parameters(
                    contract=adapter_contract,
                    context=run.context,
                    entry=entry,
                    local_config=local_config,
                )
                if binding.source_id in BROWSER_ACQUISITION_SOURCE_IDS:
                    browser_lock = runtime.browser_lock()
                    failure_code = "BROWSER_ACQUISITION_LOCK_OCCUPIED"
                    browser_lock.acquire(
                        {
                            "workflow_run_id": args.workflow_run_id,
                            "acquisition_attempt_id": args.acquisition_attempt_id,
                            "adapter_id": args.adapter_id,
                            "acquired_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                failure_code = "ADAPTER_ACQUISITION_FAILED"
                result = acquire_boundary()
                completed = datetime.now(timezone.utc)
                failure_code = "ADAPTER_RESULT_VALIDATION_FAILED"
                if not isinstance(result, AdapterResult):
                    raise ProviderCapabilityError(
                        "Configured Adapter returned no valid acquisition result; fallback is prohibited"
                    )
                manifest, manifest_reference = runtime.persist_automated_result(
                    run=run,
                    business_key=key,
                    attempt_id=args.acquisition_attempt_id,
                    attempt_root=attempt_root,
                    query_contract=adapter_contract,
                    result=result,
                    started_at=started.isoformat(),
                    completed_at=completed.isoformat(),
                    duration_ms=max(0, round((perf_counter() - started_clock) * 1000)),
                )
            except Exception as exc:
                completed = datetime.now(timezone.utc)
                error_code = _automated_failure_code(exc, failure_code)
                runtime.persist_failed_automated_attempt(
                    run=run,
                    business_key=key,
                    attempt_id=args.acquisition_attempt_id,
                    attempt_root=attempt_root,
                    error_code=error_code,
                    started_at=started.isoformat(),
                    completed_at=completed.isoformat(),
                    duration_ms=max(0, round((perf_counter() - started_clock) * 1000)),
                    query_contract=(
                        adapter_contract
                        if isinstance(adapter_contract, QueryContract)
                        else None
                    ),
                )
                raise ProviderCapabilityError(
                    f"Automated acquisition blocked with {error_code}; fallback is prohibited"
                ) from exc
            finally:
                if browser_lock is not None:
                    browser_lock.release()
            runtime.bind_successful_attempt(
                run, key, manifest, manifest_reference
            )
            run_manifest_reference = runtime.finalize_run_input_manifest(run)
            runtime.consume_bound_input(run, key)
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "workflow_run_id": args.workflow_run_id,
                        "acquisition_attempt_id": args.acquisition_attempt_id,
                        "dataset_id": args.dataset_id,
                        "attempt_manifest_reference": manifest_reference,
                        "run_input_manifest_reference": run_manifest_reference,
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "request-draft-creation":
            decision = evaluate_draft_gate(
                workflow_status=args.workflow_status,
                runtime_acceptance_passed=args.runtime_acceptance_passed,
                human_confirmation=args.human_confirmation,
                post_acceptance_auto_draft_owner_approved=(
                    args.post_acceptance_auto_draft_owner_approved
                ),
            )
            print(json.dumps(decision.__dict__, sort_keys=True))
        return 0
    except (AcquisitionError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
