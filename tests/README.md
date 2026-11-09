# Test Suite

Tests are split into two paths.

`deterministic/` is the suite GitHub runs. A test here calls code in this process. It does not start another program. The top-level directories match the codebase: `autonomy`, `implementations`, `cli`, and `qca`. In-process integration tests also live under `integration/`.

`systems/` is everything else. These tests start another program, such as `cli/automa`, a fresh Python, `node`, Chrome, or `git`. They are grouped by the surface they drive, not by codebase package.

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

The browser install is only for the systems test that opens Chromium. CI does not install a browser and does not run this suite. Name one or more groups to run only those, for example `python3 tests/run.py --systems journeys frontend`.

Either suite takes `-k PATTERN` to run only the tests whose ID (`module.Class.method`) matches, as unittest's `-k` does: a substring, or a glob when it contains `*`. Repeat it to widen the match, for example `python3 tests/run.py --systems journeys -k upload -k arming`. A pattern that matches no test fails the run.

The deterministic run fails any test that starts a program. The error names the command and points at `tests/systems/`. The [Python quality check](#python-quality-checks) lints `tests/`, including `systems/`, so a change that adds an undefined name there fails without running the suite. It also fails when a change adds a retired word outside `docs/deprecated/`.

## Validating a change

Run the tests your change reaches, not every suite. `commands` and `journeys` together take about two minutes, and most changes reach a few of their modules.

1. While you work, run the deterministic tests beside the code you changed, for example `python3 tests/run.py -k decision_cycle`.
2. Before pushing, run the whole deterministic suite once. It takes about a minute and a half, and CI runs the same command.
3. For Python changes, run `python3 -m scripts.validation.check` before pushing.
   It compares your branch, working tree included, with `origin/main`. Repair
   blocking findings and report the result.
4. For Python changes and reviews, inspect the review questions the same command
   lists for the final revision; the PR's Python quality summary lists them for
   the committed head. Investigate material candidates and report their locations
   and dispositions: fixed, retained with a code-based reason, or unresolved. If
   a review check cannot run, the summary says why; report it. Review questions do not fail
   the check, and a passing check does not mean they were reviewed.
5. CI does not run `systems/`. Run the systems tests that drive the code you changed, chosen with a group and `-k`:

| Change touches | Run |
|---|---|
| One `automa` command family in `cli/automa_cli/` | The journeys that call that command, plus `--systems commands -k commands.<family>` for `automation`, `help`, or `simulators` |
| Plugin upload, arming, disarming, or the plugin catalog | `--systems journeys -k journeys.plugins` |
| Automation runs, streams, recordings, or replay | `--systems journeys -k automation_pipeline` |
| The Chase runtime host | `--systems journeys -k runtime_host` |
| `cli/automa_cli/frontend/pages/` | `--systems frontend` |
| `deploy/`, the release bundle, or the import surface | `--systems packaging` |
| `qca/` | `--systems qca` |

To find the tests that call a command, search for its words, for example `git grep -l '"disarm"' -- tests/systems`, then select those modules with `-k`.

Run a whole group only when the change reaches code that most of its tests go through. For `commands`, that is `cli/automa` or `cli/automa_cli/app.py`. For `journeys`, it is the runtime host loop in `autonomy/runtime/` or the runtime view. A change to `tests/run.py`, `tests/support/`, or a `tests/shared/` fixture that systems tests import needs every group that uses it.

List each command you ran and its result under Validation in the pull request. Do not rerun a check that passed unless the code it covers has changed since. After merging `main`, rerun only the tests the merged changes reach. If a test outside your change fails, run it alone with `-k`, and on `main`, before treating it as yours, and report it either way.

## Python quality checks

Install `requirements-test.txt` (which includes the quality tools), or install
`requirements-quality.txt` for checks alone. One command checks a change, whole
files, or text, and reports every finding in the same shape:

```sh
python3 -m scripts.validation.check                                # this branch and working tree against origin/main
python3 -m scripts.validation.check --base origin/main --head HEAD   # a pull request's committed head
python3 -m scripts.validation.check --base <commit>~1 --head <commit>  # one commit
python3 -m scripts.validation.check cli autonomy/runtime/client.py   # whole files
python3 -m scripts.validation.check --text cli/draft.py < draft.py   # text, as if it were cli/draft.py
```

A change reports findings throughout its changed files, not just on changed
lines. Project-wide checks also report findings introduced in other files, such
as callers broken by a signature change. Each check compares the head with its
merge base to identify those findings elsewhere. Whole files and text report all
their findings; type checking needs the project and skips text.

Ruff, basedpyright, import-linter, and the retired-vocabulary check report
blocking findings. The command exits 0 when clean, 1 for blocking findings, and 2
when a required check cannot run. `--ruff-only` skips the slower checks while
editing.

Three checks only ask review questions, and a failure of one is reported in the
summary without failing the command:

- QCA: opaque conditionals, duplicated functions, functions above its size,
  complexity, and nesting limits, and weak tests (assertions that read back an
  assigned value or match formatted output, and tests that reach production
  private names). Plain string equality is not asked about; about one test
  assertion in four compares a string.
- Vulture: production classes, functions, methods, properties, and attributes no
  production code refers to, with the tests that still use them. It matches names,
  not bindings, so a name used anywhere counts as used, and code reached only by
  reflection or configuration is reported. Findings sit at the definition.
- Related functions: for each function, up to three in the same module, an
  importing module, or the same directory that make the same calls, weighted by how
  rare each call is. For functions in changed files, it asks whether similar
  ones need the same edit, or whether they should share code.

Vulture, related functions, basedpyright, and import-linter read the whole project
on both sides of a change, so a change answers for what it does to files it did
not touch.

`--output DIR` writes `summary.md` and `findings.json`, whose findings share the
fields described in [QCA's findings](../qca/README.md#findings-for-any-context).
CI runs the command on each PR's merge base and head and on each push to `main`,
publishes the summary, and keeps both files in the `quality-findings` artifact.
The configuration in `pyproject.toml` also works with Ruff and basedpyright editor
integrations, which show existing findings as well as new ones.

Ruff, QCA, Vulture, and related functions cover `autonomy/`, `cli/`,
`deploy/`, `implementations/`, `qca/`, `scripts/validation/`, and `tests/`, less
Ruff's `extend-exclude`: the vehicle artifacts in the top-level `runtime/` and
`lab/`, the vehicle host code in `implementations/runtime/`, and vendored code.
Vulture and related functions read tests only to report where they use production
code. In `tests/`, Ruff checks only names, imports, and bare `except`s; QCA's
limits still ask about long or complex tests. basedpyright covers the paths its own
`include` lists, and import-linter the root packages of the contracts in
`[tool.importlinter]`: `autonomy` depends on neither `implementations` nor `cli`,
`implementations` does not depend on `cli`, shared decision-cycle code depends on
no plugin or host, and plugin packages import each other without cycles.
The initial type gate enables explicit `Any`, redundant instance/comparison
checks, unreachable code, missing imports, and invalid type forms. General
argument/return checking and `Any` propagation are left for a separate rollout.
Keep necessary validation at external boundaries; a targeted rule suppression
should explain why the runtime check is still needed.

Changes to the quality command also need
`python3 tests/run.py --systems packaging -k test_quality`; changes to
`qca/evaluation.py` need `python3 tests/run.py --systems qca -k test_evaluation`.

## Where a test goes

File a test by what its behavior needs, not by what its setup happens to call.

A test that runs in this process goes under `deterministic/`, next to the codebase package it exercises. An in-process test that crosses packages goes under `deterministic/integration/`. Setup alone does not make a systems test: if only the arrangement starts a program, arrange it in-process instead.

Calling `automa` does not make a systems test either. Starting `cli/automa` costs about two seconds per command, so a test that needs only a command's exit code and output runs it with `invoke_automa` from `shared/cli/invoke.py`, which returns what `run_automa` does without starting a program. Keep `run_automa` for a test of the executable itself or of a command that must run in its own process, such as `hosts run`, which starts the worker. A systems test that needs one such command runs its other commands with `invoke_automa`. `invoke_automa` shares this process's imported modules, so a command whose result depends on a fresh import, such as replaying an uploaded plugin after its source is removed, needs `run_automa`. A module that reads an environment variable when it is imported needs an entry in `IMPORT_TIME_ENV` there, or commands run in this process miss a test's value.

A test whose behavior needs another program goes under `systems/`, in the group for the surface it drives:

| Group | Covers | Starts |
|---|---|---|
| `commands/<family>/` | `automa` commands whose behavior needs another program: `help` through the executable, `simulators` against a fake headless launcher, and `automation` recovery around a running process | `cli/automa` or a child process |
| `journeys/` | Flows that cross commands and hosts: the automation pipeline, plugin upload and arming, and the runtime host lifecycle | `cli/automa`, runtime hosts, and Chromium for one upload test |
| `frontend/` | Runtime viewer and workbench page scripts in `cli/automa_cli/frontend/pages/`. Each test keeps its `.js` driver beside it | `node`; skipped when it is missing |
| `packaging/` | Fresh-interpreter import order, source visibility, and the Pi installer, service, and runner scripts | A fresh Python, `bash`, and `git` |
| `qca/` | The QCA analyzer against a scratch repository | `git` |
| `live/` | Bounded checks against a real simulator or Pi | Only with `--live-sim` or `--live-pi` |

A fixture both suites import goes under `shared/`, at the path of the deterministic code it serves.

For new or materially changed tests, name a concrete regression and the observable behavior that would catch it.

Prefer realistic state transitions and their emitted signals. Do not assert frontend source snippets, help-text paragraphs, or retired delivery-process artifacts. Do not test what a packaged plugin does: plugins change as automations do and are not fixtures of the codebase. Test the framework that loads and runs them, the catalog, and the shared code and feeds under `implementations/`; a framework test that needs a plugin defines a small one beside it. Keep happy paths and meaningful failure boundaries. Split large modules by behavior (aim below 700 lines), with only small, adjacent fixtures; do not introduce a shared testing framework. Include `__init__.py` in test directories so the repository runner discovers them.

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

This command runs the systems suite and enables one bounded automation smoke test against the Chase simulator already running at `CHASE_UI_WS_URL` (default `ws://localhost:5050/ws/control`). That test stays skipped in `python3 tests/run.py --systems`.

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

This path sends read-only requests to `/host/status`. It requires an available runtime host with a loaded action step, in mode `manual`. It does not send drive or mode-change requests, restart the runtime, use SSH, capture frames, or move the vehicle. An unreachable or unsafe endpoint is reported as `unavailable` with exit code 2 before the test suite starts; it is not converted into a passing skip. The live Pi test stays skipped unless this flag is set.
