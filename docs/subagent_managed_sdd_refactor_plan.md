# Subagent-Managed SDD Refactor Plan

## Goal

Refactor Autonomous SDD toward an agent-centric architecture that keeps the autonomous lifecycle core while reducing duplicated responsibilities already handled by the OpenCode host environment.

The target shape is:

- Preserve the full `brainstorm -> archive` lifecycle.
- Preserve deterministic gates, checkpointing, repair, recovery, and closeout.
- Preserve `OpenSpec + SuperSpec` as the artifact and workflow backbone.
- Shift execution toward stage-scoped subagents with minimal task packets.
- Treat skill discovery and installation as an external host concern.
- Keep skill routing configurable so internal company skills can be swapped in later.

## What Stays

- One-command unattended execution.
- Persistent state authority in runtime state files.
- Stage handoffs, evidence, deterministic gates, and Git checkpoints.
- Independent `apply`, `review`, and `verify` execution.
- OpenSpec artifacts and the bundled SuperSpec lifecycle schema.

## What Changes

- The system should be described and evolved as a hosted agent, not as a standalone skill.
- The main controller should behave as a thin orchestrator that delegates stage work to subagents.
- Skills should be modeled as pluggable capabilities, not hard-coded delivery assets.
- Skill provisioning logic should be removed from the critical path when the host environment already guarantees availability.
- Competition-specific defaults should move out of the core runtime and into scenario profiles.

## Non-Goals

- Removing the autonomous lifecycle.
- Removing repair or recovery behavior.
- Replacing OpenSpec or SuperSpec.
- Turning the project into a pure template copier.

## Target Architecture

```text
Hosted SDD Agent
  ├─ runner/state machine
  ├─ deterministic gates
  ├─ recovery/checkpoint/closeout
  ├─ profile selection
  ├─ stage packet builder
  └─ stage subagent invocations

OpenSpec
  └─ proposal/specs/design/tasks/archive artifacts

SuperSpec
  └─ lifecycle and artifact dependency rules

Pluggable Skills
  └─ coding/review/verification/domain-specific capabilities
```

## Refactor Boundaries

### Keep in Core Runtime

- Run state model and durable storage.
- Repository/workspace isolation.
- Runtime budgets, retries, and failure signatures.
- Gate evaluation and structured receipts.
- Stage transition rules.

### Move Behind Profiles or Contracts

- Built-in default objectives.
- Domain-specific requirement themes.
- Competition-specific acceptance rules.
- Stage-to-skill routing choices.
- Prompt and packet wording that depends on a scenario.

### Remove from Core Responsibilities

- Skill installation and placement mechanics when the host already provides them.
- Hard-coded coupling to one competition problem or one language/tooling stack.

## Delivery Shape

The project should be delivered as an agent-oriented hosted workflow with:

- a main orchestration agent contract,
- stage/subagent contracts,
- OpenSpec/SuperSpec templates,
- optional pluggable skills,
- scenario profiles.

It should not be framed as a single monolithic skill.

## Implementation Phases

### Phase 1: Establish Contracts

- Add explicit agent protocol models for stage packets and skill requests.
- Document the target architecture and profile boundaries.
- Stop introducing new competition-specific logic into the runtime core.

### Phase 2: Extract Scenario-Specific Logic

- Move built-in competition defaults into a profile module.
- Isolate requirement coverage rules behind profile hooks.
- Separate skill routing from stage control flow.

### Phase 3: Introduce Subagent Execution Layer

- Formalize stage-level task packets.
- Add stage/subagent role definitions.
- Keep the runner as the sole authority for lifecycle state changes.

### Phase 4: Trim Provisioning Responsibilities

- Remove or downgrade skill copy/install logic from the main execution path.
- Leave skill availability as a host precondition with clear validation.

## Immediate Next Steps

Completed:

1. Added protocol models for stage packets and pluggable skill requirements.
2. Added profile-driven objectives, acceptance themes, and skill routing.
3. Added explicit profile selection to hosted workflow entry points.
4. Added a bounded OpenCode stage agent selected for every isolated stage session.

Next:

1. Move remaining competition-only fixture text and policy naming behind profiles.
2. Add host skill availability preflight without installing or copying skills.
3. Split the monolithic runner into lifecycle, packet, executor, and gate modules.

## Agent Delivery Contract

- `.opencode/agents/autonomous-sdd.md` is the only user-facing delivery entry.
- `.opencode/agents/sdd-stage.md` executes isolated runner-controlled stages.
- The runtime does not ship an `autonomous-sdd` skill or a domain skill.
- `.sdd/config.yaml` maps logical capabilities to ordered project or host skill candidates.
- Empty candidate lists retain profile defaults; configured candidates take precedence.
- Stage results record selected skills in `skills_used` for handoff and audit evidence.
