# Test Suite

Tests are split into two paths.

`deterministic/` is the suite GitHub runs. A test here calls code in this process. It does not start another program. The top-level directories match the codebase: `autonomy`, `implementations`, `cli`, and `qca`. In-process integration tests also live under `integration/`.

`systems/` is everything else. These tests start another program, such as `cli/automa`, a fresh Python, `node`, Chrome, or `git`. A file that contains both kinds of test is split, and each half keeps the same relative path.

`shared/` holds fixtures both suites import. `support/` is the mechanics package both suites use: the command runner, fakes, and the offline `sitecustomize`. Neither one is a suite.

Run the deterministic suite from the repository root:

```sh
python3 -m pip install -r requirements-test.txt
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
```

The flagless command does not launch a simulator, contact a Pi, or record runtime artifacts. It reports the ten slowest tests, including setup and cleanup. Use `python3 tests/run.py --durations 25` to inspect more hotspots, or `--durations 0` to suppress the report. CI runs this same suite with coverage.

Run the systems suite:

```sh
python3 -m playwright install chromium
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --systems
```

The browser install is only for the systems test that opens Chromium. CI does not install a browser and does not run this suite.

## Where a test goes

Put an in-process test under `deterministic/`, next to the codebase package it exercises. A test that starts a program goes under `systems/` at the same relative path. A fixture both suites import goes under `shared/` at that same relative path.

For new or materially changed tests, name a concrete regression and the observable behavior that would catch it.

Prefer realistic state transitions and their emitted signals. Do not assert frontend source snippets, help-text paragraphs, or retired delivery-process artifacts. Keep happy paths and meaningful failure boundaries. Split large modules by behavior (aim below 700 lines), with only small, adjacent fixtures; do not introduce a shared testing framework. Include `__init__.py` in test directories so the repository runner discovers them.

## Support Contract

Code under `tests/support/` may only provide test mechanics:

- execute a public command as a subprocess;
- write explicit fixture documents to a disposable filesystem;
- emulate an external executable and record how it was called;
- isolate subprocess networking from real vehicle and simulator endpoints.

The CLI helper uses a 30-second subprocess deadline and loads `support/offline/sitecustomize.py` for deterministic runs. It permits local fixture servers, rejects external DNS and the default live service ports (5050 and 8887), and leaves explicit live runs unrestricted. Protocol fixtures must bind an ephemeral port. Catalogue tests copy only the selected manifests and source files into a temporary directory; never scan local model caches, virtual environments, or recorded runs as test inputs.

Support code must not call internal command handlers, calculate expected application outcomes, render operator output, or reproduce production decision logic. Scenario and contract tests retain responsibility for arranging domain state and asserting its meaning. A helper that becomes specific to one owning test module should remain beside that module instead of expanding this shared surface.

## Live Simulator Boundary

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --live-sim
```

This command runs the systems suite. It may launch local simulator and UI processes. It first requires a usable Chase frontend, then enables one bounded automation smoke test. That test stays skipped in `python3 tests/run.py --systems`.

## Live Pi Boundary

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --live-pi
```

This command runs the systems suite and enables the read-only Pi check. The Pi must be powered on, reachable at `$PIRACER_BASE_URL` (the URL the CLI's discovery reads; default `http://piracer.local:8887`), and running the boot-enabled `automa-donkey.service`. A prepared Pi reaches this state automatically after boot. The smoke path is read-only. Override the endpoint or request timeout when needed:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --live-pi \
  --picar-url http://192.168.8.120:8887 \
  --pi-timeout-s 3
```

This path sends read-only requests to `/autonomy/status`. It requires an available runtime host with a loaded action step, in mode `manual`. It does not send drive or mode-change requests, restart the runtime, use SSH, capture frames, or move the vehicle. An unreachable or unsafe endpoint is reported as `unavailable` with exit code 2 before the test suite starts; it is not converted into a passing skip. The live Pi test stays skipped unless this flag is set.
