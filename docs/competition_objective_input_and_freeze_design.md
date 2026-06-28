# Competition Objective Input and Freeze Design

## 1. Goal

`autonomous-sdd` should accept competition requirements as a single upfront input.

If no external competition input is supplied, it should fall back to the branch default competition requirements.

Once the run starts, the effective objective must be frozen and reused by every subsequent stage.

## 2. Why this is required

The branch is intended for unattended competition delivery.

Allowing staged requirement input during execution would create:

- goal drift
- design invalidation
- unstable acceptance criteria
- hidden human intervention

This is incompatible with a real unattended workflow.

## 3. Input model

The system should support two input sources:

### 3.1 External competition input

A single task file or structured input supplied at run start.

This should be the preferred path when the full competition statement is known.

### 3.2 Default branch competition objective

If no external input is provided, the branch should use the built-in default objective for the current C++ competition target.

This default should already include:

- custom header payload parameter
- variable-length payload support
- unpack correctness
- unchanged build entrypoint
- original CLI compatibility
- skill delivery
- THX/header inspection capability
- test delivery

## 4. Effective objective

At run start, the system should resolve one final effective objective:

- external input if present
- otherwise default branch objective

This effective objective should be normalized and frozen.

Recommended runtime concept:

- `effective_competition_objective`

## 5. Freeze rule

After run start:

- no new requirement input is accepted
- no mid-run requirement expansion is accepted
- no stage may reinterpret the competition goal beyond the frozen objective

If requirements change materially, the correct action is:

- start a new run
- or create a new change
- or use a new branch

## 6. What must be persisted

The frozen objective must be persisted in machine-readable form.

Recommended storage:

- `.sdd/runtime/competition-objective.json`
- `.sdd/runtime/state.json`
- handoff artifacts for traceability

Recommended fields:

- original_input_source
- original_input_path
- objective_text
- normalized_constraints
- frozen_at
- branch_default_used

## 7. What downstream stages must consume

All downstream packets should consume the frozen effective objective instead of raw task text.

This includes:

- brainstorm packet
- proposal packet
- specs packet
- design packet
- tasks packet
- plan packet
- apply packets
- review packet
- verify packet

## 8. Merge policy

If both external input and default constraints exist:

- the default constraints remain the minimum required baseline
- external input may add detail or tighten constraints
- external input may not silently remove branch-core competition constraints

If a true override mode is ever needed, it should be explicit and auditable.

## 9. Success criteria

This feature is complete when:

- competition requirements can be given once at startup
- default requirements are used when no external input exists
- the resolved objective is frozen and persisted
- all stages consume the same frozen objective
- later stages no longer depend on the raw user prompt text

