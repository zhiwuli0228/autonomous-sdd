# Hosted Autonomous SDD

This repository is controlled by `.sdd/runtime/state.json`.

Mandatory rules:

1. Read `.sdd/runtime/task-packet.json` before acting.
2. Execute exactly one declared stage or implementation task.
3. Modify only packet-approved paths.
4. Never modify `.sdd/policy/`, `.sdd/baseline/`, `.sdd/bin/`, or `openspec/schemas/`.
5. Never add dependencies, alter protected APIs, commit, archive, or advance lifecycle state.
6. Write `.sdd/runtime/agent-result.json` before returning.
7. Conversation history and summaries are not authoritative.

The external Runner performs validation, commits, handoffs, recovery, and lifecycle advancement.
