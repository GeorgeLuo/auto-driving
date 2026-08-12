# Proposal: CLI journey coverage foundation

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | CLI journey coverage foundation |
| Proposal branch | `m007/cli-journey-coverage-proposal` |
| Implementation branch | `m007/cli-journey-coverage` |
| Exit criterion | M007-07 |

## Review Question

Can a developer record reproducible branch-aware owned-Python coverage for
named CLI commands and multi-command journeys across foreground and Python
subprocess/background-worker execution while separating bootstrap/import
footprint from command-specific behavior and avoiding false correctness or
dead-code claims?

This unit establishes trustworthy attribution before the complete CLI-surface
audit. It consumes the already accepted primary journey and required scenario
continuity families; it does not define another journey, re-judge their
behavior, or decide what uncovered code should become.

## Proposed Contract

### Acceptance statement

An implementation answers the review question only when it delivers both:

1. a deterministic collector/finalizer whose process, context, isolation, and
   report invariants are covered by focused tests; and
2. one tracked `pass` report produced from the accepted primary journey and the
   three required continuity families at an exact owned-source revision.

The tracked capture is a bounded, machine-only application of the collector.
It adds no new human judgment and does not re-open the behavioral acceptance in
PRs #88 or #100. If the environment cannot execute the declared journeys, or
if any expected process fails to flush coverage, the implementation remains
`incomplete`; deterministic collector tests alone do not mark M007-07 `Met`.

### Versioned command/context manifest

The implementation creates a tracked
`m007_cli_coverage_manifest_v1` manifest. The manifest is the sole authority
for turning accepted catalog structure into coverage context identity.

It must bind, by repository-relative path and SHA-256:

- `catalogs/m007-acceptance.yaml`, including every command-bearing step in the
  accepted primary journey and help audit;
- `catalogs/m007-continuity.yaml`, including all commands in
  `continuity.offline_perception`, `continuity.live_config_swap`, and
  `continuity.memory_lifecycle`; and
- one dedicated bootstrap/help probe, repeating `./cli/automa help` under a
  bootstrap context before journey collection.

The manifest records stable journey, family, step, command-ordinal, and role
identities. **Logical context IDs** are generated from those stable fields,
never from raw argv, absolute paths, timestamps, PIDs, vehicle responses, or
operator input. They must match `^[a-z0-9][a-z0-9._/-]{0,159}$` without `|`.
The documented hierarchy is `m007/bootstrap/<probe>`,
`m007/journey/<journey-or-family>/<step>/cmd-<NN>`, or
`m007/support/<role>/<step>/cmd-<NN>`. Every expanded logical ID is unique.

Logical identity alone is not raw-data provenance. Before any measured command,
the collector exclusively creates the empty session root, obtains at least 128
bits from the operating system CSPRNG, encodes them as one 32-character
lowercase hexadecimal `collection_id`, and writes that value once into the
immutable session-start receipt. The value is unpredictable before root
creation and cannot be caller-supplied, derived from a commit/timestamp/PID, or
copied from an earlier receipt; every collection performs a fresh CSPRNG draw.

Coverage.py records one **measurement context** per process in this exact form:

```text
m007-run/<collection_id>/<logical_context_id>
```

These are static contexts and dynamic context switching is disabled. Before
combination, the collector reads each shard independently through the supported
`CoverageData` API. Every context in every candidate shard must contain the
current receipt's exact `collection_id` and map one-to-one to an expected
logical context. Empty contexts, a previous/future/unknown collection ID,
multiple logical contexts in one process shard, or a value that cannot be
mapped without lossy normalization rejects the shard and prevents `pass`.
Only after this validation may the report map measurement contexts back to
stable logical contexts for command and journey aggregation.

Every Python CLI invocation performed by the runner receives a context,
including catalog commands, `capture_json` commands, precondition checks or
stops, supplemental validators, and final cleanup. Each context declares one
of these attribution roles:

| Role | Meaning | Included in declared journey rollup? |
| --- | --- | --- |
| `bootstrap` | Dedicated root-help/import baseline | No; comparison baseline only |
| `journey_command` | Command explicitly declared in the accepted catalog step | Yes |
| `supplemental_capture` | Runner-generated status/JSON/view-support command | No; separate support rollup |
| `precondition` | Safety/readiness inspection or pre-existing-worker cleanup | No; separate support rollup |
| `cleanup` | Terminal stop/status or restoration command | Separate cleanup rollup; included only when the catalog declares it as a journey command |

The same argv appearing in two steps or journeys receives two distinct logical
and measurement contexts. An unregistered or empty measured context, an
unknown CLI invocation, a missing manifest entry, a wrong `collection_id`, or
a catalog digest mismatch prevents `pass` before the data can be presented as
complete journey coverage.

For every executed command, the durable report records:

- collection ID, measurement context, logical manifest context, role,
  journey/family/step, and command ordinal;
- argv template and resolved argv as an array, normalized working directory,
  expected and observed exit code, and ordered start/end timestamps;
- normalized identities for run-produced paths such as the offline source
  directory, plus their content lineage digest; and
- whether a Python background worker is expected to inherit the command
  context.

Absolute developer paths are replaced by stable tokens such as `$REPO`,
`$SESSION`, or a named artifact identity. Redaction must not erase command or
source lineage needed to reproduce the invocation.

### Collector and runner boundary

The accepted live CLI session runner remains the owner of catalog binding,
safety preflight, command ordering, machine validators, restoration, and
cleanup. The new coverage collector owns only:

- the manifest and context mapping;
- an isolated Coverage.py session configuration and data root;
- opt-in per-command coverage environment injection;
- raw-shard inventory and combination;
- attribution/report generation; and
- the coverage-specific completeness/freshness verdict.

The runner may gain one opt-in instrumentation hook or coverage-only execution
mode. With that mode disabled, argv, environment, verdicts, artifacts, prompts,
and cleanup behavior must remain unchanged. Coverage mode may suppress human
prompts and record `behavioral_verdict: not_evaluated`; it may not turn skipped
visual gates into an M007-05/M007-10 pass, weaken safety preflight, omit machine
validators, or bypass cleanup.

Catalog command argv must remain the actual child argv. The collector must not
prefix each catalog command with an explicit `coverage run`, because doing so
would measure a different process invocation. It uses Coverage.py's documented
automatic process-start mechanism and the existing `patch = subprocess`
configuration so each foreground CLI process starts measuring at interpreter
startup and passes its static context to Python descendants.

The detached automation worker inherits the `journey_command` context of the
`vehicles automation run` invocation that launched it. The report need not
pretend foreground and worker execution are separate commands, but collection
must prove that worker-only execution reached the combined data under that
launch context.

### Authoritative pre-interpreter environment

The supported public entrypoint is a small POSIX executable launcher in the
new coverage-tool directory, not direct execution of its internal Python
module. This is an enforcement boundary: it runs before Python startup and
refuses collection or finalization if the inherited environment contains any
variable whose name begins `COVERAGE_`. This includes an invocation nested
beneath another subprocess-patched Coverage.py session. Refusal occurs before
the collector interpreter starts, so an ambient `COVERAGE_PROCESS_CONFIG`,
`COVERAGE_PROCESS_START`, `COVERAGE_FILE`, or `COVERAGE_RCFILE` cannot start the
collector under foreign measurement or write an outside-root file on exit.

After that clean launch, the collector constructs every measured CLI child
environment from the sanitized parent. At the injection boundary it removes
all `COVERAGE_*` keys, then adds only
`COVERAGE_PROCESS_START=<absolute session-owned command config>`. It leaves
`COVERAGE_FILE`, `COVERAGE_RCFILE`, and `COVERAGE_PROCESS_CONFIG` absent.
Coverage.py may then create `COVERAGE_PROCESS_CONFIG` from that verified
configuration inside the foreground CLI process for its Python descendants;
that generated value, rather than any caller value, is the sole descendant
process configuration.

Before any catalog command, the collector loads every generated command config
through supported Coverage.py configuration access and verifies the effective
data-file realpath is inside the session root, branch measurement is enabled,
the exact current measurement context is selected, subprocess patching and
parallel data are active, and SIGTERM saving is enabled. A measured support
probe under the same authoritative child environment confirms those effective
values at interpreter startup. Any mismatch fails before a journey command and
all data written by the probe is already session-contained.

Tests must invoke the public launcher with hostile individual and combined
`COVERAGE_*` environments, including a parent `patch = subprocess` session and
outside-root data/config paths. They prove refusal happens before the internal
Python entrypoint marker executes and that outside sentinels remain unchanged.
Direct Python execution of the internal module is unsupported and must refuse
normal operation unless the launcher has established its sealed clean-launch
marker.

### Process completion and background-worker flush

The coverage session configuration preserves `.coveragerc` source, omit,
branch, relative-path, and subprocess settings and enables Coverage.py's
SIGTERM save behavior. The implementation adds `sigterm = True` to the shared
configuration and tests that the prior/default application signal behavior is
preserved.

For each expected measured Python process, the collector inventories newly
created readable parallel data shards while they are still separate and
rejects any shard whose supported-API contexts do not bind to the current
collection receipt. For a background automation run it must additionally
establish this lifecycle:

1. the runner records a worker start and expected launch context;
2. runner/runtime evidence records the worker generation and, when the catalog
   has a later status command, that status observes the same generation;
3. the worker either exits cleanly after its finite frame bound or declared /
   terminal cleanup sends the normal stop signal, then the runner waits for and
   verifies death; and
4. a new readable shard containing that launch context and worker-only
   execution appears after termination.

The implementation can use shard creation windows, runner process evidence,
and known worker-only fixture/source execution to prove this without depending
on Coverage.py's private SQLite schema or treating a filename's PID formatting
as a stable API.

A timeout, missing shard, unreadable shard, still-live worker, forced SIGKILL,
or process exit path that cannot flush is `incomplete` or `failed`, never a
partial `pass`. Coverage collected before the failure may be retained for
diagnosis but must carry the non-pass result.

### Session isolation and combination

Raw data lives under a newly created, caller-named session root. The resolved
Coverage.py data-file base, parallel shards, temporary configs, combined
database, and intermediate reports must all remain under that root.

The collector must:

1. require a caller-selected, nonexistent session-root path and create it
   atomically with owner-only permissions; refuse any pre-existing file,
   symlink, empty directory, or nonempty directory, or a data-file path outside
   the resolved session root;
2. snapshot repository-root `.coverage` / `.coverage.*` identities before
   collection and prove they are unchanged afterward;
3. never call `coverage erase` against the repository or use an implicit
   current-directory combine/report operation;
4. combine only explicitly named session inputs, report every unreadable or
   rejected shard, and retain raw shards through finalization (equivalent to
   `coverage combine --keep` semantics); and
5. keep binary coverage databases and shards out of tracked evidence.

The isolated-data guarantee applies on success, refusal, command failure,
timeout, interruption, and cleanup failure. A plausible report produced after
mixing stale, foreign, or developer coverage data is an acceptance failure.

### Context-aware report schema

The durable report uses schema `m007_cli_journey_coverage_v1`. It is generated
through Coverage.py's supported `Coverage` / `CoverageData` APIs and documented
reporting interfaces, not direct SQLite queries or private underscore APIs.

Required sections are:

| Section | Required content |
| --- | --- |
| `result` | `pass`, `incomplete`, or `failed`; reason codes; immutable receipt-sourced collection/finalization timestamps; cleanup verdict |
| `subject` | auto-driving commit, clean-worktree assertion, relevant-source tree identity, platform, exact Python interpreter identity, exact Coverage.py version, and collection ID |
| `inputs` | manifest/config/collector/launcher/runner/catalog paths and SHA-256 values; owned source roots and omit rules; Metrics UI identity used for live commands |
| `dependency_environment` | Both requirements-file hashes and canonical sorted installed-distribution receipt for the exact interpreter |
| `commands` | Ordered normalized command receipts with exact context and attribution role |
| `process_completeness` | Expected/observed foreground and background process receipts, collection-bound raw-shard stable IDs/hashes, readability, and flush verdicts |
| `contexts` | Collection ID; expected/observed measurement-to-logical context mapping; no empty, foreign, or unknown contexts; journey/support membership |
| `files` | Repository-relative owned files with sorted executed statement lines and executed arcs for each context |
| `bootstrap_comparison` | Raw bootstrap, shared-with-bootstrap, bootstrap-only, and command/journey-specific line and arc sets |
| `aggregates` | Informational command, journey, support, cleanup, and all-context counts; no pass threshold |
| `integrity` | Exact canonical digest algorithm/projection and freshness checks against post-run source/config/tool/catalog/dependency identities |
| `non_claims` | Explicit false values for behavioral correctness, dead-code proof, production value, and numeric coverage gating |

Executed arcs are the context-aware raw representation used for branch
measurement. The report may include Coverage.py's informational statement and
branch totals, but it must not relabel every recorded arc as a distinct branch
opportunity or invent a per-context denominator the supported API did not
provide.

Line sets use sorted ranges and arcs use sorted integer pairs so regeneration
from the same frozen raw session is byte-stable. Entry/exit arcs with negative
line numbers remain distinguishable. Source paths are canonical
repository-relative POSIX paths under only these existing owned roots:

```text
autonomy/
implementations/
cli/automa_cli/
```

An absolute alias, case-fold collision, symlink escape, or measured file
outside those roots is rejected rather than silently merged.

### Canonical dependency-environment receipt

“Exact dependency identity” has one required representation. The collector
records SHA-256 values for repository-root `requirements.txt` and
`requirements-test.txt`, including the latter's recursive reference to the
former. For the exact Python executable used by the runner and every measured
CLI process, it then uses the standard `importlib.metadata` interface to record
**all** visible installed distributions, not a hand-selected subset that could
omit a branch-affecting transitive package.

Each distribution entry contains its PEP 503-normalized lowercase name and
exact version. When `direct_url.json` exists, the receipt records its SHA-256
but not its potentially sensitive absolute URL. Entries sort by normalized
name then version; duplicate normalized names, missing/invalid names or
versions, or conflicting visible versions prevent `pass`. The interpreter
receipt also records implementation, full version, ABI/cache tag, normalized
executable identity, and executable SHA-256.

The canonical dependency receipt is captured before commands, repeated after
cleanup, included in the report, and compared again by finalization and
`verify-report`. Any difference in requirements hashes, interpreter identity,
or installed-distribution entries makes the capture stale. This records the
environment in which branches executed; it does not introduce a lockfile or
claim that a different environment should execute the same live path.

### Bootstrap/import classification

The bootstrap probe is a separately repeated `./cli/automa help` invocation in
the same interpreter environment and source revision as the journeys. It is a
measured CLI bootstrap/help baseline, not a claim to isolate imports from every
root-help behavior.

For each command and journey, the report preserves raw executed sets and
computes:

```text
shared_with_bootstrap = raw_context ∩ bootstrap
command_specific      = raw_context - bootstrap
bootstrap_only        = bootstrap - raw_context
```

The same set operations apply to executed lines and arcs. Shared execution is
never deleted from raw coverage or called command-specific. The separately
executed primary `help-top` command remains a named command even when its
command-specific delta is empty or small.

Here, `command_specific` means “not observed in the bootstrap probe.” It does
not mean unique to that command rather than another command; cross-command and
cross-journey sharing remains visible in the raw per-context sets.

No aggregate subtraction is used to manufacture a new percentage. In
particular, an eager import appearing in a command context means only that the
line executed during that invocation; the comparison determines whether it
also belongs to the measured bootstrap footprint.

### Immutable timestamps and canonical report digest

Canonical output never calls the clock while assembling report fields. The
collector writes three immutable, session-owned receipts with UTC RFC 3339
timestamps at the events they name:

1. `session-start.json` is exclusively created before any measured process and
   contains `collection_id` and `collection_started_at_utc`.
2. `session-seal.json` is atomically written once after terminal cleanup and
   raw-shard inventory. It contains `collection_ended_at_utc`, the ordered raw
   input/shard hashes, and hashes of the command, environment, and cleanup
   receipts. After sealing, those inputs are immutable.
3. `finalization-receipt.json` is atomically written once immediately before
   the first report assembly. It contains `finalized_at_utc` plus the exact
   `session-seal.json` hash. Later finalization must reuse it; a different seal,
   missing immutable input, or attempted timestamp replacement fails.

The report's timestamps come only from those receipts. Re-running finalization
therefore does not create a new report timestamp. Volatile invocation facts
such as verifier start/end time, elapsed time, caller working directory, and
the final PR-head check live only in stdout and optional untracked
`<session-root>/diagnostics/` receipts. They are excluded from raw-input
sealing, the tracked report, and its digest domain; `verify-report` never
modifies `report.json`.

The canonical digest algorithm is exact:

1. Report values are limited to JSON null, booleans, integers, strings, arrays,
   and objects; floats and non-finite numbers are forbidden.
2. The digest projection is the complete report object with only
   `integrity.report_sha256` omitted. No timestamp or acceptance field is
   otherwise normalized or excluded.
3. Serialize that projection with Python's `json.dumps` using
   `ensure_ascii=False`, `allow_nan=False`, `sort_keys=True`, and
   `separators=(",", ":")`; encode the resulting string as UTF-8 with no
   trailing newline; SHA-256 those bytes.
4. Set `integrity.report_sha256` to the lowercase hexadecimal digest. Serialize
   the full report with the same options and write exactly one trailing LF.

Verification removes only the digest field, recomputes this projection, and
compares it with a strict lowercase-hex value. Two finalizations of the same
sealed session and immutable finalization receipt must produce byte-identical
files. A changed receipt, self-digest, pretty-print variation, volatile
timestamp insertion, reordered set-derived array, or post-write mutation fails
verification.

### Reproducibility, freshness, and evidence

For this frontier, reproducible means:

- stable logical manifest contexts, collection-bound measurement contexts, and
  an exact replay command through the authoritative launcher;
- exact source, tool, config, catalog, dependency, and external identities;
- deterministic report generation from a sealed raw session and reused
  immutable finalization receipt; and
- explicit deltas when a later live replay follows a different path.

It does not mean two live simulator runs must execute byte-identical line/arc
sets. Time, state, and external responses can legitimately change execution;
such differences remain visible rather than being normalized away.

An acceptance capture starts from a clean auto-driving worktree. The report
records the source commit at collection time. A freshness verifier run at the
final implementation head permits only tracked evidence-file changes after
that subject commit; any change to owned source, `.coveragerc`, collector,
launcher, runner, manifest, catalog, either requirements file, exact Python
interpreter, or installed-distribution receipt invalidates `pass` until
collection is rerun.

Tracked evidence lives under
`docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/` and
contains only:

- a concise `README.md` stating the procedure, result, and non-claims;
- the exact tracked manifest used for collection (or a digest-bound reference
  to the canonical tool manifest); and
- the canonical `report.json`.

The report must cover the accepted primary command journey and all three
required continuity families. Machine validators and cleanup still run.
Human visual judgments are deliberately not repeated, and the report may not
be cited as behavioral acceptance. The previously accepted evidence in PRs
#88 and #100 remains the owner of those claims.

### Pass, incomplete, and failed outcomes

`pass` requires all of the following:

- preflight identity and clean-source checks pass;
- the public launcher established a clean pre-interpreter environment and every
  effective command configuration resolves inside the session root;
- requirements, interpreter, and installed-distribution receipts match before
  commands, after cleanup, and at final verification;
- every manifest journey command executes with its expected exit code;
- every runner-generated Python CLI command has a registered nonempty logical
  context and a measurement context bound to the current collection ID;
- all expected foreground and background process data is readable and
  individually provenance-validated, then combined from the isolated session
  root;
- branch/arc data is present for the configured owned source roots;
- terminal cleanup succeeds and no repository-owned automation worker remains;
- immutable receipts validate, repeated finalization is byte-stable, and the
  report validates against its schema and exact canonical digest projection;
  and
- the freshness verifier matches the final implementation tree, allowing only
  evidence-only descendants of the recorded subject commit.

`incomplete` covers unavailable live prerequisites, an intentionally
unexecuted expected context, or missing/unflushed process data. `failed` covers
integrity, safety, command, cleanup, schema, or freshness violations. Neither
can satisfy M007-07 or promote the next frontier. A conclusive external blocker
uses a separately reviewed exceptional handoff rather than weakening `pass`.

## Ownership

| Concern | Owner |
| --- | --- |
| Catalog safety, command ordering, machine validation, restoration, cleanup | Accepted live CLI session runner |
| Pre-interpreter ambient Coverage.py refusal and sealed child environment | New POSIX coverage-session launcher |
| Manifest expansion, collection ID, logical/measurement context mapping, coverage-only runner integration | New CLI journey coverage collector |
| Process startup, parallel data, SIGTERM flush, context-preserving combination | Session-scoped Coverage.py configuration built from `.coveragerc` |
| Dependency/interpreter receipt, context-aware line/arc extraction, bootstrap classification, canonical schema/digest | New coverage report/finalizer module using public Python and Coverage.py APIs |
| Source, dependency-environment, and evidence freshness | Finalizer comparing subject commit and relevant path/environment identities to final implementation head |
| Behavioral correctness of primary/continuity journeys | Existing accepted PR #88 / PR #100 evidence, not this collector |
| Later complete leaf and US-01 through US-10 accounting | Next frontier, Complete CLI surface and sequence audit |

The coverage finalizer is the single acceptance owner. A runner `pass`, a
Coverage.py summary percentage, or a successfully written combined database
cannot independently produce `m007_cli_journey_coverage_v1.result = pass`.

## Affected Paths

- `.coveragerc`, `requirements.txt`, and `requirements-test.txt` define the
  existing owned-code measurement engine and declared dependency ranges; their
  hashes are report inputs.
- `autonomy/`, `implementations/`, and `cli/automa_cli/` are measured inputs;
  this frontier does not modify their product behavior.
- `cli/automa_cli/automation.py` supplies the detached Python-worker topology
  the collector must measure through process configuration rather than product
  instrumentation.
- `docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/`
  remains the command/safety/cleanup execution owner and gains only bounded
  opt-in coverage integration.
- A new
  `docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/`
  directory owns the pre-interpreter launcher, manifest, collector, report
  schema/finalizer, and usage documentation.
- `docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/`
  owns the tracked acceptance report.
- `tests/milestones/` owns deterministic collection, isolation, attribution,
  subprocess, background-worker, runner-compatibility, schema, and freshness
  regressions.

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| Repository already has `.coverage` and `.coverage.*` sentinels | Collector uses only its fresh session root; outside names and hashes are unchanged on success and failure |
| Public launcher inherits any `COVERAGE_*` variable | Refuse before Python startup; neither internal-entrypoint marker nor outside-root data changes |
| Collector is invoked beneath another `patch = subprocess` Coverage.py process | Launcher detects inherited process configuration and refuses before its Python interpreter can be auto-measured |
| Direct internal-Python entrypoint bypasses the launcher | Refuse normal operation because no sealed clean-launch marker exists; documented commands never use this path |
| Generated command config or effective `COVERAGE_FILE` resolves outside the session | Refuse before a catalog command; the measured config probe writes only inside the session |
| Nonexistent versus pre-existing empty/nonempty/symlink session root | Collector atomically creates only the nonexistent root; every pre-existing form is refused before execution |
| Combine/report command omits an explicit input path | Validation rejects the implementation path; no current-directory implicit combine is permitted |
| Same-commit, same-config, same-logical-context worker shard from a prior successful run is copied into the fresh root | Per-shard public-API inspection sees the prior collection ID and rejects it before combine; it cannot conceal the missing current worker |
| Other stale or foreign shard is injected into the session | Collection-ID/context/source checks reject `pass`; it is never silently unioned |
| Parallel shard is unreadable or has no branch arcs | `incomplete`/`failed` with the shard receipt; no summary-only fallback |
| Normal foreground CLI command | A registered context and readable shard appear with expected command receipt and owned execution |
| Foreground command spawns a normal Python child | Child-owned sentinel execution combines under the command context |
| Detached automation worker exits through normal stop/SIGTERM | Worker-only execution and a post-stop readable shard are present under the launch context |
| Worker remains alive, times out, or is SIGKILLed before flush | Terminal cleanup still runs; result cannot be `pass` |
| Foreground shard exists but expected worker shard is absent | Process completeness fails; foreground data cannot mask the missing worker |
| Empty, unknown, malformed, or duplicate context ID | Fail closed before report `pass` |
| Identical argv appears in two steps/families | Separate stable contexts and separate rollups are preserved |
| Runner emits supplemental status/precondition/cleanup commands | Every command is labeled; support execution cannot inflate declared journey-only rollups |
| Catalog or manifest changes after collection | Freshness fails until the full capture is rerun |
| Owned source or `.coveragerc` changes after collection | Freshness fails even if the old report still parses |
| Requirements file, interpreter, or installed distribution changes after collection | Dependency freshness fails with the exact receipt delta; report cannot remain `pass` |
| Two allowed environments use different NumPy/OpenCV/Pillow/Requests/PyYAML or transitive versions | Each report records a different canonical distribution receipt; the path delta is interpretable rather than silently called irreproducible |
| Duplicate normalized distribution names or sensitive `direct_url.json` | Duplicate/conflicting identities fail; only the direct-URL hash is recorded |
| Evidence-only commit follows the recorded subject commit | Finalizer permits it only when every non-evidence relevant digest is unchanged |
| Auto-driving worktree is dirty at acceptance capture | Refuse canonical `pass`; diagnostic collection may be explicitly noncanonical |
| Metrics UI is unavailable or its required identity cannot be recorded | `incomplete`, not skip/pass; no simulator reconfiguration workaround |
| Command exits unexpectedly but writes usable coverage | Retain diagnostic data, mark `failed`, and run cleanup |
| Bootstrap and command execute the same import line/arc | Raw sets retain it and `shared_with_bootstrap` owns it; it is absent from `command_specific` |
| Primary help command matches the bootstrap probe | Both contexts remain named; an empty/small help delta is truthful |
| Source path arrives through absolute/relative alias or symlink escape | Canonicalize only valid owned paths; collisions/escapes reject `pass` |
| Same sealed raw data is finalized twice at different wall-clock times | Immutable finalization receipt is reused; canonical `report.json` payload and digest are byte-identical |
| Digest verification reads `integrity.report_sha256` | The field is omitted from the digest projection, then strictly compared; no self-referential hash |
| Finalizer or verifier adds invocation timestamps/paths/durations | Volatile values remain only in stdout or untracked diagnostics and cannot change the tracked report |
| Session seal or finalization receipt is replaced, reordered, or mismatched | Receipt/hash validation fails; report is not regenerated from altered provenance |
| Live replay executes a different valid path | Report exposes the line/arc delta; reproducibility is not falsely failed |
| Consumer asks whether covered behavior is correct | `non_claims.behavioral_correctness` is false; refer to behavioral tests/evidence |
| Consumer asks whether unobserved code is dead | `non_claims.dead_code` is false; defer capability reconciliation to M007-09 |
| Aggregate coverage is low or high | Informational only; no `fail_under`, milestone gate, deletion, or value claim |
| Coverage mode is disabled on the existing runner | Existing runner behavior and evidence schemas are unchanged by regression test |

## External Assumptions

- The repository-supported `coverage>=7.15,<8` dependency provides documented
  [measurement contexts](https://coverage.readthedocs.io/en/latest/contexts.html),
  [Python-process startup and SIGTERM handling](https://coverage.readthedocs.io/en/latest/subprocess.html),
  [explicit combination](https://coverage.readthedocs.io/en/latest/commands/cmd_combine.html),
  and the supported
  [CoverageData API](https://coverage.readthedocs.io/en/latest/api_coveragedata.html).
  The exact runtime version is recorded because patch releases can change
  diagnostics or serialization.
- Foreground and background Automa processes use the same Python environment
  with Coverage.py installed. Non-Python subprocess execution is outside this
  Python report and must not be presented as measured.
- Acceptance collection runs on a POSIX environment that supports the runner's
  pre-interpreter shell launcher plus detached process-group and SIGTERM cleanup
  semantics. An unsupported platform reports `incomplete` rather than bypassing
  the launcher or assuming worker coverage.
- The exact Python interpreter exposes installed-distribution metadata through
  the standard `importlib.metadata` API. Missing or contradictory metadata is
  an explicit non-pass environment finding, not a reason to omit the receipt.
- The local Metrics UI/Chase environment can execute the already accepted
  observation-only commands and provides a recordable repository/version
  identity. This proposal authorizes no hidden simulator setup, movement, or
  external repository change.
- PR #88 and PR #100 remain the behavioral authority for the accepted journeys.
  Their acceptance is not inferred from coverage and is not invalidated by
  legitimate coverage deltas.

## Non-Goals

- Full parser-leaf inventory or exhaustive #88 US-01 through US-10
  definition/disposition (M007-08).
- Capability-level expose/retain/remove decisions (M007-09).
- Product CLI fixes, new commands, redesign, or deletion.
- Re-running HITL visual acceptance or changing the accepted primary and
  continuity catalogs to make coverage easier.
- Measuring Metrics UI JavaScript, third-party dependencies, shell commands,
  or non-Python subprocesses in this report.
- Treating root-help execution, import-time execution, or any covered line as
  proof that a capability was meaningfully used or correct.
- Treating a missing line/arc as proof of dead, unused, removable, or
  CLI-required code.
- A repository-wide or per-journey numeric coverage threshold.
- Replacing the existing informational deterministic-test coverage job.
- Tracking binary `.coverage` databases or raw process shards as milestone
  evidence.
- Introducing a dependency lockfile or claiming the recorded environment is
  the only valid environment; the receipt records what executed this capture.
- Guaranteeing byte-identical paths across repeated live simulator runs.

## File Impact

### Proposal PR only

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/cli-journey-coverage.md` | Add this reviewed contract |
| `docs/milestones/007-cli-operator-usability/plan.md` / `plan.html` | Record `proposal_in_review` transition |

### Expected implementation PR

| Path | Change |
| --- | --- |
| `.coveragerc` | Enable SIGTERM data save while preserving branch/source/relative/subprocess behavior |
| `docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/README.md` | Operator/developer procedure and non-claims |
| `docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/manifest.json` | `m007_cli_coverage_manifest_v1` accepted journey/context mapping |
| `docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session` | Public POSIX entrypoint; reject ambient Coverage.py control before interpreter startup and seal the clean launch |
| `docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session.py` | Internal isolated collection, collection-ID/context binding, runner orchestration, process/shard/immutable receipts, and finalization CLI |
| `docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_report.py` | Public-API line/arc extraction, dependency receipt, bootstrap classification, exact canonical report/digest schema and verification |
| `docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py` | Bounded opt-in per-command context/environment hook and coverage-only non-acceptance mode |
| `docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/README.md` | Tracked capture procedure/result/non-claims |
| `docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json` | Canonical `m007_cli_journey_coverage_v1` collection-bound pass report |
| `tests/milestones/test_cli_journey_coverage.py` | Launcher, collector, process/provenance, isolation, dependency, attribution, canonical digest/schema, and freshness tests |
| `tests/milestones/test_live_cli_session_runner.py` | Disabled-mode compatibility and coverage-only safety/cleanup/context tests |

No file under `autonomy/`, `implementations/`, or `cli/automa_cli/` is planned
to change. If trustworthy background collection requires product
instrumentation rather than the process boundary above, stop and amend the
proposal instead of hiding that expansion in implementation.

## Validation Plan

### Proposal PR

```sh
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 -m unittest \
  tests.docs.test_milestone_proposal_workflow \
  tests.docs.test_milestone_planning
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/cli-journey-coverage-proposal \
  --base-sha <merge-base> \
  --head-sha <head>
git diff --check
```

Reviewers verify that the proposal changes only the declared artifact and plan
transition, its handoff materializes against M007-07 and the queued M007-08
candidate, and no implementation file is present.

### Implementation PR (after proposal acceptance)

Deterministic focused validation:

```sh
python3 -m unittest \
  tests.milestones.test_cli_journey_coverage \
  tests.milestones.test_live_cli_session_runner
python3 tests/run.py
```

Focused tests must cover every adversarial row that does not require the live
Metrics UI, including:

- manifest digest/logical-context uniqueness, unpredictable collection-ID
  generation, exact measurement-to-logical mapping, and all accepted
  command-producing fields;
- exact argv preservation and disabled-runner-mode compatibility;
- public-launcher refusal before Python startup for hostile ambient
  `COVERAGE_*` variables and parent subprocess-patched collection, sealed
  internal-entrypoint enforcement, effective-config containment, and unchanged
  outside-root sentinels;
- normal foreground, nested Python child, detached background worker, SIGTERM
  flush, missing worker shard, same-commit/same-logical-context prior worker
  shard, timeout, and SIGKILL non-pass fixtures;
- atomic nonexistent-root enforcement, stale/foreign/unreadable shards,
  explicit combine, and unchanged repository-root coverage sentinels on all
  exits;
- empty/unknown contexts, support-versus-journey rollups, same-argv distinct
  contexts, bootstrap line/arc set arithmetic, and canonical source paths;
- public CoverageData API extraction with branch arcs and no private SQLite
  dependency;
- requirements-file hashes, canonical interpreter/all-distribution receipts,
  duplicate/conflicting distribution refusal, direct-URL hashing, and
  dependency drift;
- dirty source, post-capture source/config/tool/catalog drift, evidence-only
  descendant handling, immutable start/seal/finalization receipts,
  byte-identical repeated finalization, exact digest projection, volatile
  diagnostics exclusion, schema validation, and digest verification; and
- no numeric gate and all required `non_claims` values.

Bounded live collection, run from a clean implementation tree with an existing
safe Chase environment:

```sh
session_parent="$(mktemp -d)"
session_root="$session_parent/collection"
coverage_session="docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session"
"$coverage_session" \
  validate-manifest
"$coverage_session" \
  collect \
  --session-dir "$session_root" \
  --metrics-ui-origin http://localhost:5050 \
  --metrics-ui-repo /path/to/Stream-Metrics-UI
"$coverage_session" \
  finalize \
  --session-dir "$session_root" \
  --output docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
"$coverage_session" \
  verify-report \
  docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
```

The implementation PR records the actual temporary path without committing it,
the exact Metrics UI/dependency/interpreter identity, collection ID, final
command results, report digest, logical/measurement context and process
completeness counts, cleanup result, immutable receipt hashes, and proof that
pre-existing repository-root `.coverage*` identities are unchanged.
Regenerating the report from the same sealed session must be byte-identical. A
second live run may produce a coverage delta and is not required for acceptance.

The report is accepted only at `result: pass`. Reviewers inspect the manifest,
collection ID and measurement-to-logical mapping, launcher/config containment
receipts, canonical dependency environment, bootstrap comparison,
process-completeness receipts, at least one known worker-only region under an
automation-run context, immutable timestamp/seal receipts and report digest,
journey/support separation, freshness result, and explicit non-claims. No human
browser judgment is part of this validation.

## Expected Handoff

Post-merge successful implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Reproducible branch-aware owned-Python CLI journey coverage in PR #{pr}: a digest-bound command/context manifest; unpredictable collection-bound shard provenance; pre-interpreter ambient Coverage.py refusal and session-contained effective configuration; isolated foreground, subprocess, and SIGTERM-flushed background-worker collection; complete expected-context and process receipts; canonical requirements/interpreter/all-distribution identity; raw plus bootstrap/shared/command-specific statement and arc attribution; immutable timestamp receipts and an exact byte-stable public-API report digest; terminal cleanup; explicit correctness/dead-code/numeric-gate non-claims; and tracked pass evidence under docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/",
  "criterion_updates": {
    "M007-07": {
      "status": "Met",
      "evidence": "PR #{pr} provides a versioned manifest and collection-bound pass report attributing branch-aware owned-Python execution to the accepted primary and continuity command/journey contexts across foreground and background Python processes, with pre-interpreter environment isolation, canonical dependency identity, bootstrap classification, immutable receipts, exact digest semantics, completeness/freshness checks, cleanup, and no correctness, dead-code, or percentage-gate claim"
    }
  },
  "risk_remove": [
    "Eager CLI imports execute shared module top levels before a leaf handler runs"
  ],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "Complete CLI surface and sequence audit is promoted after trustworthy named-command and journey attribution satisfies M007-07.",
    "revisit_when": "The audit maps every parser leaf and #88 US-01 through US-10 entry to realistic usage, evidence, safety, coverage treatment, and an explicit owned disposition before capability-level reconciliation."
  }
}
```

This success template applies only to a tracked `m007_cli_journey_coverage_v1`
`pass` with no foreign collection identity, ambient/outside-root Coverage.py
control, missing expected process/context, source or dependency drift,
receipt/digest mismatch, cleanup failure, or unresolved collector integrity
finding. An incomplete external capture or a conclusive findings unit does not
mark M007-07 `Met` and does not promote the complete CLI surface and sequence
audit.

### Sequence after this proposal merges

1. Merge this proposal into `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal`; verify `ready_for_implementation` and the
   exact accepted proposal merge commit.
3. Start `m007/cli-journey-coverage` and implement only this collector,
   authoritative launcher, opt-in runner integration, collection-bound
   manifest/report/dependency/receipt contracts, focused tests, and evidence
   scaffold.
4. Pass deterministic process/isolation/attribution/freshness validation.
5. Run the bounded machine-only collection against the accepted primary and
   continuity catalogs; finalize and commit one internally consistent report.
6. Re-run freshness and full deterministic validation at the final
   implementation head.
7. On complete `pass`, accept the implementation and promote **Complete CLI
   surface and sequence audit**. Otherwise stop without promotion and preserve
   the exact non-pass evidence.
