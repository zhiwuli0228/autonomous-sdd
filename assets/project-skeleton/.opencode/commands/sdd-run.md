---
description: Execute exactly the current Autonomous SDD stage
agent: build
---

Read @.sdd/runtime/task-packet.json, @.sdd/runtime/state.json, and
@.sdd/runtime/current-handoff.json. Execute exactly the declared stage or one
implementation task. Obey AGENTS.md. Write `.sdd/runtime/agent-result.json`.
Do not commit or advance lifecycle state.
