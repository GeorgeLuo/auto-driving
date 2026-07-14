# Test Ownership

Tests mirror the ownership boundaries of production code. The suite is moving
into this tree incrementally; until that mechanical migration lands,
`python3 cli/run_tests.py` remains the canonical runner.

## Support Contract

Code under `tests/support/` may only provide test mechanics:

- execute a public command as a subprocess;
- write explicit fixture documents to a disposable filesystem;
- emulate an external executable and record how it was called.

Support code must not call internal command handlers, calculate expected
application outcomes, render operator output, or reproduce production decision
logic. Scenario and contract tests retain responsibility for arranging domain
state and asserting its meaning. A helper that becomes specific to one owning
test module should remain beside that module instead of expanding this shared
surface.
