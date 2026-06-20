"""Core infrastructure for the Autonomous SDD runner."""

from .config import (
    DEFAULT_CONFIG,
    DEFAULT_POLICY,
    EffectiveRuntime,
    build_effective_runtime,
    freeze_effective_runtime,
    verify_frozen_runtime,
)
from .errors import ConfigurationError, PathSafetyError, RepositoryError, WorkspaceError
from .locking import FileLock, LockSet, repository_lock_key
from .ingestion import prepare_input_workspace
from .models import ArtifactRef, InputWorkspaceSnapshot, RepositoryStatus, RunContext
from .repository import Repository
from .services import RuntimeServices, create_runtime_services
from .workspace import RunWorkspace, create_run_context, default_run_root

__all__ = [
    "ArtifactRef",
    "ConfigurationError",
    "DEFAULT_CONFIG",
    "DEFAULT_POLICY",
    "EffectiveRuntime",
    "FileLock",
    "LockSet",
    "InputWorkspaceSnapshot",
    "PathSafetyError",
    "Repository",
    "RepositoryError",
    "RepositoryStatus",
    "RunContext",
    "RuntimeServices",
    "RunWorkspace",
    "WorkspaceError",
    "build_effective_runtime",
    "create_run_context",
    "create_runtime_services",
    "default_run_root",
    "prepare_input_workspace",
    "freeze_effective_runtime",
    "repository_lock_key",
    "verify_frozen_runtime",
]
