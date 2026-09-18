# Chase Max-Age Expiry Evidence

This package records a live `chaser-depth-obstacles` memory check on
2026-07-26. Chase's built-in `programmatic` controller retained simulator
authority while the external automation worker ran observe-only and emitted
zero, unapplied control.

The check used the `floor_continuity` perception candidate and a deliberately
short, explicit `max_age_ms=1000` runtime bound. No dropout was injected. The
moving scene caused `floor_boundary_002` to disappear naturally after it had
been observed and retained.

## Result

- All seven phases passed: history boundary, atomic shadow alignment, ordered
  provenance, observe-only safety, shadow isolation, max-age expiry, and final
  reset.
- Five source frames advanced in one simulation epoch and aligned exactly with
  their evaluator-only shadow references.
- All 34 sampled memory records had valid current/prior source-frame
  provenance; one record was retained from a prior sampled frame.
- `floor_boundary_002` was current in `chase_frame_082364`, retained with that
  provenance in `chase_frame_082379`, and absent in
  `chase_frame_082424`.
- The tracked key left after 1,133 ms against `max_age_ms=1000`, with
  `reset_used=false`, stable worker/run/simulation identity, capacity headroom,
  and no capacity eviction.
- The later cleanup reset advanced `epoch-3` to `epoch-4`; it was not used to
  establish expiry.

## Dependency

The run used Metrics UI commit `351e3af` (`m002/04-closeout`), which adds the
bounded actor-control reference to the atomic evaluation capture. That
producer branch remains separate from this repository and was not modified by
this evidence run.

## Artifacts

- [Machine-readable result](result.json)
- [Provenance extract](provenance_extract.html)
- [Current source frame](frames/chase_frame_082364.png)
- [Retained-prior frame](frames/chase_frame_082379.png)
- [Expiry frame](frames/chase_frame_082424.png)

All six sampled frames referenced by the provenance extract are included under
`frames/`; the three links above identify the lifecycle transition directly.

The full opt-in run remains under
`lab/runs/memory-check/chase-sim-chaser-20260726-014031` and is intentionally
not tracked.
