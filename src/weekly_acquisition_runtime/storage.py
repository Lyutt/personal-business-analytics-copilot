"""Local-only immutable Attempt storage outside Git and OneDrive."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .errors import ImmutableArtifactError, StorageBoundaryError


class LocalRuntimeStorage:
    SUBDIRECTORIES = ("manifests", "inputs", "intermediate", "outputs", "diagnostics")

    def __init__(self, root: Path, repository_root: Path) -> None:
        self.root = root.resolve()
        self.repository_root = repository_root.resolve()
        self._validate_root()

    def _validate_root(self) -> None:
        if self.root == self.repository_root or self.repository_root in self.root.parents:
            raise StorageBoundaryError("LOCAL_WORKFLOW_DATA_ROOT must be outside Git")
        if any("onedrive" in part.casefold() for part in self.root.parts):
            raise StorageBoundaryError("LOCAL_WORKFLOW_DATA_ROOT must be outside OneDrive")

    @staticmethod
    def _safe_component(value: str, name: str) -> str:
        if not value or value in {".", ".."} or any(char in value for char in "/\\:"):
            raise StorageBoundaryError(f"Unsafe {name}")
        return value

    def initialize(self) -> None:
        for relative in ("runtime-config", "browser-profiles", "state", "logs", "runs"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)

    def create_attempt(self, workflow_run_id: str, acquisition_attempt_id: str) -> Path:
        run_id = self._safe_component(workflow_run_id, "workflow_run_id")
        attempt_id = self._safe_component(acquisition_attempt_id, "acquisition_attempt_id")
        attempt_root = self.root / "runs" / run_id / "attempts" / attempt_id
        try:
            attempt_root.mkdir(parents=True, exist_ok=False)
            for name in self.SUBDIRECTORIES:
                (attempt_root / name).mkdir()
        except FileExistsError as exc:
            raise ImmutableArtifactError("Acquisition Attempt already exists; overwrite is prohibited") from exc
        return attempt_root

    def opaque_reference(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise StorageBoundaryError("Artifact is outside LOCAL_WORKFLOW_DATA_ROOT") from exc
        return PurePosixPath(relative).as_posix()

    def resolve_opaque_reference(self, reference: str) -> Path:
        pure = PurePosixPath(reference)
        if pure.is_absolute() or ".." in pure.parts:
            raise StorageBoundaryError("Opaque reference is not root-relative")
        resolved = (self.root / Path(*pure.parts)).resolve()
        if self.root not in resolved.parents:
            raise StorageBoundaryError("Opaque reference escapes LOCAL_WORKFLOW_DATA_ROOT")
        return resolved

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def write_json_exclusive(path: Path, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
        except FileExistsError as exc:
            raise ImmutableArtifactError(f"Artifact already exists: {path.name}") from exc

    @staticmethod
    def copy_stream_exclusive(source: BinaryIO, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as output:
                shutil.copyfileobj(source, output)
        except FileExistsError as exc:
            raise ImmutableArtifactError(f"Input already exists: {target.name}") from exc
