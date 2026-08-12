"""Local Adapter Runner CLI with no scheduler and no Send command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters import AdapterRegistry
from .config_validation import load_yaml, validate_composition
from .contracts import AttemptManifest
from .draft_gate import evaluate_draft_gate
from .errors import AcquisitionError, ProviderCapabilityError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="weekly-acquisition-runner")
    sub = parser.add_subparsers(dest="command", required=True)

    config = sub.add_parser("validate-config")
    config.add_argument("--runtime-bundle", type=Path, required=True)
    config.add_argument("--extension", type=Path, required=True)

    attempt = sub.add_parser("validate-attempt")
    attempt.add_argument("--manifest", type=Path, required=True)

    acquire = sub.add_parser("acquire-one-explicit-binding")
    acquire.add_argument("--adapter-id", required=True)

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
            if registry is None:
                raise ProviderCapabilityError(
                    "No Stage 5 Provider is configured; fallback is prohibited"
                )
            registry.require(args.adapter_id)
            raise ProviderCapabilityError("Adapter execution requires an explicitly configured Stage 5 Provider")
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
