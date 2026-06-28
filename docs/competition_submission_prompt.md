# Competition Submission Prompt

Use this prompt when asking an implementation agent to build the submission-packaging layer.

## Objective

Upgrade `autonomous-sdd` so that it can automatically produce a competition-ready zip package for a C++ SDD delivery.

## Required outputs

The tool must generate:

- `README.md`
- `solution.md`
- `design/`
- `evidence/`
- `deliverables/`
- `result-summary.json`
- final `zip`

## Constraints

- Keep the workflow fully unattended
- Keep the C++ competition boundary
- Do not remove existing SDD artifacts
- Do not drop evidence logs
- Do not require manual packaging steps

## Implementation priority

1. add package layout generation
2. copy final docs and handoffs into `design/`
3. copy evidence into `evidence/`
4. copy runnable outputs into `deliverables/`
5. emit `result-summary.json`
6. create the final zip archive

## Acceptance checklist

- package is created automatically
- package contains the required documents
- package contains evidence files
- package contains runnable deliverables
- package includes a machine-readable result summary
- package can be zipped without manual editing

## Failure policy

If any required piece cannot be generated automatically, preserve the partial output and report the missing artifact explicitly.

