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
`2345c10901ebaafe44773ec511d87c0e584b9634` and clean Metrics UI commit
`722e070fdc9f4ee89d13f947bf3996e62dcb2783` on
`m002/04-passive-observation`. Collection
`c947c3f2038336b594954ec9ab2d31e6` used CPython 3.11.7 and Coverage.py
7.15.2 with a canonical receipt for all 539 visible distributions.

The report contains 34 commands, 34 logical contexts, 37 validated raw shards,
and context-aware execution for 63 owned Python files. All three automation
launch contexts have two shards and worker-only execution inside
`run_vehicle_automation`. Both catalogs passed their machine preflight, terminal
cleanup proved every observed worker dead, dependency/source freshness passed,
and repository-root `.coverage*` identities were unchanged. The runners remain
`behavioral_verdict: not_evaluated`, as required for this coverage-only replay.

Integrity identities:

- Report projection SHA-256:
  `35fe4969170cb5ac91575c2b942d62316cb796c0fa24ee9e99868ca205355718`
- Session seal SHA-256:
  `2cb0d89da5e9aa9fde12e32b4cd61e351eb45b629e5c72a5cab5e67467feabf5`
- Immutable finalization receipt SHA-256:
  `2367f73b812c1f0e7ce92cc9ff63d63fa42b0f8c12b748279af6a2179c7e70eb`

Verify the tracked report from the repository root:

```sh
docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session \
  verify-report \
  docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
```
