# Draft D1 — Live shadow-cycle publication

## Status

Feedback draft only. This document is not an accepted proposal, does not
change the M006 plan, and does not authorize implementation.

## The question

Can D1 expose and accept one current decision result from an activated vehicle
decision stage, with genuine source identity and bounded freshness, so Automa
can inspect it and reject it safely when the producer stops or the publication
becomes stale? PiRacer is the first live fixture, not a special decision
architecture.

The desired operator experience is simple:

1. Put the vehicle in `user` mode with pilot output at zero.
2. Start the vehicle runtime; its activated decision stage invokes the shared
   decision cycle on camera input.
3. Use the same decision-stage CLI surface used for other providers to read
   one current result with enough correlated vehicle, runtime, activation,
   frame, source, proposal, and authority identity to identify what ran.
4. Stop the producer or wait past its freshness limit and see **unavailable**,
   not the last cycle presented as current.

The live cycle should preserve the same decision meaning that #203 exercises
offline. A live cycle may contain a proposed steering intent; it must not apply
that intent to the vehicle. The proposal fixes that observable boundary, not a
permanent implementation name or a frozen internal decision algorithm.

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
- Perception and memory already treat stage execution as a framework-owned
  boundary around replaceable implementations. Perception activation loads a
  mapper and configuration behind its activation surface; memory exposes a
  small update/reset/snapshot protocol while the framework owns timing,
  status, and failure isolation. Decision should follow that same pattern
  rather than making `shadow-proposals` or PiRacer a permanent architectural
  owner.

The likely gap is therefore a physical publication/transport adapter and its
read-side acceptance—not a second decision loop or a new decision policy.

## Open questions: options and baseline

The following is a feasibility baseline for the open design questions. It is
not an implementation commitment. The options are grounded in the current
physical-vehicle/Donkey source, the generated-vendor patch, and the M006 local
decision surface. A later accepted proposal should retain only the selected
boundary and the smallest rejection cases.

### Highest-leverage question: what is D1's logical boundary?

The operator outcome is already clear enough to serve as a premise: one
ordinary decision-stage check should show a current result and fail closed when
that result is no longer trustworthy. The highest-leverage open question is
therefore the logical ownership of that stage: is decision a peer stage with
provider adapters, or is D1 a Pi-specific live surface? This choice informs
more of the other questions than any individual route or field choice.

Perception and memory already provide the useful mental directory: stage-level
`update`, `info`, and `stream` commands, with provider-specific execution
hidden behind the stage boundary. For example, the memory stream keeps one
command while choosing a PiCar or Chase implementation internally
(`cli/automa_cli/memory.py:1368-1410`), and perception uses the same staged
activation/inspection pattern (`cli/automa_cli/app.py:1429-1485`). The
framework-owned perception activation and memory protocol are the relevant
precedents (`autonomy/perception/activation.py:15-150`,
`autonomy/decision/activation.py:43-84,215-380`, and
`autonomy/decision/plugin.py:17-34`). Decision generation should be a peer
stage at that same logical level:

```text
vehicles update/info/stream decision
                ↓
       narrow decision-provider seam
          ├─ Chase/local provider
          └─ Pi/Donkey provider
                ↓
       common decision publication
```

The Pi branch in that diagram is an adapter choice, not a Pi-specific operator
workflow. The operator should not need a different command family or a second
decision concept just because the source is physical. Likewise,
`shadow-proposals` and its proposal plugins are current mutable decision
implementations, not permanent architecture. The existing pipeline callables
in `autonomy/decision/cycle.py:49-85` are a plausible thin implementation
boundary, not a mandate to introduce a framework. The stable seam should cover
only the stage input/source identity, one decision result/publication, health
and freshness, and the distinction between proposed and host-applied output.
Plugin choice, policy, internal cycle wiring, and serialization details remain
replaceable behind that seam. If the seam grows substantially larger than the
current implementation, the implementation review should keep the interface
small and defer generalization rather than building an abstraction whose code
outweighs the decision implementation.

| Option | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Make decision a peer stage with one provider interface and provider-specific adapters | High; recommended | Matches perception/memory usage, keeps `vehicles update/info/stream decision` stable, and lets the decision generator change without rewriting Pi, Chase, or operator code. | Requires choosing a deliberately narrow interface for source identity, result, health, and authority correlation. |
| Add Pi-specific decision commands and controls | Medium technically, low as the product shape | It can expose the physical route quickly and may be easy to prototype. | Makes Pi a special case, duplicates operator concepts, and couples transport details to the user workflow. |
| Put all future decision behavior into one universal abstraction now | Medium technically, low for this proposal | It promises one framework for every future provider and engine. | It spends review attention on hypothetical providers and risks more abstraction than implementation. |
| Leave the current local worker wiring as the permanent boundary | High for Chase, low for the milestone outcome | It already works for the simulator and has useful validation. | It does not cover Pi and would make the physical path a second architecture rather than another provider. |

The working recommendation is the first option. This proposal accepts the
stage boundary and operator-visible behavior; it does not freeze the internal
decision-generation implementation or require a perfect long-term abstraction.

#### Downstream hit analysis

The following is a qualitative dependency check, not a claim that every
downstream choice is mathematically determined. A check mark means that the
boundary option directly supports the recommended selection; `~` means it
could support it only after adding another architectural decision; `×` means
it conflicts with the recommended D1 shape. This is the useful comparison
across options:

| Downstream selection | A. Common stage/provider seam | B. Pi-specific surface | C. Universal abstraction now | D. Chase worker as boundary |
| --- | :---: | :---: | :---: | :---: |
| One `vehicles update/info/stream decision` surface | ✓ | × | ✓ | × |
| Vehicle runtime owns source identity | ✓ | ~ | ~ | × |
| Existing physical runtime boundary, transport behind adapter | ✓ | ~ | ~ | × |
| Provider-neutral result acceptance | ✓ | × | ✓ | × |
| Small bounded freshness predicate | ✓ | ~ | ~ | × |
| Mutable decision implementation with a small interface | ✓ | × | × | × |
| No Pi-only workflow, bridge, or future-proof framework | ✓ | × | × | ✓ |
| **Qualitative recommendation hits** | **7** | **0** | **2** | **1** |

The point is not that option A makes every detail automatic. It establishes
the ownership and user-facing shape that make the remaining choices local:
the runtime produces evidence, the provider adapts it, the common validator
accepts its meaning, and the CLI presents it. Option A therefore has the
highest leverage and should be selected first. The later sections are
dependent design details or explicit deferrals, not independent alternatives
that all remain open after this selection.

### Downstream question: who owns source identity?

Once decision is a peer stage, this question is about the producer boundary,
not about inventing a Pi-specific decision owner. The selected answer should
let each vehicle provider attach truthful source identity to the result it
actually produced.

The existing physical path has a publisher, but it is an observation publisher, not
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
| Have the vehicle runtime wiring attach source identity to each cycle | High; recommended | `manage.py` starts the Donkey process, loads `runtime/decision/active.json`, constructs `AutonomyCycleHost`, and wires `AutonomyPilotPart`. The part owns the actual frame/cycle pairing. A process-start/run token plus the successfully loaded activation token can therefore be attached at the source without making `manage.py` the decision-stage owner. | Neither component currently exposes an explicit run token or generation token. This is a small source-boundary addition, not an already-existing contract. |
| Make `AutonomyPilotPart`/`AutonomyCycleHost` independently authoritative | Medium | It is the narrowest owner of the cycle result, frame identity, and retained snapshot, so it can produce a coherent publication without asking another process to reconstruct the cycle. | It is an in-process object, not a process lifecycle boundary; its current frame counter resets on restart and cannot by itself prove producer liveness or distinguish activation generations. |
| Reuse the local Automa automation worker's `run_id`, PID, and `state.json` | Low for a physical vehicle | The M006 validator already checks these fields and rejects dead, stopped, mismatched, or stale local workers. | That worker is the Chase-simulator producer. Reusing its identity for a physical vehicle would make the observer or CLI impersonate the physical producer and would conflate two runtimes. |
| Create a new bridge process that mints its own run/generation identity | Medium as transport, low as D1 boundary; defer | A bridge could adapt the existing HTTP payload to the M006 frame contract without changing Donkey vendor code. | A bridge-created token identifies the bridge session, not the process that ran the cycle; it adds another liveness boundary and risks accepting a disconnected or relabeled cycle. |

The current recommendation is therefore: the loaded activation selects the
decision implementation, the vehicle runtime wiring attaches source identity
to each completed cycle, and the HTTP publisher plus Automa CLI transport,
validate, and reject mismatches. The local Automa worker remains authoritative
only for Chase. `manage.py drive` is a runtime assembly owner, not a new
decision-stage API, and PiRacer is simply the first physical provider. This
requires adding the missing source identity fields; no existing component
currently supplies the entire tuple.

### Downstream question: how does the provider publish?

This follows source ownership. The transport only needs to carry one
correlated result from that owner; it should not become a second logical stage
or force the CLI to reconstruct evidence from unrelated reads.

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
| Add a decision representation/route backed by the same onboard publisher | Medium-high | It can keep observation compatibility while giving D1 a clearly named decision boundary. A dedicated `/autonomy/decision/latest` route is one reasonable transport choice, but the route is behind the provider adapter rather than the logical operator contract. | It crosses the generated-vendor boundary and may add one route plus a remote CLI fetcher. The response must be produced atomically from the same cycle rather than reconstructing fields from unrelated status reads. |
| Have the Automa CLI reconstruct a decision frame from existing observation/status routes | Medium as a temporary adapter, low as the accepted owner | It avoids changing the Pi HTTP route and can reuse existing physical fetch code. | The CLI would be assembling the claim from multiple mutable reads; it cannot authoritatively mint Pi run/generation identity or prove the reads refer to one cycle. It is acceptable only if the Pi payload already contains a complete signed/correlated source record, which it currently does not. |
| Add a separate bridge process between Pi and Automa | Medium as transport, low as D1 boundary; defer | It could isolate protocol conversion and keep vendor patch changes small. | It adds a second lifecycle and identity boundary. A bridge-generated token would describe the bridge, not the process that ran the cycle, unless it merely forwards Pi-owned identity. |

The current recommendation is to keep the decision publication in the existing
patched Donkey server and back it with the same `AutonomyPilotPart` snapshot
and vehicle-owned identity. Whether that is an extension of the observation
route or a dedicated decision route is a transport detail for implementation
review. Keep the existing observation consumer compatible, and do not have the
CLI synthesize authoritative identity from unrelated HTTP responses. The
logical contract is the common decision provider and CLI; the repository's
vendor patch is only the source-controlled transport boundary, even though its
handler executes from the generated DonkeyCar checkout.

### Mostly independent scope question: what host evidence is needed?

This question is mostly independent of route shape, but it is still bounded by
the operator claim. If D1 only proves shadow generation while stationary, it
does not need to grow into a full actuator-observation contract.

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
| Add a Donkey-owned host-control snapshot at the `DriveMode` boundary | Medium; defer unless the claim expands | One record can correlate runtime identity, frame identity, `mode`, pilot steering/throttle, and final pre-drivetrain output. It directly supports the stronger claim `user + pilot=0 + final_host_output=0`. | Requires a reporter/wiring change and is not needed for the first bounded decision-generation check. It must be named `final_host_output` or `drivetrain_command`, not actuator-applied control, unless the hardware path reports application separately. |
| Infer host output from `ShadowAuthorityResult` | None; reject | It would be cheap and would reuse an existing decision payload. | It violates the authority boundary: proposed/authorized shadow values are not the Donkey host's selected output and can disagree while the car remains safely in `user` mode. |

The first D1 check should claim only what it needs: decision generation ran,
the host is in `user` mode, and the pilot output is zero. `AutonomyPilotPart`
already enforces the zero pilot output in `user` mode. A full cycle-correlated
`DriveMode`/actuator snapshot is a useful later extension, but is not required
to make D1 tractable and should not be smuggled into the contract. Whatever
record is exposed must continue to distinguish a proposed decision from host
output; D1 does not claim actuator application.

### Derived operator surface: CLI or live page

This is downstream of the stage/provider and result contract. The existing
stage-level CLI is therefore the default, while a page remains a separate
presentation question.

The existing `stream decision` surface is a strong presentation and acceptance
candidate, but its current producer/consumer pair is local: it reads
`latest_decision.json`, the local activation, and local automation state. The
current physical path instead polls `/autonomy/observation/latest`. Therefore
the command is not physical-provider-ready unchanged, even though it is the
best place to expose the first live check after a provider is added.

| Option | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Reuse `vehicles stream decision` unchanged | High for Chase, low for a physical provider | It already has concise output, `--once`, `--json`, proposal/source/selection/authority formatting, and strict acceptance. | It consumes local `latest_decision.json` and local worker state; the physical vehicle does not currently publish that file or identity shape. |
| Add a vehicle provider to `vehicles stream decision` | High; recommended | It preserves one decision-facing CLI surface and one stage-level concept while adapting the physical publication. `--once` gives a bounded first live check; the physical observation endpoint can remain supporting frame/mode evidence. | The provider must preserve the accepted decision-result meaning; current observation JSON cannot simply be relabeled as a decision frame, and its physical representation need not impersonate local worker state. |
| Add a separate physical decision CLI command | Medium | It can make the HTTP transport and physical failure cases explicit without changing the local Chase consumer. | It creates a second decision presentation and risks duplicate semantics/schema drift; it is justified only if provider differences cannot be isolated behind the existing command. |
| Build a live decision page now | Low for the first check; defer | A page could eventually make live state easier to inspect. | The current decision HTML is an offline exact-frame artifact, not a live publisher. Adding it now would combine transport, liveness, and visual-correlation questions and reopen the separate D2 UI scope. |
| Defer all operator-facing decision output | None for D1; reject | It avoids CLI work. | It would leave no direct way to verify selected proposal versus idle host authority, contrary to the M006 operator workflow. |

The current recommendation is to add a vehicle provider behind the existing
stage-level command, rather than a PiRacer-only command. The first bounded
operator check is intentionally ordinary:

```sh
./cli/automa vehicles update decision --id <vehicle_id> --engine shadow-proposals
./cli/automa vehicles stream decision --id <vehicle_id> --once
```

`--json` can provide machine-readable evidence. Keep the observation route as
supporting evidence and defer the live page. This changes transport/provider
ownership, not the decision engine or the operator's decision concept.

### Derived acceptance question: what counts as current?

Freshness is downstream of publication ownership and the bounded operator
check. It should specify the minimum fail-closed behavior without turning
continuous liveness into a prerequisite for the first result.

The existing Pi observation publisher already computes publication age using a
default 0.5-second cycle cadence and a minimum 1,000 ms stale threshold. That
is a useful measured baseline, not a D1 number that should be frozen before
the provider is implemented. The local decision validator separately checks a
30-second configurable age ceiling, activation generation, local `run_id`,
local PID state, and local PID liveness. Those local PID checks cannot prove
that a remote process is alive.

| Option | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Timestamp age only | Strong existing baseline | A stopped or hung cycle becomes stale after more than 1,000 ms without adding a new heartbeat protocol. | The current physical route can still return a stale payload with `ok=true`; clock skew, future timestamps, or a frozen process need explicit handling. |
| Producer heartbeat/PID | Medium | A producer identity and heartbeat can distinguish a runtime generation and support a stopped-process check. | A remote CLI cannot use local `os.kill` to verify a Pi PID; PID alone proves existence, not cycle progress, and PID reuse is possible. |
| Sequence advancement | Medium-high | Existing frame IDs, frame indices, and counters can show whether the latest cycle advances rather than merely being replayed. | Counters reset on process restart unless bound to a run/generation, and sequence advancement still needs a timeout for a slow or hung producer. |
| systemd/MainPID identity | Medium-high as binding evidence | The service is supervised with `Type=simple` and `Restart=always`, so service restart can define a new runtime generation. | Service existence does not prove the camera/decision loop advances, and the current publication does not expose remote `MainPID` or unit generation. |
| Correlated current publication: source/runtime identity + activation match + bounded timestamp | High; recommended for D1 | It covers the first meaningful stopped/stale check with one provider-owned publication and avoids applying local-worker process checks to a remote producer. | Continuous heartbeat, sequence progress, and systemd identity are not proved by this minimum and may be separate future evidence. |

The current recommendation is a provider-owned source/runtime identity,
activation match, and bounded timestamp freshness. The 1,000 ms value can be a
starting configuration derived from the observed physical cadence; the
implementation review may choose a better cadence-relative threshold without
changing the operator contract. Missing, future-dated, mismatched, or stale
data is `unavailable`. Continuous heartbeat, sequence-advancement, and
systemd/MainPID proof are deferred unless the bounded check shows they are
needed. The existing validator can be reused for result semantics, cycle
alignment, activation, and timestamp checks, but local `os.kill`/`state.json`
checks stay in the local worker provider and are not fabricated on the
physical path.

### Cross-cutting compatibility question: what must remain stable?

This is the constraint that cuts across the other questions: preserve the
meaning the operator and existing decision surfaces consume, while allowing
provider-specific evidence and mutable implementation details behind it.

The accepted M006 decision frame is a compatibility baseline for the decision
meaning, not permission for a physical provider to impersonate the local worker
or for this proposal to freeze every field placement. The current local
validator requires the exact `vehicle_decision_stream_frame_v0` top-level keys
and nested shadow-cycle export. D1 should reuse that shape where it is truthful,
but should factor provider-neutral result acceptance from local-worker
`worker_pid`/`state.json` liveness. If the physical source cannot fit the
existing shape honestly, a small versioned amendment or adapter can return to
proposal review; it should not be invented silently during implementation.

| Shape | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Reuse the existing frame/result meaning through a provider-neutral validator | High; recommended | Preserves cycle/source/authority alignment and keeps the operator's decision concept stable while allowing local and physical providers to verify different liveness evidence. | Requires a deliberately small separation between shared result semantics and provider-specific source checks. |
| Publish the decision result plus a separately correlated host-control record | Medium; defer unless the zero-control claim needs it | Keeps decision output lossless and lets a host-control boundary own its own source semantics. | It adds correlation and another record before D1 needs a full final-output claim. |
| Revise the M006 frame schema/validator to add physical fields | Medium, but only through amendment | Gives physical producer fields first-class names and can unify local/physical acceptance. | It changes the accepted contract and requires amendment/re-review rather than being a fill-in task inside this proposal. |
| Add extra fields and let the current validator ignore them | None; reject | Minimal code change. | The current validator explicitly requires an exact key set, so this would fail acceptance and create an ambiguous contract. |

The draft therefore assumes a small provider-neutral result boundary, with
physical source identity and freshness verified by the physical adapter and
local PID/state checks retained by the Chase adapter. A separately correlated
host-control record is not part of the first D1 claim unless implementation
evidence shows it is necessary. In every shape, the vehicle runtime produces
identity and evidence; the CLI is only the provider-aware consumer.

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
vehicle adapter → sensor/frame input → activated decision stage
        ↓
stable decision result + source/runtime freshness
        ↓
provider publication adapter (file, HTTP, or equivalent)
        ↓
common `vehicles update/info/stream decision` CLI
```

D1 owns the narrow missing provider/publication seam: expose one decision
result with genuine source/runtime identity and bounded freshness, then accept
it through the common decision surface. The first check also needs enough host
context to show `user` mode and zero pilot output. The existing shadow engine,
activated cycle execution, and provider-specific liveness checks remain owners
of their current responsibilities. `manage.py`, a Donkey route, or a new
bridge process is an implementation detail behind the physical provider, not a
new logical stage.

The first useful result is one honest live cycle and one honest stopped/stale
result. Left/right decision behavior remains the behavior established by #203;
this proposal does not retune perception or claim vehicle avoidance.

## In scope

- Confirm whether the existing physical publication can carry the accepted full
  decision result; if not, add the smallest provider adapter or transport
  needed.
- Source/runtime, activation, frame, and bounded freshness identity sufficient
  to say what decision stage ran and to reject a mismatched result.
- The current observation/memory and resulting plan, proposal, and authority
  data needed to correlate one cycle.
- Host mode and pilot-output observations sufficient to distinguish proposed
  intent from the stationary host behavior. A full final-output/actuator
  snapshot is not required by the first claim.
- A narrow provider interface behind the existing decision-stage command
  family; no general-purpose framework is required.
- Explicit unavailable behavior for a stopped, stale, mismatched, or
  incomplete publication.
- A short operator procedure for checking the path while the vehicle remains
  stationary.

## Out of scope

- Vehicle movement or applying proposed commands.
- Reimplementing the decision cycle, shadow engine, perception, memory,
  tracking, prediction, or steering policy.
- The offline toggle/page implemented by #203.
- A live decision URL, retained-image overlay, or redesign of the operator
  page; those are separate questions.
- PiRacer-only commands, controls, or a second operator-facing decision
  concept.
- Chase evaluator state or privileged simulator inputs.
- A new unreviewed decision schema parallel to
  `vehicle_decision_stream_frame_v0`; a versioned amendment remains possible
  if the physical source cannot truthfully use the existing representation.
- Continuous heartbeat, sequence-advancement, systemd/MainPID, or full
  actuator-application proof unless the bounded D1 check demonstrates that it
  is necessary.
- A universal future-proof abstraction or a second bridge lifecycle.
- Automation-launcher changes unless inspection confirms the existing vehicle
  runtime cannot be exercised without them.

## Independence from #203

There is no implementation dependency on #203. #203 loads a saved decision
sequence and runs the real shadow engine for two offline scenarios. D1 should
produce the underlying live cycle contract that can later be replayed or
inspected by the same decision surfaces; it should not import or turn the
offline inspector into a live producer.

The branches should remain separate. D1 should reuse the underlying decision
meaning and existing shadow engine where appropriate, not depend on #203's
`automa_decision_inspection_v1` wrapper or edit its inspector page. A later
live-page integration would be a separate review question. If physical
provenance requires a small versioned contract amendment, that is a proposal
review decision rather than an implementation surprise.

## Approximate implementation impact

These are rough added/changed-line estimates for sizing discussion, not a
commitment. They exclude proof-of-work reports, generated HTML, and the
existing contents of the files. They reflect the existing cycle and local
decision publisher described above.

| Candidate file | Likely work | Estimate |
| --- | --- | ---: |
| `implementations/runtime/donkeycar/donkey_part.py` | Attach source identity and the smallest decision publication payload; cycle execution already exists | 25–70 |
| `deploy/targets/donkeycar/patches/waveshare-donkeycar-local.patch` and, if needed, `manage.py` | Expose the provider publication through the existing Donkey boundary | 20–70 |
| `cli/automa_cli/physical_observation.py` or a focused physical-decision adapter | Fetch, decode, correlate, and report the physical decision result | 30–80 |
| `cli/automa_cli/decision.py` | Separate shared result acceptance from local-worker liveness where needed | 15–50 |
| `cli/automa_cli/app.py` | Route the existing decision-stage command to the provider | 0–20 |
| `tests/cli/` and `tests/integration/` | Fixtures for identity, stop/stale/mismatch, source correlation, and user-mode zero output | 50–120 |

Likely in-repository base case: **90–220 production LOC plus 50–120 test
LOC**, or roughly **140–340 LOC total**. This smaller estimate assumes one
provider adapter, no bridge process, no full `DriveMode` snapshot, and no new
framework. The estimate still excludes any new decision engine, decision
policy, page, or #203 code; implementation review can revise it after the seam
is selected.

The baseline check found a separately owned *runtime boundary*, but not a
separately sourced repository: `manage.py` and the vendor patch are
source-controlled here, while the patched handler executes from the generated
DonkeyCar checkout during deployment. The remaining question is which of the
options above should be accepted as the one D1 semantic boundary and how the
selected provider transport is kept aligned with the deployed vendor checkout.

## Expected handoff

After feedback, a higher-reasoning review should first accept or reject the
highest-leverage stage/provider boundary using the hit analysis above. It can
then resolve only the downstream choices that remain consequential: source
authority, provider transport, result acceptance, and the bounded procedure.
It need not settle a universal abstraction or freeze the mutable decision
engine. Only then should this become a formal M006 proposal with a separate
implementation review unit.
