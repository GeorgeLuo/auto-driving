# Physical strategy qualification

- status: `reject_keep_control`
- source: `lab/runs/perception-check/piracer-20260717-145716`
- control: `lightweight_observer`
- candidate: `floor_continuity`

## Decision

Candidate did not improve at least two material behavioral measures on the labeled physical-check frames; keep packaged floor-plane control.

- behavioral improvements: []
- behavioral regressions: ['clear_false_positive_boundaries_mean', 'mean_boundary_count']
- onboard Pi viability measured: False

## Metrics

| measure | control | candidate | delta | improved | regressed |
|---|---:|---:|---:|---|---|
| overall_pass_rate | 0.8333333333333334 | 0.8333333333333334 | 0.0 | False | False |
| directional_zone_hit_rate | 0.75 | 0.75 | 0.0 | False | False |
| clear_false_positive_boundaries_mean | 1.0 | 3.0 | 2.0 | False | True |
| removal_pass_rate | 1.0 | 1.0 | 0.0 | False | False |
| mean_boundary_count | 1.5 | 2.6666666666666665 | 1.166667 | False | True |
| median_duration_ms_desktop | 58.8825 | 18.8595 | -40.023 | True | False |

## Limits

- Offline desktop apply only; not an onboard Raspberry Pi latency/RSS measurement.
- Placement labels come from the human-guided physical-check folder names.
- Scores use generic floor_boundary zone/presence checks, not semantic object identity.
- Promotion to stable deploy still requires explicit package activation and Pi viability evidence.
