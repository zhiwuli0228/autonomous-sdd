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
5. Write `.sdd/runtime/agent-result.json`.
6. Never commit, advance state, archive, or alter policy.

The Runner independently verifies every claim. Missing or malformed result data is a failed invocation, not proof of failure or success.
