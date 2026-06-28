"""Core infrastructure for the Autonomous SDD runner."""

from .agent_protocol import SkillRequirement, StageAgentPacket, StageAgentResult
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
from .profiles import (
    COMPETITION_PROFILE,
    DEFAULT_PROFILE,
    GENERIC_HOSTED_PROFILE,
    PROFILE_REGISTRY,
    ScenarioProfile,
    get_profile,
    normalize_profile_text,
    registered_profiles,
    resolve_profile_objective,
    stage_skill_requirements,
    task_expected_themes,
    theme_markers,
    themes_from_text,
    validate_requirement_coverage,
)

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
    "COMPETITION_PROFILE",
    "DEFAULT_PROFILE",
    "GENERIC_HOSTED_PROFILE",
    "PROFILE_REGISTRY",
    "ScenarioProfile",
    "SkillRequirement",
    "StageAgentPacket",
    "StageAgentResult",
    "WorkspaceError",
    "build_effective_runtime",
    "create_run_context",
    "create_runtime_services",
    "default_run_root",
    "prepare_input_workspace",
    "normalize_profile_text",
    "freeze_effective_runtime",
    "get_profile",
    "repository_lock_key",
    "registered_profiles",
    "resolve_profile_objective",
    "stage_skill_requirements",
    "task_expected_themes",
    "theme_markers",
    "themes_from_text",
    "validate_requirement_coverage",
    "verify_frozen_runtime",
]
