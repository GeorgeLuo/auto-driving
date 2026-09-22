# Temporal floor continuity

This lab candidate keeps the existing multi-cue floor-continuity detector but
limits the decision-facing output to one boundary associated with the prior
frame. The bounding box is exponentially smoothed and a short detector gap can
reuse the last geometry with decayed confidence.

It is intended for the captured PiCar sequence. It is representation evidence,
not object identity, semantic detection, depth, or safe traversability.

Run it against the capture with:

```sh
./cli/automa vehicles perception apply \
  lab/runs/cv-synthesis-20260919/retry/frames \
  --candidate floor_continuity_temporal --record
```
