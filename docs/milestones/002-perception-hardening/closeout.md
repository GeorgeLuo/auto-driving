# Milestone 002 Closeout: Perception Hardening

Status: closed 2026-07-13

## Outcome

The repository now has one component-driven, provider-neutral perception path for
live Chase frames, live PiRacer frames, and recorded replay. Perception plugins
share a narrow structured-evidence contract, while the generic runner owns
input injection, lifecycle, status, timing, text rendering, and diagnostics.
The CLI provides
bounded run, replay, candidate comparison, setup, and inspection workflows.
The automation worker publishes current camera frames independently from its
latest perception result to a loopback-only live view without enabling
recording. A bounded latest-frame queue prevents slow inference from building a
backlog, and the view reports the overlay source frame and lag explicitly.
The deployed Pi runtime loads the same lightweight perception activation from
a hashed autonomy release before the idle decision stage.

## Durable Decisions

- Perception outputs evidence, not durable world facts or semantic truth.
- The stable stage knows only the generic sensor snapshot and plugin lifecycle;
  it contains no camera types or perception algorithms.
- Plugins declare named component inputs; the framework resolves and caches
  them before invoking plugin logic. A concrete shared camera adapter normalizes
  file-backed and in-memory readings only when requested.
- Plugins consume injected components independently and return only structured
  signals, spatial evidence, and named measurements. The perception result does
  not duplicate the enclosing sensor snapshot. Cross-plugin fusion
  must be introduced as an explicit later layer rather than implicit ordering.
- Framework-derived plugin runs own availability, warm-up, errors, timing,
  counts, source attribution, text output, and diagnostic namespaces. Plugins
  do not duplicate those operational concerns.
- Temporal perception state is bounded and run-local. Durable identity and
  history belong to the later memory stage.
- Camera publication and perception inference use separate cadences. Slow
  inference skips superseded frames and never silently presents an old overlay
  as current.
- Floor boundaries are the production lightweight evidence path. Classical
  coherent regions remain a promotion candidate; FastSAM and feature-motion
  tracks remain local or offline diagnostics.
- Artifact recording is opt-in. Normal live and onboard perception writes no
  frame or diagnostic history to disk.
- Operational visualization consumes the serialized perception contract
  outside `autonomy`. It overlays only image-coordinate evidence and leaves
  other coordinate systems in the data view.
- Optional model candidates run behind an isolated local worker. Core receives
  only the stable perception contract, and physical deployment does not ship
  candidate environments or weights.

## Validation

- The default repository harness reports 93 passed and one intentionally
  skipped live-simulator test. That opt-in live test also passes when enabled.
- A post-refactor staged <code>sim_debug</code> run processed two live
  <code>chaser-depth-obstacles</code> frames with zero failures. The full
  automation worker then published the same v2 signals and spatial evidence at
  its loopback view, with recording and disk logging disabled.
- The live Chase perception view keeps versioned frame URLs while publishing
  camera frames independently from perception. It displays source frame, frame
  lag, and result age for every overlay. Desktop and mobile checks found no
  horizontal overflow or browser errors, and region, label, and per-kind
  overlay controls all changed the rendered canvas as intended.
- A live FastSAM activation produced eleven image-space region polygons in the
  depth-obstacles scene. Polygon rendering, box fallback, collision-free labels,
  isolated-worker cleanup, and no-recording artifact behavior were verified.
- Live Chase runs completed five lightweight and five temporal frames with no
  failures and no movement authority.
- The CLI explicitly selected `chaser-depth-obstacles` and captured fourteen
  stationary/action views. FastSAM, classical regions, and the temporal chain
  replayed that same corpus with zero failed frames and no map input.
- A live ten-frame PiRacer run completed with no failures or recording, using
  the same CLI and mapper contract as Chase.
- Fifteen onboard Pi cycles completed with zero perception or control errors.
  Total perception cost was 293 ms median and 318 ms p95; floor processing was
  285 ms median and frame observation was 6.5 ms median.
- The live idle engine emitted only zero steering and throttle. The Pi returned
  to Donkey `user` mode, reported no thermal or voltage throttling, and retained
  recording-off state.

## Deferred Work

- Define the memory input contract from the structured current evidence proven
  here; do not persist raw artifacts or run-local track ids as world truth.
- Optimize floor processing or decouple perception cadence before an active
  controller assumes a fresh result at Donkey's configured 20 Hz loop rate.
- Evaluate the classical region candidate against controlled simulator truth
  and physical annotations before promotion.
- Add broader lighting, texture, blur, and layout fixtures with explicit quality
  labels; representation health is not semantic accuracy or obstacle recall.
- Make the Donkey drive service start reliably after a Pi power cycle, or make
  runtime readiness an explicit operator operation. This power-cycle required
  an autonomy restart even though the deployed release remained installed.

## References

- Frozen [milestone plan](plan.html)
- Tracked [Chase depth-obstacles validation record](evidence/chase-depth-obstacles-validation.json)
- Tracked [Pi validation record](evidence/pi-validation.json)
- Hashed [local artifact manifest](evidence/local-artifacts.json)
- Tracked [live FastSAM view validation](evidence/live-fastsam-view-validation.json)
- [Documentation ledger](../completed.md)
