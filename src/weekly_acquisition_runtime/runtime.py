"""Deterministic Run Context -> Attempt -> Manifest -> binding runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping

from .adapters import AdapterResult, QueryContract
from .browser_lock import BrowserAcquisitionLock
from .contracts import (
    AcquisitionMode,
    AttemptManifest,
    BusinessKey,
    InputBindingRegistry,
    LockedRunContext,
    RunInputEntry,
    RunInputManifestBuilder,
)
from .errors import ContractViolation, UnboundInputError
from .storage import LocalRuntimeStorage


@dataclass
class RuntimeRun:
    context: LockedRunContext
    run_input_manifest: RunInputManifestBuilder


class AcquisitionRuntime:
    """Minimum runtime core implementing only the frozen 1.1.0 chain."""

    def __init__(self, storage: LocalRuntimeStorage, input_binding_registry: InputBindingRegistry) -> None:
        self.storage = storage
        self.input_binding_registry = input_binding_registry
        self.storage.initialize()

    def start_run(self, context_values: Mapping[str, Any]) -> RuntimeRun:
        context = LockedRunContext.lock(context_values)
        return RuntimeRun(
            context,
            RunInputManifestBuilder(context.workflow_run_id, self.input_binding_registry),
        )

    def declare_input(self, run: RuntimeRun, entry: RunInputEntry) -> None:
        if entry.business_key.workflow_run_id != run.context.workflow_run_id:
            raise ContractViolation("Input entry does not belong to the locked Run Context")
        self._validate_source_date_binding(run, entry)
        run.run_input_manifest.add_entry(entry)

    def create_attempt(self, run: RuntimeRun, business_key: BusinessKey, attempt_id: str) -> Path:
        self._require_key_in_run(run, business_key)
        return self.storage.create_attempt(run.context.workflow_run_id, attempt_id)

    def browser_lock(self) -> BrowserAcquisitionLock:
        return BrowserAcquisitionLock(self.storage.root / "state")

    def ingest_manual_fallback(
        self,
        *,
        run: RuntimeRun,
        business_key: BusinessKey,
        attempt_id: str,
        filename: str,
        source: BinaryIO,
        manifest_fields: Mapping[str, Any],
    ) -> tuple[AttemptManifest, str]:
        entry = run.run_input_manifest.get_entry(business_key)
        if entry.acquisition_mode is not AcquisitionMode.MANUAL_FALLBACK:
            raise ContractViolation("Manual fallback requires acquisition_mode=manual_fallback")
        attempt_root = self.create_attempt(run, business_key, attempt_id)
        target = attempt_root / "inputs" / LocalRuntimeStorage._safe_component(filename, "filename")
        self.storage.copy_stream_exclusive(source, target)
        input_reference = self.storage.opaque_reference(target)
        values = dict(manifest_fields)
        values.update(
            {
                "business_key": business_key,
                "acquisition_attempt_id": attempt_id,
                "acquisition_mode": AcquisitionMode.MANUAL_FALLBACK,
                "local_input_opaque_reference": input_reference,
                "sha256": self.storage.sha256(target),
            }
        )
        manifest = AttemptManifest(**values)
        return manifest, self.persist_attempt_manifest(attempt_root, manifest)

    def persist_attempt_manifest(self, attempt_root: Path, manifest: AttemptManifest) -> str:
        manifest_path = attempt_root / "manifests" / "attempt_manifest.json"
        self.storage.write_json_exclusive(manifest_path, manifest.as_dict())
        return self.storage.opaque_reference(manifest_path)

    def persist_automated_result(
        self,
        *,
        run: RuntimeRun,
        business_key: BusinessKey,
        attempt_id: str,
        attempt_root: Path,
        query_contract: QueryContract,
        result: AdapterResult,
        started_at: str,
        completed_at: str,
        duration_ms: int,
    ) -> tuple[AttemptManifest, str]:
        """Validate and persist one automated result under its pre-created Attempt."""

        entry = run.run_input_manifest.get_entry(business_key)
        if entry.acquisition_mode is not AcquisitionMode.AUTOMATED:
            raise ContractViolation("Automated result requires acquisition_mode=automated")
        registered = self.input_binding_registry.require(business_key.dataset_id)
        expected_attempt_root = (
            self.storage.root
            / "runs"
            / run.context.workflow_run_id
            / "attempts"
            / attempt_id
        ).resolve()
        if attempt_root.resolve() != expected_attempt_root:
            raise ContractViolation("Automated result Attempt path does not match the explicit Attempt ID")
        if any(
            (
                query_contract.adapter_id != registered.adapter_id,
                query_contract.source_id != registered.source_id,
                query_contract.dataset_id != business_key.dataset_id,
                query_contract.query_asset_id
                != registered.query_asset_id_or_not_applicable,
            )
        ):
            raise ContractViolation("Query Contract does not match the explicit Dataset binding")
        if dict(result.normalized_parameter_readback) != dict(query_contract.parameters):
            raise ContractViolation("Automated result parameter readback is not an exact match")
        if tuple(result.columns) != tuple(query_contract.expected_columns):
            raise ContractViolation("Automated result schema is not an exact match")

        input_path = attempt_root / "inputs" / "automated_result.json"
        self.storage.write_json_exclusive(
            input_path,
            {
                "columns": list(result.columns),
                "rows": [dict(row) for row in result.rows],
            },
        )
        input_reference = self.storage.opaque_reference(input_path)
        schema_payload = json.dumps(
            list(result.columns), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        manifest = AttemptManifest(
            business_key=business_key,
            acquisition_attempt_id=attempt_id,
            acquisition_mode=AcquisitionMode.AUTOMATED,
            adapter_id=query_contract.adapter_id,
            adapter_version=query_contract.adapter_version,
            provider_id=query_contract.provider_id,
            query_asset_id_or_not_applicable=query_contract.query_asset_id,
            normalized_parameter_readback=dict(result.normalized_parameter_readback),
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            session_status_code=result.session_status_code,
            local_input_opaque_reference=input_reference,
            sha256=self.storage.sha256(input_path),
            row_count_or_not_applicable=len(result.rows),
            schema_fingerprint_or_not_applicable=hashlib.sha256(schema_payload).hexdigest(),
            page_contract_version_or_not_applicable=query_contract.page_contract_version,
            validation_status="passed",
        )
        manifest.require_passed()
        return manifest, self.persist_attempt_manifest(attempt_root, manifest)

    def bind_successful_attempt(
        self,
        run: RuntimeRun,
        business_key: BusinessKey,
        manifest: AttemptManifest,
        manifest_reference: str,
    ) -> None:
        persisted = self.load_attempt_manifest(manifest_reference)
        if persisted.as_dict() != manifest.as_dict():
            raise ContractViolation("Persisted Attempt Manifest does not match the binding candidate")
        run.run_input_manifest.bind_successful_attempt(
            business_key, persisted, manifest_reference
        )

    def consume_bound_input(self, run: RuntimeRun, business_key: BusinessKey) -> Path:
        entry = run.run_input_manifest.get_entry(business_key)
        entry.validate(self.input_binding_registry)
        binding = entry.acquisition_attempt_binding
        if binding is None:
            if entry.acquisition_mode is AcquisitionMode.LEGACY_PREPARED_LOCAL_INPUT:
                return self.storage.resolve_opaque_reference(entry.local_input_reference)
            raise UnboundInputError("Pipeline input has no explicit successful Attempt binding")
        manifest = self.load_attempt_manifest(binding.attempt_manifest_reference)
        manifest.require_passed()
        if manifest.business_key != business_key:
            raise ContractViolation("Bound Attempt Manifest business key mismatch")
        if manifest.acquisition_attempt_id != binding.acquisition_attempt_id:
            raise ContractViolation("Bound Attempt ID mismatch")
        if manifest.local_input_opaque_reference != entry.local_input_reference:
            raise ContractViolation("Run Input entry does not reference the bound Attempt input")
        input_path = self.storage.resolve_opaque_reference(manifest.local_input_opaque_reference)
        if not input_path.is_file():
            raise ContractViolation("Bound Attempt input does not exist")
        if self.storage.sha256(input_path) != manifest.sha256.lower():
            raise ContractViolation("Bound Attempt input SHA-256 does not match the Attempt Manifest")
        return input_path

    def finalize_run_input_manifest(self, run: RuntimeRun) -> str:
        value = run.run_input_manifest.finalize()
        path = self.storage.root / "runs" / run.context.workflow_run_id / "run_input_manifest.json"
        self.storage.write_json_exclusive(path, value)
        return self.storage.opaque_reference(path)

    def load_attempt_manifest(self, reference: str) -> AttemptManifest:
        path = self.storage.resolve_opaque_reference(reference)
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return AttemptManifest.from_dict(value)

    @staticmethod
    def _require_key_in_run(run: RuntimeRun, business_key: BusinessKey) -> None:
        if business_key.workflow_run_id != run.context.workflow_run_id:
            raise ContractViolation("Attempt business key does not belong to the locked Run Context")
        run.run_input_manifest.get_entry(business_key)

    def _validate_source_date_binding(self, run: RuntimeRun, entry: RunInputEntry) -> None:
        registered = self.input_binding_registry.require(entry.business_key.dataset_id)
        if registered.source_id == "SRC_CORP_OUTLOOK_PRIMARY_MAILBOX":
            if entry.source_report_date != run.context.values["workflow_reporting_date"]:
                raise ContractViolation(
                    "Outlook source_report_date must equal the locked workflow_reporting_date"
                )
            if entry.source_business_data_cutoff_date == "not_applicable":
                raise ContractViolation(
                    "Outlook Revenue input requires source_business_data_cutoff_date"
                )
