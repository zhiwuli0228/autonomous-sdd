"""Lightweight contracts for agent-centric stage execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


AgentResultStatus = Literal["completed", "needs_repair", "blocked"]


@dataclass(frozen=True)
class SkillRequirement:
    """A pluggable capability the host environment should make available."""

    capability: str
    purpose: str
    candidates: tuple[str, ...] = ()
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        candidates = list(self.candidates or (self.capability,))
        return {
            "capability": self.capability,
            "candidates": candidates,
            "selection_policy": "first_available",
            # Compatibility alias for packet consumers that still expect one name.
            "name": candidates[0],
            "purpose": self.purpose,
            "required": self.required,
        }


@dataclass(frozen=True)
class StageAgentPacket:
    """Minimal packet passed to a stage-scoped agent or subagent."""

    stage: str
    objective: str
    change_id: str
    task_id: str | None = None
    allowed_paths: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    skill_requirements: tuple[SkillRequirement, ...] = ()
    context_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": self.stage,
            "objective": self.objective,
            "change_id": self.change_id,
            "task_id": self.task_id,
            "allowed_paths": list(self.allowed_paths),
            "required_artifacts": list(self.required_artifacts),
            "skill_requirements": [requirement.to_dict() for requirement in self.skill_requirements],
            "metadata": dict(self.metadata),
        }
        if self.context_summary is not None:
            payload["context_summary"] = self.context_summary
        return payload


@dataclass(frozen=True)
class StageAgentResult:
    """Structured result returned by a stage-scoped agent."""

    status: AgentResultStatus
    summary: str
    files_read: tuple[str, ...] = ()
    files_changed: tuple[str, ...] = ()
    next_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "files_read": list(self.files_read),
            "files_changed": list(self.files_changed),
            "next_hints": list(self.next_hints),
        }
