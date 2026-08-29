# Capture-calibrated floor continuity

This isolated candidate reuses the multi-cue floor-continuity detector with
defaults calibrated against the archived
`chase-stream-depth-obstacles-decision-30s-20260829-0436-retry` image stream.
It is deliberately separate from the active packaged `floor_plane` plugin and
does not establish object identity, depth, or safe traversability.

The capture-tuned defaults retain boundaries with at least `0.70` confidence
and `0.03` image-width support. They remove the smallest frame-19 fragment
while retaining the larger obstacle contacts.

Apply it to frame 19:

```sh
./cli/automa vehicles perception apply path/to/chase_frame_000019_front_camera.jpg \
  --candidate floor_continuity_capture --record
```

Apply it to the complete capture:

```sh
./cli/automa vehicles perception apply path/to/frames \
  --candidate floor_continuity_capture --record
```

This is a lab candidate. Its capture results are representation evidence, not
semantic accuracy or promotion evidence.
