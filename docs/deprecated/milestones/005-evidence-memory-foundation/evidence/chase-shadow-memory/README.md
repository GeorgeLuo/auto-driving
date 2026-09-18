# Chase Shadow Memory Evidence

This evidence records a live `chaser-depth-obstacles` check against the local
`bounded_evidence` memory stage on 2026-07-22. Chase's built-in controller kept
movement authority while the rewritten cycle ran observe-only and emitted no
applied control.

The run consumed Metrics UI's unmerged Milestone 002 atomic evaluation capture.
Each response paired the rendered chaser camera frame with evaluator-only control
state from the same simulation epoch and frame index. The evaluator state stayed
outside the candidate sensor snapshot, observation, and memory inputs.

## Result

- Atomic alignment: candidate and evaluator records matched at simulator frames
  `63220` and `63247` in one simulation epoch.
- Retention: `thing:floor_boundary_000` and
  `signal:floor_boundary_available` were observed in `chase_frame_063220`, absent
  from the next perception, and retained in `chase_frame_063247` with their
  original source-frame provenance.
- Provenance: all 12 sampled memory records cited an observed current or prior
  frame; 10 were current-frame records and 2 were retained-prior records.
- Reset: the final reset returned an empty snapshot and advanced `epoch-11` to
  `epoch-12` and `reset_count` from 10 to 11.
- Safety: the simulator remained the authority, candidate control was complete
  zero/not-applied evidence, and no shadow data leaked into observation or memory.

This run does not claim a live max-age expiry phase. Expiry remains a separate
Milestone 005 closeout requirement rather than being inferred from reset.

## Artifacts

- [Provenance extract](provenance_extract.html)
- [Initial source frame](frames/chase_frame_063220.png)
- [Retained-evidence frame](frames/chase_frame_063247.png)
- [Machine-readable result](result.json)

The full opt-in development record remains under
`lab/runs/memory-check/chase-sim-chaser-20260722-020048` and is intentionally not
tracked.
