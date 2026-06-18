---
name: autonomous-sdd
description: Execute one bounded stage or implementation task in an unattended OpenSpec/Superspec competition workflow. Use when `.sdd/runtime/task-packet.json` exists and the external Autonomous SDD Runner invokes OpenCode.
---

# Autonomous SDD Executor

Read `AGENTS.md`, `.sdd/runtime/state.json`, the current handoff, and the task
packet. Perform exactly one declared unit of work.

Never:

- modify policy, baseline, runner, schema, dependency manifests, or protected API;
- execute later lifecycle stages;
- commit or rewrite Git history;
- trust conversation context over persisted files.

For implementation, inspect existing APIs first, add or update a focused test,
observe failure when feasible, implement the minimum coherent change, and run
the declared task gate.

Always write `.sdd/runtime/agent-result.json` before returning.
