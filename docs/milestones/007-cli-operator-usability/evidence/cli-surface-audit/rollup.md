# M007-08 CLI surface audit rollup

- Leaves: **49** (action=32, meta=10, alias=7; all classified; residual unclassified: 0)
- Sequences by disposition: {'passed': 2, 'deferred': 7, 'blocked': 1}
- Sequences by completeness: {'evidenced': 2, 'template': 8}
- Sequences by coverage: {'measured': 2, 'unmeasured': 7, 'not_applicable': 1}
- Passed evidence: cited=2, executed=0
- Help drift: ok

## Deferred / blocked
- `US-03` deferred: owner=cli-perception-offline; unlock=Exact-step #88 US-03 evidence (visual_observer apply + compare) after citation amendment; family aggregate is not enough
- `US-04` deferred: owner=cli-perception-plugins; unlock=Exact-step #88 US-04 evidence (visual_observer swap, disable, second run) after citation amendment
- `US-05` deferred: owner=cli-memory-lifecycle; unlock=Exact-step #88 US-05 evidence (visual_observer + stream + memory check) after citation amendment
- `US-06` deferred: owner=cli-perception-plugins; unlock=Seal continuity.plugin_ablation or successor with HITL
- `US-07` deferred: owner=cli-automation-status; unlock=Seal temporal backpressure sequence
- `US-08` deferred: owner=cli-memory-lifecycle; unlock=Exact-step #88 US-08 evidence (reset and repopulation) after citation amendment
- `US-09` deferred: owner=cli-memory-replay; unlock=Seal deterministic replay sequence
- `US-10` blocked: owner=physical-perception-lab; unlock=Labeled physical corpus available

## Coverage residuals (unmeasured / not_applicable)
- `US-03` coverage=unmeasured: Cited #100 offline family is a command subset of the registered US-03 sequence
- `US-04` coverage=unmeasured: Cited #100 live-config family is a command subset of the registered US-04 sequence
- `US-05` coverage=unmeasured: Cited #100 memory family is a command subset of the registered US-05 sequence
- `US-06` coverage=unmeasured: Optional ablation not sealed
- `US-07` coverage=unmeasured: Optional backpressure not sealed
- `US-08` coverage=unmeasured: Cited #100 memory family is a command subset of the registered US-08 sequence
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
