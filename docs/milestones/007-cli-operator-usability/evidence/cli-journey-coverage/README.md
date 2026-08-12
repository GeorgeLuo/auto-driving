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
`3d2eae9d29136fd05b75ed0af09151c5a55b6c8f` and clean Metrics UI commit
`722e070fdc9f4ee89d13f947bf3996e62dcb2783` on
`m002/04-passive-observation`. Collection
`eb80eaae562cd2272b3cecc8c7685d0e` used CPython 3.11.7 and Coverage.py
7.15.2 with a canonical receipt for all 539 visible distributions.

The report contains 34 commands, 34 logical contexts, 37 validated raw shards,
and context-aware execution for 63 owned Python files. Required command argv
templates and collection order are bound to the accepted expansion, including
exact reconstruction of dynamic substitutions from each receipt's variables.
Immutable session-start, session-seal, and finalization-receipt contents are
embedded so offline verification re-derives digests, timestamps, collection ID,
and sealed offline-lineage input binding.

Both catalogs passed their machine preflight, terminal cleanup proved every
observed worker dead, dependency/source freshness passed, and repository-root
`.coverage*` identities were unchanged. The runners remain
`behavioral_verdict: not_evaluated`, as required for this coverage-only replay.

Integrity identities:

- Report projection SHA-256:
  `1d95526aa5948c9d112c22dba6e57b93d0f8d183ea9025803a407310deb1dcc3`
- Session seal SHA-256:
  `7715e29a9973d03bcd23798e9092b35ecaef01104d9dfbb117e5f79444ab5d22`
- Immutable finalization receipt SHA-256:
  `2577f756738895be2fcaab1e8401e03ee3342c9bc978e6ba8aecc070e264c5d6`

Verify the tracked report from the repository root:

```sh
export M007_COVERAGE_PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session \
  verify-report \
  docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json
```
