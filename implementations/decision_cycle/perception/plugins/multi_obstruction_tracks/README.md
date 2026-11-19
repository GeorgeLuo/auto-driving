# Multi-obstruction temporal tracks

This plugin is the corrective replay proposed by the Astra review. It
uses low-threshold rectangular edge contours, drops boxes against a fixed
image cut-off (`floor_cutoff_y`, plus the contour edge gates), and associates
several remaining generic image-space regions across adjacent frames. The
cut-off is an image-row rule. This plugin does not read `floor_plane` or
`floor_continuity` output. When a contour disappears, a bounded optical-flow
prediction is marked explicitly rather than silently turning into a held ghost.

Contour gates are `contour_min_vertices`..`contour_max_vertices` (4..15),
`contour_min_width`..`contour_max_width` (0.04..0.85) and
`contour_min_height`..`contour_max_height` (0.08..0.60). A pair with the low
end above the high end is rejected. Image-edge rejection uses `reject_bottom_y`
(0.95), `reject_above_y` (0.28), `reject_top_edge_y` (0.03) and
`reject_top_edge_height` (0.25). The tracker uses an LK window
`flow_window` (21), `flow_levels` (3), a forward-backward gate
`flow_forward_backward_px` (1.5) and `feature_seed_cap` (80).

The output is deliberately generic `obstacle` evidence. It does not claim a
semantic class, depth, traversability, or autonomous object identity. Each
record carries a track id, association score, separate `shape_support` and
`flow_support` evidence scores, and an explicit `new`, `matched`, `held`, or
`reacquired` status. Lost track ids are recorded in the `association` measurements rather
than held as unsupported ghost obstacles. Association uses a hard spatial gate
before the score tie-break, and both detector misses and lost-identity expiry
are bounded by configuration.

Track association, optical-flow history, and ID allocation live in this
plugin and keep their history in the host map. A selection change at or before this plugin
starts fresh tracks and warmup. The `obstruction_observer` preset selects it after
`frame` and `floor_plane`. The durability scoring and diagnostic panels are
scripts under `scripts/perception/multi_obstruction_tracks/`.

`obstruction_tracks` has been retired. Use this plugin for explicit selections.
Existing staged selections containing `obstruction_tracks`, including previously
staged `obstruction_observer` presets, must be replaced. Activations store plugin
IDs and entrypoints; the preset name in metadata does not rewrite them.
`hosts update` replaces them with the step's default:

```sh
./cli/automa hosts update --name VEHICLE
```

The preset keeps its name and tuning and now selects `multi_obstruction_tracks`.
The composite variants also track their own detections and do not need this
plugin as a companion.
