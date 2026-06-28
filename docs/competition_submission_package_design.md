# Competition Submission Package Design

## 1. Goal

`autonomous-sdd` must not only deliver a working C++ competition solution. It must also produce a complete competition submission package that can be zipped and submitted directly.

The package must include:

- usage instructions
- solution reasoning
- execution or interaction records
- runnable deliverables
- execution results
- evidence artifacts

## 2. Scope

This design applies to C++ competition delivery only.

It does not change the project goal. It changes how the tool packages the result.

## 3. Required submission contents

The final `zip` must contain at least:

1. `README.md`
2. `solution.md`
3. `design/`
4. `evidence/`
5. `deliverables/`
6. `result-summary.json`

### 3.1 `README.md`

Must explain:

- what the project does
- how to build it
- how to run it
- how to verify it
- what files are inside the submission

### 3.2 `solution.md`

Must explain:

- the problem being solved
- the main design choices
- compatibility strategy
- safety strategy
- why the implementation is structured the way it is

### 3.3 `design/`

Must contain the SDD artifacts:

- `proposal.md`
- `design.md`
- `tasks.md`
- `plan.md`

If applicable, also include:

- `specs/`
- `handoff/`

### 3.4 `evidence/`

Must contain machine-verifiable evidence:

- build logs
- test logs
- smoke test logs
- stage handoff logs
- failure/recovery logs if any

Evidence must be bound to the current repository state or Git commit whenever possible.

### 3.5 `deliverables/`

Must contain runnable or directly usable outputs:

- `skill/`
- `mcp/`
- `agent/`
- build artifacts if required by the task

The exact contents depend on the competition requirements, but the directory must exist and be populated.

### 3.6 `result-summary.json`

Must provide a machine-readable summary:

- project name
- branch
- final status
- build result
- test result
- key acceptance outcomes
- evidence file paths

## 4. Package layout

Recommended layout:

```text
submission/
├─ README.md
├─ solution.md
├─ design/
│  ├─ proposal.md
│  ├─ design.md
│  ├─ tasks.md
│  ├─ plan.md
│  └─ specs/
├─ evidence/
│  ├─ logs/
│  ├─ tests/
│  ├─ recovery/
│  └─ interactions/
├─ deliverables/
│  ├─ skill/
│  ├─ mcp/
│  ├─ agent/
│  └─ artifacts/
└─ result-summary.json
```

## 5. Evidence requirements

The package must preserve the following categories of evidence:

- design evidence
- implementation evidence
- verification evidence
- failure and recovery evidence

At minimum, evidence should answer:

- what was changed
- why it was changed
- how it was verified
- what failed during the process
- how the failure was resolved

## 6. Automation requirements

The tool should generate the submission package automatically.

Required automation steps:

1. collect final design artifacts
2. collect runnable deliverables
3. collect build and test logs
4. collect stage or interaction records
5. write `solution.md` and `README.md`
6. write `result-summary.json`
7. zip the full package

No manual file assembly should be required at submission time.

## 7. Structural rules

### 7.1 Do not lose the process record

The submission must keep enough detail for a reviewer to understand how the result was produced.

### 7.2 Do not confuse runtime logs with final deliverables

Logs go to `evidence/`.

Runnable outputs go to `deliverables/`.

### 7.3 Do not hide failure history

If the implementation had failures, the submission should still preserve the successful recovery path and the final state.

### 7.4 Do not weaken the core solution

The submission package is a wrapper around the solution, not a replacement for the solution.

## 8. Tooling requirements for autonomous-sdd

`autonomous-sdd` should add or strengthen these abilities:

- export final documentation automatically
- export interaction history automatically
- export verification evidence automatically
- produce a final zip package automatically
- emit a structured summary JSON

## 9. Success criteria

The submission packaging feature is complete when:

- a single command can produce the full package
- the package contains the required files
- the package is zip-ready
- the package is understandable by a human reviewer
- the package is reproducible by a later agent

