# CLI Scenario Harness

Run:

```sh
python3 cli/run_tests.py
```

The harness treats `./cli/automa` as a black-box command and checks common development flows that should stay stable while autonomy work evolves.

Default scenarios:

- First-time orientation: top-level and nested help stay scoped to the current command level.
- Discovery snapshot: `vehicles active --json` can return machine-readable JSON without touching live endpoints when providers are disabled.
- Runtime status: `vehicles automation status --json` reports locally deployed automation state from a disposable runtime root.
- Stale worker handling: `vehicles automation stop` marks a dead recorded worker as stopped.
- Perception and decision inspection: `vehicles info ... --json` exposes schemas from the locally staged implementations.
- Activation editing: perception plugins can be enabled/disabled and decision activation can be updated in a disposable bundle.
- Physical deployment: dry-run output and the actual remote installer logic are tested without requiring a Pi.
- Bounded operations: a fake image vehicle verifies the generic startup action-check plan and artifacts.
- Simulator readiness: `simulators status --json` and `simulators ensure --json` are exercised against a fake `simeval` binary for online, launch, missing-frontend-tab, and launch-failure cases.

Live simulator scenario:

```sh
python3 cli/run_tests.py --live-sim
```

This ensures the Chase simulator is usable, opens/connects the Metrics UI frontend if needed, and enables a bounded `automation run --frames 1` smoke test. It is skipped by default because the live path may launch local simulator/UI processes.

Live-test boundary:

- The continuous `vehicles automation run` path requires a live simulator; offline tests cover its lifecycle, contracts, and bounded dependencies instead.
- The live simulator smoke test is only invoked by `run_tests.py --live-sim`; default test runs still avoid launching local simulator/UI processes.
