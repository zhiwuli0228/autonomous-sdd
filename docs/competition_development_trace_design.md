# Competition Development Trace Design

## 1. Goal

This design is only about development-stage trace data.

It does not define runner responsibility, packaging responsibility, or submission export responsibility.

It defines what development-process materials must be retained so they can later be used as raw competition evidence.

## 2. Scope

The trace scope is limited to the development lifecycle before final submission assembly.

It covers:

- requirement understanding
- brainstorming
- proposal
- design
- task decomposition
- implementation planning
- key technical decisions

It does not cover:

- zip packaging
- final exporter structure
- runner final result formatting

## 3. Required trace principle

Every major development decision must leave a durable trace.

The trace should answer:

- what the requirement was
- how it was interpreted
- what alternatives were considered
- what decision was made
- why the decision was made
- how the later work derives from that decision

## 4. Required trace classes

### 4.1 Requirement trace

Must retain:

- original competition statement
- normalized development objective
- frozen constraints
- acceptance criteria

### 4.2 Brainstorm trace

Must retain:

- candidate solution directions
- protocol alternatives
- compatibility alternatives
- skill integration alternatives
- rejected options and reasons

### 4.3 Design trace

Must retain:

- format design
- compatibility design
- CLI design
- unpacking strategy
- skill strategy
- test strategy
- risk analysis

### 4.4 Task trace

Must retain:

- task decomposition
- task ordering rationale
- requirement-to-task mapping
- explicit delivery checkpoints

### 4.5 Decision trace

Must retain:

- major technical decisions
- boundary decisions
- non-goals
- postponed work
- known unknowns

## 5. Minimum retained artifacts

At minimum, the following artifacts should be preserved as source materials:

- `brainstorm.md`
- `proposal.md`
- `specs/*/spec.md`
- `design.md`
- `tasks.md`
- `plan.md`
- stage handoff notes
- decision notes if separated from stage artifacts

## 6. Trace quality rules

### 6.1 Preserve raw reasoning, not just summaries

The artifacts should preserve the actual development reasoning, not only a final polished explanation.

### 6.2 Preserve alternative paths

If alternatives were considered, keep them in the trace. Do not overwrite history so that only the winner remains visible.

### 6.3 Preserve requirement linkage

Every major design or task artifact should visibly connect back to the competition requirements.

### 6.4 Preserve compatibility reasoning

For this C++ competition branch, compatibility is core. Any artifact that affects behavior should explain:

- why compatibility is preserved
- what old behavior remains unchanged
- what new behavior is added

## 7. Recommended artifact structure

Suggested development-trace structure:

```text
development-trace/
├─ objective/
│  ├─ original-requirement.md
│  ├─ normalized-objective.md
│  └─ frozen-constraints.md
├─ brainstorming/
│  └─ brainstorm.md
├─ design/
│  ├─ proposal.md
│  ├─ spec.md
│  ├─ design.md
│  ├─ tasks.md
│  └─ plan.md
└─ decisions/
   └─ key-decisions.md
```

This is a logical structure requirement, not necessarily the final on-disk implementation.

## 8. C++ competition-specific trace requirements

For this branch, the trace must explicitly show reasoning about:

1. variable-length custom header payload
2. unpack correctness after customization
3. unchanged build entrypoint
4. original CLI compatibility
5. skill delivery for THX/header inspection
6. test coverage strategy

If the trace does not show these topics, it is incomplete for this branch.

## 9. Future formatter/checker reservation

If an internal format-checking tool is introduced later through MCP, Skill, or an IntelliJ IDEA plugin bridge, the development trace should also retain:

- whether the tool was used
- what it checked
- what result it produced

But the absence of the tool must not block trace generation.

## 10. Success criteria

This trace design is complete when:

- the development process can be reconstructed from retained artifacts
- the reasoning path is visible, not just the final answer
- competition-specific compatibility and skill decisions are visible
- later submission materials can reuse these artifacts directly

