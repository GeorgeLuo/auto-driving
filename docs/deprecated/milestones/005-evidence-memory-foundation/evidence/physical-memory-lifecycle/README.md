# Stationary Pi Memory Lifecycle Evidence

This evidence records a non-moving PiRacer check against the active onboard
`bounded_evidence` memory stage on 2026-07-20. The operator placed a cardboard
box in view for the present phase and removed it for dropout. The check sent no
movement commands and confirmed zero live steering and throttle.

The physical run was `piracer-20260720-153802`. The tracked extract was
regenerated as `piracer-20260720-154113` from that run's immutable report,
sequence, and JPEGs after the record writer was fixed to persist recording
metadata and embed source frames. No observations or lifecycle results were
recomputed during regeneration.

## Result

- Present: `thing:floor_boundary_002` was refreshed from exact frame
  `donkey_frame_000626` while the box was visible.
- Dropout: exact frame `donkey_frame_000723` no longer refreshed that key after
  the box was removed, while live onboard memory retained it.
- Expiry: the key was absent after the configured 10-second maximum age.
- Reset: onboard reset returned an empty snapshot and advanced `epoch-4` to
  `epoch-5` and `reset_count` from 3 to 4.
- Safety: `movement_commands_sent=false`, `forced_dropout=false`, and
  `ephemeral_local_reducer=false`.

## Artifacts

- [Provenance extract](provenance_extract.html)
- [Present source frame](frames/donkey_frame_000626.jpg)
- [Dropout source frame](frames/donkey_frame_000723.jpg)
- [Machine-readable result](result.json)

The full opt-in development record remains under
`lab/runs/memory-check/piracer-20260720-154113` and is intentionally not tracked.
