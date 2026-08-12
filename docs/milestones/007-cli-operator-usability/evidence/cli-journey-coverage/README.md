# M007 CLI journey coverage evidence

This directory owns the canonical `m007_cli_journey_coverage_v1` report for the
accepted primary CLI journey and the three required continuity families.

The report is generated only through the reviewed
[`coverage_session`](../../tools/cli-journey-coverage/README.md) procedure from
a clean implementation revision and a recorded clean Metrics UI revision. Raw
Coverage.py databases, process shards, temporary configs, and local session
paths remain in the untracked session root.

Acceptance requires `report.json` to contain `result: pass`, a valid canonical
self-digest, complete collection-bound foreground/background process receipts,
successful cleanup, and current source and dependency identities.

This evidence reports executed owned-Python statements and arcs. It makes no
claim of behavioral correctness, dead code, production value, or a sufficient
numeric coverage threshold; PRs #88 and #100 remain the behavioral authority.

## Canonical capture

The 2026-08-12 UTC capture passed from clean auto-driving commit
`20ced979221b635993ea4ef85b3b27fb05cc0e75` and clean Metrics UI commit
`722e070fdc9f4ee89d13f947bf3996e62dcb2783` on
`m002/04-passive-observation`. Collection
`298509b3a1fa8540383c97e60ac790e6` used CPython 3.11.7 and Coverage.py
7.15.2 with a canonical receipt for all 539 visible distributions.

The report contains 34 commands, 34 logical contexts, 37 validated raw shards,
and context-aware execution for 63 owned Python files. Each automation launch
is bound to its PID and run generation, a later same-generation status, terminal
death, one foreground shard, and a distinct post-termination worker shard with
execution inside `run_vehicle_automation`. The three offline replay commands
share one sealed four-frame source identity with manifest, ordered-input, and
frame-receipt digests, and the promoted identity is re-derived from a sealed
path-tokenized raw-lineage receipt content. Deterministic primary,
continuity-family, support/cleanup, and all-context rollups plus
command/journey bootstrap comparisons are included directly in the report.

Both catalogs passed their machine preflight, terminal cleanup proved every
observed worker dead, dependency/source freshness passed, and repository-root
`.coverage*` identities were unchanged. The runners remain
`behavioral_verdict: not_evaluated`, as required for this coverage-only replay.

Integrity identities:

- Report projection SHA-256:
  `301022722f248197d0ce17dbdfe79b3d000d9ea0818718273b0f20681bc665d7`
- Session seal SHA-256:
  `980f44cf3a8df6ab2a2c914e8705f2a1d23d4a39a9fc90ef0d17538cfda91e71`
- Immutable finalization receipt SHA-256:
  `c6be66c28a4cc66a4241a76d11c140fd00a1a583e88d6e1c34658c33eae7e579`

Verify the tracked report from the repository root:

```sh
docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session \
  verify-report \
  docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
```
