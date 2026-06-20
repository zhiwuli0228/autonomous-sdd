"""Path normalization and isolation checks."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import PathSafetyError


def canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def is_within(path: Path, parent: Path) -> bool:
    candidate = os.path.normcase(str(canonical_path(path)))
    boundary = os.path.normcase(str(canonical_path(parent)))
    try:
        return os.path.commonpath([candidate, boundary]) == boundary
    except ValueError:
        return False


def require_separate_trees(project_root: Path, run_root: Path) -> None:
    project = canonical_path(project_root)
    runtime = canonical_path(run_root)
    if project == runtime or is_within(runtime, project):
        raise PathSafetyError(f"Run root must be outside the target project: {runtime}")


def resolve_beneath(root: Path, relative: str | Path) -> Path:
    value = Path(relative)
    if value.is_absolute():
        raise PathSafetyError(f"Absolute paths are not allowed inside a workspace: {value}")
    candidate = canonical_path(canonical_path(root) / value)
    if not is_within(candidate, root):
        raise PathSafetyError(f"Path escapes workspace boundary: {value}")
    return candidate


def relative_to(root: Path, path: Path) -> str:
    try:
        return canonical_path(path).relative_to(canonical_path(root)).as_posix()
    except ValueError as exc:
        raise PathSafetyError(f"Path is outside expected root: {path}") from exc
