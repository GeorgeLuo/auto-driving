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
`90dabe3f47cb1f8778fcb22b568ea6ddf35933e0` and clean Metrics UI commit
`722e070fdc9f4ee89d13f947bf3996e62dcb2783` on
`m002/04-passive-observation`. Collection
`23b3e32ea5878bf277b16c9b529d3a75` used CPython 3.11.7 and Coverage.py
7.15.2 with a canonical receipt for all 539 visible distributions.

The report contains 34 commands, 34 logical contexts, 37 validated raw shards,
and context-aware execution for 63 owned Python files. Required command argv
templates and collection order are bound to the accepted expansion. Immutable
session-seal and finalization-receipt contents are embedded so offline
verification re-derives their digests, timestamps, and sealed offline-lineage
input binding. Each automation launch is bound to its PID and run generation,
terminal death, and distinct worker shards with execution inside
`run_vehicle_automation`.

Both catalogs passed their machine preflight, terminal cleanup proved every
observed worker dead, dependency/source freshness passed, and repository-root
`.coverage*` identities were unchanged. The runners remain
`behavioral_verdict: not_evaluated`, as required for this coverage-only replay.

Integrity identities:

- Report projection SHA-256:
  `2ad61a74fad4cf231c005acb24beb56876d520c395f3167ad99e8fbd033b761f`
- Session seal SHA-256:
  `8106a33dbd6765e5350814970cdac73ec540ad590b39cb2b65c51a4ad7199c12`
- Immutable finalization receipt SHA-256:
  `46b47a230e7fa9e98538713ab67542b9d7863ca0bc5b1d1d92640aa11845d1da`

Verify the tracked report from the repository root:

```sh
export M007_COVERAGE_PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session \
  verify-report \
  docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
```
