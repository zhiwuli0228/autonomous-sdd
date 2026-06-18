# Recovery Protocol

## Checkpoint contents

Every transition records:

- run ID and change ID
- completed and next stage
- Git HEAD
- changed paths
- artifact hashes
- executed commands and exit codes
- findings and residual risks
- exact next action

## Recovery sequence

1. Read state and latest handoff.
2. Verify policy and schema hashes against the baseline.
3. Verify Git HEAD equals the expected checkpoint or is explainable as the active atomic task.
4. Verify protected API and protected-file baselines.
5. Verify required artifacts for completed stages.
6. Re-run the smallest safe gate.
7. Continue only on `PASS`.

Conversation summaries and model recollections are never recovery evidence.
