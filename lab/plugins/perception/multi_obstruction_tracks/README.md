# Multi-obstruction temporal tracks

This lab candidate is the corrective replay proposed by the Astra review. It
uses low-threshold rectangular edge contours, removes regions that look like
lower-frame floor/background support, and associates several remaining generic
image-space regions across adjacent frames. When a contour disappears, a
bounded optical-flow prediction is marked explicitly rather than silently
turning into a held ghost.

The output is deliberately generic `obstacle` evidence. It does not claim a
semantic class, depth, traversability, or autonomous object identity. Each
record carries a track id, association score, separate `shape_support` and
`flow_support` evidence scores, and an explicit `new`, `matched`, `held`, or
`reacquired` status. Lost tracks are recorded in the diagnostic summary rather
than held as unsupported ghost obstacles. Association uses a hard spatial gate
before the score tie-break, and both detector misses and lost-identity expiry
are bounded by configuration.

This is an offline lab candidate only.
