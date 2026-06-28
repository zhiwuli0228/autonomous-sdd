# Change Proposal

## Why

Describe how the target C++ packaging project will add parameter-driven custom header payload support while preserving compatibility and unpack correctness.

## What Changes

- Extend pack flow to accept custom header payload input.
- Extend unpack flow to parse variable-length customized headers.
- Preserve legacy CLI usage and build entrypoints.
- Deliver a callable skill for THX/header inspection.

## Capabilities

### New Capabilities

- `custom-header-payload`: parameter-driven variable-length header customization
- `header-inspection-skill`: THX/header inspection support through the delivered skill

### Modified Capabilities

- existing pack/unpack behavior for compatibility-preserving header parsing

## Impact

### Allowed Areas

### Protected Areas

### Dependencies

No dependency changes unless competition policy explicitly authorizes them.
