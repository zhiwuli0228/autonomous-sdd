## ADDED Requirements

### Requirement: Support parameter-driven custom header payload

The system MUST support a parameter that injects custom header payload content into the generated package.

#### Scenario: Variable-length custom payload is packed successfully

- **WHEN** the caller provides supported custom payload content with non-fixed length
- **THEN** the package contains that payload and remains structurally valid

### Requirement: Preserve unpack correctness and compatibility

The system MUST preserve unpack behavior and legacy invocation compatibility after the customization.

#### Scenario: Customized package unpacks successfully

- **WHEN** a package contains custom header payload content
- **THEN** unpack restores the packaged content correctly

#### Scenario: Legacy invocation remains compatible

- **WHEN** the original tool parameters are used without the new customization parameter
- **THEN** behavior remains compatible with the legacy contract
