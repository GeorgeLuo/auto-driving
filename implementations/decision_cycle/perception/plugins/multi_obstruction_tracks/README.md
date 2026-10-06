# Multi-obstruction temporal tracks

This plugin is the corrective replay proposed by the Astra review. It
uses low-threshold rectangular edge contours, removes regions that look like
lower-frame floor/background support, and associates several remaining generic
image-space regions across adjacent frames. When a contour disappears, a
bounded optical-flow prediction is marked explicitly rather than silently
turning into a held ghost.

The output is deliberately generic `obstacle` evidence. It does not claim a
semantic class, depth, traversability, or autonomous object identity. Each
record carries a track id, association score, separate `shape_support` and
`flow_support` evidence scores, and an explicit `new`, `matched`, `held`, or
`reacquired` status. Lost track ids are recorded in the `association` measurements rather
than held as unsupported ghost obstacles. Association uses a hard spatial gate
before the score tie-break, and both detector misses and lost-identity expiry
are bounded by configuration.

Track association, optical-flow history, and ID allocation live in this
plugin and keep their history in the host map, so a replacement instance
continues the same tracks. The `obstruction_observer` preset selects it after
`frame` and `floor_plane`. The durability scoring and diagnostic panels are
scripts under `scripts/perception/multi_obstruction_tracks/`.

`obstruction_tracks` has been retired. Use this plugin for explicit selections.
Existing staged selections containing `obstruction_tracks`, including previously
staged `obstruction_observer` presets, must be restaged. Activations store plugin
IDs and entrypoints; the preset name in metadata does not rewrite them. To
restage the preset:

```sh
./cli/automa vehicles update perception --id VEHICLE --preset obstruction_observer
```

The preset keeps its name and tuning and now selects `multi_obstruction_tracks`.
The composite variants also track their own detections and do not need this
plugin as a companion.
