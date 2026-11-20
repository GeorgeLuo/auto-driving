# Temporal floor continuity

This plugin keeps the existing multi-cue floor-continuity detector but
limits the decision-facing output to one boundary associated with the prior
frame. The bounding box is exponentially smoothed and a short detector gap can
reuse the last geometry with decayed confidence.

It is intended for the captured PiCar sequence. It is representation evidence,
not object identity, semantic detection, depth, or safe traversability.

Held confidence decays by `held_confidence_decay` (0.82) on each missed frame.
Association uses `overlap_weight` 0.55, `distance_weight` 0.30 and
`confidence_weight` 0.15. Each weight is a number in 0..1.

Run it against the capture with:

```sh
./cli/automa plugins inspect perception \
  lab/runs/cv-synthesis-20260919/retry/frames \
  --plugin frame --plugin floor_continuity_temporal
```

Temporal state (previous box/confidence, miss count, and age) lives under
`perception.<plugin_id>.history` in the host map. The plugin reconstructs a
call-local workspace, performs its existing smoothing/hold policy, and commits
after successful output. It requires shared memory and clears only its own
namespace on reset or unavailable input.
