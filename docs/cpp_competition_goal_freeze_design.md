# C++ Competition Goal Freeze Design

## 1. Purpose

This branch is not a general-purpose delivery branch.

It is a C++ competition branch, and the development target must be frozen at the tool level so that every SDD stage serves the same problem.

The goal is to prevent drift during:

- brainstorming
- proposal
- specs
- design
- tasks
- plan
- apply
- review
- verify

## 2. Frozen competition goal

The fixed problem statement is:

Modify the target C++ packaging project to support custom header payload content provided by a parameter.

The following constraints are mandatory and must be treated as non-negotiable system goals:

1. The custom header payload content length is variable.
2. After customization is added, unpacking must still work correctly.
3. The build entrypoint must remain unchanged.
4. The original pack tool must still work with its original arguments.
5. Backward compatibility with existing integrations must be preserved.
6. A callable skill must be added for the tool.
7. The skill must support THX-related handling and header inspection.
8. Reasonable tests must be designed from the codebase structure to validate the change.

## 3. What must be frozen in the workflow

The workflow may remain generic, but the target must be frozen.

That means:

- `brainstorm` is not allowed to redefine the product goal
- `proposal` is not allowed to weaken compatibility requirements
- `design` is not allowed to change the build entrypoint requirement
- `tasks` must explicitly include skill work and test work
- `verify` must explicitly check compatibility and unpack behavior

## 4. Required invariant set

These invariants must be present in every implementation-oriented packet:

- variable-length header payload support
- successful unpack after customization
- unchanged build entrypoint
- original CLI compatibility
- skill delivery requirement
- validation test requirement

If any stage output loses one of these invariants, the stage should fail gate validation.

## 5. Required stage-specific interpretations

### 5.1 Brainstorm

Allowed:

- compare alternative header designs
- compare compatibility strategies
- compare skill interface strategies

Not allowed:

- changing the goal from "header customization" to some unrelated packaging improvement
- dropping compatibility as a non-goal

### 5.2 Proposal

Must state:

- why custom header payload is required
- how backward compatibility will be preserved
- that unpack correctness remains mandatory
- that skill delivery is part of the scope

### 5.3 Specs

Must include normative statements for:

- header customization input
- variable-length header parsing
- successful unpack
- original-argument compatibility
- skill-observable header inspection

### 5.4 Design

Must include:

- header format impact
- compatibility strategy
- CLI parameter strategy
- unpack parsing strategy
- skill invocation strategy
- test matrix

### 5.5 Tasks

Must contain explicit tasks for:

- format change
- compatibility preservation
- unpack verification
- skill implementation
- test implementation

### 5.6 Verify

Must explicitly prove:

- new parameter works
- unpack still works
- old parameter path still works
- build entrypoint is unchanged
- skill works
- tests cover the required scenarios

## 6. Task packet changes

The task packet should carry frozen competition constraints explicitly rather than relying on a stage prompt to restate them loosely.

Recommended additions:

- `frozen_goal`
- `competition_constraints`
- `required_acceptance_invariants`
- `tooling_integration_constraints`

## 7. Agent contract changes

The agent contract should require:

- no goal drift
- no compatibility weakening
- no omission of skill work
- no omission of test work

An implementation result should not be considered valid if it modifies code but does not address the frozen invariants.

## 8. Formatter/tooling integration reservation

The branch should reserve a future integration point for an internal formatting or checking tool.

Current assumptions:

- the tool is internal
- the invocation contract is unknown today
- it may appear as an MCP tool
- it may appear as a Skill
- it may be triggered through an IntelliJ IDEA plugin bridge

This should not block the branch, but the architecture must leave room for it.

### 8.1 Integration requirements

The future formatter/checker integration should be represented as an optional verification capability, not a hard dependency.

Required behavior:

- if unavailable, the workflow continues with existing gates
- if available, the tool may be used during review or verify
- the invocation should be captured as machine evidence
- the tool must not become the only proof of correctness

### 8.2 Suggested packet fields

Recommended future fields in the task packet:

- `optional_tooling`
- `formatter_tool_available`
- `formatter_invocation_hint`
- `formatter_expected_evidence`

### 8.3 Suggested gate usage

The formatter/checker should be usable in:

- `review`
- `verify`

It should not redefine stage authority. It is supporting evidence only.

## 9. Success criteria

This goal-freeze customization is complete when:

- every stage remains aligned with the competition problem
- compatibility cannot silently disappear from the plan
- skill and test work cannot be omitted
- formatter/checker integration can be added later without redesigning the branch

