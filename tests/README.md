# Test Suite

Tests mirror the ownership boundaries of production code. Run the deterministic
suite from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
```

The flagless command includes unit, contract, CLI, and local integration tests.
It does not launch a simulator, contact a Pi, or record runtime artifacts.

## Ownership

- `autonomy/` protects stable interfaces and framework contracts.
- `implementations/` protects concrete adapters, plugins, runtime hosts, and
  bounded operations.
- `cli/` treats `./cli/automa` as a black-box command and asserts
  operator-visible behavior.
- `integration/` exercises compatibility across ownership boundaries using
  disposable local state.
- `lab/` protects experimental candidates without presenting them as production
  implementations.
- `live/` contains named, bounded checks that require explicit opt-in.

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

## Live Simulator Boundary

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --live-sim
```

This command may launch local simulator and UI processes. It first requires a
usable Chase frontend, then enables one bounded automation smoke test. The same
test remains an explicit skip in the default suite.

## Live Pi Boundary

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --live-pi
```

The Pi must be powered on, reachable at `http://piracer.local:8887`, and running
the deployed Donkey server. Override the endpoint or request timeout when needed:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --live-pi \
  --picar-url http://192.168.8.120:8887 \
  --pi-timeout-s 3
```

This path sends read-only requests to `/autonomy/status`. It requires an
available autonomy manager, a loaded decision engine and perception algorithm,
and Donkey drive mode `user`. It does not send drive or mode-change requests,
restart the runtime, use SSH, capture frames, or move the vehicle. An unreachable
or unsafe endpoint is reported as `unavailable` with exit code 2 before the test
suite starts; it is not converted into a passing skip. The live Pi test remains
an explicit skip in the flagless suite.
