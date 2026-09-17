# Composite box fusion

This is a local, disposable issue #219 experiment. It invokes the current
perception implementations inside one isolated lab candidate, preserves every
raw spatial cue, creates bounded geometric hypotheses locally, and optionally
asks Jev to select one hypothesis per spatial cluster.

The candidate never changes the decision engine or the stable plugin runner.
The Jev key is read only from `JEV_API_KEY` or `TYPESAFE_API_KEY`; it is not
stored in the manifest or artifacts. Response cache files contain the request
and response but no credential.

The current real-photo permutation uses six deliberately different cues:
`classical_regions`, `canny_morph`, `quad_rectangularity`, `partial_contour`,
`photometric_windows`, and `hough_fragments`. Hough remains visible in the
individual CV audit but is excluded from the Jev packet because its useful
failure mode is mostly room and carpet geometry in this frame. The adaptive
contour and corner/junction prototypes are also available as explicit detector
permutations; the latter is capped tightly when used in fusion because broad
corner support can chain unrelated regions.

The Jev-facing prompt is image-agnostic: it distinguishes tiled partial cues
from disconnected modes, prefers a robust outer extent over a coordinate median
when fragments are complementary, and rejects generic floor, wall, and border
support. It contains no frame coordinates, object labels, or reference boxes.
The full detector properties remain in `findings.json`, while Jev receives a
compact raw-cue representation so the ensemble can retain more source-balanced
evidence without exceeding the request limit. The local scene-edge guards are
weak generic eligibility filters; they are not a learned detector or production
prior.

The latest real-photo baseline used `min_box_area_fraction=0.001`,
`max_box_area_fraction=0.30`, `max_fusion_area_fraction=0.30`,
`max_raw_boxes=120`, `max_clusters=12`,
`cluster_iou_threshold=0.12`, `cluster_center_distance=0.07`, and four
hypotheses per cluster. It selected robust aggregate envelopes around both
visible boxes while leaving the individual noisy cues in the audit.

Run it on a fixed image manifest or directory:

```sh
JEV_API_KEY=... ./cli/automa vehicles perception apply path/to/frames \
  --candidate composite_box_fusion --record --json
```

Each recorded frame emits:

- `cv_only.png`: raw detector boxes and source labels;
- `jev_boundaries.png`: Jev-selected bounded hypotheses;
- `side_by_side.png`: the primary visual comparison;
- `findings.json`: raw cues, hypotheses, request, response, and statuses.

For the large per-detector comparison sheet used in this experiment:

```sh
python3 lab/experiments/issue-219/stitch_live.py RUN_DIR \
  --scale 0.55 --columns 2 --output RUN_DIR/stitched_large
```

The sheet places each individual detector panel, the all-cues audit, and the
Jev aggregate in one image. The final real-photo artifact is kept under the
local run directory for `IMG_1001.JPG`; no credential is written there.

`sim_color_targets` is intentionally not an input. It is a simulator-only
reference signal and would make the real/simulation comparison unfair.
