# Quantitative change analysis prototype

This package is the standalone, observations-only experiment for issue #180.
It is intentionally Python-first and standard-library-only.  It does not
assign a quality score, change proposal workflow state, or create a blocking
check.

From the repository root:

```sh
python3 -m qca analyze .
python3 -m qca analyze --ref HEAD
python3 -m qca diff --base <base-ref> --head <head-ref>
python3 -m qca diff --base <base-ref> --head <head-ref> --json report.json --markdown report.md
python3 -m qca diff --base <base-ref> --head <head-ref> --json report.json --html report.html
python3 -m qca render report.json --html report.html
python3 -m qca backtest --manifest qca/backtests/m008.json
```

Use `--include-root` repeatedly to bound core measurements to an ownership
area.  The source inventory still classifies files outside those roots so
changed-file attribution remains visible.  JSON is the stable machine-facing
record; Markdown is a compact operator-facing rendering.  Diff
`review_targets` are deterministic inspection prompts for agents and humans,
not findings that should be accepted without review.  Rename detection is
deliberately disabled in v0: a move is represented as an old-file deletion and
a new-file addition until a stable callable-matching rule is justified.

## Factor reports (analyzer 0.3)

Normal snapshots and diffs include `factors`, with numeric measurements,
locations to inspect, and limitations. Diffs carry `base_metrics` and `delta`;
their findings are restricted to changed files while their totals describe
the full configured Python scope. The report stores the configuration as well
as its hash. HTML renders the same JSON record, including all inspection
candidates and expandable raw data.

| Factor | Computed evidence | Interpretation |
| --- | --- | --- |
| `redundancy` | Nontrivial callable clones and repeated branch bodies | Candidates for shared behavior; identifier normalization is approximate |
| `patterns` | Recognized error-handling patterns | Inspect intent at the owning boundary |
| `functional_style` | State writes, mutable defaults, recognized effects, unresolved calls | Candidates for pure transformations; absence of recognized effects does not prove purity |
| `functionality` | Stub and obvious unreachable-code patterns | Inspect intentional hooks and protocols before removing code |
| `coupling` | Resolved local edges, fan-in/fan-out, cycles | Dependencies to examine, including relative imports |
| `contracts` | Public signatures, literal return-key shapes, CLI declarations | Static surface changes; no runtime compatibility proof |
| `test_effectiveness` | Literal/same-operand assertion candidates; string-expected and formatted-literal assertions; private production imports/calls from tests | Runtime coverage and mutation evidence can be attached separately |
| `end_to_end` | Attached differential execution evidence | `not_measured` until evidence is supplied |
| `ui_behavior` | Attached browser evidence | API or static HTML inspection alone does not establish visual behavior |
| `lifecycle` | Recognized lifecycle sites plus optional execution evidence | Source names alone do not prove cleanup or side-effect boundaries |

Application HTML/CSS counts toward production file/churn attribution. It is
listed as unsupported for Python structural metrics. Effective/logical LOC
and the new factors are measured only on included Python files. Earlier
0.2 artifacts retain their original classification and should not be compared
numerically with 0.3 output without rerunning both revisions.

`diff --evidence evidence.json` attaches a `qca/verification/v1` record whose
base/head must match the requested revisions. The record has a `factors`
mapping for the four verification factors above. Each entry supplies
`status` (`passed`, `failed`, or `not_measured`) and command/results or
expected/actual data; an unmeasured entry explains its reason. This is
explicitly caller-supplied evidence, not an authenticated execution receipt.
The schema is documented in `qca/factors/verification.py`.

## Reproduce the refined M008 experiment

Install the repository test dependencies, then run from the repository root:

```sh
python3 -m qca.experiments.refine_m008 --output-dir /tmp/m008-refined
```

The runner measures seven historical merge transitions, including the whole
milestone contribution to `main`. It then reconstructs candidate patches
in disposable historical clones, runs consumer tests with line coverage, and
compares replay probes with the baseline. Two original trials plus three later
workbench samples are proposed simplifications; one original trial
deliberately removes validation to check whether a lower static count is
rejected by behavior checks. Product changes exist only in those temporary
clones and the committed patch inputs. `--trials` selects candidate ids;
`--skip-historical` reuses the committed state measurements.

The runner writes `m008-report.json`, `.md`, and `.html` as regular per-analysis
reports for the whole milestone, plus `experiment.json` / `experiment.md` for
hypotheses, measured deltas, execution outcomes, and trial decisions. Runtime
logs, probe traces, and line coverage stay in the output directory. The probe
uses synthetic inputs; full browser acceptance and universal behavior
equivalence are not claimed.

Committed copies of the experiment record and the milestone Markdown report:

- `docs/synthesis/artifacts/m008-qca-refined.json`
- `docs/synthesis/artifacts/m008-qca-refined.md`
- `docs/synthesis/artifacts/m008-qca-refined-report.md`
