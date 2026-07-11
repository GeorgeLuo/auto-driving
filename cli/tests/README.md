# CLI Scenario Harness

Run:

```sh
python3 cli/run_tests.py
```

The harness treats `./cli/automa` as a black-box command and checks common development flows that should stay stable while autonomy work evolves.

Default scenarios:

- First-time orientation: top-level and nested help stay scoped to the current command level.
- Discovery snapshot: `vehicles active --json` can return machine-readable JSON without touching live endpoints when providers are disabled.
- Runtime status: `vehicles automation status --json` reports deployed automation state from a disposable runtime root.
- Stale worker handling: `vehicles automation stop` marks a dead recorded worker as stopped.
- Perception contract inspection: `vehicles info perception --json` exposes the active perception schema.
- Simulator readiness: `simulators status --json` and `simulators ensure --json` are exercised against a fake `simeval` binary for online, launch, missing-frontend-tab, and launch-failure cases.

Live simulator scenario:

```sh
python3 cli/run_tests.py --live-sim
```

This ensures the Chase simulator is usable, opens/connects the Metrics UI frontend if needed, and enables a bounded `automation run --frames 1` smoke test. It is skipped by default because the live path may launch local simulator/UI processes.

Current CLI gaps to keep in mind:

- `vehicles automation run` is not deterministic without a live simulator or a fake vehicle provider, so only status/stop flows are covered offline.
- The live simulator smoke test is only invoked by `run_tests.py --live-sim`; default test runs still avoid launching local simulator/UI processes.
