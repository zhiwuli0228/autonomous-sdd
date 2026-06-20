"""Small immutable data models shared by infrastructure modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ArtifactRef:
    namespace: Literal["project", "run"]
    path: str
    sha256: str | None = None

    def to_dict(self) -> dict[str, str]:
        value = {"namespace": self.namespace, "path": self.path}
        if self.sha256 is not None:
            value["sha256"] = self.sha256
        return value


@dataclass(frozen=True)
class RepositoryStatus:
    staged: tuple[str, ...] = ()
    unstaged: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    conflicted: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not (self.staged or self.unstaged or self.untracked or self.conflicted)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "staged": list(self.staged),
            "unstaged": list(self.unstaged),
            "untracked": list(self.untracked),
            "conflicted": list(self.conflicted),
        }


@dataclass(frozen=True)
class RunContext:
    project_root: Path
    run_root: Path
    run_id: str

    @property
    def run_dir(self) -> Path:
        return self.run_root / self.run_id

    @property
    def work_dir(self) -> Path:
        return self.run_dir / "work"


@dataclass(frozen=True)
class InputWorkspaceSnapshot:
    source_root: Path
    work_root: Path
    source_head: str | None
    baseline_commit: str
    run_branch: str
    source_status: RepositoryStatus

    def to_dict(self) -> dict[str, object]:
        return {
            "source_root": str(self.source_root),
            "work_root": str(self.work_root),
            "source_head": self.source_head,
            "baseline_commit": self.baseline_commit,
            "run_branch": self.run_branch,
            "source_status": self.source_status.to_dict(),
        }
