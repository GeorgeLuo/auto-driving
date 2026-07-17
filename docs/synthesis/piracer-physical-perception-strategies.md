# PiRacer Physical Perception Strategy Research Report

- **Prepared:** 2026-07-16
- **Repository:** `GeorgeLuo/auto-driving`
- **Milestone:** 004 — Physical Perception Parity
- **Scope:** Raspberry Pi 4 CPU-only, fixed 640×480 monocular front camera, manual movement authority
- **Status:** `evaluating`
- **Source:** Independent research against the repository's physical-perception black-box brief

> `floor-continuity-v1` is a candidate hypothesis, not an accepted implementation.
> Milestone adoption requires common-frame comparison with `floor-plane-v0`,
> measured Pi viability, and the promotion gate defined below. The active plan is
> [`../milestones/004-physical-perception-parity/plan.html`](../milestones/004-physical-perception-parity/plan.html).

## Implementation checkpoint: simulator and archived Pi frames

The bounded candidate now exists under
[`lab/plugins/perception/floor_continuity/`](../../lab/plugins/perception/floor_continuity/).
It uses the existing plugin contract, stays stateless, processes at 320x240, and
writes diagnostics only for explicitly recorded runs. Focused synthetic tests
cover clear floor, a similar-color interruption, current-frame clearing, and
exact-frame opt-in diagnostics.

On 2026-07-16 the current default completed a five-frame same-source application in
the `chaser-depth-obstacles` simulator scenario with no failed frames. It
emitted 20 boundary regions and measured a 40.320 ms unrecorded plugin median
on the development Mac. The overlay retained bottom-connected floor support,
found the principal pale-box contacts, and removed one weak edge fragment per
frame relative to the initial prototype.

The same configuration then processed 50 archived Pi frames from six capture
sets with no failed frames and 190 emitted boundaries. Unrecorded plugin
medians ranged from 37.263 to 65.531 ms. On the 11-frame forward sequence the
candidate emitted 41 boundaries at a 37.263 ms plugin median; the packaged
`floor-plane-v0` control emitted 67 at 211.783 ms. This is not an accuracy win:
the archive has no coarse labels, representation-health scores do not measure
semantic correctness, and visual review still shows carpet fragments.

Physical application motivated two generic defaults rather than a second plugin: a
minimum boundary confidence of 0.65 and minimum absolute edge strength of
0.24. A 0.75 confidence floor removed useful distant box evidence. The CLI now
accepts one image or a directory and repeatable candidate-only `--set
NAME=VALUE` overrides, with the effective configuration retained by recorded
runs.

This checkpoint proves implementation shape, current-frame behavior,
simulator operability, and compatibility with archived Pi imagery. It does
**not** establish labeled improvement, onboard Pi latency or memory cost,
live publication, carpet generalization, or fitness for promotion. Reproduce
the checks with the commands in the candidate README; retain generated run
artifacts outside source control.

## Executive recommendation

Promote **one new classical candidate, “floor-continuity-v1,” into `lab/plugins/perception/`** and qualify it before changing the packaged implementation catalog. It should remain **stateless**, operate internally at **320×240**, and combine:

1. robust lower-center floor seeding;
2. color/chroma, luminance, local texture, and gradient cues;
3. bottom-connected region growth constrained by image-space perspective;
4. first sustained interruption after supported floor;
5. explicit cue-agreement and ambiguity measurements for confidence.

This is the best fit for Milestone 004 because it directly answers the contract question, adds no runtime dependency beyond the existing NumPy/OpenCV stack, is inspectable, preserves exact-frame semantics, and should materially improve on the current color-only baseline without introducing a model-runtime or licensing project.

Use **LiteDepth through a TFLite/LiteRT interpreter as the research fallback**, not as the first implementation. It has unusually strong published hardware evidence: a 1.4–1.5 MB model and 37 ms inference at VGA resolution on a Raspberry Pi 4. However, the published training domain is not a close match for a low indoor PiRacer camera, the output is coarse before upsampling, and the public repository does not presently provide a clean, easily auditable milestone-ready package with an explicit license and obvious deployable `.tflite` artifact. Admit it only if those packaging and licensing questions are resolved and offline application results show a meaningful quality gain.

Retain **`floor-plane-v0` as the operational fallback and integration control**. Do not replace it merely because a learned model produces a more attractive visualization.

## Decision

- **Primary milestone candidate:** `floor-continuity-v1` — classical, stateless, 320×240 internal processing.
- **Conditional research fallback:** `litedepth-boundary-v0` — pretrained relative-depth evidence via TFLite/LiteRT.
- **Operational fallback/control:** existing `floor-plane-v0`.
- **Do not prioritize for Milestone 004:** TopFormer-T semantic segmentation; it has no comparable Pi 4 benchmark, adds a substantially heavier deployment path, and its public repository licensing file is ambiguous.

## Why the existing contract should remain unchanged

The repository already has the necessary generic machinery:

- plugin contracts declare `stateless`, `pairwise`, or `windowed` behavior;
- inputs include the current `frame_id`, capture timestamp, resolved components, metadata, and an opt-in diagnostic sink;
- evidence supports normalized image-space boxes and polygons;
- results distinguish `ok`, `empty`, `warming_up`, `unavailable`, and `error`;
- the runner can isolate component unavailability and plugin failures;
- diagnostics are framework-owned and disabled unless explicitly requested.

The new work belongs entirely behind the existing plugin boundary. No change under `autonomy/` is justified by this research.

## Comparison table

> Published runtimes below are not automatically equivalent to plugin p95 or end-to-end result age. “Unknown” means a Pi 4 benchmark is required; desktop or phone figures are not converted into unsupported Pi estimates.

| Candidate | Native output | Contract mapping | State mode | Internal resolution | Expected Pi p95 | RSS/model size | Dependencies/license | Main failure modes | Recommendation |
|---|---|---|---|---:|---:|---:|---|---|---|
| **Existing `floor-plane-v0`** | Per-frame floor-color mask and first sustained non-floor scanline hits | Existing direct mapping to `floor_visible`, `floor_boundary_available`, and normalized `floor_boundary` boxes | Stateless | 640×480 | **Unmeasured.** Establish as control; engineering target ≤75 ms | No model; process RSS not yet measured | Existing NumPy, OpenCV, Pillow; project license | Seed occupied by object; shadows; floor transitions; similar-colored objects; patterned carpet; exposure shifts | Keep as integration control and operational fallback |
| **Proposed `floor-continuity-v1`** | Bottom-connected floor region plus interruption/contact components using fused color, texture, gradient, and perspective cues | `floor_visible` from connected floor support; `floor_boundary_available` from qualified interruption components; emit bbox/polygon and cue measurements | Stateless | **320×240**, coordinates normalized to original frame | **Unmeasured. Qualification target ≤100 ms; hard rejection >350 ms p95** | No model; target incremental working set <16 MB; measure process RSS | Existing NumPy/OpenCV only; project license | Very low-contrast object; strong shadow/reflection; patterned carpet fragmentation; seed occlusion; blur | **Primary candidate** |
| **LiteDepth relative-depth boundary** | Dense monocular relative/depth-like map, internally downsampled then upsampled | Fit a robust bottom-connected floor trend; emit regions where depth residual/vertical discontinuity interrupts supported floor. Never expose metric distance as established fact | Stateless | 640×480 input; model resizes to 160×128 and produces a coarse map before upsampling | Published inference **37 ms on Pi 4 at VGA**; require plugin p95 ≤150 ms including post-processing | Published model 1.4–1.5 MB; runtime RSS unreported | TFLite/LiteRT interpreter plus model. Public code exists, but repository licensing/deployable artifact must be resolved | Domain shift; coarse edges; reflective/translucent objects; scale ambiguity; thin objects; near-camera artifacts | **Conditional fallback; benchmark before admission** |
| **TopFormer-T floor segmentation** | ADE20K semantic class probabilities/mask | Discard semantic names; use bottom-connected floor-class probability and non-floor contact regions as generic evidence | Stateless | 448×448 or 512×512 in published configurations | **Unknown on Pi 4; do not infer from Snapdragon 865** | 1.4M parameters for tiny model; actual checkpoint/runtime RSS unreported | PyTorch/mmcv or TNN conversion; public repo license file contains conflicting license text | Indoor taxonomy mismatch; floor-class mistakes; unknown objects; conversion/runtime burden; edge distortion | Audit-only; not Milestone 004 priority |

## Candidate 1: existing `floor-plane-v0`

### What it does

The current plugin constructs a robust color/chroma model from a lower-center seed rectangle, estimates a floor mask below an assumed horizon, and scans upward from the bottom of each image column. Once enough floor support has been observed, the first sustained non-floor run becomes boundary evidence. Connected hit components become normalized image-space regions.

### Strengths

- already integrated with the common evidence contract;
- exact current-frame evidence;
- no model or extra runtime;
- easy to inspect and reproduce;
- useful latency and integration control.

### Weaknesses

Its definition of floor is dominated by current-frame color similarity. It will therefore confuse a shadow, floor material transition, wall, dark carpet patch, or lower-center object with an obstruction. Re-seeding independently each frame also makes output sensitive to exposure and seed contamination.

### Role

Keep it unchanged while evaluating candidates. Its purpose is to prove whether a proposed addition gives a real behavioral gain per unit cost.

## Candidate 2: proposed `floor-continuity-v1`

### Core algorithm

1. **Resize** the 640×480 source to 320×240 with area interpolation. Preserve the original dimensions only for diagnostics and coordinate normalization.
2. **Define a conservative analysis trapezoid** below a configurable horizon. Exclude a thin edge margin where barrel distortion is strongest.
3. **Build per-cell features** on an 8×8 pixel grid:
   - normalized chroma or Lab `a/b`;
   - median luminance and local luminance spread;
   - gradient magnitude and orientation consistency;
   - a low-cost texture statistic such as local absolute deviation or small LBP-like comparisons;
   - optional saturation/glare flags.
4. **Seed floor support** from several lower-center cells rather than one rectangular aggregate. Reject cells with extreme blur, clipping, or high disagreement. Estimate robust feature centers and dispersion using medians and MAD.
5. **Grow a bottom-connected floor graph** upward and sideways. A neighbor is accepted when its fused feature distance, boundary gradient, and perspective-conditioned continuity cost remain below adaptive limits. The cost should increase toward the image edge and above the expected near-floor region.
6. **Find interruptions** by scanning from supported bottom-connected floor toward the horizon. Require a sustained non-floor run and group adjacent columns/cells into regions.
7. **Cross-check the proposed boundary**:
   - floor support immediately below it;
   - gradient or texture change at the transition;
   - minimum width and vertical persistence;
   - cue agreement rather than color distance alone;
   - rejection of broad global exposure changes.
8. **Emit current evidence** only. No previous frame is needed; no stale geometry is reused.

### Why this should improve the baseline

It changes the question from “does this pixel resemble the seed color?” to “is this region connected to the visible floor by a plausible, locally continuous path, and is there multi-cue support for an interruption?” Carpet texture can remain floor if it changes gradually and remains connected. A similarly colored box can still be detected through gradient/contact evidence. A smooth shadow can be down-weighted when it preserves texture and continuity rather than producing a true region termination.

This is still heuristic perception, not physical proof. The advantage is that every decision remains inspectable in an overlay and summary.

### Contract mapping

#### Signals

`floor_visible`

- `value=true` when the bottom-connected candidate floor occupies a minimum portion of the analysis trapezoid and spans a minimum number of center columns.
- `confidence` increases with seed quality, connected-floor support, and agreement among color/texture/gradient cues.

`floor_boundary_available`

- `value=true` when at least one interruption component exceeds minimum support and confidence.
- `confidence` is the maximum or support-weighted mean of emitted components; the aggregation rule must be documented.

#### Spatial evidence

For each component:

```text
kind: "floor_boundary" or "obstruction_evidence"
label: "supported floor interruption"
location.frame: "image"
location.zone: near_left / near_center / near_right / ...
location.bbox_xyxy_norm: normalized to original 640×480 source
location.polygon_xy_norm: optional boundary/contact polygon
properties:
  floor_support_below
  width_fraction
  vertical_persistence
  edge_agreement
  texture_discontinuity
  color_discontinuity
  cue_disagreement
  seed_quality
  blur_score
  clipping_fraction
  processing_width_px: 320
  processing_height_px: 240
```

### Confidence derivation

Confidence should be a monotonic engineering score, not a probability. A suitable initial formula is:

```text
boundary_confidence = clamp01(
    0.25 * width_support
  + 0.20 * floor_support_below
  + 0.20 * transition_edge_support
  + 0.15 * vertical_persistence
  + 0.10 * cue_agreement
  + 0.10 * seed_quality
  - 0.15 * blur_penalty
  - 0.15 * clipping_or_glare_penalty
  - 0.20 * ambiguity_penalty
)
```

Each term should be recorded separately. Confidence should fall under blur, clipped exposure, weak seed support, and disagreement between color, texture, and edge cues.

### Expected resource profile

There is no published Pi 4 benchmark because this is a proposed implementation. Its operations are bounded array transforms, local statistics, graph/flood fill on a coarse grid, morphology, and connected components. The qualification target should be:

- p50 ≤75 ms;
- p95 ≤100 ms preferred;
- rejection if p95 exceeds 350 ms;
- no model storage;
- incremental working arrays targeted below 16 MB;
- stable RSS with less than 5 MB drift over the last 45 seconds of a fixed 60-second run.

These are acceptance targets, not claimed measurements.

## Candidate 3: LiteDepth relative-depth boundary

### Published evidence

LiteDepth uses a modified MobileNetV3 encoder and a lightweight decoder. Its paper reports a 1.4 MB model and 37 ms inference per VGA image on a Raspberry Pi 4. The network first resizes the 480×640 input to 128×160; its decoder produces a 32×64 map before final upsampling to source resolution. The 2022 challenge evaluated VGA depth models directly on Raspberry Pi 4 hardware.

This is the only pretrained strategy in this shortlist with a directly relevant published Pi 4/VGA result. That result is inference latency under the authors’ benchmark, not this repository’s plugin p95 or end-to-end freshness.

### Contract mapping

Do not treat its output as calibrated metric depth. Instead:

1. normalize the current depth map robustly within the lower analysis region;
2. identify a smooth, bottom-connected candidate floor band;
3. fit a simple per-row or low-order image-space floor trend using robust regression;
4. compute residual depth and vertical depth gradient;
5. require visible floor support below a proposed discontinuity;
6. group adjacent discontinuity pixels into generic current-frame regions;
7. emit only normalized image geometry and supporting relative-depth measurements.

Useful properties include median residual, vertical-gradient support, floor-trend fit error, map entropy, and contact width. A poor trend fit should lower confidence rather than fabricate a boundary.

### Installation and licensing implications

The runtime can be small if a compatible `.tflite` model is available: use a TFLite/LiteRT interpreter rather than full TensorFlow. Current Google documentation supports lightweight Python interpreters on Raspberry Pi ARM variants, but exact wheel compatibility must be checked against the deployed OS, architecture, Python version, and glibc.

The public LiteDepth repository documents a training/conversion toolchain involving PyTorch, monocular-depth tooling, ONNX conversion, and TensorFlow. That full stack should **not** be installed on the Pi. More importantly, the repository audit did not find a clean explicit license file or an obvious packaged, versioned `.tflite` release suitable for redistribution. Therefore:

- obtain or build a reproducible `.tflite` artifact off-device;
- record its source commit, checksum, input/output quantization, and preprocessing;
- resolve code and weight licensing explicitly before catalog promotion;
- fail the candidate early if this cannot be done without retraining or ambiguous redistribution.

### Main technical risk

The model was trained for a broad mobile-depth challenge using ZED-derived data with depth ranges extending tens of meters. That is not the same domain as a low, wide-angle indoor camera viewing carpet, furniture, and nearby floor contact. Coarse 32×64 native output can also smear narrow contact boundaries. Its impressive speed does not establish usefulness for this evidence question.

## Candidate 4: TopFormer-T semantic segmentation

### Native output

TopFormer-T is a small mobile semantic segmentation model. Published ADE20K configurations use 1.4 million parameters and roughly 0.5–0.6 GFLOPs at 448–512 square input sizes. The authors report mobile ARM latency on a Snapdragon 865, not on a Raspberry Pi 4.

### Contract mapping

- use only the probability assigned to floor-like classes;
- discard class labels before emitting evidence;
- create a bottom-connected floor mask;
- identify non-floor regions that interrupt or contact the floor mask;
- emit generic `floor_boundary` or `obstruction_evidence` records.

### Why it is not preferred

A semantic floor class is appealing, but ADE20K class behavior is not guaranteed for the PiRacer’s camera, flooring, or household objects. The official implementation relies on PyTorch/mmcv and points to a separate TNN mobile route. There is no comparable Pi 4 result, and the repository’s license file contains conflicting Apache and MIT text with merge-conflict markers. This is too much packaging and legal uncertainty for a milestone whose goal can be tested with a narrow classical method.

## Cross-condition failure analysis

| Condition | `floor-plane-v0` | `floor-continuity-v1` | LiteDepth | TopFormer-T |
|---|---|---|---|---|
| **Plain carpet** | Usually viable if seed representative | Strong candidate; continuity and texture spread should help | Domain-dependent; test required | Floor taxonomy may work, but confidence unknown |
| **Patterned carpet** | Likely fragmented/false boundaries | Better if gradual changes remain graph-connected; strong pattern can still fragment | May smooth pattern, but coarse output can miss contact | May classify carpet correctly or fail by appearance |
| **Hard floor** | Usually viable; reflections and seams problematic | Good with glare flags and multi-cue agreement | Reflections and textureless regions are known depth ambiguities | Often plausible but dataset/domain dependent |
| **Shadow** | High false-positive risk | Down-weight smooth chromatic/luma change without edge/contact agreement; not eliminated | May infer false depth edge | May change class confidence or preserve floor |
| **Weak object-floor contrast** | Miss likely | Gradient/texture/contact cues can recover some cases; fully camouflaged objects remain hard | Relative-depth prior may help if model generalizes | Semantic prior may help if object recognized, but labels are discarded |
| **Barrel distortion** | Edge-zone geometry and thresholds degrade | Crop margin and normalized source mapping should tolerate moderate distortion; evaluate left/right | Model may be sensitive to unseen lens distortion | Model may be sensitive to unseen lens distortion |
| **Motion blur** | Mask becomes unstable | Blur score lowers confidence and may suppress evidence | Depth edges smear | Semantic boundaries smear |
| **Exposure change** | Seed and mask may jump | Per-frame robust normalization and clipping penalties reduce impact | Preprocessing/model sensitivity; no stale reuse | Model confidence may shift |
| **Partial occlusion** | Emits visible part only; may split component | Emits current visible interruption only; grouping can split | Coarse map may merge regions | Segmentation may merge/split regions |

No candidate establishes traversability, collision distance, object identity, or persistent identity.

## Is temporal support worth it?

Not for the first Milestone 004 addition.

The camera and objects are stationary during acceptance, and the contract requires removal to clear within two published results. A stateless method makes exact-frame interpretation and failure isolation simpler. Temporal median filtering can make overlays look stable while preserving stale evidence, which is directly contrary to the milestone’s current-evidence semantics.

After a stateless candidate qualifies, a **pairwise diagnostic experiment** may compare current regions with the immediately previous frame to measure jitter or reject a one-frame transient. It should not replace current geometry, and it must reset on unavailable input. Promotion should require a demonstrated behavioral gain large enough to justify `warming_up` and reset complexity.

## Camera calibration and distortion

Calibration should not be a prerequisite for Milestone 004 because all required evidence is in source-image coordinates and only coarse left/center/right placement is accepted. Moderate radial distortion changes shape and apparent width, especially near image edges, but does not invalidate normalized image locations.

Initial handling:

- reserve a 3–5% horizontal margin for low-confidence edge evidence;
- avoid metric top-down projection;
- record zone and source coordinates exactly;
- include left/right distortion cases in offline application;
- add optional undistortion only if the same physical object repeatedly fails at edges while center performance remains good.

If introduced later, calibration belongs in a shared camera component, not hidden inside one algorithm.

## Minimal prototype plan

### Location

```text
lab/plugins/perception/floor_continuity/
  __init__.py
  plugin.py
  model.py
  config.py
  confidence.py
  diagnostics.py
  tests/
    test_contract.py
    test_floor_graph.py
    test_boundary_grouping.py
    test_confidence.py
    test_apply.py
```

### Contract declaration

```python
plugin_id = "floor-continuity-v1"
state_mode = "stateless"
inputs = (FRONT_CAMERA_RGB_INPUT,)
diagnostic_artifacts = (
    "floor_mask",
    "boundary_mask",
    "overlay",
    "summary",
)
```

### Runtime behavior

- require the current `CameraFrame`;
- process only the provided immutable RGB array;
- never read or cache an older successful result;
- return `empty` naturally when valid current input yields no boundary;
- allow framework handling for unavailable input and isolated error;
- write artifacts only through the enabled diagnostic sink;
- map all geometry back to normalized original-source coordinates;
- cap emitted regions, for example at eight sorted by confidence/support.

### Unit and contract tests

1. **Coordinate contract:** every bbox/polygon is ordered and within `[0,1]`.
2. **No stale state:** two unrelated frames cannot influence each other.
3. **Clear floor:** emits `floor_visible=true` and no strong central boundary on synthetic smooth and textured floors.
4. **Left/center/right:** a synthetic interruption moves zones correctly.
5. **Removal:** the next valid clear frame does not retain the old region.
6. **Unavailable:** no spatial evidence is fabricated when the frame component cannot be produced.
7. **Diagnostics:** disabled mode creates no files; enabled artifacts share the invocation’s frame ID in the summary.
8. **Bounded output:** component cap and array sizes remain fixed.

## Common offline application evaluation

### Smallest credible rejection dataset

Capture **240 primary frames**:

```text
2 floor surfaces (carpet, hard floor)
× 2 objects (box-like, irregular)
× 5 states (clear, left, center, right, removed)
× 3 placement cycles
× 4 consecutive frames
= 240 frames
```

Add a **30-frame challenge set** covering:

- hard shadow across the floor;
- dim exposure;
- overexposure/glare;
- mild motion blur;
- an object close to the left and right distorted edges.

Total: **270 frames**. This is small enough to label manually but large enough to reject candidates that only work on one object or floor.

### Labels

For each frame:

- expected status;
- `floor_visible` yes/no/ambiguous;
- boundary present yes/no;
- expected horizontal zone;
- a coarse human polygon or contact-band region;
- ambiguity tags: shadow, glare, blur, weak contrast, partial occlusion.

Use the same source images for every strategy. Do not compare candidates on separately captured scenes.

### Behavioral metrics

- boundary presence precision/recall;
- left/center/right accuracy;
- central false-positive rate on clear floor;
- contact-band overlap with a tolerance, plus bbox IoU where meaningful;
- centroid displacement and consecutive-frame IoU in stationary scenes;
- confidence variation within each four-frame stationary group;
- results required to clear after removal;
- unavailable/error correctness.

### Proposed qualification gates

These are project gates, not claims of model accuracy:

- ≥95% correct horizontal zone among true-positive object frames;
- ≤5% strong central false-positive rate on clear-floor frames;
- ≥90% boundary detection across both object types and both floors;
- evidence cleared by the first valid removed frame, never later than two published results;
- stationary centroid p95 displacement ≤4% of image width;
- median consecutive-region IoU ≥0.60 when a stable region is present;
- no evidence with confidence ≥0.60 when input is unavailable or algorithm execution failed;
- plugin p95 ≤350 ms, with ≤100 ms preferred for the classical candidate.

A candidate fails if it only passes after scene-specific threshold tuning. Configuration may depend on image dimensions and generic camera placement, not on the color of a particular floor or object.

## 60-second physical benchmark

Run the car stationary and in manual `user` mode. The autonomy output remains zero steering and throttle.

### Core run

Use a 60-second sequence:

- 0–10 s: clear floor;
- 10–20 s: object left;
- 20–30 s: object center;
- 30–40 s: object right;
- 40–50 s: object removed;
- 50–60 s: repeat the most difficult condition from offline application.

Run at least three cycles on different surfaces/lighting. Conduct a separate camera-unavailable test because deliberately interrupting capture can distort the timing of the core run.

### Measure

- plugin duration p50/p95;
- end-to-end result age p50/p95;
- fresh results per second;
- captured frames skipped;
- average and peak process CPU;
- baseline, peak, and final RSS;
- RSS slope over the final 45 seconds;
- cold initialization time;
- dependency and model disk size;
- maximum camera-loop gap;
- maximum manual-control-loop gap;
- status endpoint latency and failures.

Use queue depth one/newest-frame replacement. Skipped frames are acceptable and counted; backlog is not.

### Recording policy

Normal operation writes nothing. For an explicit benchmark run, enable the diagnostic sink and record only:

- selected exact source frames;
- frame-matched overlay/masks;
- one JSON result/measurement record per selected frame;
- process metrics.

Store the run in a dedicated temporary or externally chosen directory and make recording opt-in. A diagnostic image without the same frame ID as its structured result invalidates the sample.

### Physical pass gates

- ≥2 fresh results per second;
- p95 end-to-end result age ≤1 second;
- plugin p95 ≤350 ms preferred, never >500 ms for a 2 Hz pipeline;
- correct left/center/right current evidence;
- removal clears within two published results;
- unavailable input exposes stale/unavailable state and withholds fresh geometry;
- camera, status, and manual control remain responsive;
- no sustained RSS growth.

## Promotion decision

Promote `floor-continuity-v1` from `lab/` only when:

1. it passes the common application set without per-scene tuning;
2. it beats `floor-plane-v0` on at least two material dimensions, especially clear-floor false positives and cross-surface object detection;
3. the improvement is visible in structured metrics, not merely overlays;
4. Pi p95 and freshness meet the contract;
5. memory is stable;
6. diagnostics are exact-frame and opt-in;
7. assumptions and limitations are recorded;
8. tests are topically split;
9. it is registered in the implementation catalog only after qualification.

Admit LiteDepth instead only if it materially outperforms the classical candidate and the model artifact, license, runtime installation, and Pi measurements are fully reproducible.

## What the output means

A positive result means:

> In this exact source frame, this algorithm found image evidence consistent with visible floor being interrupted at this location.

It does **not** mean:

- the region is certainly a physical object;
- the floor is safe or traversable;
- a collision will occur;
- the system knows object identity or class;
- the distance is metric or calibrated;
- the region is the same object as in another frame;
- the evidence should directly control steering or throttle.

An empty result means the algorithm found no qualifying evidence in the current valid frame. `unavailable` means a required input could not be produced. `error` means execution failed. These cases must remain distinguishable.

## Answers to the open questions

1. **Simplest material improvement:** multi-cue, bottom-connected floor continuity with first-interruption grouping.
2. **Can pretrained vision meet 2 Hz?** LiteDepth plausibly can on raw inference speed; usefulness and packaging remain unproven.
3. **Best initial resolution:** 320×240 for the proposed classical candidate; preserve 640×480 source coordinates. Learned models should use their published input path first.
4. **Temporal support:** not initially worth the stale-evidence and state complexity risk.
5. **Confidence:** derive it from visible support and cue agreement, with explicit blur/exposure/ambiguity penalties.
6. **Empty vs unavailable vs error:** already supported by the framework; the plugin must not catch and flatten these states.
7. **Lens distortion:** likely tolerable for image-space zones; test edge placements before adding calibration.
8. **Carpet and hard-floor generalization:** continuity and texture cues are more promising than fixed color thresholds, but must be demonstrated on the shared application set.
9. **Most useful later-memory output:** current image-space floor-interruption regions plus transparent support measurements, not identity or metric geometry.
10. **Smallest useful rejection dataset:** approximately 270 frames as specified above, followed by three 60-second physical cycles.

## Sources and evidence notes

### Project sources

- Research brief supplied for this report.
- `autonomy/perception/plugin.py`
- `autonomy/perception/evidence.py`
- `autonomy/perception/interface.py`
- `implementations/perception/catalog.py`
- `implementations/perception/traversability/plugin.py`
- `implementations/perception/traversability/model.py`

Repository: https://github.com/GeorgeLuo/auto-driving

### External primary sources

1. Li et al., [**LiteDepth: Digging into Fast and Accurate Depth Estimation on Mobile Devices**](https://arxiv.org/abs/2209.00961), 2022.
2. Ignatov et al., [**Efficient Single-Image Depth Estimation on Mobile Devices, Mobile AI & AIM 2022 Challenge: Report**](https://arxiv.org/abs/2211.04470), 2022.
3. [LiteDepth code repository](https://github.com/zhyever/LiteDepth).
4. Zhang et al., [**TopFormer: Token Pyramid Transformer for Mobile Semantic Segmentation**](https://arxiv.org/abs/2204.05525), CVPR 2022.
5. [TopFormer code repository](https://github.com/hustvl/TopFormer).
6. Google AI Edge, [**LiteRT Python and ARM deployment documentation**](https://ai.google.dev/edge/litert/).

### Evidence caveats

- Published inference latency is not p95 plugin latency and excludes this repository’s capture, post-processing, publication, and browser age.
- No published Pi 4 result was found for TopFormer under comparable conditions.
- No Pi 4 measurement exists yet for the proposed classical candidate or the current repository baseline.
- LiteDepth’s repository packaging and license should be treated as unresolved until a specific source commit, model file, checksum, and redistribution basis are documented.
