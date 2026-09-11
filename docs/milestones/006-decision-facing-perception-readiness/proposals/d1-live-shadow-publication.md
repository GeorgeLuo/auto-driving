# Draft D1 — Live shadow-cycle publication

## Status

Feedback draft only. This document is not an accepted proposal, does not
change the M006 plan, and does not authorize implementation.

## The question

Can a stationary PiRacer publish a genuinely live shadow decision cycle that
Automa can identify, inspect, and reject safely when the producer stops or the
publication becomes stale?

The desired operator experience is simple:

1. Put the PiRacer in `user` mode with pilot output at zero.
2. Start the existing shadow decision path.
3. Observe a current cycle with its vehicle, run, activation, generation, frame,
   source, proposal, and authority identity.
4. Stop the producer or wait past its freshness limit and see **unavailable**,
   not the last cycle presented as current.

The live cycle should use the same decision contract that #203 exercises
offline. A live cycle may contain a proposed steering intent; it must not apply
that intent to the vehicle.

## Review kind

Live or external evidence

## Proposed boundary

```text
PiRacer observation and memory
        ↓
existing shadow decision engine
        ↓
D1 live publication and liveness boundary
        ↓
existing Automa decision stream / inspection surfaces
```

D1 owns the live producer boundary: genuine run and activation identity,
generation-scoped liveness, frame/source identity, and the host observations
needed to show `user` mode and zero pilot output. It should reuse the existing
shadow engine and decision-cycle types rather than create a second decision
format.

The first useful result is one honest live cycle and one honest stopped/stale
result. Left/right decision behavior remains the behavior established by #203;
this proposal does not retune perception or claim vehicle avoidance.

## In scope

- A supported PiRacer live shadow-cycle publication path.
- Run, activation, generation, frame, and producer-liveness identity.
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
- New perception, memory, tracking, prediction, or steering policy.
- The offline toggle/page implemented by #203.
- A live decision URL, retained-image overlay, or redesign of the operator
  page; those are separate questions.
- Chase evaluator state or privileged simulator inputs.
- Deployment automation beyond what is necessary to identify the actual
  running producer.

## Independence from #203

There is no implementation dependency on #203. #203 loads a saved decision
sequence and runs the real shadow engine for two offline scenarios. D1 should
produce the underlying live cycle contract that can later be replayed or
inspected by the same decision surfaces; it should not import or turn the
offline inspector into a live producer.

The branches should remain separate. Shared decision schemas and the shadow
engine are intentional interface dependencies. The D1 owner should avoid
editing #203's inspector page unless a later, separately reviewed integration
is requested.

## Approximate implementation impact

These are rough added/changed-line estimates for sizing discussion, not a
commitment. They exclude proof-of-work reports, generated HTML, and the
existing contents of the files. The current Donkey part already runs the
shared cycle, so the likely work is publication and acceptance plumbing.

| Candidate file | Likely work | Estimate |
| --- | --- | ---: |
| `implementations/runtime/donkeycar/donkey_part.py` | Add or expose run/activation/generation identity and the decision-cycle fields alongside the existing onboard snapshot | 40–90 |
| `cli/automa_cli/physical_observation.py` | Fetch, decode, correlate, and report a physical decision publication with explicit stale/unavailable results | 60–120 |
| `cli/automa_cli/decision.py` | Reuse or factor the existing stream-frame validation for a remote physical producer and host-observation checks | 50–110 |
| `cli/automa_cli/app.py` | Wire the existing decision-stream command to the PiRacer path if the current command cannot do so | 10–40 |
| `cli/automa_cli/automation.py` | Only if Automa must start and own the PiRacer producer; otherwise no change | 0–120 |
| `tests/cli/` and `tests/integration/` | Contract fixtures for live identity, stop/stale/mismatch, source correlation, and user-mode zero output | 120–220 |
| Donkey host `manage.py` (possibly a separately owned repository) | Add the actual HTTP route if the current host exposes observation but not decision publication | 30–80 external |

Likely in-repository base case: **160–360 production LOC plus 120–220 test
LOC**, or roughly **280–580 LOC total**. If a new host endpoint and Automa
producer launcher are both required, the upper bound is approximately
**400–700 in-repository LOC**, plus the external host change. No new decision
engine, decision policy, page, or #203 code should be needed.

The first implementation pass should confirm whether the existing physical
observation endpoint already carries enough of the cycle. That one check may
move the work from the lower to the upper estimate and may identify an
external owner before any product code is written.

## What needs feedback

- Is the smallest D1 deliverable a publisher/liveness capability, or only a
  live verification procedure over an existing publisher?
- Which existing PiRacer process is authoritative for run, activation, and
  generation identity?
- Where can mode, pilot input, and final host output be sampled without
  inferring them from the shadow engine's authority result?
- Is the CLI decision stream sufficient for the first live check, with a live
  page deferred?
- What is the smallest stopped/stale interval that gives a trustworthy answer?

## Expected handoff

After feedback, a higher-reasoning review should finalize the owner, exact
public interface, rejection cases, and live validation procedure. Only then
should this become a formal M006 proposal with a separate implementation
review unit.
