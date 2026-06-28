"""Runtime service composition for an external hosted SDD run."""

from __future__ import annotations

import datetime as dt
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .config import EffectiveRuntime, build_effective_runtime, freeze_effective_runtime
from .locking import FileLock, LockSet, repository_lock_key
from .models import RunContext
from .repository import Repository
from .workspace import RunWorkspace, create_run_context


@dataclass
class RuntimeServices:
    context: RunContext
    workspace: RunWorkspace
    repository: Repository
    config: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)

    def freeze_runtime(self) -> dict[str, str]:
        runtime = EffectiveRuntime(config=dict(self.config), policy=dict(self.policy))
        return freeze_effective_runtime(self.context.run_dir, runtime)

    def locks(self) -> LockSet:
        token = uuid.uuid4().hex
        common_owner = {
            "run_id": self.context.run_id,
            "project_root": str(self.context.project_root),
            "pid": os.getpid(),
            "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
            "token": token,
        }
        run_lock = FileLock(self.context.run_dir / "locks" / "run.lock", common_owner)
        key = repository_lock_key(self.repository.git_common_dir())
        repository_lock = FileLock(
            self.context.run_root / ".locks" / "repositories" / f"{key}.lock",
            common_owner,
        )
        return LockSet(run_lock, repository_lock)

    def work_repository(self) -> Repository:
        return Repository(self.workspace.work_project_root)


def create_runtime_services(
    project: Path,
    *,
    run_root: Path | None = None,
    run_id: str | None = None,
    config: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
) -> RuntimeServices:
    context = create_run_context(project, run_root, run_id)
    repository = Repository(context.project_root)
    runtime = build_effective_runtime(config, policy)
    return RuntimeServices(
        context=context,
        workspace=RunWorkspace(context),
        repository=repository,
        config=runtime.config,
        policy=runtime.policy,
    )
