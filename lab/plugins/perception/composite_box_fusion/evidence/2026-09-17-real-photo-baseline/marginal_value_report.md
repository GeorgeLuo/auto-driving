# Issue #219 marginal-value pilot

This is a manual six-object pilot on the three recorded real photos. It is not a production benchmark; labels are approximate visible-object envelopes.

IoU match threshold: `0.50`.

| selector | predictions | matched | coverage/recall | precision | F1 | mean matched IoU | mean best IoU | unmatched | single-source unmatched |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| raw_confidence | 32 | 0 | 0.000 | 0.000 | 0.000 | — | 0.146 | 32 | 32 |
| covering_union | 12 | 6 | 1.000 | 0.500 | 0.667 | 0.753 | 0.753 | 6 | 0 |
| robust_extent | 12 | 5 | 0.833 | 0.417 | 0.556 | 0.754 | 0.700 | 7 | 0 |
| handwritten_selector | 32 | 5 | 0.833 | 0.156 | 0.263 | 0.754 | 0.700 | 27 | 20 |
| jev | 14 | 5 | 0.833 | 0.357 | 0.500 | 0.744 | 0.692 | 9 | 2 |

The `raw_confidence` selector chooses the highest-confidence raw CV proposal per cluster. `covering_union` and `robust_extent` choose only those exact recorded aggregate hypotheses. `handwritten_selector` is the plugin's existing deterministic heuristic output. `jev` uses the recorded Jev choice and omits `none` selections.

Unmatched predictions are a measurable nuisance-boundary proxy, not a semantic nuisance label. Single-source counts are reported because Jev was explicitly asked to favor independent source support, yet the recorded run contains some accepted single-source choices.
