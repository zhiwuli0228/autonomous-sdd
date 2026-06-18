# Binding Autonomous SDD Instructions

Read `.sdd/runtime/task-packet.json` and perform exactly one declared stage or
implementation task.

Do not modify policy, baseline, runner, OpenSpec schema, dependency manifests,
protected APIs, grader files, or paths outside the packet allowlist.

Do not commit, archive, advance state, or infer authority from conversation
history. Write `.sdd/runtime/agent-result.json` before returning.
