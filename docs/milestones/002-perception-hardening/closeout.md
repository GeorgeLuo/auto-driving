# Milestone 002 Closeout: Perception Hardening

Status: closed 2026-07-13

## Outcome

The repository now has one normalized, provider-neutral perception path for
live Chase frames, live PiRacer frames, and recorded replay. Perception plugins
share a lifecycle and structured evidence contract, while the CLI provides
bounded run, replay, candidate comparison, setup, and inspection workflows.
The deployed Pi runtime loads the same lightweight perception activation from
a hashed autonomy release before the idle decision stage.

## Durable Decisions

- Perception outputs evidence, not durable world facts or semantic truth.
- Camera inputs are normalized at the runtime boundary so plugins do not care
  whether frames originated as files or in-memory arrays.
- Plugins consume normalized sensor inputs independently. Cross-plugin fusion
  must be introduced as an explicit later layer rather than implicit ordering.
- Temporal perception state is bounded and run-local. Durable identity and
  history belong to the later memory stage.
- Floor boundaries are the production lightweight evidence path. Classical
  coherent regions remain a promotion candidate; FastSAM and feature-motion
  tracks remain local or offline diagnostics.
- Artifact recording is opt-in. Normal live and onboard perception writes no
  frame or diagnostic history to disk.

## Validation

- All 78 repository tests pass; the one opt-in live-simulator test is skipped
  by the default suite.
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
- [Documentation ledger](../completed.md)
