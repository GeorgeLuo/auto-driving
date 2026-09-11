# D2 — Standalone decision inspection

## Current scope

Operator-approved redesign on PR #203, 2026-09-10. This supersedes the earlier
live publication, image-retention, generation, and host-observation display
design in this branch. The live perception/memory server refactor is a separate
PR targeting main.

Review question: can an operator load a saved decision input through the CLI,
toggle obstruction left/right, and inspect the actual shadow engine input and
result without live capture, an automation worker, or a fixture process?

## Operator journey

```sh
./cli/automa vehicles decision inspect --from-run examples/decision-inspection --open
```

The CLI loads one frame from the existing offline decision sequence format,
computes both side scenarios, starts a dedicated loopback inspector, and opens
its root page. Right obstruction produces steer left for the supplied sample;
left obstruction produces steer right. The selected result is the prominent
black-box card. Disclosure state persists across toggles.

`--json` returns the same artifacts and exits. `--frame` selects a zero-based
sequence position. Optional `--id` reads a staged shadow configuration;
otherwise packaged defaults apply. No vehicle staging is required for the sample.

## Ownership and contracts

- Existing strict observation/memory decoders validate the input.
- The application operation invokes the existing shadow engine's `run_cycle`
  for each side, retaining source time, observation, and engine configuration.
- Supported image-relative obstruction records are copied and repositioned;
  original bounding-box/polygon geometry is cleared.
- The page selects computed artifacts and renders their signals.
- Both artifacts include their full cycle source, plan, authority, input hash,
  frame identity, changed record IDs, and engine configuration.
- Missing repositionable evidence is an explicit input error. Stale evidence
  can legitimately produce hold. The UI never substitutes steering for hold.
- No authority is applied, no live runtime record is written, and no input file
  is changed. The local server lifetime ends with Ctrl-C.
- Existing decision apply, info, and live terminal stream remain supported.
  Info supplies the standalone inspection command rather than probing a live
  page. Live perception no longer owns a decision publisher or decision routes.

## Verification and limits

Verify through the public CLI, compare `--json` with the served artifact, and
exercise both directions and disclosure persistence in Chromium. Run the
existing decision/perception/memory compatibility checks. No new regression
scaffolding is requested.

The supplied input is explicitly synthetic. This is offline decision inspection,
not evidence of perception quality or Chase/Pi behavior. M006-06/07 live evidence
remains outstanding; this redesign does not promote any milestone criterion.

See [implementation notes](IMPLEMENTATION-SPEC.md) and the
[sample usage](../../../../examples/decision-inspection/README.md).
