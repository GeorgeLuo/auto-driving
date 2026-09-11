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
- On the M006 branch, the shadow adapter keeps the accepted
  `ShadowDecisionCycleResult` (including `source`, `plan`, and `authority`)
  separately from the generic `DecisionCycleResult`. The current Pi
  observation publication does not prove that inner result is exported
  atomically with the onboard snapshot, so D1 must close that correlation gap.

The likely gap is therefore a physical publication/transport adapter and its
read-side acceptance—not a second decision loop or a new decision policy.

## Open questions: options and baseline

The following is a feasibility baseline for the open design questions. It is
not an implementation commitment. The options are grounded in the current
PiRacer/Donkey source, the generated-vendor patch, and the M006 local decision
surface. A later accepted proposal should retain only the selected boundary
and the smallest rejection cases.

### Publisher and identity authority

The existing Pi path has a publisher, but it is an observation publisher, not
yet a complete decision-stream publisher. `AutonomyPilotPart` retains the
latest cycle and exposes `automa_physical_observation_publication_v0` through
the patched Donkey HTTP routes `/autonomy/observation/latest` and
`/autonomy/observation/latest/frame.jpg`. The payload includes frame timing,
mode, control, perception, observation, and memory, but does not currently
carry the full run/activation/generation identity tuple
([`donkey_part.py`](https://github.com/GeorgeLuo/auto-driving/blob/milestone/006-decision-facing-perception-readiness/implementations/runtime/donkeycar/donkey_part.py#L267-L352),
[`waveshare-donkeycar-local.patch`](https://github.com/GeorgeLuo/auto-driving/blob/milestone/006-decision-facing-perception-readiness/deploy/targets/donkeycar/patches/waveshare-donkeycar-local.patch#L267-L390)).

| Option | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Extend the existing Pi observation endpoint into the accepted decision publication | High | The endpoint already reads the in-process publisher and already transports the cycle components and freshness state. This keeps one Pi HTTP boundary and avoids a second decision loop. | The current schema and route do not establish run identity or activation-generation matching; the schema/adapter must be tightened rather than treating the current payload as sufficient. |
| Make `manage.py drive` the runtime authority and have `AutonomyPilotPart` attach its identity to each cycle | High; recommended | `manage.py` starts the Donkey process, loads `runtime/decision/active.json`, constructs `AutonomyCycleHost`, and wires `AutonomyPilotPart`. The part owns the actual frame/cycle pairing. A process-start/run token plus the successfully loaded activation token can therefore be attached at the source. | Neither component currently exposes an explicit run token or generation token. This is a small source-boundary addition, not an already-existing contract. |
| Make `AutonomyPilotPart`/`AutonomyCycleHost` independently authoritative | Medium | It is the narrowest owner of the cycle result, frame identity, and retained snapshot, so it can produce a coherent publication without asking another process to reconstruct the cycle. | It is an in-process object, not a process lifecycle boundary; its current frame counter resets on restart and cannot by itself prove producer liveness or distinguish activation generations. |
| Reuse the local Automa automation worker's `run_id`, PID, and `state.json` | Low for PiRacer | The M006 validator already checks these fields and rejects dead, stopped, mismatched, or stale local workers. | That worker is the Chase-simulator producer. Reusing its identity for PiRacer would make the observer or CLI impersonate the physical producer and would conflate two runtimes. |
| Create a new bridge process that mints its own run/generation identity | Medium as transport, low as authority | A bridge could adapt the existing HTTP payload to the M006 frame contract without changing Donkey vendor code. | A bridge-created token identifies the bridge session, not the Pi process that ran the cycle; it adds another liveness boundary and risks accepting a disconnected or relabeled cycle. |

The current recommendation is therefore: `manage.py drive` owns the Pi
runtime instance and the activation it successfully loaded;
`AutonomyPilotPart` attaches that source identity to each completed cycle; the
HTTP publisher and Automa CLI transport, validate, and reject mismatches. The
local Automa worker remains authoritative only for Chase. This requires adding
the missing source identity fields; no existing component currently supplies
the entire tuple.

### Endpoint ownership and adapter shape

The endpoint is a separate patched DonkeyCar host boundary, not an unknown
external service in this checkout. The app assembly is source-controlled in
`deploy/targets/donkeycar/app/manage.py`, while the Tornado route handlers are
source-controlled in `deploy/targets/donkeycar/patches/waveshare-donkeycar-local.patch`;
deployment generates the pinned DonkeyCar checkout and applies that patch.
`manage.py` instantiates the controller and assigns the onboard part as
`observation_publisher`, but it does not own the route handler. The existing
physical CLI fetches that observation route, while the M006 decision stream
currently reads a local file produced by the Chase worker.

| Option | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Treat `/autonomy/observation/latest` as the decision publisher | Low; reject | It already carries a frame and selected cycle components and already has freshness handling. | Its declared schema is `automa_physical_observation_publication_v0`; it does not establish the exact decision-frame envelope, activation generation, producer run identity, or remote liveness. Relabeling it would make provenance look stronger than it is. |
| Extend `AutonomyPilotPart.publish_latest()` and the existing route | High | The source publisher already owns the coherent latest snapshot and the route already reads it without re-entering manager status. This is the smallest code path and preserves one physical HTTP boundary. | Changing the observation payload in place risks breaking the physical perception consumer and still requires a distinct accepted decision shape or adapter. |
| Add a dedicated `/autonomy/decision/latest` route backed by the same onboard publisher | Medium-high; recommended | It keeps observation compatibility, gives D1 a clearly named decision boundary, and can return the exact `vehicle_decision_stream_frame_v0` only after source identity and host-control fields are present. The route belongs in the vendor patch; `manage.py` only wires the source publisher. | It crosses the generated-vendor boundary and adds one route plus a remote CLI fetcher. The response must be produced atomically from the same cycle rather than reconstructing fields from unrelated status reads. |
| Have the Automa CLI reconstruct a decision frame from existing observation/status routes | Medium as a temporary adapter, low as the accepted owner | It avoids changing the Pi HTTP route and can reuse existing physical fetch code. | The CLI would be assembling the claim from multiple mutable reads; it cannot authoritatively mint Pi run/generation identity or prove the reads refer to one cycle. It is acceptable only if the Pi payload already contains a complete signed/correlated source record, which it currently does not. |
| Add a separate bridge process between Pi and Automa | Medium as transport, low as authority | It could isolate protocol conversion and keep vendor patch changes small. | It adds a second lifecycle and identity boundary. A bridge-generated token would describe the bridge, not the process that ran the cycle, unless it merely forwards Pi-owned identity. |

The current recommendation is a dedicated decision route in the existing
patched Donkey server, backed by the same `AutonomyPilotPart` snapshot and
Pi-owned identity. Keep `/autonomy/observation/latest` unchanged as supporting
perception/frame evidence. The CLI should fetch and validate the decision route,
not synthesize authoritative identity from separate HTTP responses. The source
of truth for that route is the repository's vendor patch, even though the
runtime handler executes from the generated DonkeyCar checkout.

### Host mode, pilot input, and final output

The shadow authority result is not a substitute for host control evidence. The
current Donkey patch exposes the controller's current drive mode, while the
vehicle loop separately computes pilot output and then applies `DriveMode` to
choose the final pre-drivetrain steering/throttle values. The existing
observation publisher reports the engine's control record, not that complete
host-control path.

| Option | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Read `/autonomy/status` and the existing observation publication | High for diagnostics | Both routes already exist and can prove the current controller mode, engine status, and observation freshness with little change. | They do not provide a cycle-correlated pilot value and final `DriveMode` output; manager `last_control` must not be relabeled as applied host output. |
| Use `AutonomyPilotPart` as the host-control source | High for pilot output | It receives mode/user inputs, computes the engine control, and deliberately emits zero pilot output in `user` mode. | Its public `control` is the engine result, not the post-gate pilot value, and skipped ticks may return held values. It cannot prove the final host output. |
| Read Donkey vehicle memory after `DriveMode` | Medium | The final `steering`/`throttle` signals are produced by the existing `DriveMode` boundary, so a value sampled there has the right semantic owner. | Vehicle memory is mutable last-value storage without an atomic frame identity or timestamp; an HTTP read can race the loop and cannot distinguish a current value from a held value. |
| Add a Donkey-owned host-control snapshot at the `DriveMode` boundary | High; recommended | One record can correlate runtime identity, frame identity, `mode`, pilot steering/throttle, and final pre-drivetrain output. It directly supports the honest claim `user + pilot=0 + final_host_output=0`. | Requires a small reporter/wiring change. It must be named `final_host_output` or `drivetrain_command`, not actuator-applied control, unless the hardware path reports application separately. |
| Infer host output from `ShadowAuthorityResult` | None; reject | It would be cheap and would reuse an existing decision payload. | It violates the authority boundary: proposed/authorized shadow values are not the Donkey host's selected output and can disagree while the car remains safely in `user` mode. |

The current recommendation is a Donkey-owned, cycle-correlated host-control
snapshot produced at or immediately after `DriveMode`, carried alongside the
decision publication. `/autonomy/status` may remain a convenient diagnostic
route, but it is not sufficient as the D1 evidence contract. The publication
must distinguish engine proposal, pilot output, final host output, and actual
actuator application; D1 only claims the first three unless the actuator path
adds its own observation.

### First operator surface: CLI or live page

The existing `stream decision` surface is a strong presentation and acceptance
candidate, but its current producer/consumer pair is local: it reads
`latest_decision.json`, the local activation, and local automation state. The
current physical path instead polls `/autonomy/observation/latest`. Therefore
the command is not PiRacer-ready unchanged, even though it is the best place
to expose the first live check after a physical provider is added.

| Option | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Reuse `vehicles stream decision` unchanged | High for Chase, low for PiRacer | It already has concise output, `--once`, `--json`, proposal/source/selection/authority formatting, and strict acceptance. | It consumes local `latest_decision.json` and local worker state; the physical Pi does not currently publish that file or identity shape. |
| Add a PiRacer provider to `vehicles stream decision` | High; recommended | It preserves one decision-facing CLI surface and one accepted schema while adapting the existing physical publication. `--once` gives a bounded first live check; the physical observation endpoint can remain supporting frame/mode evidence. | The provider must emit/accept the exact `vehicle_decision_stream_frame_v0` contract; current observation JSON cannot simply be relabeled as a decision frame. |
| Add a separate physical decision CLI command | Medium | It can make the HTTP transport and physical failure cases explicit without changing the local Chase consumer. | It creates a second decision presentation and risks duplicate semantics/schema drift; it is justified only if provider differences cannot be isolated behind the existing command. |
| Build a live decision page now | Low for the first check; defer | A page could eventually make live state easier to inspect. | The current decision HTML is an offline exact-frame artifact, not a live publisher. Adding it now would combine transport, liveness, and visual-correlation questions and reopen the separate D2 UI scope. |
| Defer all operator-facing decision output | None for D1; reject | It avoids CLI work. | It would leave no direct way to verify selected proposal versus idle host authority, contrary to the M006 operator workflow. |

The current recommendation is to add a physical provider behind the existing
`vehicles stream decision` command, use `--once --json` for the first bounded
check, retain `/autonomy/observation/latest` as supporting evidence, and defer
the live page. This changes transport/provider ownership, not the decision
engine or the operator's accepted schema.

### Freshness and producer liveness

The existing Pi observation publisher already computes publication age using a
default 0.5-second cycle cadence and a minimum 1,000 ms stale threshold. The
local decision validator separately checks a 30-second configurable age
ceiling, activation generation, local `run_id`, local PID state, and local PID
liveness. Those local PID checks cannot prove that a remote Pi process is
alive.

| Option | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Timestamp age only | Strong existing baseline | A stopped or hung cycle becomes stale after more than 1,000 ms without adding a new heartbeat protocol. | The current physical route can still return a stale payload with `ok=true`; clock skew, future timestamps, or a frozen process need explicit handling. |
| Producer heartbeat/PID | Medium | A producer identity and heartbeat can distinguish a runtime generation and support a stopped-process check. | A remote CLI cannot use local `os.kill` to verify a Pi PID; PID alone proves existence, not cycle progress, and PID reuse is possible. |
| Sequence advancement | Medium-high | Existing frame IDs, frame indices, and counters can show whether the latest cycle advances rather than merely being replayed. | Counters reset on process restart unless bound to a run/generation, and sequence advancement still needs a timeout for a slow or hung producer. |
| systemd/MainPID identity | Medium-high as binding evidence | The service is supervised with `Type=simple` and `Restart=always`, so service restart can define a new runtime generation. | Service existence does not prove the camera/decision loop advances, and the current publication does not expose remote `MainPID` or unit generation. |
| Combined predicate: exact frame + generation + liveness + age + progress | High; recommended | It covers stale, stopped, hung, restarted, mismatched, and incomplete producers while reusing the existing strict frame/cycle checks. | It needs one atomic Pi-owned publication record and can report unavailable during restart or transport gaps; each field must be produced by the same generation. |

The current recommendation is a 1,000 ms starting freshness ceiling, bounded by
the existing physical cadence, plus a Pi-owned generation identity and atomic
progress value. For a continuous stream, the reader should require sequence
advancement within that window. Missing, future-dated, mismatched,
non-advancing, or stale data is `unavailable`; systemd/MainPID is binding
evidence, not the sole freshness test. The existing validator can be reused
for schema, cycle alignment, activation, and timestamp checks, but D1 needs a
remote-aware liveness/identity adapter rather than applying local `os.kill` to
a Pi PID.

### Contract compatibility constraint

The accepted M006 decision frame is a closed export: the current validator
requires the exact `vehicle_decision_stream_frame_v0` top-level keys and the
exact nested shadow-cycle export. D1 must not add arbitrary top-level fields or
silently relabel the local `worker_pid`/`state.json` contract as physical
liveness. The implementation choice must therefore be one of the following:

| Shape | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Reuse the exact frame envelope and place host evidence in an existing, semantically appropriate nested field | Medium-high | Preserves the accepted schema and strict validator while keeping cycle/source/authority alignment. | Requires confirming that the existing `host_application`/drive-mode structures can carry mode, pilot, and final host output without changing their meaning. |
| Publish the exact decision frame plus a separately correlated host-control/liveness record | High; recommended fallback | Keeps the decision frame lossless and lets the host-control record be owned by the `DriveMode` boundary with its own source semantics. Both records can share Pi run, activation, generation, and frame identity. | The CLI must require and correlate two records atomically enough to fail closed; a decision frame without its host record is incomplete for the zero-control claim. |
| Revise the M006 frame schema/validator to add physical fields | Medium, but out of scope for this draft | Gives physical producer fields first-class names and can unify local/physical acceptance. | It changes the accepted contract and would require an amendment/re-review rather than being a fill-in task inside this proposal. |
| Add extra fields and let the current validator ignore them | None; reject | Minimal code change. | The current validator explicitly requires an exact key set, so this would fail acceptance and create an ambiguous contract. |

The draft assumes the second shape unless review shows the existing nested
authority/application envelope is sufficient. In either shape, the Pi is the
producer of identity and evidence; the CLI is only the remote-aware consumer.

Baseline references used for these options are the Pi assembly and activation
load in `deploy/targets/donkeycar/app/manage.py:451-558`, the controller and
HTTP route boundary in
`deploy/targets/donkeycar/patches/waveshare-donkeycar-local.patch:250-390`, the
onboard snapshot in `implementations/runtime/donkeycar/donkey_part.py:127-465`,
the local frame contract and acceptance predicate in
`cli/automa_cli/decision.py:90-115` and `935-1065`, the local worker lifecycle
in `cli/automa_cli/automation.py:268-340` and `492-516`, and the physical fetch
path in `cli/automa_cli/physical_observation.py:111-307`.

## Proposed boundary

```text
PiRacer camera and memory
        ↓
Donkey `manage.py` + AutonomyPilotPart + loaded shadow adapter
        ↓
patched Donkey host decision publication route
        ↓
Automa physical provider + existing decision frame validator/CLI
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
| `implementations/runtime/donkeycar/donkey_part.py` | Add source identity, host-control snapshot fields, and the decision publication payload; cycle execution already exists | 40–110 |
| `deploy/targets/donkeycar/patches/waveshare-donkeycar-local.patch` | Add or adapt the Donkey HTTP decision route and expose the host-control snapshot | 50–140 |
| `deploy/targets/donkeycar/app/manage.py` | Wire the source publisher/host snapshot only if the route needs an assembly change | 0–30 |
| `cli/automa_cli/physical_observation.py` or a focused physical-decision adapter | Fetch, decode, correlate, and report the physical decision publication | 40–100 |
| `cli/automa_cli/decision.py` | Reuse or factor current frame acceptance for a remote physical producer | 20–80 |
| `cli/automa_cli/app.py` | Route the existing decision-stream command to the physical provider if needed | 0–30 |
| `tests/cli/` and `tests/integration/` | Fixtures for identity, stop/stale/mismatch, source correlation, and user-mode zero output | 80–160 |

Likely in-repository base case: **150–350 production LOC plus 80–160 test
LOC**, or roughly **230–510 LOC total**. The per-file maxima sum higher only
if every optional path is required; that worst combination is approximately
**650 LOC**. The estimate still excludes any new decision engine, decision
policy, page, or #203 code.

The baseline check found a separately owned *runtime boundary*, but not a
separately sourced repository: `manage.py` and the vendor patch are
source-controlled here, while the patched handler executes from the generated
DonkeyCar checkout during deployment. The remaining question is which of the
options above should be accepted as the one D1 boundary and how the patch is
kept aligned with the deployed vendor checkout.

## Expected handoff

After feedback, a higher-reasoning review should confirm the endpoint owner,
decide whether an in-repository adapter is enough, and finalize the exact
public interface, rejection cases, and live validation procedure. Only then
should this become a formal M006 proposal with a separate implementation
review unit.
