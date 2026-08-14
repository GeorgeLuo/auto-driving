# M007-08 CLI surface audit rollup

- Leaves: **42** (action=32, meta=10; all classified; residual unclassified: 0)
- Sequences by disposition: {'passed': 6, 'deferred': 3, 'blocked': 1}
- Sequences by completeness: {'evidenced': 6, 'template': 4}
- Sequences by coverage: {'measured': 6, 'unmeasured': 3, 'not_applicable': 1}
- Passed evidence: cited=6, executed=0
- Help drift: ok

## Deferred / blocked
- `US-06` deferred: owner=cli-perception-plugins; unlock=Seal continuity.plugin_ablation or successor with HITL
- `US-07` deferred: owner=cli-automation-status; unlock=Seal temporal backpressure sequence
- `US-09` deferred: owner=cli-memory-replay; unlock=Seal deterministic replay sequence
- `US-10` blocked: owner=physical-perception-lab; unlock=Labeled physical corpus available

## Coverage residuals (unmeasured / not_applicable)
- `US-06` coverage=unmeasured: Optional ablation not sealed
- `US-07` coverage=unmeasured: Optional backpressure not sealed
- `US-09` coverage=unmeasured: Optional replay not sealed
- `US-10` coverage=not_applicable: Physical labeled path out of measured M007-08 set

## LIVE residuals
- `M007-LIVE-001` deferred owner=cli-perception-apply
- `M007-LIVE-002` deferred owner=lab-candidates-compare
- `M007-LIVE-003` deferred owner=cli-perception-compare
- `M007-LIVE-004` deferred owner=cli-perception-review-ux
- `M007-LIVE-005` deferred owner=cli-perception-run

## Non-claims
- Cited `passed` is **historical** (`head_claim: historical`), not HEAD re-verification.
- Template / deferred rows are not product roadmap commitments.
- Coverage treatment is annotation, not a percentage gate.
