# Quantitative change analysis prototype

This package is the standalone, observations-only experiment for issue #180.
It is intentionally Python-first and standard-library-only.  It does not
assign a quality score, change proposal workflow state, or create a blocking
check.

From the repository root:

```sh
python3 -m qca analyze .
python3 -m qca analyze path/to/file.py
python3 -m qca analyze path/to/dir
python3 -m qca analyze src tests
python3 -m qca analyze qca tests/qca --python
python3 -m qca analyze qca/analyzer.py tests/qca --python
python3 -m qca analyze --ref HEAD qca tests/qca
python3 -m qca diff --base <base-ref> --head <head-ref>
python3 -m qca diff --base <base-ref> --head <head-ref> --python qca tests/qca
python3 -m qca diff --base <base-ref> --head <head-ref> --json report.json --markdown report.md
python3 -m qca diff --base <base-ref> --head <head-ref> --json report.json --html report.html
python3 -m qca render report.json --html report.html
python3 -m qca backtest --manifest qca/backtests/m008.json
```

`analyze` and `diff` accept one or more files and directories, including
scattered collections, so a change that spans the tree can be measured in one
report. Git is not required for `analyze` unless `--ref` is set. Multiple
paths are combined under their common parent so `tests/` still classifies as
tests. `--python` keeps only `.py` / `.pyi` files.

Use `--include-root` repeatedly to bound core measurements to an ownership
area.  The source inventory still classifies files outside those roots so
changed-file attribution remains visible.  JSON is the stable machine-facing
record; Markdown is a compact operator-facing rendering.  Factor findings
include additive `indicator` identity and `evidence` source blocks; Markdown
keeps a compact list, while HTML presents the same code.  Diff
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
candidates, source evidence, and expandable raw data. Each static finding
carries `indicator` (`id`, `version`) and `evidence` (`revision`, `primary`,
`related`, `pattern`, `uncertainty`, `inspection_question`, `primary_reason`).

| Factor | Computed evidence | Interpretation |
| --- | --- | --- |
| `redundancy` | Nontrivial callable clones and repeated branch bodies | Candidates for shared behavior; identifier normalization is approximate |
| `patterns` | Recognized error-handling patterns and redundant `any()` guards | Inspect intent at the owning boundary |
| `functional_style` | State writes, mutable defaults, recognized effects, unresolved calls | Candidates for pure transformations; absence of recognized effects does not prove purity |
| `functionality` | Stub and obvious unreachable-code patterns | Inspect intentional hooks and protocols before removing code |
| `coupling` | Resolved local edges, fan-in/fan-out, cycles | Dependencies to examine, including relative imports |
| `contracts` | Public signatures, literal return-key shapes, CLI declarations | Static surface changes; no runtime compatibility proof |
| `test_effectiveness` | Literal/same-operand and assignment-readback candidates; string-expected and formatted-literal assertions; private production imports/calls from tests | Runtime coverage and mutation evidence can be attached separately |
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

### Assignment-readback candidates (analyzer 0.3.6)

`test_effectiveness.assignment_readback_candidates` counts assertions such as
`value = 3; assert value == 3` and `obj.value = 3; self.assertEqual(obj.value, 3)`.
The metric lives under `factors.test_effectiveness.metrics`; each
`assignment_readback` finding points to the assertion and includes an
`assignment` object with the source path, line, column, and expression.
Readbacks take precedence over string-expected classification so each assertion
contributes once to `candidate_assertion_count`.

The scan tracks scalar literal assignments to local names or one-level
attributes in straight-line `test_*` function bodies. It recognizes single
`==` / `is` assertions and two-argument `assertEqual` / `assertIs` calls in
either operand order. Calls, control flow, other statements, uncertain writes,
and assertions end tracking; aliases, constructors, subscripts, and nested
control-flow bodies are not followed. These deliberate limits favor a small,
inspectable candidate set over speculative dataflow analysis.

A candidate asks whether a meaningful boundary was exercised. Attribute writes
and reads can invoke useful setters/getters, so this is not proof of a bad test.
Normalization, serialization, transport, and consumer-output assertions remain
valuable. No count is a quality grade or a reason to delete a test automatically.

Analyzer `0.3.7` retains all test inspection candidates and reports their
completeness without the obsolete 64-site limit metadata. Shared factor path
normalization and finding ordering keep those behaviors consistent.

### Manually discovered redundant guards (analyzer 0.3.8)

Manual inspection found repeated `bool(container) and any(...)` guards in
`qca/factors/verification.py`. For ordinary containers, `any()` already returns
false on empty input, including generator expressions over that container.
Those production sites now use `any(...)` directly; the detector still reports
the original two-part syntax wherever it remains.
The `patterns.metrics.redundant_any_guard_count` metric reports this narrow
syntax, with `redundant_any_guard` findings carrying path, line, and expression.

The detector requires a two-part `and`, explicit `bool(name)`, and `any(name)`
or one synchronous generator over the same name. It also recognizes zero-arg
`values()`, `keys()`, and `items()` on that name. It does not follow aliases,
attribute guards, iterator factories, or multiple generator clauses. `all()`,
`or`, guards for None, and checks over another collection are excluded because
they may protect different behavior.

These are manual-inspection candidates: builtin shadowing, custom truthiness,
custom mapping methods, and concurrent mutation can make the guard observable.
The scan does not prove these absent and does not rewrite code. Tests establish
result and predicate-call equivalence for a bounded sample of ordinary
containers and preserve `all([])` as a negative control. The original sites
remain available for inspection; candidate count is not a removal target.

### Measurement correctness (analyzer 0.3.10)

Git path transport is lossless for whitespace, non-ASCII, and escaped names in
revision inventories and diffs. Single-file Git diffs retain basename inventory
paths and callable locations, including deleted files identified from the
selected revision. Evidence attachment accepts null, booleans, integers, finite
floats, strings, string-keyed mappings, and lists/tuples. Mappings become plain
dictionaries and tuples become lists; sets, unsupported scalar objects,
non-string keys, non-finite floats, and cycles are rejected with their evidence
location before report rendering.
Lifecycle `sites_are_complete` is derived from retained and total site counts.

Coupling edges retain per-import `resolution_details`: `exact` matches a
supplied module, `ancestor_fallback` matches only a parent, and `symbol_owner`
locates the module of a `from ... import ...` request without proving the symbol
exists. Records include `requested_name`, `matched_module`, and
`unresolved_suffix`. Wholly unresolved imports stay in `unresolved_external`.
Aggregated edges expose their common resolution or `mixed`; inspect the details
for each import. Edge and cycle counts include candidate fallback dependencies.
`cycles` remains a list of SCC member sets, explicitly marked by
`cycle_representation: scc_members`; member order never asserts an arrow path.

Approximate clone matching remains deliberately broad. Each clone occurrence
now includes an end line, reconstructed code, and `identifier_usage` containing
identifier spellings and their equality pattern in AST traversal order. The
finding's `identifier_usage_differs` exposes distinctions erased by matching,
such as `a + b` versus `x + x`. This is spelling evidence, not lexical binding
resolution or proof of equivalent behavior.

### Independently testable indicators (analyzer 0.3.11)

Detectors live in `qca/indicators/` as separately importable modules that share
one `AnalysisContext`. Structure and coupling reuse the same cached AST when
parse options match; verification keeps stub files, case-insensitive `.py`
suffixes, and type-comment parsing. `qca.indicators.INDICATOR_REGISTRY` lists
the shipped detector identities. There is no plugin discovery.

When a detector has an AST node, `evidence.primary` is that exact range and
source text. Graph-wide observations set `primary` to `null`, explain the
broader scope in `primary_reason`, and cite contributing blocks in
`evidence.related`. Git-backed `analyze --ref` and `diff` reports set
`evidence.revision` to the analyzed SHA; in-memory and working-tree analyses
leave it `null`. Matching rules, metrics, and historic finding fields are
unchanged.

Independent tests can call a detector predicate or `analyze(context)` without
building a public report. New prototype indicators should follow the same
module, registry, and evidence shape.

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
