# Milestone 004 Closeout: Physical Perception Parity

Status: closed 2026-07-18

## Outcome

The PiRacer now continuously runs the shared perception cycle while Donkey drive
mode remains manual `user`. Observation no longer depends on pilot mode. The
deployed runtime publishes one bounded latest frame/result snapshot over
read-only HTTP, and Automa can inspect that path through `info`, replacing
terminal stream, local frame-matched overlay, guided placement check, offline
strategy qualification, and a sustained viability measurement.

The operational algorithm remains the packaged `lightweight_observer`
(floor-plane control). One lab candidate, `floor_continuity`, was compared on
identical labeled physical-check frames and rejected with an explicit
`reject_keep_control` decision. Milestone 005 may now trust the physical
observation input path for bounded memory work; it must still consume evidence,
not promote perception claims into world truth.

## Durable Decisions

- Always-on observation uses one `AutonomyPilotPart` cycle with cadence gating
  (`AUTONOMY_OBSERVATION_INTERVAL_S`, default 0.5 s) and newest-frame
  consumption. Intermediate camera ticks are counted as skips, not backlog.
- Manual `user` mode forces zero pilot outputs even if an engine returns
  non-zero. DriveMode remains the movement authority gate.
- Observation status providers must never re-enter `AutonomyManager.status`.
  Status stays bounded; exact findings and JPEG bytes are separate opt-in GETs.
- Publish one in-memory latest snapshot only:
  `GET /autonomy/observation/latest` and
  `GET /autonomy/observation/latest/frame.jpg` with read-time health and matching
  frame identity. No default history or project-local perception logs.
- Automa owns physical operator presentation (stream, local loopback view,
  guided check). The Pi publishes snapshots; it does not host a second frontend.
- Stationary guided placement is the physical acceptance shape. The check never
  commands movement or mode changes. Unavailable camera-disable remains optional
  and is not required for routine runs.
- Qualify at most one stateless lab candidate against the packaged control on
  identical labeled frames. Promote only with at least two material behavioral
  gains; documented rejection is a valid milestone outcome.
- Viability gates use ≥90% of the configured observation cadence (and the 2 Hz
  design target as an upper bound), p95 result age ≤ 1 s, zero control, and
  user mode over a 60-second publication poll. Hard wall-clock 2.00 Hz is not
  required when the configured interval is 0.5 s.

## What Was Demonstrated

| Claim | Evidence |
| --- | --- |
| Boot to supervised manual-ready runtime | [boot-readiness.json](evidence/boot-readiness.json) |
| Always-on observation under `user` mode with zero control | [always-on-observation-smoke.json](evidence/always-on-observation-smoke.json) |
| Exact latest snapshot publication | Packages 2–3 implementation + live stream/check use of `/autonomy/observation/latest` |
| Guided placement check (clear/left/center/right/removed) | Live recorded run `lab/runs/perception-check/piracer-20260717-145716` |
| Floor-continuity rejected; keep control | [qualification report](evidence/floor-continuity-physical-check-qualification.json) |
| 60 s operational viability | [physical-viability-60s.md](evidence/physical-viability-60s.md) |

Measured operational cost on `lightweight_observer` (60 s live):

- ~1.90 fresh published results/s at configured 0.5 s interval
- p95 result age 513 ms; median processing duration ~317 ms
- control always zero; mode always `user`
- manage.py RSS roughly 196–212 MB; host CPU sample ~94%

## Failures And Residual Limits

- Guided check overall did not fully pass: the first **right** placement produced
  no boundary (`zones=[]`). A closer right sanity frame later recovered a right
  zone under the same control algorithm. Directional reliability is therefore
  distance/placement sensitive, not perfect.
- Clear-floor scenes can still emit a weak extra boundary on the packaged
  control (one left zone on the clear step in the recorded run). Scoring treats
  “no strong central boundary” rather than absolute zero proposals.
- Unavailable camera-disable was deliberately left optional after operator
  preference; routine acceptance uses clear → left → center → right → removed.
- Viability polls the publication endpoint; it does not instrument in-process
  Donkey loop counters directly. Host CPU samples can appear saturated and are
  process-sample evidence, not a full system profile.
- Floor-continuity was faster on desktop but increased clear-floor false
  positives and boundary count; it remains lab-only.
- Semantic object identity, metric depth, temporal tracking, autonomous mode,
  and decision memory were out of scope and remain unproven.

## Validation

- Deterministic suite at closeout head: 191 tests discovered; default run
  reports 189 passes and 2 named live skips.
- Live Pi path exercised across packages 0–6 with read-only status, publication,
  stream, guided check, and 60 s viability. No closeout step commanded movement.
- Strategy qualification is offline common-frame scoring over the live-recorded
  check frames; it does not claim onboard candidate latency.

## Deferred Work

- Milestone 005: bounded evidence memory over the proven observation path.
- Improve or re-qualify physical right-side and clear-floor false-positive
  behavior only when a new candidate states an explicit adoption gate.
- Motion-capable physical validation remains a separately named future
  operation with stronger safety requirements.
- Optional unavailable-camera step remains available for explicit failure-path
  drills, not default operator runs.

## References

- Frozen [milestone plan](plan.html)
- [Documentation ledger](../completed.md)
- Implementation PRs: [#22](https://github.com/GeorgeLuo/auto-driving/pull/22),
  [#23](https://github.com/GeorgeLuo/auto-driving/pull/23),
  [#25](https://github.com/GeorgeLuo/auto-driving/pull/25),
  [#26](https://github.com/GeorgeLuo/auto-driving/pull/26),
  [#27](https://github.com/GeorgeLuo/auto-driving/pull/27),
  [#28](https://github.com/GeorgeLuo/auto-driving/pull/28),
  [#29](https://github.com/GeorgeLuo/auto-driving/pull/29),
  [#30](https://github.com/GeorgeLuo/auto-driving/pull/30),
  [#31](https://github.com/GeorgeLuo/auto-driving/pull/31)
