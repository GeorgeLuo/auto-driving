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
`43d6905bff6f7fcfb4e5e137d93e1bf1c3ea80a5` and clean Metrics UI commit
`722e070fdc9f4ee89d13f947bf3996e62dcb2783` on
`m002/04-passive-observation`. Collection
`8a25e7b9d282e5d66a13aa8a03ae5294` used CPython 3.11.7 and Coverage.py
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
  `e3f7518af559c6f757fa10457ec93f6641de3596e91fee0309caba07bfa48464`
- Session seal SHA-256:
  `2f990bfe137ea2f68f56a4e8b2124a0a2144aa12c3cffe78396a6590eefbc60f`
- Immutable finalization receipt SHA-256:
  `4926df98fc15a94826ed854ccece4e716bad37ee15d3610b782f94de65d90173`

Verify the tracked report from the repository root:

```sh
docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session \
  verify-report \
  docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
```
