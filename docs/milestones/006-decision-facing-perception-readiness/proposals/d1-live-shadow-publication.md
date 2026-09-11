# Draft D1 — Live shadow-cycle publication

## Status

Feedback draft only. This document is not an accepted proposal, does not
change the M006 plan, and does not authorize implementation.

## The question

Can D1 expose and accept the shadow cycle that the PiRacer path already
computes, with genuine host identity and liveness, so Automa can inspect it and
reject it safely when the producer stops or the publication becomes stale?

The desired operator experience is simple:

1. Put the PiRacer in `user` mode with pilot output at zero.
2. Start the existing PiRacer runtime; its onboard part already invokes the
   shared decision cycle on camera input.
3. Have Automa read one full current cycle with vehicle, run, activation,
   generation, frame, source, proposal, and authority identity.
4. Stop the producer or wait past its freshness limit and see **unavailable**,
   not the last cycle presented as current.

The live cycle should use the same decision contract that #203 exercises
offline. A live cycle may contain a proposed steering intent; it must not apply
that intent to the vehicle.

## Review kind

Live or external evidence

## What already exists

Source inspection changes the likely shape of this work:

- `implementations/runtime/donkeycar/donkey_part.py` already calls the shared
  `AutonomyCycleHost` and retains the resulting cycle in its latest onboard
  snapshot. It also reports frame timing, mode, and engine control data.
- The M006 decision surface already builds and strictly validates
  generation-scoped `vehicle_decision_stream_frame_v0` frames for the local
  automation worker in `cli/automa_cli/decision.py`.
- The physical HTTP publication currently exposes the onboard observation and
  selected cycle components, but it is not yet established that it exposes the
  complete accepted decision frame with the same identity and liveness fields.

The likely gap is therefore a physical publication/transport adapter and its
read-side acceptance—not a second decision loop or a new decision policy.

## Proposed boundary

```text
PiRacer camera and memory
        ↓
existing Donkey AutonomyPilotPart
        ↓
physical publication bridge (D1 gap to confirm)
        ↓
existing Automa decision frame validator and CLI
```

D1 owns only the missing bridge: expose the full cycle and genuine run,
activation, generation, frame/source, and producer-liveness identity at the
physical boundary, then accept it through the existing decision contract. It
also needs the host observations needed to show `user` mode and zero pilot
output. The existing shadow engine, `AutonomyPilotPart` cycle execution, and
local frame validator remain owners of their current responsibilities.

The first useful result is one honest live cycle and one honest stopped/stale
result. Left/right decision behavior remains the behavior established by #203;
this proposal does not retune perception or claim vehicle avoidance.

## In scope

- Confirm whether the existing PiRacer publication can carry the accepted full
  decision frame; if not, add the smallest adapter or endpoint needed.
- Run, activation, generation, frame, source, and producer-liveness identity.
- The current observation/memory and resulting plan, proposal, and authority
  data needed to correlate one cycle.
- Host mode and pilot-output observations sufficient to distinguish proposed
  intent from actual vehicle output.
- Explicit unavailable behavior for a stopped, stale, mismatched, or
  incomplete publication.
- A short operator procedure for checking the path while the PiRacer remains
  stationary.

## Out of scope

- Vehicle movement or applying proposed commands.
- Reimplementing the decision cycle, shadow engine, perception, memory,
  tracking, prediction, or steering policy.
- The offline toggle/page implemented by #203.
- A live decision URL, retained-image overlay, or redesign of the operator
  page; those are separate questions.
- Chase evaluator state or privileged simulator inputs.
- A new decision schema parallel to `vehicle_decision_stream_frame_v0`.
- Automation-launcher changes unless inspection confirms the existing PiRacer
  runtime cannot be exercised without them.

## Independence from #203

There is no implementation dependency on #203. #203 loads a saved decision
sequence and runs the real shadow engine for two offline scenarios. D1 should
produce the underlying live cycle contract that can later be replayed or
inspected by the same decision surfaces; it should not import or turn the
offline inspector into a live producer.

The branches should remain separate. D1 should reuse the underlying
`vehicle_decision_stream_frame_v0` contract and existing shadow engine, not
depend on #203's `automa_decision_inspection_v1` wrapper or edit its inspector
page. A later live-page integration would be a separate review question.

## Approximate implementation impact

These are rough added/changed-line estimates for sizing discussion, not a
commitment. They exclude proof-of-work reports, generated HTML, and the
existing contents of the files. They reflect the existing cycle and local
decision publisher described above.

| Candidate file | Likely work | Estimate |
| --- | --- | ---: |
| `implementations/runtime/donkeycar/donkey_part.py` | Add only the missing public cycle/identity fields; the cycle execution already exists | 20–70 |
| `cli/automa_cli/physical_observation.py` or a focused physical-decision adapter | Fetch, decode, correlate, and report the physical decision publication | 40–100 |
| `cli/automa_cli/decision.py` | Reuse or factor current frame acceptance for a remote physical producer | 20–80 |
| `cli/automa_cli/app.py` | Make the existing decision-stream command usable for PiRacer if provider routing needs a small change | 0–30 |
| `cli/automa_cli/automation.py` | Only if the existing PiRacer runtime cannot be exercised without a launcher change | 0–80 |
| `tests/cli/` and `tests/integration/` | Fixtures for identity, stop/stale/mismatch, source correlation, and user-mode zero output | 80–160 |
| Donkey host `manage.py` (possibly a separately owned repository) | Add the actual HTTP route only if the current host cannot expose the full cycle | 30–80 external |

Likely in-repository base case: **80–250 production LOC plus 80–160 test
LOC**, or roughly **160–410 LOC total**. If both a new host endpoint and an
Automa producer-launcher change are required, the upper bound is approximately
**360–520 in-repository LOC**, plus the external host change. No new decision
engine, decision policy, page, or #203 code should be needed.

The first implementation pass should confirm whether the existing physical
observation endpoint can be extended by this repository or is owned by the
Donkey host's `manage.py`. That check determines whether the work stays near
the lower estimate or requires an external capability owner before product
code is written.

## What needs feedback

- Can the existing physical observation publisher be extended to expose the
  accepted full decision frame, or is its host endpoint separately owned?
- Which existing PiRacer process is authoritative for run, activation,
  generation, and producer liveness identity?
- Where can mode, pilot input, and final host output be sampled without
  inferring them from the shadow engine's authority result?
- Is the CLI decision stream sufficient for the first live check, with a live
  page deferred?
- What is the smallest stopped/stale interval that gives a trustworthy answer,
  and can the existing validator enforce it for a physical producer?

## Expected handoff

After feedback, a higher-reasoning review should confirm the endpoint owner,
decide whether an in-repository adapter is enough, and finalize the exact
public interface, rejection cases, and live validation procedure. Only then
should this become a formal M006 proposal with a separate implementation
review unit.
