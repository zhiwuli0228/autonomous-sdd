"""Read-only repository inspection primitives."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import RepositoryError
from .models import RepositoryStatus
from .paths import canonical_path, relative_to


class Repository:
    def __init__(self, root: Path):
        self._root = canonical_path(root)
        if not self._root.is_dir():
            raise RepositoryError(f"Project directory does not exist: {self._root}")
        if self._run_git("rev-parse", "--is-inside-work-tree", check=False).returncode != 0:
            raise RepositoryError(f"Project is not a Git worktree: {self._root}")

    @property
    def root(self) -> Path:
        return self._root

    def head(self) -> str | None:
        result = self._run_git("rev-parse", "--verify", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def branch(self) -> str | None:
        result = self._run_git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def git_common_dir(self) -> Path:
        result = self._run_git("rev-parse", "--git-common-dir")
        value = Path(result.stdout.strip())
        if not value.is_absolute():
            value = self._root / value
        return canonical_path(value)

    def relative_path(self, path: Path) -> str:
        return relative_to(self._root, path)

    def status(self) -> RepositoryStatus:
        result = self._run_git("status", "--porcelain=v2", "-z", "--untracked-files=all")
        staged: set[str] = set()
        unstaged: set[str] = set()
        untracked: set[str] = set()
        conflicted: set[str] = set()
        records = result.stdout.split("\0")
        index = 0
        while index < len(records):
            record = records[index]
            index += 1
            if not record:
                continue
            kind = record[0]
            if kind == "?":
                untracked.add(record[2:])
                continue
            if kind == "u":
                conflicted.add(record.rsplit(" ", 1)[-1])
                continue
            if kind not in {"1", "2"}:
                continue
            fields = record.split(" ")
            xy = fields[1]
            path = fields[-1]
            if xy[0] != ".":
                staged.add(path)
            if xy[1] != ".":
                unstaged.add(path)
            if kind == "2" and index < len(records):
                index += 1
        return RepositoryStatus(
            staged=tuple(sorted(staged)),
            unstaged=tuple(sorted(unstaged)),
            untracked=tuple(sorted(untracked)),
            conflicted=tuple(sorted(conflicted)),
        )

    def tracked_paths(self) -> tuple[str, ...]:
        return self._path_list("ls-files", "-z")

    def untracked_paths(self) -> tuple[str, ...]:
        return self._path_list("ls-files", "--others", "--exclude-standard", "-z")

    def _path_list(self, *args: str) -> tuple[str, ...]:
        result = self._run_git(*args)
        if not result.stdout:
            return ()
        return tuple(sorted(path for path in result.stdout.split("\0") if path))

    def _run_git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=self._root,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        if check and result.returncode != 0:
            raise RepositoryError(
                f"Git command failed ({result.returncode}): git {' '.join(args)}\n{result.stderr.strip()}"
            )
        return result
