# Competition Design Traceability Design

## 1. Goal

All design artifacts must leave a durable trace that can later be submitted as raw design-process evidence.

This is not optional metadata. It is part of the competition deliverable value.

## 2. Required principle

The system must preserve not only the final result but also the original design path.

That means:

- original objective source must be retained
- intermediate design decisions must be retained
- stage handoffs must be retained
- review and verify evidence must be retained
- recovery and rework history must be retained when relevant

## 3. Traceability targets

The following must be traceable:

1. what the competition objective was
2. how the system interpreted it
3. what alternatives were considered
4. what design was chosen
5. how tasks were derived
6. how implementation was verified
7. what failed and how it was repaired

## 4. Required artifacts

The branch should preserve at least these artifact classes:

### 4.1 Objective artifacts

- original task input
- normalized effective objective
- objective freeze record

### 4.2 Stage artifacts

- `brainstorm.md`
- `proposal.md`
- `spec.md`
- `design.md`
- `tasks.md`
- `plan.md`
- `review.md`
- `verify.md`
- `finalize.md`
- `retrospective.md`

### 4.3 Transition artifacts

- stage handoffs
- execution journal records
- evidence logs
- recovery decisions

### 4.4 Verification artifacts

- build logs
- test logs
- focused smoke tests
- requirement evidence mappings
- optional formatter/checker evidence when available

## 5. Traceability rules

### 5.1 No silent replacement

If an artifact is revised during the run, the system should preserve the stage handoff and evidence trail showing how the newer artifact replaced the older one.

### 5.2 No stage without evidence

A stage transition should not be treated as complete unless there is both:

- a persisted output artifact
- a persisted handoff or evidence record

### 5.3 Traceability must be machine-linkable

Artifacts should be linkable through stable identifiers such as:

- `run_id`
- `change_id`
- `stage`
- `task_id`
- `commit_before_checkpoint`

## 6. Suggested runtime files

Recommended additions or stronger guarantees around:

- `.sdd/runtime/competition-objective.json`
- `.sdd/runtime/current-handoff.json`
- `.sdd/runtime/execution-journal.jsonl`
- `.sdd/evidence/`
- `.sdd/changes/<change>/handoffs/`

## 7. Suggested packet and handoff additions

### 7.1 Task packet

Suggested fields:

- `objective_trace_ref`
- `design_trace_required`
- `frozen_goal`

### 7.2 Handoff

Suggested fields:

- `objective_trace_ref`
- `artifact_trace`
- `evidence_trace`
- `decision_summary`

## 8. Formatter/checker traceability

If the future internal formatter/checker tool is available through MCP, Skill, or IDEA plugin integration, its invocation should be preserved as trace evidence.

Required evidence should include:

- invocation source
- command or bridge name
- success/failure result
- produced report path

This evidence is additive. It must not replace core verification evidence.

## 9. Submission relevance

This traceability layer exists so that a later submission exporter can assemble:

- usage instructions
- solution reasoning
- design process records
- runnable deliverables
- execution results

without reconstructing history manually.

## 10. Success criteria

This design is complete when:

- every stage leaves recoverable trace artifacts
- the original competition objective is preserved
- design evolution is auditable
- verification evidence is linked to the design path
- later submission packaging can reuse the trace data directly

