# Execution Plan

## Execution Strategy

Use one fresh OpenCode session per unchecked task. The external Runner is the
only lifecycle writer.

## Tasks

Theme rules:
- Every `Theme` line must list all material requirements implied by that task's title and detail bullets, not only the primary implementation headline.
- If a task mentions behavior preservation, verification evidence, documentation, or routed skill usage anywhere in the task body, repeat those topics explicitly in `Theme`.
- Keep `Verification`, `Evidence`, `Implementation Targets`, and `Test Targets` concrete and file-oriented.

### Task 1.1

- Theme: requested behavior, focused verification
- Verification: run the exact command or test that proves the task-specific behavior with exit code 0
- Evidence: focused command output plus exact changed file paths tied to the verified behavior
- Implementation Targets: src/example.py
- Test Targets: tests/test_example.py

### Task 1.2

- Theme: behavior preservation, regression coverage
- Verification: run the exact regression command or test that proves unaffected behavior remains intact
- Evidence: regression output plus exact changed file paths tied to preserved behavior
- Implementation Targets: src/example.py, tests/test_example.py
- Test Targets: tests/test_example.py

### Task 1.3

- Theme: documentation or routed skill usage, verification evidence
- Verification: review the exact documentation, configuration, or skill-routed behavior needed by the task and confirm it is complete
- Evidence: updated file paths or execution output that prove the remaining scope is complete
- Implementation Targets: README.md
- Test Targets: None (documentation-only change)

## Verification

## Checkpoint Strategy

Checkpoint only after deterministic gates pass.
