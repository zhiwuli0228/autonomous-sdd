# Execution Plan

## Execution Strategy

Use one fresh OpenCode session per unchecked task. The external Runner is the
only lifecycle writer.

## Tasks

Theme rules:
- Every `Theme` line must list all competition topics implied by that task's title and detail bullets, not only the primary implementation headline.
- If a task mentions custom headers, variable-length payloads, unpack behavior, original CLI compatibility, unchanged build entrypoint, skill delivery, THX handling, or header inspection anywhere in the task body, repeat those topics explicitly in `Theme`.
- Keep `Verification`, `Evidence`, `Implementation Targets`, and `Test Targets` concrete and file-oriented.

### Task 1.1

- Theme: custom header payload, variable-length header payload, unpack correctness
- Verification: run the specific custom-header regression binary or command and confirm the targeted custom_header roundtrip behavior passes with exit code 0
- Evidence: focused test output plus the exact changed test file paths that prove custom_header roundtrip and variable-length payload coverage
- Implementation Targets: tests/unit/test_format.cpp
- Test Targets: tests/unit/test_format.cpp

### Task 1.2

- Theme: custom header payload, unpack correctness, legacy CLI compatibility, unchanged build entrypoint
- Verification: run the exact unpack and CLI regression targets and confirm customized unpack, original CLI behavior, and build entry stability all pass
- Evidence: unpack regression output, CLI regression output, and file-level proof tied to the changed integration test paths
- Implementation Targets: tests/integration/test_integration.cpp, tests/integration/test_cli.cpp
- Test Targets: tests/integration/test_integration.cpp, tests/integration/test_cli.cpp

### Task 1.3

- Theme: custom header payload, skill delivery, THX handling, header inspection
- Verification: review the exact skill file content and confirm THX/header inspection guidance, custom_header behavior, and usage examples are present
- Evidence: updated skill file path plus content review proof for THX handling and header inspection sections
- Implementation Targets: skills/unitool/SKILL.md
- Test Targets: None (documentation-only change)

## Verification

## Checkpoint Strategy

Checkpoint only after deterministic gates pass.
