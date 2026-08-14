# M007-08 CLI surface audit rollup

- Leaves: **32** (all classified; residual unclassified: 0)
- Sequences by disposition: {'passed': 6, 'deferred': 3, 'blocked': 1}
- Sequences by completeness: {'evidenced': 6, 'template': 4}
- Sequences by coverage: {'measured': 6, 'unmeasured': 3, 'not_applicable': 1}
- Passed evidence: cited=6, executed=0
- Help drift: ok

## Deferred / blocked
- `US-06` deferred: owner=cli-perception-plugins; unlock=When ablation sequence is sealed with machine-first/HITL under continuity.plugin_ablation or successor
- `US-07` deferred: owner=cli-automation-status; unlock=When temporal backpressure sequence is sealed with captured/processed/skipped cues
- `US-09` deferred: owner=cli-memory-replay; unlock=When deterministic memory replay is sealed with Deterministic: yes confirmation
- `US-10` blocked: owner=physical-perception-lab; unlock=When labeled physical-check corpus exists and non-destructive qualify sequence is sealed

## Coverage residuals (unmeasured / not_applicable)
- `US-06` coverage=unmeasured: Optional US-06 family not sealed as required #107 journey
- `US-07` coverage=unmeasured: Optional US-07 family not sealed as required #107 journey
- `US-09` coverage=unmeasured: Optional US-09 family not sealed as required #107 journey
- `US-10` coverage=not_applicable: Physical labeled qualification outside measured M007-08 journey coverage

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
