# M008 refined QCA experiment

- schema: `qca/refinement-experiment/v1`
- analyzer: `0.3.0`
- baseline: `3fce449d1eb64d408458231163c3f8b9b5c23af3`
- negative control detected: `1`

Factor-specific source measurements will identify concrete simplifications while independent behavior checks reject metric improvements that remove required behavior.

## Historical states

| State | Files | Core churn | Decision burden Δ |
| --- | ---: | ---: | ---: |
| `proposal` | 3 | 0 | +0 |
| `plugin-proposal` | 3 | 0 | +0 |
| `live-toggle-amendment` | 3 | 0 | +0 |
| `implementation` | 24 | 7401 | +583 |
| `acceptance` | 10 | 454 | +10 |
| `closeout` | 5 | 0 | +0 |
| `milestone-total` | 40 | 7855 | +593 |

## Trials

### `combine_actions` (small)

Merge identical idle and terminal action branches; decision burden should fall by one, with action order and fresh lists preserved.

- decision: `supported_by_checks`
- tests: 39 run, 0 failed, 0 errors
- replay trace equal: `True`
- decision-burden Δ: -1
- candidate: `b0aa447cb365daa47b295a3bf3b2b84964b7548d`

### `path_containment` (medium)

Delegate two path-containment checks to Path.is_relative_to; decision burden should fall by two with identical lexical path semantics.

- decision: `supported_by_checks`
- tests: 39 run, 0 failed, 0 errors
- replay trace equal: `True`
- decision-burden Δ: -2
- candidate: `f25f65edf9196943833d091fb4f256621db208e6`

### `skip_validation` (negative-control)

Removing frame-sequence validation lowers decision burden but must be rejected when duplicate/non-increasing inputs cease to fail.

- decision: `rejected_by_checks`
- tests: 39 run, 2 failed, 0 errors
- replay trace equal: `True`
- decision-burden Δ: -3
- candidate: `466649c11af9b9cc30c7c03721ec3f9804be525d`

## Limitations

- Synthetic replay and existing tests cover a bounded sample, not universal behavior equivalence.
- Visual Chrome interactions and full side-effect monitoring were not executed.
- Static measurements repeat; runtime logs, source fixture directories, and elapsed durations can vary.
- Candidate commits are reconstructed locally from the pinned parent, patches, and fixed commit metadata.
