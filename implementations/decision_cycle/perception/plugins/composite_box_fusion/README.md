# Composite shared CV obstruction tracks

This plugin is the Step 2 composite for the paired bright/dark
exploration. It keeps the `multi_obstruction_tracks` output contract and adds
issue-219's `canny_morph`, `quad_rectangularity`, `partial_contour`,
`photometric_windows`, Hough, and corner/junction cues. Classical color regions
and the existing floor-continuity model run on raw RGB. A shared luminance path
feeds all edge, partial-face, and line/junction cues. Line and junction boxes
are stored as support measurements and cannot create object candidates by
themselves.

The recovered issue-219 cue source is recorded in
`issue219_cues.py`; its source commit is `94292e8`'s parent implementation
from commit `2e0a49d`. Step 3 enumerates bounded raw proposals, spatial
clusters, raw/union/robust/median/intersection hypotheses, and deterministic
selector choices before the inherited tracker receives a selected geometry.

This plugin inherits temporal association from `MultiObstructionTracksPlugin`.
It emits tracked `obstacle` things and the `multi_obstruction_tracks_available`
signal, and keeps optical-flow history and ID allocation at
`perception.composite_box_fusion.history` in the host map. Select it directly,
for example `plugins inspect perception images --plugin composite_box_fusion`.
The `object_separated_fusion` preset selects this same plugin with
`object_separated_geometry` enabled, so reports use the `composite_box_fusion`
plugin id. Selecting `multi_obstruction_tracks` alongside it runs an independent
detector and tracker and emits evidence from both. The memory `bounded_evidence`
plugin retains the tracked things for later steps.

Corner junctions use `corner_max_corners` (600), `corner_radius_px` (96),
and a pair-distance range `corner_pair_min_px`..`corner_pair_max_px` (24..360).
A range with the low end above the high end is rejected at construction.
`adaptive_contours_max_boxes` remains accepted so older staged configs still
construct; the adaptive-contour cue itself is gone.

Diagnostic scratch is call-local; selector-response caches remain
content-addressed experiment inputs rather than temporal tracking history.
