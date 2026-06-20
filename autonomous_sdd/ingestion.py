"""Input repository isolation and baseline capture."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .errors import WorkspaceError
from .models import InputWorkspaceSnapshot
from .paths import canonical_path, resolve_beneath
from .repository import Repository


BASELINE_COMMIT_MESSAGE = "chore: capture competition input baseline"

def prepare_input_workspace(source_root: Path, work_root: Path, run_id: str) -> InputWorkspaceSnapshot:
    source = Repository(source_root)
    target_root = canonical_path(work_root)
    if target_root.exists():
        raise WorkspaceError(f"Work directory already exists: {target_root}")
    target_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        _git(
            source.root,
            "clone",
            "--local",
            "--no-hardlinks",
            "--",
            str(source.root),
            str(target_root),
        )
        _overlay_tree(source, target_root)
        _copy_tree(source.root / ".sdd" / "baseline", target_root / ".sdd" / "baseline")
        _copy_tree(source.root / ".sdd" / "bin", target_root / ".sdd" / "bin")
        branch_name = f"sdd/run-{run_id}"
        _git(target_root, "config", "user.name", "Autonomous SDD")
        _git(target_root, "config", "user.email", "autonomous-sdd@localhost")
        _git(target_root, "add", "--all")
        _git(target_root, "commit", "--allow-empty", "-m", BASELINE_COMMIT_MESSAGE)
        _git(target_root, "checkout", "-B", branch_name)
        baseline_commit = _git(target_root, "rev-parse", "HEAD").strip()
        status = source.status()
        _assert_clean_worktree(target_root)
        return InputWorkspaceSnapshot(
            source_root=source.root,
            work_root=target_root,
            source_head=source.head(),
            baseline_commit=baseline_commit,
            run_branch=branch_name,
            source_status=status,
        )
    except Exception:
        shutil.rmtree(target_root.parent.parent, ignore_errors=True)
        raise


def _overlay_tree(source: Repository, target_root: Path) -> None:
    source_root = source.root
    tracked = set(source.tracked_paths())
    untracked = set(source.untracked_paths())
    overlay_paths = sorted(tracked | untracked)
    for relative in overlay_paths:
        source_path = canonical_path(source_root / Path(relative))
        target_path = resolve_beneath(target_root, relative)
        if source_path.is_dir() and not source_path.is_symlink():
            continue
        if source_path.exists() or source_path.is_symlink():
            _copy_entry(source_path, target_path)
        else:
            _remove_entry(target_path)
    _remove_missing_tracked_entries(source_root, target_root, tracked)


def _remove_missing_tracked_entries(source_root: Path, target_root: Path, tracked: set[str]) -> None:
    for relative in tracked:
        source_path = canonical_path(source_root / Path(relative))
        if source_path.exists() or source_path.is_symlink():
            continue
        _remove_entry(resolve_beneath(target_root, relative))


def _copy_entry(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.is_symlink():
        if target_path.exists() or target_path.is_symlink():
            _remove_entry(target_path)
        link_target = os.readlink(source_path)
        try:
            target_path.symlink_to(link_target, target_is_directory=source_path.is_dir())
        except (NotImplementedError, OSError) as exc:
            raise WorkspaceError(f"Cannot recreate symlink in worktree: {source_path}") from exc
        return
    shutil.copy2(source_path, target_path)


def _remove_entry(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_tree(source_root: Path, target_root: Path) -> None:
    if not source_root.exists():
        return
    for path in source_root.rglob("*"):
        relative = path.relative_to(source_root)
        destination = target_root / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            if destination.exists() or destination.is_symlink():
                _remove_entry(destination)
            destination.symlink_to(os.readlink(path), target_is_directory=path.is_dir())
        else:
            shutil.copy2(path, destination)


def _assert_clean_worktree(root: Path) -> None:
    result = _git(root, "status", "--porcelain")
    if result.strip():
        raise WorkspaceError("Isolated worktree is not clean after baseline capture")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        raise WorkspaceError(
            f"Git command failed ({result.returncode}): git {' '.join(args)}\n{result.stderr.strip()}"
        )
    return result.stdout.strip()
