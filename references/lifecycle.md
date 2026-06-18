# Lifecycle

## State authority

`.sdd/runtime/state.json` is the only dynamic lifecycle authority. OpenSpec artifacts describe the change; handoffs describe completed transitions; neither may independently authorize the next stage.

## Stages

| Stage | Required output | Exit gate |
|---|---|---|
| brainstorm | `brainstorm.md` | objective, scope, alternatives, decision present |
| proposal | `proposal.md` | Why, What Changes, Capabilities, Impact present |
| specs | `specs/*/spec.md` | normative requirement and scenario present |
| design | `design.md` | boundaries, existing API verification, decisions, tests present |
| tasks | `tasks.md` | numbered unchecked task list present |
| plan | `plan.md` | exact files, tests, task ordering, commit points present |
| apply | source/test changes and checked tasks | task test passes, scope/API/policy guards pass |
| review | `review.md` | fresh-session scope, specification, tests, and clean-code review pass |
| verify | `verify.md` | full configured verification and traceability pass |
| finalize | `finalize.md` | clean checkpoint and delivery receipt present |
| archive | archived change and synced specs | OpenSpec validation and repository consistency pass |
| retrospective | `retrospective.md` | deviations, failures, improvements present |
| closed | final report | all gates and evidence agree |

## Failure semantics

- `repairable`: open a fresh repair session, bounded by retry policy.
- `policy_violation`: restore the last verified checkpoint and stop.
- `ambiguous_requirement`: stop; unattended execution must not invent product intent.
- `inconsistent_state`: stop and emit a recovery report.
- `verification_failure`: repair within budget, otherwise stop.
