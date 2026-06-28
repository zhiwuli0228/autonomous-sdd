# cpp-unitool-header

Use this skill when the target project is the C++ packaging competition project and the task involves packing, unpacking, or inspecting THX/HWX-style artifacts.

## Required behavior

You must help the agent do only the following:

1. build the tool using the existing project build entrypoint;
2. pack files using the original compatible CLI flow;
3. pack files using the custom header payload parameter;
4. unpack generated artifacts;
5. inspect header information, including custom header payload length and content when present;
6. report exact commands, outputs, and validation results.

## Guardrails

- Do not invent flags or file formats.
- Do not assume the header layout; infer it from project code and actual tool output.
- Always verify both legacy and customized flows.
- Treat unpack correctness and legacy CLI compatibility as mandatory.
- Preserve build entry compatibility.

## Minimum validation

Before declaring success, show evidence for:

- default pack still works;
- custom-header pack works;
- customized artifact can be unpacked;
- header inspection exposes the custom payload;
- failing cases are reported with exact commands and exit codes.

## Suggested workflow

1. Read the project build script and tool help output.
2. Build the tool with the unchanged project entrypoint.
3. Establish a legacy pack/unpack baseline.
4. Exercise the custom header payload flow.
5. Inspect the generated header.
6. Compare results and summarize compatibility.
