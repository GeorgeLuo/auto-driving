# Composite shared CV obstruction tracks

This plugin is the Step 2 composite candidate for the paired bright/dark
exploration. It keeps the `multi_obstruction_tracks` output contract and adds
issue-219's `canny_morph`, `quad_rectangularity`, `partial_contour`,
`photometric_windows`, Hough, and corner/junction cues. Classical color regions
and the existing floor-continuity model run on raw RGB. A shared luminance path
feeds all edge, partial-face, and line/junction cues. Line and junction boxes
are stored as support measurements and cannot create object candidates by
themselves.

The recovered issue-219 cue source is recorded in
`src/issue219_cues.py`; its source commit is `94292e8`'s parent implementation
from commit `2e0a49d`. Step 3 enumerates bounded raw proposals, spatial
clusters, raw/union/robust/median/intersection hypotheses, and deterministic
selector choices before the inherited tracker receives a selected geometry.
