"""Strict runtime configuration and non-relaxable safety policy."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigurationError, WorkspaceError
from .serialization import read_json, sha256_file, write_json_atomic


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "executor": {
        "name": "opencode",
        "model": None,
    },
    "timeouts": {
        "agent_seconds": 1200,
        "stage_agent_seconds": {},
        "focused_test_seconds": 300,
        "verification_seconds": 3600,
    },
    "budget": {
        "maximum_agent_invocations": 30,
        "maximum_task_attempts": 3,
        "maximum_review_cycles": 3,
        "maximum_verify_repairs": 2,
        "maximum_wall_seconds": 7200,
        "maximum_repeated_failure_signatures": 4,
    },
    "git": {
        "auto_commit": True,
    },
}

DEFAULT_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "changes": {
        "maximum_changed_files": 50,
        "maximum_added_lines": 2000,
        "maximum_deleted_lines": 2000,
        "allow_binary_files": False,
        "allow_dependency_changes": False,
        "allow_public_api_changes": False,
    },
    "agent": {
        "allow_network": False,
        "allow_commits": False,
        "allow_state_changes": False,
        "allow_policy_changes": False,
        "require_exact_result_schema": True,
        "restore_on_invalid_result": True,
        "restore_on_scope_violation": True,
    },
}

# These values are runner invariants, not user preferences. Overrides may make
# limits stricter, but cannot grant an untrusted agent more authority.
SAFETY_FLOORS: dict[tuple[str, ...], Any] = {
    ("changes", "allow_binary_files"): False,
    ("changes", "allow_dependency_changes"): False,
    ("changes", "allow_public_api_changes"): False,
    ("agent", "allow_network"): False,
    ("agent", "allow_commits"): False,
    ("agent", "allow_state_changes"): False,
    ("agent", "allow_policy_changes"): False,
    ("agent", "require_exact_result_schema"): True,
    ("agent", "restore_on_invalid_result"): True,
    ("agent", "restore_on_scope_violation"): True,
}

CONFIG_SAFETY_FLOORS: dict[tuple[str, ...], Any] = {
    ("git", "auto_commit"): True,
}

MAXIMUM_OVERRIDE_PATHS = (
    ("timeouts", "agent_seconds"),
    ("timeouts", "focused_test_seconds"),
    ("timeouts", "verification_seconds"),
    ("budget", "maximum_agent_invocations"),
    ("budget", "maximum_task_attempts"),
    ("budget", "maximum_review_cycles"),
    ("budget", "maximum_verify_repairs"),
    ("budget", "maximum_wall_seconds"),
    ("budget", "maximum_repeated_failure_signatures"),
)

POLICY_MAXIMUM_OVERRIDE_PATHS = (
    ("changes", "maximum_changed_files"),
    ("changes", "maximum_added_lines"),
    ("changes", "maximum_deleted_lines"),
)


@dataclass(frozen=True)
class EffectiveRuntime:
    config: dict[str, Any]
    policy: dict[str, Any]


def build_effective_runtime(
    config_override: Mapping[str, Any] | None = None,
    policy_override: Mapping[str, Any] | None = None,
) -> EffectiveRuntime:
    config = _strict_merge(DEFAULT_CONFIG, config_override or {}, "config")
    policy = _strict_merge(DEFAULT_POLICY, policy_override or {}, "policy")
    _validate_config(config)
    _validate_policy(policy)
    _validate_required_values(config, CONFIG_SAFETY_FLOORS, "config")
    _validate_safety_floors(policy)
    _validate_not_increased(config, DEFAULT_CONFIG, MAXIMUM_OVERRIDE_PATHS, "config")
    _validate_not_increased(policy, DEFAULT_POLICY, POLICY_MAXIMUM_OVERRIDE_PATHS, "policy")
    return EffectiveRuntime(config=config, policy=policy)


def load_optional_override(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return read_json(path)


def freeze_effective_runtime(run_dir: Path, runtime: EffectiveRuntime) -> dict[str, str]:
    config_path = run_dir / "effective-config.json"
    policy_path = run_dir / "effective-policy.json"
    write_json_atomic(config_path, runtime.config, overwrite=False)
    write_json_atomic(policy_path, runtime.policy, overwrite=False)
    return {
        "config_sha256": sha256_file(config_path),
        "policy_sha256": sha256_file(policy_path),
    }


def verify_frozen_runtime(run_dir: Path, expected_hashes: Mapping[str, str]) -> EffectiveRuntime:
    config_path = run_dir / "effective-config.json"
    policy_path = run_dir / "effective-policy.json"
    actual = {
        "config_sha256": sha256_file(config_path),
        "policy_sha256": sha256_file(policy_path),
    }
    for key, digest in actual.items():
        if expected_hashes.get(key) != digest:
            raise WorkspaceError(f"Frozen runtime file changed: {key}")
    config = read_json(config_path)
    policy = read_json(policy_path)
    _validate_config(config)
    _validate_policy(policy)
    _validate_required_values(config, CONFIG_SAFETY_FLOORS, "config")
    _validate_safety_floors(policy)
    return EffectiveRuntime(config=config, policy=policy)


def _strict_merge(base: Mapping[str, Any], override: Mapping[str, Any], location: str) -> dict[str, Any]:
    if not isinstance(override, Mapping):
        raise ConfigurationError(f"{location} override must be an object")
    unknown = sorted(set(override) - set(base))
    if unknown:
        raise ConfigurationError(f"Unknown {location} fields: {', '.join(unknown)}")
    result = copy.deepcopy(dict(base))
    for key, value in override.items():
        expected = base[key]
        child_location = f"{location}.{key}"
        if isinstance(expected, Mapping):
            if not isinstance(value, Mapping):
                raise ConfigurationError(f"{child_location} must be an object")
            result[key] = _strict_merge(expected, value, child_location)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_config(config: Mapping[str, Any]) -> None:
    _require_int(config["schema_version"], "config.schema_version", minimum=1, maximum=1)
    executor = config["executor"]
    if executor["name"] not in {"opencode", "fixture", "replay"}:
        raise ConfigurationError("config.executor.name is unsupported")
    model = executor["model"]
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ConfigurationError("config.executor.model must be null or a non-empty string")
    timeouts = config["timeouts"]
    _require_int(timeouts["agent_seconds"], "config.timeouts.agent_seconds", 10, 7200)
    stage_agent_seconds = timeouts["stage_agent_seconds"]
    if not isinstance(stage_agent_seconds, Mapping):
        raise ConfigurationError("config.timeouts.stage_agent_seconds must be an object")
    for stage, value in stage_agent_seconds.items():
        if not isinstance(stage, str) or not stage.strip():
            raise ConfigurationError("config.timeouts.stage_agent_seconds keys must be non-empty strings")
        _require_int(value, f"config.timeouts.stage_agent_seconds.{stage}", 10, 7200)
    _require_int(timeouts["focused_test_seconds"], "config.timeouts.focused_test_seconds", 5, 3600)
    _require_int(timeouts["verification_seconds"], "config.timeouts.verification_seconds", 10, 14400)
    budget = config["budget"]
    _require_int(budget["maximum_agent_invocations"], "config.budget.maximum_agent_invocations", 1, 200)
    _require_int(budget["maximum_task_attempts"], "config.budget.maximum_task_attempts", 1, 10)
    _require_int(budget["maximum_review_cycles"], "config.budget.maximum_review_cycles", 1, 10)
    _require_int(budget["maximum_verify_repairs"], "config.budget.maximum_verify_repairs", 0, 10)
    _require_int(budget["maximum_wall_seconds"], "config.budget.maximum_wall_seconds", 60, 86400)
    _require_int(
        budget["maximum_repeated_failure_signatures"],
        "config.budget.maximum_repeated_failure_signatures",
        1,
        5,
    )
    _require_bool(config["git"]["auto_commit"], "config.git.auto_commit")


def _validate_policy(policy: Mapping[str, Any]) -> None:
    _require_int(policy["schema_version"], "policy.schema_version", minimum=1, maximum=1)
    changes = policy["changes"]
    _require_int(changes["maximum_changed_files"], "policy.changes.maximum_changed_files", 1, 500)
    _require_int(changes["maximum_added_lines"], "policy.changes.maximum_added_lines", 1, 100000)
    _require_int(changes["maximum_deleted_lines"], "policy.changes.maximum_deleted_lines", 0, 100000)
    for key in (
        "allow_binary_files",
        "allow_dependency_changes",
        "allow_public_api_changes",
    ):
        _require_bool(changes[key], f"policy.changes.{key}")
    for key, value in policy["agent"].items():
        _require_bool(value, f"policy.agent.{key}")


def _validate_safety_floors(policy: Mapping[str, Any]) -> None:
    _validate_required_values(policy, SAFETY_FLOORS, "policy")


def _validate_required_values(
    value: Mapping[str, Any],
    required_values: Mapping[tuple[str, ...], Any],
    root_name: str,
) -> None:
    for path, required in required_values.items():
        current: Any = value
        for part in path:
            current = current[part]
        if current != required:
            dotted = ".".join((root_name, *path))
            raise ConfigurationError(f"{dotted} cannot override the runner safety invariant")


def _validate_not_increased(
    value: Mapping[str, Any],
    defaults: Mapping[str, Any],
    paths: tuple[tuple[str, ...], ...],
    root_name: str,
) -> None:
    for path in paths:
        current: Any = value
        maximum: Any = defaults
        for part in path:
            current = current[part]
            maximum = maximum[part]
        if current > maximum:
            dotted = ".".join((root_name, *path))
            raise ConfigurationError(f"{dotted} cannot exceed the runner safety default {maximum}")


def _require_int(
    value: Any,
    field: str,
    minimum: int,
    maximum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ConfigurationError(f"{field} must be between {minimum} and {maximum}")


def _require_bool(value: Any, field: str) -> None:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{field} must be a boolean")
