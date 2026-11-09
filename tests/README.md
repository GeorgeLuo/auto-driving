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

The deterministic run fails any test that starts a program. The error names the command and points at `tests/systems/`. The quality command checks tests for undefined names and rejects retired vocabulary in tracked files outside `docs/deprecated/`.

## Validating a change

Run the tests your change reaches, not every suite. `commands` and `journeys` together take about two minutes, and most changes reach a few of their modules.

1. While you work, run the deterministic tests beside the code you changed, for example `python3 tests/run.py -k decision_cycle`.
2. Before pushing, run the whole deterministic suite once. It takes about a minute and a half, and CI runs the same command.
3. For Python changes, run `python3 -m scripts.validation.check` before pushing.
   It runs Ruff, import contracts, and basedpyright; repair new diagnostics and report the result.
4. For Python changes and reviews, inspect QCA for the final committed revision.
   Use the PR's QCA inspection summary or run the local command in
   [Python quality checks](#python-quality-checks). For uncommitted work without
   a PR, inspect the changed paths with `python3 -m qca analyze <paths> --python`
   and report that a revision diff was unavailable. Investigate material
   candidates and report their locations and dispositions: fixed, retained with a
   code-based reason, or unresolved. If inspection cannot run, report why. QCA is
   informational, and a successful job does not mean its candidates were reviewed.
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
`requirements-quality.txt` for checks alone, then run
`python3 -m scripts.validation.check`. CI runs this command in the Python quality
job on PRs and main. `--ruff-only` skips type and import-contract checking for a faster editing loop;
the full command is required before pushing.
The configuration in `pyproject.toml` also works with Ruff and basedpyright editor
integrations; an editor's Ruff diagnostics may include unchanged findings.

The checks cover `autonomy/`, `cli/`, `implementations/`, `qca/`, and
`scripts/validation/`. Ruff also covers maintained Python under `deploy/` (excluding
the generated DonkeyCar vendor checkout) and checks `tests/` for name/import errors;
test complexity and typing are outside this initial rollout. Generated runtime
bundles, experiments, and vendored code are outside the check scope.
The initial type gate enables explicit `Any`, redundant instance/comparison
checks, unreachable code, missing imports, and invalid type forms. General
argument/return checking and `Any` propagation are left for a separate rollout.
Ruff also rejects new unused arguments (`ARG`), identical conditional arms
(`SIM114`), needless Boolean returns (`SIM103`), and assignments immediately
returned (`RET504`). Necessary callback signatures still need their parameters;
use an explicit override, an allowed unused-parameter name, or a justified
suppression instead of removing a parameter required by a caller.
The broader readability guard uses standard Ruff rules:

| Pressure | Rule and limit | Preferred repair |
| --- | --- | --- |
| Too many intermediate values | [`PLR0914`](https://docs.astral.sh/ruff/rules/too-many-locals/), 15 locals | Separate meaningful stages or responsibilities; keep useful names |
| Ambiguous argument order | [`PLR0917`](https://docs.astral.sh/ruff/rules/too-many-positional-arguments/), 5 positional parameters | Name optional settings with keyword-only parameters |
| Unnecessary nesting after an exit | `RET505`–`RET508`, `PLR5501` | Use guard clauses or `elif` |
| Repeated dispatch or prefix checks | `SIM116`, `PIE810` | Use a lookup for literal alternatives or a tuple of prefixes |
| Parameters reused as loop/handler targets | `PLR1704` | Give the second value its own name |
| Bare exception catches | `E722` | Catch expected failures explicitly; let process-exit signals propagate |

These complement the existing branch, statement, Boolean-expression and nesting
limits. Do not replace named intermediates with dense expressions or split a
single operation into arbitrary helpers to satisfy a count. Many named keyword
parameters and necessary boundary validation can be clearer than a generic
configuration object or hidden dispatch. The engineer role's abstraction and
readability preferences still require review.

The same quality command runs [Import Linter](https://import-linter.readthedocs.io/en/stable/)
against the contracts in `pyproject.toml`. Autonomy cannot depend on implementations
or CLI, implementations cannot depend on CLI, shared step code cannot depend on
concrete plugins or hosts, and plugin packages cannot form dependency cycles.
Forbidden dependencies include indirect paths, relative imports, and type-only
imports. Deliberate one-way plugin
specialization remains allowed; general reusable behavior belongs in shared code.
The graph checks static imports, not dynamic imports or semantic ownership.
Use `lint-imports --no-cache --no-logo` to run only these checks.

The CLI architecture tests also reject file reads in modules declared pure,
models, or rendering, using the same convention as their existing write checks.

Ruff and basedpyright report diagnostics on added or edited lines relative to the
merge base. On `main`, CI uses the previous push revision; local runs default to
`origin/main`. This avoids emitting existing debt without retaining scan records.
A diagnostic attached to an unchanged line can be missed even if a change causes
it; QCA's change inspection provides additional review leads, not a guarantee.
Keep necessary validation at external boundaries; a targeted rule suppression
should explain why the runtime check is still needed.

The quality command also runs QCA on each PR using its merge base and head. Its summary
highlights growing functions, conditional/clone candidates involving changed
functions, and added import edges outside the standard library. Candidates can
predate the PR and require inspection; the full report retains file-level evidence.
QCA is informational, including when analysis fails; the quality job log and
artifact record failures. The `quality-findings` artifact includes full JSON and
HTML evidence and `findings.json`, whose records share a type, location,
question, source, status, related locations, and evidence. The artifact includes
the revision once at the top level. Status distinguishes blocking diagnostics,
and informational review candidates. Findings are
questions for review, not labels that establish real problems. To reproduce the
full CI quality process locally against committed revisions:

```sh
PR_BASE=main PR_HEAD=HEAD python3 -m scripts.validation.check --output /tmp/quality-review
```

To run just QCA:

```sh
python3 -m scripts.validation.qca_summary --base <merge-base> --head <head> --output /tmp/qca-review
```

The summary also runs the experimental production-usage and related-function
inspection. It uses pinned Vulture on production sources, retaining test
references separately and applying Vulture's bundled callback allowances.
Newly unused symbols are included even in unchanged
files; existing unused candidates are included only in changed files. It ranks
up to three comparisons per changed function by shared call spellings in the
same module, sibling directory, or one import edge in either direction.
`inspection.json` retains source snippets, locations, scores, and limitations.
Neither absence of a static reference nor similarity proves a defect. Dynamic
entrypoints, public APIs, and deliberate shared behavior require review.

Run just this inspection, or replay the selected recent review fixes:

```sh
python3 -m scripts.validation.inspection --base <merge-base> --head <head> --output /tmp/change-inspection
python3 -m scripts.validation.review_examples --output /tmp/review-examples.json
```

The historical examples require full Git history. They report findings before
and after accepted fixes, candidate counts, and comparison ranks. They are
selected known examples, not an estimate of precision or 80% coverage. Related
functions can remain similar after a correct consolidation. Label irrelevant
prompts and add representative negative examples before promoting these
heuristics to gates. Block-clone tools and broader formatter/type-check adoption
remain separate experiments; this inspection does not replace QCA's clone scan.

Changes to the quality runner also need
`python3 tests/run.py --systems packaging -k test_quality`; changes to the QCA
summary need `python3 tests/run.py --systems qca -k test_quality_summary`.

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
