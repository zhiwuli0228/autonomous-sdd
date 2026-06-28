"""Scenario profile definitions and helpers for hosted SDD execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class ScenarioProfile:
    name: str
    default_objective: str
    constraints: tuple[str, ...]
    acceptance_invariants: tuple[str, ...]
    tooling_integration_constraints: Mapping[str, Any]
    requirement_themes: Mapping[str, tuple[str, ...]]
    stage_skill_map: Mapping[str, tuple[Mapping[str, Any], ...]]


COMPETITION_PROFILE = ScenarioProfile(
    name="competition-cpp-header-payload",
    default_objective=(
        "Modify the target C++ packaging project to support custom header payload content provided by a parameter "
        "while preserving unpack correctness, build entry compatibility, original CLI compatibility, skill delivery, "
        "and verification completeness."
    ),
    constraints=(
        "Support custom header payload content through a parameter.",
        "The custom header payload content length is variable and must be parsed correctly.",
        "Unpack must still work correctly after customization.",
        "The project build entrypoint must remain unchanged.",
        "The original packaging tool invocation must still work with its original arguments.",
        "Backward compatibility with existing callers must be preserved.",
        "A callable skill for the packaging tool must be delivered.",
        "The skill must support THX-related handling and header inspection.",
        "Reasonable tests must be designed from the codebase structure to validate the change.",
    ),
    acceptance_invariants=(
        "variable_length_header_payload",
        "successful_unpack_after_customization",
        "unchanged_build_entrypoint",
        "original_cli_compatibility",
        "skill_delivery_required",
        "validation_tests_required",
    ),
    tooling_integration_constraints={
        "formatter_tool_available": False,
        "formatter_invocation_hint": None,
        "formatter_expected_evidence": None,
        "optional_tooling": [
            "future internal formatter/checker may arrive via MCP",
            "future internal formatter/checker may arrive via Skill",
            "future internal formatter/checker may arrive via IDEA plugin integration",
        ],
    },
    requirement_themes={
        "custom_header_payload": (
            "custom header",
            "header payload",
            "variable-length header",
            "variable length header",
            "variable_length_header_payload",
            "custom_header_payload",
        ),
        "unpack_correctness": (
            "unpack",
            "successful_unpack_after_customization",
            "unpack_correctness",
        ),
        "compatibility": (
            "compatibility",
            "backward compatibility",
            "backward-compatible",
            "source-compatible",
            "existing callers",
            "legacy cli",
            "original cli",
            "build entrypoint",
            "original_cli_compatibility",
            "unchanged_build_entrypoint",
        ),
        "skill_delivery": (
            "skill",
            "header inspection",
            "thx",
            "skill_delivery_required",
            "validation_tests_required",
        ),
    },
    stage_skill_map={
        "apply": (
            {
                "capability": "coding",
                "candidates": ("coding-skill",),
                "purpose": "Apply scoped implementation changes under the active scenario profile",
                "required": False,
            },
        ),
        "review": (
            {
                "capability": "review",
                "candidates": ("review-skill",),
                "purpose": "Review scoped implementation changes under the active scenario profile",
                "required": False,
            },
        ),
        "verify": (
            {
                "capability": "verification",
                "candidates": ("review-skill",),
                "purpose": "Verify scoped implementation changes under the active scenario profile",
                "required": False,
            },
        ),
    },
)


GENERIC_HOSTED_PROFILE = ScenarioProfile(
    name="generic-hosted",
    default_objective=(
        "Implement the requested project change while preserving existing public behavior, honoring repository policies, "
        "and producing verifiable evidence for the hosted SDD lifecycle."
    ),
    constraints=(
        "Preserve existing public behavior unless the task explicitly changes it.",
        "Respect repository policy and allowed modification boundaries.",
        "Prefer minimal scoped changes with verifiable evidence.",
        "Provide tests or other concrete verification evidence for behavior changes.",
    ),
    acceptance_invariants=(
        "behavior_preservation_or_declared_change",
        "scoped_modification_only",
        "verifiable_evidence_required",
    ),
    tooling_integration_constraints={
        "formatter_tool_available": False,
        "formatter_invocation_hint": None,
        "formatter_expected_evidence": None,
        "optional_tooling": [
            "host-provided coding skill may be available",
            "host-provided review skill may be available",
        ],
    },
    requirement_themes={
        "behavior_change": (
            "behavior",
            "feature",
            "workflow",
            "output",
            "logic",
        ),
        "verification": (
            "test",
            "verify",
            "validation",
            "evidence",
            "proof",
        ),
        "documentation": (
            "docs",
            "documentation",
            "readme",
            "guide",
        ),
    },
    stage_skill_map={
        "apply": (
            {
                "capability": "coding",
                "candidates": ("coding-skill",),
                "purpose": "Apply scoped implementation changes under the active scenario profile",
                "required": False,
            },
        ),
        "review": (
            {
                "capability": "review",
                "candidates": ("review-skill",),
                "purpose": "Review scoped implementation changes under the active scenario profile",
                "required": False,
            },
        ),
        "verify": (
            {
                "capability": "verification",
                "candidates": ("verification-skill",),
                "purpose": "Verify scoped implementation changes under the active scenario profile",
                "required": False,
            },
        ),
    },
)


PROFILE_REGISTRY: dict[str, ScenarioProfile] = {
    COMPETITION_PROFILE.name: COMPETITION_PROFILE,
    GENERIC_HOSTED_PROFILE.name: GENERIC_HOSTED_PROFILE,
}


def normalize_profile_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def get_profile(name: str | None = None) -> ScenarioProfile:
    selected = name or COMPETITION_PROFILE.name
    try:
        return PROFILE_REGISTRY[selected]
    except KeyError as exc:
        known = ", ".join(sorted(PROFILE_REGISTRY))
        raise ValueError(f"Unknown scenario profile: {selected}. Known profiles: {known}") from exc


def registered_profiles() -> tuple[str, ...]:
    return tuple(sorted(PROFILE_REGISTRY))


def stage_skill_requirements(
    stage: str,
    profile: ScenarioProfile = COMPETITION_PROFILE,
    skill_routing: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    requirements = profile.stage_skill_map.get(stage, ())
    overrides = (skill_routing or {}).get("capabilities", {})
    result: list[dict[str, Any]] = []
    for item in requirements:
        current = dict(item)
        capability = str(current.get("capability") or current.get("name"))
        default_candidates = current.get("candidates", (current.get("name"),))
        configured_candidates = overrides.get(capability)
        candidates = (
            default_candidates
            if configured_candidates is None or configured_candidates == [] or configured_candidates == ()
            else configured_candidates
        )
        if not isinstance(candidates, (list, tuple)) or not all(
            isinstance(candidate, str) and candidate.strip() for candidate in candidates
        ):
            raise ValueError(f"Skill routing for {capability} must be a list of non-empty names")
        current["capability"] = capability
        current["candidates"] = list(dict.fromkeys(candidate.strip() for candidate in candidates))
        current["selection_policy"] = "first_available"
        current["name"] = current["candidates"][0] if current["candidates"] else capability
        current["purpose"] = str(current.get("purpose", "")).replace("active scenario profile", profile.name)
        result.append(current)
    return result


def theme_markers(profile: ScenarioProfile, theme: str) -> list[str]:
    return list(profile.requirement_themes[theme])


def themes_from_text(value: str, profile: ScenarioProfile = COMPETITION_PROFILE) -> list[str]:
    text = normalize_profile_text(value)
    themes: list[str] = []
    for theme, markers in profile.requirement_themes.items():
        if any(marker in text for marker in markers):
            themes.append(theme)
    return themes


def task_expected_themes(task: dict[str, Any] | None, profile: ScenarioProfile = COMPETITION_PROFILE) -> list[str]:
    if not task:
        return []
    title = normalize_profile_text(str(task.get("title", "")))
    details = normalize_profile_text(str(task.get("details", "")))
    text = f"{title} {details}".strip()
    expected: list[str] = []
    for theme, markers in profile.requirement_themes.items():
        if any(marker in text for marker in markers):
            expected.append(theme)
    if (
        "skill_delivery" in profile.requirement_themes
        and any(marker in text for marker in ["info --json", "info json"])
        and "skill_delivery" not in expected
    ):
        expected.append("skill_delivery")
    return list(dict.fromkeys(expected))


def resolve_profile_objective(
    value: str | None,
    root: Path,
    profile: ScenarioProfile = COMPETITION_PROFILE,
    *,
    frozen_at: str,
) -> dict[str, Any]:
    if value:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            source = "file"
            input_path: str | None = str(candidate.resolve())
        else:
            text = value.strip()
            source = "inline"
            input_path = None
        if len(text) < 10:
            raise ValueError("Competition task must contain at least 10 characters")
        branch_default_used = False
    else:
        text = profile.default_objective
        source = "default"
        input_path = None
        branch_default_used = True
    constraints = list(profile.constraints)
    required_outcomes = list(profile.acceptance_invariants)
    tooling_constraints = dict(profile.tooling_integration_constraints)
    return {
        "profile": profile.name,
        "source": source,
        "input_path": input_path,
        "raw_text": text,
        "effective_objective": text,
        "frozen_goal": profile.default_objective,
        "scenario_constraints": constraints,
        "required_outcomes": required_outcomes,
        "scenario_tooling_constraints": tooling_constraints,
        # Compatibility aliases for existing competition-oriented consumers.
        "competition_constraints": constraints,
        "required_acceptance_invariants": required_outcomes,
        "tooling_integration_constraints": tooling_constraints,
        "branch_default_used": branch_default_used,
        "frozen_at": frozen_at,
    }


def validate_requirement_coverage(
    stage: str,
    evidence: list[dict[str, Any]],
    profile: ScenarioProfile = COMPETITION_PROFILE,
) -> list[str]:
    if stage not in {"verify", "finalize", "archive", "retrospective", "closed"}:
        return []
    if not evidence:
        return [f"No accumulated requirement evidence found for {profile.name} acceptance coverage"]
    satisfied = [item for item in evidence if isinstance(item, dict) and item.get("status") == "satisfied"]
    errors: list[str] = []
    for theme, markers in profile.requirement_themes.items():
        if not any(any(marker in normalize_profile_text(str(item.get("requirement", ""))) for marker in markers) for item in satisfied):
            errors.append(f"Scenario requirement coverage missing theme: {theme}")
    return errors
