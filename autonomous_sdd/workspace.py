"""External run workspace creation and durable state storage."""

from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

from .errors import WorkspaceError
from .ingestion import prepare_input_workspace
from .models import ArtifactRef, InputWorkspaceSnapshot, RunContext
from .paths import canonical_path, relative_to, require_separate_trees, resolve_beneath
from .serialization import append_jsonl, read_json, sha256_file, write_json_atomic


RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


def default_run_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return canonical_path(base / "AutonomousSDD" / "runs")
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return canonical_path(base / "autonomous-sdd" / "runs")


def generate_run_id() -> str:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def create_run_context(
    project: Path,
    run_root: Path | None = None,
    run_id: str | None = None,
) -> RunContext:
    project_root = canonical_path(project)
    if not project_root.is_dir():
        raise WorkspaceError(f"Project directory does not exist: {project_root}")
    resolved_run_root = canonical_path(run_root or default_run_root())
    require_separate_trees(project_root, resolved_run_root)
    selected_run_id = run_id or generate_run_id()
    if not RUN_ID_PATTERN.fullmatch(selected_run_id):
        raise WorkspaceError(f"Invalid run ID: {selected_run_id}")
    return RunContext(project_root=project_root, run_root=resolved_run_root, run_id=selected_run_id)


class RunWorkspace:
    def __init__(self, context: RunContext):
        self.context = context

    @property
    def run_dir(self) -> Path:
        return self.context.run_dir

    @property
    def metadata_path(self) -> Path:
        return self.run_dir / "metadata.json"

    @property
    def state_path(self) -> Path:
        return self.run_dir / "state.json"

    @property
    def journal_path(self) -> Path:
        return self.run_dir / "journal.jsonl"

    @property
    def work_root(self) -> Path:
        return self.context.work_dir

    @property
    def work_project_root(self) -> Path:
        return self.work_root / "project"

    def initialize(self, runner_version: str) -> InputWorkspaceSnapshot:
        if self.metadata_path.exists():
            raise WorkspaceError(f"Run directory already initialized: {self.run_dir}")
        snapshot = prepare_input_workspace(self.context.project_root, self.work_project_root, self.context.run_id)
        metadata = {
            "schema_version": 1,
            "run_id": self.context.run_id,
            "project_root": str(self.context.project_root),
            "work_project_root": str(self.work_project_root),
            "run_root": str(self.context.run_root),
            "git_common_dir": str(snapshot.work_root / ".git"),
            "source_head": snapshot.source_head,
            "baseline_commit": snapshot.baseline_commit,
            "run_branch": snapshot.run_branch,
            "source_status": snapshot.source_status.to_dict(),
            "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "runner_version": runner_version,
            "host": {"platform": platform.system().lower(), "python": sys.version.split()[0]},
        }
        write_json_atomic(self.metadata_path, metadata, overwrite=False)
        return snapshot

    def load_metadata(self) -> dict[str, Any]:
        metadata = read_json(self.metadata_path)
        if metadata.get("run_id") != self.context.run_id:
            raise WorkspaceError("Run metadata does not match the run directory")
        if canonical_path(Path(str(metadata.get("project_root", "")))) != self.context.project_root:
            raise WorkspaceError("Run metadata points to a different project")
        if canonical_path(Path(str(metadata.get("work_project_root", "")))) != self.work_project_root:
            raise WorkspaceError("Run metadata points to a different work project")
        return metadata

    def load_state(self) -> dict[str, Any]:
        return read_json(self.state_path)

    def save_state(self, state: Mapping[str, Any]) -> None:
        write_json_atomic(self.state_path, dict(state))

    def append_event(self, event: Mapping[str, Any]) -> int:
        state_sequence = 0
        if self.state_path.exists():
            state_sequence = int(self.load_state().get("sequence", 0))
        journal_sequence = 0
        if self.journal_path.exists():
            lines = self.journal_path.read_text(encoding="utf-8").splitlines()
            if lines:
                try:
                    journal_sequence = int(json.loads(lines[-1]).get("sequence", 0))
                except (json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
                    raise WorkspaceError(f"Invalid journal tail: {self.journal_path}") from exc
        sequence = max(state_sequence, journal_sequence) + 1
        append_jsonl(
            self.journal_path,
            {
                "sequence": sequence,
                "at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
                **dict(event),
            },
        )
        return sequence

    def write_json(self, relative: str, value: Any) -> Path:
        path = resolve_beneath(self.run_dir, relative)
        write_json_atomic(path, value)
        return path

    def read_json(self, relative: str) -> dict[str, Any]:
        return read_json(resolve_beneath(self.run_dir, relative))

    def write_evidence(self, category: str, name: str, content: str) -> ArtifactRef:
        path = resolve_beneath(self.run_dir, Path("evidence") / category / f"{name}.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        return ArtifactRef(namespace="run", path=relative_to(self.run_dir, path), sha256=sha256_file(path))
