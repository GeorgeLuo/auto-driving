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
`84444d6d51f1f46d5c6ecee4e01a00d11081d6b4` and clean Metrics UI commit
`722e070fdc9f4ee89d13f947bf3996e62dcb2783` on
`m002/04-passive-observation`. Collection
`a33a8df306b510305574b0a06ff574ad` used CPython 3.11.7 and Coverage.py
7.15.2 with a canonical receipt for all 539 visible distributions.

The report contains 34 commands, 34 logical contexts, 37 validated raw shards,
and context-aware execution for 63 owned Python files. Required command argv
templates and collection order are bound to the accepted expansion, including
exact reconstruction of dynamic substitutions from each receipt's variables.
Immutable session-start, session-seal, and finalization-receipt contents are
embedded so offline verification re-derives digests, timestamps, collection ID,
and sealed projections for commands, runner results, worker checks, cleanup,
collection checks, dependency environment, offline lineage, and raw-shard
inventory.

Both catalogs passed their machine preflight, terminal cleanup proved every
observed worker dead, dependency/source freshness passed, and repository-root
`.coverage*` identities were unchanged. The runners remain
`behavioral_verdict: not_evaluated`, as required for this coverage-only replay.

Integrity identities:

- Report projection SHA-256:
  `b75486afcbfba125d9e30b70336729e02c7c2cec281ff72b22abcb643a7fbb77`
- Session seal SHA-256:
  `930c6c28f6e6dd6d2dc2c00fe4da27a0017b7b80e5ae9e991f9a80d3c272f836`
- Immutable finalization receipt SHA-256:
  `296b169d114c94add89419a5393065bff198b7cbc8718a730c2b3fb243e1ba2a`

Verify the tracked report from the repository root:

```sh
export M007_COVERAGE_PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session \
  verify-report \
  docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
```
