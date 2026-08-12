# CLI journey coverage collector

This tool records branch-aware owned-Python execution for the accepted M007
primary CLI journey and the three required continuity families. It attributes
raw lines and arcs to stable command and journey identities while binding each
Coverage.py shard to one unpredictable collection ID.

It answers **which owned Python paths executed**. It does not decide whether
the behavior was correct, whether unobserved code is dead, whether the path has
production value, or whether a numeric coverage percentage is sufficient.
Those are explicit false values in every canonical report.

## Enforcement boundary

Use only the executable `coverage_session`. It checks the inherited environment
before Python starts and refuses every `COVERAGE_*` variable, including an
ambient subprocess-patched coverage session. Direct execution of
`coverage_session.py` is unsupported and always refuses; the launcher imports
the internal module only after the PATH-independent environment check.

For each measured command, the collector:

- preserves the accepted catalog argv exactly;
- supplies a sanitized child environment containing only a session-owned
  `COVERAGE_PROCESS_START` control variable;
- verifies the effective branch, relative-file, source, omit, subprocess,
  parallel, SIGTERM, context, and data-path configuration;
- uses a measurement context of
  `m007-run/<collection_id>/<logical_context_id>`; and
- rejects symlinked/non-regular shard inputs, inspects every parallel shard
  through the public `CoverageData` API, and seals every retained raw shard
  before finalization.

The live session runner remains the safety, command-ordering, machine-validation,
restoration, and cleanup owner. Coverage mode is opt-in and records
`behavioral_verdict: not_evaluated`; it cannot produce M007-05 or M007-10
behavioral acceptance.

## Preconditions

- POSIX process and signal semantics.
- A clean auto-driving implementation checkout.
- A clean, identifiable local Stream Metrics UI checkout serving the accepted
  safe Chase environment.
- The same `python3` interpreter for the collector, CLI shebang, and spawned
  Python worker, with `coverage>=7.15,<8` installed.
- A caller-selected session path that does not already exist. A file, symlink,
  empty directory, or nonempty directory at that path is refused.

The collector does not start, reconfigure, or move the simulator. It consumes
the accepted observation-only runner catalogs.

## Canonical procedure

From the repository root:

```sh
session_parent="$(mktemp -d)"
session_root="$session_parent/collection"
coverage_session="docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session"

"$coverage_session" validate-manifest
"$coverage_session" collect \
  --session-dir "$session_root" \
  --metrics-ui-origin http://localhost:5050 \
  --metrics-ui-repo /path/to/Stream-Metrics-UI
"$coverage_session" finalize \
  --session-dir "$session_root" \
  --output docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
"$coverage_session" verify-report \
  docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
```

The session root is deliberately not tracked. It retains raw shards, generated
configs, runner sessions, dependency/source/cleanup receipts, the combined
database, the immutable session seal, and the immutable first-finalization
receipt for review and diagnosis.

## Pass conditions

A canonical `pass` requires all declared journey and supplemental commands to
execute with expected exits, every executed command context to have readable
branch data, each automation launch to bind its foreground shard to the same
PID/run generation through a later observation and terminal death, and a
distinct worker shard to be visible after termination. Offline replay commands
must bind the exact manifest, ordered-input, and frame-receipt digests they
produced or consumed. Cleanup must prove every observed worker dead, both
runner machine preflights must pass, and repository coverage sentinels must
remain unchanged.

The requirements files, exact interpreter executable, every visible installed
distribution, owned source/config/tool paths, catalogs, manifest, and Metrics UI
identity must remain unchanged through collection and verification. A stale or
foreign shard, missing worker, failed cleanup, changed immutable receipt, or
digest mismatch cannot produce `pass`.

The same semantic validator owns collection, finalization, and verification.
It recomputes context, worker, bootstrap, command, journey-family,
support/cleanup, and all-context summaries, rejects contradictory pass fields,
and recursively rejects unnormalized local absolute paths.

## Reproducibility

`session-start.json`, `session-seal.json`, and
`finalization-receipt.json` own the report timestamps. Re-finalizing a sealed
session reuses the first finalization receipt. The report digest projects the
entire report except `integrity.report_sha256`, uses sorted compact UTF-8 JSON,
and writes exactly one trailing line feed. Repeated finalization is required to
be byte-identical; invocation times and local session paths stay outside the
tracked report.
