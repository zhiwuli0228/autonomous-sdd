# Agent Contract

Every invocation receives one task packet and must perform one role only.

Required packet sections:

- objective
- required reads
- required output
- allowed paths
- forbidden paths/actions
- binding decisions
- acceptance checks
- response file

The agent must:

1. Read every required file before editing.
2. Modify only allowed paths.
3. Preserve protected APIs and dependency manifests.
4. For implementation, demonstrate test failure before production changes when feasible.
5. For implementation, return the exact packet `task_id` and do not edit task checkboxes.
6. Return commands as argument arrays, structured test executions, requirement-to-file evidence, and concrete residual risks.
7. Mark a requirement `satisfied` only when both implementation and test files exist.
8. Write `.sdd/runtime/agent-result.json`.
9. Never commit, advance state, archive, or alter policy.

The Runner independently derives and executes focused tests, verifies result structure,
checks declared files against the worktree, and owns task completion markers.
Missing or malformed result data is a failed invocation, not proof of failure or success.
