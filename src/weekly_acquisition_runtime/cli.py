"""Local Adapter Runner CLI with no scheduler and no Send command."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .adapters import AdapterRegistry
from .config_validation import build_input_binding_registry, load_yaml, validate_composition
from .contracts import (
    AcquisitionMode,
    AttemptManifest,
    BusinessKey,
    LockedRunContext,
    RunInputEntry,
)
from .draft_gate import evaluate_draft_gate
from .errors import AcquisitionError, ProviderCapabilityError
from .storage import LocalRuntimeStorage


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_BUNDLE = (
    ROOT / "phase1_5/assets/execution/weekly_workflow_runtime_contracts_v1_1_candidate.yaml"
)
DEFAULT_EXTENSION = (
    ROOT / "phase1_5/assets/execution/weekly_acquisition_automation_contracts_v1_1_candidate.yaml"
)
DEFAULT_DATASET_INVENTORY = ROOT / "phase1_5/assets/datasets/dataset_inventory.yaml"


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

    draft = sub.add_parser("request-draft-creation")
    draft.add_argument("--workflow-status", required=True)
    draft.add_argument("--runtime-acceptance-passed", action="store_true")
    draft.add_argument("--human-confirmation", action="store_true")
    draft.add_argument("--post-acceptance-auto-draft-owner-approved", action="store_true")
    return parser


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
                extension, load_yaml(args.dataset_inventory)
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
            context = LockedRunContext.lock(local_config["run_context"])
            if context.workflow_run_id != args.workflow_run_id:
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
            if binding.source_id == "SRC_CORP_OUTLOOK_PRIMARY_MAILBOX":
                if entry.source_report_date != context.values["workflow_reporting_date"]:
                    raise ProviderCapabilityError(
                        "Outlook source_report_date does not match the locked workflow_reporting_date"
                    )
                if entry.source_business_data_cutoff_date == "not_applicable":
                    raise ProviderCapabilityError(
                        "Outlook Revenue input requires source_business_data_cutoff_date"
                    )
            LocalRuntimeStorage._safe_component(
                args.acquisition_attempt_id, "acquisition_attempt_id"
            )
            if registry is None:
                raise ProviderCapabilityError(
                    "No Stage 5 Provider is configured; fallback is prohibited"
                )
            adapter = registry.require(args.adapter_id)
            adapter_contract = getattr(adapter, "contract", None)
            if adapter_contract is not None and any(
                (
                    getattr(adapter_contract, "adapter_id", None) != args.adapter_id,
                    getattr(adapter_contract, "dataset_id", None) != args.dataset_id,
                    getattr(adapter_contract, "query_asset_id", None)
                    != args.query_asset_id_or_not_applicable,
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
            acquire_boundary()
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "workflow_run_id": args.workflow_run_id,
                        "acquisition_attempt_id": args.acquisition_attempt_id,
                        "dataset_id": args.dataset_id,
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
