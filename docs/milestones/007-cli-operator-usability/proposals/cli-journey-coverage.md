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
identities. Context IDs are generated from those stable fields, never from raw
argv, absolute paths, timestamps, PIDs, vehicle responses, or operator input.
They are static contexts, dynamic context switching is disabled, and IDs must
match `^[a-z0-9][a-z0-9._/-]{0,159}$` without `|`. The documented hierarchy is
`m007/bootstrap/<probe>`, `m007/journey/<journey-or-family>/<step>/cmd-<NN>`,
or `m007/support/<role>/<step>/cmd-<NN>`. Every expanded ID is unique.

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

The same argv appearing in two steps or journeys receives two distinct
contexts. An unregistered or empty measured context, an unknown CLI invocation,
a missing manifest entry, or a catalog digest mismatch prevents `pass` before
the data can be presented as complete journey coverage.

For every executed command, the durable report records:

- manifest context, role, journey/family/step, and command ordinal;
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

### Process completion and background-worker flush

The coverage session configuration preserves `.coveragerc` source, omit,
branch, relative-path, and subprocess settings and enables Coverage.py's
SIGTERM save behavior. The implementation adds `sigterm = True` to the shared
configuration and tests that the prior/default application signal behavior is
preserved.

For each expected measured Python process, the collector inventories newly
created readable parallel data shards while they are still separate. For a
background automation run it must additionally establish this lifecycle:

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

1. refuse a nonempty raw-data directory or a data-file path outside the
   resolved session root;
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
| `result` | `pass`, `incomplete`, or `failed`; reason codes; collection/finalization timestamps; cleanup verdict |
| `subject` | auto-driving commit, clean-worktree assertion, relevant-source tree identity, platform, Python and exact Coverage.py versions |
| `inputs` | manifest/config/collector/runner/catalog paths and SHA-256 values; owned source roots and omit rules; Metrics UI identity used for live commands |
| `commands` | Ordered normalized command receipts with exact context and attribution role |
| `process_completeness` | Expected/observed foreground and background process receipts, raw-shard stable IDs/hashes, readability, and flush verdicts |
| `contexts` | Expected and observed context sets; no empty/unknown contexts; journey/support membership |
| `files` | Repository-relative owned files with sorted executed statement lines and executed arcs for each context |
| `bootstrap_comparison` | Raw bootstrap, shared-with-bootstrap, bootstrap-only, and command/journey-specific line and arc sets |
| `aggregates` | Informational command, journey, support, cleanup, and all-context counts; no pass threshold |
| `integrity` | Canonical report payload digest and freshness checks against post-run source/config/tool/catalog identities |
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

### Reproducibility, freshness, and evidence

For this frontier, reproducible means:

- stable manifest/context identities and an exact replay command;
- exact source, tool, config, catalog, dependency, and external identities;
- deterministic report generation from a frozen raw session; and
- explicit deltas when a later live replay follows a different path.

It does not mean two live simulator runs must execute byte-identical line/arc
sets. Time, state, and external responses can legitimately change execution;
such differences remain visible rather than being normalized away.

An acceptance capture starts from a clean auto-driving worktree. The report
records the source commit at collection time. A freshness verifier run at the
final implementation head permits only tracked evidence-file changes after
that subject commit; any change to owned source, `.coveragerc`, collector,
runner, manifest, or catalog invalidates `pass` until collection is rerun.

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
- every manifest journey command executes with its expected exit code;
- every runner-generated Python CLI command has a registered nonempty context;
- all expected foreground and background process data is readable and
  combined from the isolated session root;
- branch/arc data is present for the configured owned source roots;
- terminal cleanup succeeds and no repository-owned automation worker remains;
- the report validates against its schema and canonical digest; and
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
| Manifest expansion, stable context identity, coverage-only runner integration | New CLI journey coverage collector |
| Process startup, parallel data, SIGTERM flush, context-preserving combination | Session-scoped Coverage.py configuration built from `.coveragerc` |
| Context-aware line/arc extraction, bootstrap classification, canonical schema | New coverage report/finalizer module using public Coverage.py APIs |
| Source and evidence freshness | Finalizer comparing subject commit and relevant path digests to final implementation head |
| Behavioral correctness of primary/continuity journeys | Existing accepted PR #88 / PR #100 evidence, not this collector |
| Later complete leaf and US-01 through US-10 accounting | Next frontier, Complete CLI surface and sequence audit |

The coverage finalizer is the single acceptance owner. A runner `pass`, a
Coverage.py summary percentage, or a successfully written combined database
cannot independently produce `m007_cli_journey_coverage_v1.result = pass`.

## Affected Paths

- `.coveragerc` and `requirements-test.txt` define the existing owned-code
  measurement engine and supported Coverage.py range.
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
  directory owns the manifest, collector, report schema/finalizer, and usage
  documentation.
- `docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/`
  owns the tracked acceptance report.
- `tests/milestones/` owns deterministic collection, isolation, attribution,
  subprocess, background-worker, runner-compatibility, schema, and freshness
  regressions.

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| Repository already has `.coverage` and `.coverage.*` sentinels | Collector uses only its fresh session root; outside names and hashes are unchanged on success and failure |
| Empty or reused/nonempty session data directory | Empty fresh root proceeds; reused raw-data root is refused before execution |
| Combine/report command omits an explicit input path | Validation rejects the implementation path; no current-directory implicit combine is permitted |
| Stale or foreign shard is injected into the session | Digest/context/source checks reject `pass`; it is never silently unioned |
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
| Evidence-only commit follows the recorded subject commit | Finalizer permits it only when every non-evidence relevant digest is unchanged |
| Auto-driving worktree is dirty at acceptance capture | Refuse canonical `pass`; diagnostic collection may be explicitly noncanonical |
| Metrics UI is unavailable or its required identity cannot be recorded | `incomplete`, not skip/pass; no simulator reconfiguration workaround |
| Command exits unexpectedly but writes usable coverage | Retain diagnostic data, mark `failed`, and run cleanup |
| Bootstrap and command execute the same import line/arc | Raw sets retain it and `shared_with_bootstrap` owns it; it is absent from `command_specific` |
| Primary help command matches the bootstrap probe | Both contexts remain named; an empty/small help delta is truthful |
| Source path arrives through absolute/relative alias or symlink escape | Canonicalize only valid owned paths; collisions/escapes reject `pass` |
| Same frozen raw data is finalized twice | Canonical `report.json` payload and digest are byte-identical |
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
  detached process-group and SIGTERM cleanup semantics. An unsupported platform
  reports `incomplete` rather than assuming worker coverage.
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
| `docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session.py` | Isolated collection, runner orchestration, process/shard receipts, and finalization CLI |
| `docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_report.py` | Public-API line/arc extraction, bootstrap classification, canonical report schema/verification |
| `docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py` | Bounded opt-in per-command context/environment hook and coverage-only non-acceptance mode |
| `docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/README.md` | Tracked capture procedure/result/non-claims |
| `docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json` | Canonical `m007_cli_journey_coverage_v1` pass report |
| `tests/milestones/test_cli_journey_coverage.py` | Collector, process, isolation, attribution, schema, and freshness tests |
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

- manifest digest/context uniqueness and all accepted command-producing fields;
- exact argv preservation and disabled-runner-mode compatibility;
- normal foreground, nested Python child, detached background worker, SIGTERM
  flush, missing worker shard, timeout, and SIGKILL non-pass fixtures;
- fresh-root enforcement, stale/foreign/unreadable shards, explicit combine,
  and unchanged repository-root coverage sentinels on all exits;
- empty/unknown contexts, support-versus-journey rollups, same-argv distinct
  contexts, bootstrap line/arc set arithmetic, and canonical source paths;
- public CoverageData API extraction with branch arcs and no private SQLite
  dependency;
- dirty source, post-capture source/config/tool/catalog drift, evidence-only
  descendant handling, canonical serialization, schema validation, and digest
  verification; and
- no numeric gate and all required `non_claims` values.

Bounded live collection, run from a clean implementation tree with an existing
safe Chase environment:

```sh
session_root="$(mktemp -d)"
python3 docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session.py \
  validate-manifest
python3 docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session.py \
  collect \
  --session-dir "$session_root" \
  --metrics-ui-origin http://localhost:5050 \
  --metrics-ui-repo /path/to/Stream-Metrics-UI
python3 docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session.py \
  finalize \
  --session-dir "$session_root" \
  --output docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
python3 docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session.py \
  verify-report \
  docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
```

The implementation PR records the actual temporary path without committing it,
the exact Metrics UI identity, final command results, report digest, context and
process completeness counts, cleanup result, and proof that pre-existing
repository-root `.coverage*` identities are unchanged. Regenerating the report
from the same frozen session must be byte-identical. A second live run may
produce a coverage delta and is not required for acceptance.

The report is accepted only at `result: pass`. Reviewers inspect the manifest,
bootstrap comparison, process-completeness receipts, at least one known
worker-only region under an automation-run context, journey/support separation,
freshness result, and explicit non-claims. No human browser judgment is part of
this validation.

## Expected Handoff

Post-merge successful implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Reproducible branch-aware owned-Python CLI journey coverage in PR #{pr}: a digest-bound command/context manifest; isolated foreground, subprocess, and SIGTERM-flushed background-worker collection; complete expected-context and process receipts; raw plus bootstrap/shared/command-specific statement and arc attribution; exact source/config/tool/catalog/runtime identities; terminal cleanup; byte-stable public-API report generation; explicit correctness/dead-code/numeric-gate non-claims; and tracked pass evidence under docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/",
  "criterion_updates": {
    "M007-07": {
      "status": "Met",
      "evidence": "PR #{pr} provides a versioned manifest and pass report attributing branch-aware owned-Python execution to the accepted primary and continuity command/journey contexts across foreground and background Python processes, with bootstrap classification, exact identities, isolation, completeness/freshness checks, cleanup, and no correctness, dead-code, or percentage-gate claim"
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
`pass` with no missing expected process/context, source drift, cleanup failure,
or unresolved collector integrity finding. An incomplete external capture or a
conclusive findings unit does not mark M007-07 `Met` and does not promote the
complete CLI surface and sequence audit.

### Sequence after this proposal merges

1. Merge this proposal into `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal`; verify `ready_for_implementation` and the
   exact accepted proposal merge commit.
3. Start `m007/cli-journey-coverage` and implement only this collector,
   opt-in runner integration, manifest/report contracts, focused tests, and
   evidence scaffold.
4. Pass deterministic process/isolation/attribution/freshness validation.
5. Run the bounded machine-only collection against the accepted primary and
   continuity catalogs; finalize and commit one internally consistent report.
6. Re-run freshness and full deterministic validation at the final
   implementation head.
7. On complete `pass`, accept the implementation and promote **Complete CLI
   surface and sequence audit**. Otherwise stop without promotion and preserve
   the exact non-pass evidence.
