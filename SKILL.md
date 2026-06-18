---
name: autonomous-sdd
description: Initialize and operate an unattended, single-workspace spec-driven delivery workflow using OpenSpec, a bundled Superspec schema, OpenCode, deterministic gates, stage handoffs, recovery checkpoints, protected API baselines, competition constraints, TDD, independent review, and clean-code verification. Use when a coding task or competition project must run end-to-end without human participation, especially with weaker or context-limited coding agents.
---

# Autonomous SDD

Treat the model as a bounded stage executor. Treat repository files, Git commits, OpenSpec artifacts, machine evidence, and `.sdd/runtime/state.json` as authoritative.

## Entry points

Run the full competition workflow with one command:

```text
python scripts/sdd.py --project <project> compete --task <task-file>
```

On Windows, the packaged one-command entry is:

```text
sdd-competition.cmd <project> <task-file>
```

Use `--rehearse` as the optional third argument to validate the entire control
system without invoking a model.

After installation into a project, use:

```text
<project>/sdd compete --task <task-file>
```

On Windows use `sdd.cmd`.

The Runner does not pass `--model` by default, so OpenCode uses the model
provided by the internal environment. Set `.sdd/config.yaml` `model` only when
an explicit provider/model ID is guaranteed.

## Required operating rules

1. Use one working tree and one writer.
2. Start a fresh OpenCode session for every stage or implementation task.
3. Never infer lifecycle state from conversation history.
4. Read `.sdd/runtime/state.json` and the latest handoff before acting.
5. Never edit `.sdd/policy/**`, `.sdd/baseline/**`, or `openspec/schemas/**` during a run.
6. Never advance a stage based on an agent claim. Run the deterministic gate.
7. Deny scope expansion, dependency changes, protected API changes, and build-file changes unless policy explicitly allows them.
8. Persist a handoff and evidence before every stage transition.
9. Bind verification evidence to the current Git commit.
10. Stop safely when retry limits are exhausted or repository state cannot be reconciled.

## Workflow

Use this lifecycle:

```text
brainstorm → proposal → specs → design → tasks → plan
→ apply task loop → independent review → verify → finalize → archive
→ retrospective → closed
```

The Runner creates a constrained task packet, invokes `opencode run`, validates actual outputs, writes a handoff, and advances state. During `apply`, execute one unchecked task per fresh session.

## Project constraints

Before starting a competition run, edit only the initialized policy files:

- `.sdd/policy/competition.yaml`
- `.sdd/policy/project.yaml`
- `.sdd/policy/api-contract.yaml`
- `.sdd/policy/coding-standard.yaml`
- `.sdd/policy/verification.yaml`

Then capture the immutable baseline:

```text
<project>/sdd baseline
```

Do not start implementation until baseline verification passes.

## Recovery

Run:

```text
<project>/sdd recover
```

Recovery must reconcile state, latest handoff, Git HEAD, policy hashes, protected-file hashes, and worktree status. Continue only when recovery returns `PASS`.

## References

- Read `references/lifecycle.md` when changing stage behavior.
- Read `references/agent-contract.md` when changing task packets or prompts.
- Read `references/recovery-protocol.md` when changing checkpoints, handoffs, or failure handling.

Do not duplicate these detailed rules into prompts. Let the Runner generate the minimal stage-specific context.
