# C++ Competition Goal Freeze Prompt

Use this prompt when asking an implementation agent to customize `autonomous-sdd` for this branch.

## Objective

Customize `autonomous-sdd` so that the entire SDD workflow is permanently aligned to the fixed C++ competition goal.

## Frozen goal

The workflow must always serve this objective:

- add custom header payload support through a parameter
- support variable-length custom payload
- keep unpack working
- keep the build entrypoint unchanged
- keep original CLI compatibility
- deliver a callable skill
- support THX-related handling and header inspection
- deliver tests that validate the change

## Required customization points

Modify the branch so that these requirements are carried through:

1. task packet generation
2. stage templates
3. agent contract
4. review and verify gate expectations

## Do not allow

- goal drift
- compatibility weakening
- dropping the skill requirement
- dropping the test requirement
- redefining the build entrypoint

## Tooling reservation

Reserve support for a future internal formatter/checker tool.

Assume:

- invocation may later come from MCP
- invocation may later come from Skill
- invocation may later be bridged from an IntelliJ IDEA plugin

Do not implement the integration now if the interface is unknown.

Instead:

- add packet fields or design hooks for optional tooling
- allow future review/verify evidence collection
- keep the workflow functional when the tool is absent

## Acceptance checklist

- all stages remain aligned to the frozen C++ competition goal
- task packets carry the frozen constraints
- skill and tests are mandatory in the workflow
- compatibility is mandatory in the workflow
- future formatter/checker integration can be attached without redesign

