# Autonomous SDD

Autonomous SDD is a one-command, unattended software-delivery orchestrator for
coding competitions and constrained internal development environments.

It combines:

- OpenSpec for requirements and capability specifications;
- a bundled Superspec workflow for artifact ordering and gates;
- OpenCode as the coding-agent runtime;
- persistent handoffs, Git checkpoints, policy guards, verification, archive,
  and recovery controlled by a deterministic Python runner.

The user provides a project and a task file. Autonomous SDD drives the change
from discovery through implementation, independent review, verification,
OpenSpec archive, retrospective, and final delivery reporting.

## Key properties

- One external command
- No human interaction after execution starts
- One working tree and one writer
- Fresh OpenCode session for every stage or implementation task
- OpenCode's environment-default model unless explicitly configured
- Persistent state independent of conversation context
- Stage handoff and Git checkpoint after every passing gate
- Protected API, dependency, policy, schema, and scope checks
- Bounded retries with a safe `BLOCKED` result
- Automatic OpenSpec main-spec synchronization and archive
- Windows and Unix entry points

## Requirements

The execution machine must provide:

- Python 3.10 or later
- Git
- OpenCode
- OpenSpec CLI
- The target project's build and test tools

Check locally:

```powershell
python --version
git --version
opencode --version
openspec.cmd --version
```

OpenCode must already be configured to access an available model. Autonomous
SDD does not select a model by default.

## Quick start

Create a UTF-8 task file, for example `competition-task.md`:

```markdown
Implement the requested scheduling behavior.

Constraints:

- Do not change public APIs.
- Do not add dependencies.
- Preserve existing architecture boundaries.
- Add automated tests for normal and boundary behavior.
```

Run the competition workflow:

```powershell
sdd-competition.cmd E:\path\to\project E:\path\to\competition-task.md
```

That is the only required competition command.

The target repository must be clean before execution starts.

## Rehearsal mode

Before using a real model, validate the complete control system with the
deterministic rehearsal executor:

```powershell
sdd-competition.cmd E:\path\to\project E:\path\to\competition-task.md --rehearse
```

Rehearsal mode performs the full lifecycle without calling a model:

```text
project detection
→ harness installation
→ baseline capture
→ brainstorm
→ proposal
→ specifications
→ design
→ tasks and plan
→ implementation task loop
→ independent review
→ verification
→ finalize
→ archive and main-spec sync
→ retrospective
→ CLOSED report
```

Use a disposable clone or test repository for rehearsal because the workflow
creates commits and delivery artifacts.

## Unix entry

```sh
./sdd-competition /path/to/project /path/to/competition-task.md
```

Rehearsal:

```sh
./sdd-competition /path/to/project /path/to/competition-task.md --rehearse
```

## What the one-command entry does

The runner automatically:

1. Requires a clean Git repository.
2. Detects Maven, Gradle, npm/pnpm, Python, Go, Rust, or a generic project.
3. Installs the OpenCode skill, OpenSpec schema, templates, policies, and
   runtime entry points without overwriting existing top-level instructions.
4. Configures the detected project test command.
5. Commits the installed competition harness.
6. Freezes policy, schema, dependency, and protected API baselines.
7. Creates a bounded OpenSpec change.
8. Runs each lifecycle stage in a fresh OpenCode session.
9. Executes deterministic gates before every transition.
10. Commits each verified stage.
11. Synchronizes implemented capability specs and archives the change.
12. Produces a retrospective and final delivery report.

## Outputs

Successful execution prints:

```text
RESULT=CLOSED
REPORT=<project>\.sdd\delivery-report.md
```

Important artifacts include:

```text
.sdd/delivery-report.md
.sdd/changes/<change>/handoffs/
.sdd/evidence/
openspec/specs/
openspec/changes/archive/
```

Volatile runtime state is stored under `.sdd/runtime/` and excluded from Git.

If the workflow cannot complete safely, it exits non-zero and records:

```text
status: blocked
blocking_reason: <specific reason>
last_verified_commit: <safe checkpoint>
```

## Default safety policy

The bundled policy denies:

- human interaction after execution starts;
- worktrees and parallel writers;
- dependency manifest changes;
- policy, baseline, runner, and Superspec schema changes;
- grader, evaluation, and official fixture changes;
- common `api` and `contract` package changes;
- scope expansion beyond detected source and test paths.

Project-specific rules may be placed in `.sdd/policy/` before a manually
managed run. For the one-command competition path, conservative defaults are
applied automatically.

## Model behavior

`.sdd/config.yaml` uses:

```json
{
  "model": null
}
```

When `model` is `null`, the runner does not pass `--model`; OpenCode uses the
model configured by the execution environment.

Set an explicit model only when its provider/model identifier is guaranteed:

```json
{
  "model": "provider/model-id"
}
```

## Lower-level commands

The one-command entry is recommended. Lower-level commands exist for
development and diagnostics:

```powershell
python scripts\sdd.py init E:\path\to\project
.\sdd.cmd doctor
.\sdd.cmd baseline
.\sdd.cmd start <change-id> "<objective>"
.\sdd.cmd run
.\sdd.cmd status
.\sdd.cmd recover
```

These commands are not required for normal competition execution.

## Local validation

Validate the runner and skill:

```powershell
python -m unittest discover -s tests -v
python -m py_compile scripts\sdd.py tests\test_runner.py
python C:\path\to\skill-creator\scripts\quick_validate.py .
```

The automated suite covers:

- installation into a plain Git repository;
- complete one-command lifecycle closure;
- policy tampering detection;
- protected API modification detection;
- retry exhaustion and safe blocking;
- OpenSpec archive and main-spec synchronization.

## Current status

Version `0.2.0` has passed deterministic full-lifecycle validation. Real-model
rehearsal with the target OpenCode environment is the next validation layer.
