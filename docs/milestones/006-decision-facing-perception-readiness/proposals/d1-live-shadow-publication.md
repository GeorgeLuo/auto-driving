# Draft D1 — Live shadow-cycle publication

## Status

Feedback draft only. This document is not an accepted proposal, does not
change the M006 plan, and does not authorize implementation.

M006's current cross-environment shadow-proposal frontier is already
represented by accepted proposal #201. This document is a feedback-only
candidate for a bounded live publication/inspection slice adjacent to that
frontier; it does not replace #201, satisfy its exit criteria, or silently
change the milestone scope. Any overlap or amendment must be resolved through
the normal M006 proposal review.

## The question

Can D1 expose and accept one current decision result from an activated vehicle
decision stage, with genuine source identity and bounded freshness, so Automa
can inspect it and reject it safely when the publication is missing, mismatched,
or becomes stale after the producer stops? PiRacer is the first live fixture,
not a special decision architecture.

The desired operator experience is simple:

1. Start the vehicle runtime with its decision stage activated; the stage
   invokes the shared decision cycle on camera input.
2. Use the same decision-stage CLI surface used for other providers to read
   one correlated result with vehicle, runtime, activation, frame, source,
   proposal, and explicit shadow-authority meaning sufficient to identify what
   ran.
3. See the proposed decision alongside `authority_mode=shadow_only`, an idle
   `authorized_output`, and `proposed_applied=false`; a proposed intent is not
   presented as a vehicle command.
4. If the publication is missing, mismatched, or older than its freshness
   bound, see **unavailable**, not the last cycle presented as current. For a
   stopped producer, stop it and wait beyond that bound before checking.

The physical fixture remains stationary in action-idle user mode as a run
precondition. That is an operating constraint, not a D1 claim that the
publication proves final host or actuator behavior.

The live cycle should preserve the same decision meaning that #203 exercises
offline. A live cycle may contain a proposed steering intent; it must not apply
that intent to the vehicle. The proposal fixes that observable boundary, not a
permanent implementation name or a frozen internal decision algorithm.
Here, “same decision meaning” refers to the underlying accepted decision
result semantics, not #203's `automa_decision_inspection_v1` wrapper and not a
requirement to reproduce its two offline scenarios live.

## Review kind

Live or external evidence

## What already exists

Source inspection changes the likely shape of this work:

- `implementations/runtime/donkeycar/donkey_part.py` already calls the shared
  `AutonomyCycleHost` and retains a generic serialized cycle/observation
  snapshot in its latest onboard state. It also reports frame timing, mode,
  and engine control data; it does not yet retain the complete typed shadow
  decision result for publication.
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

The likely gap is therefore a narrow source-side result-provider capability,
physical publication/transport, and read-side acceptance—not a second
decision loop or a new decision policy.

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
only the stage input/source identity, one decision result/publication, bounded
availability/freshness, and the distinction between proposed intent and
shadow-only authorized output. A host-application report is a separate
optional observation, not part of this D1 claim.
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

The working recommendation is the first option. This draft recommends the
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
| No Pi-only workflow, bridge, or future-proof framework | ✓ | × | × | × |
| **Qualitative recommendation hits** | **7** | **0** | **2** | **0** |

The point is not that option A makes every detail automatic. It establishes
the ownership and user-facing shape that make the remaining choices local:
the vehicle runtime produces and attaches source evidence, the provider
normalizes it and checks provider-specific freshness, the common validator
accepts shared result meaning, and the CLI presents it. Option A therefore has the
highest leverage and should be selected first. The later sections are
dependent design details or explicit deferrals, not independent alternatives
that all remain open after this selection.

### Downstream question: who owns source identity?

Once decision is a peer stage, this question is about the producer boundary,
not about inventing a Pi-specific decision owner. The selected answer should
let each vehicle provider attach truthful source identity to the result it
actually produced.

The ownership split is: the vehicle runtime/source mints and attaches vehicle,
run, activation, generation, and frame identity to the result it produced; the
physical provider normalizes the source record and checks provider-specific
freshness/availability; and the common validator checks shared decision
semantics and cross-field correlation. The CLI consumes those outcomes and
does not mint identity.

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
mutable decision implementation; vehicle runtime wiring attaches source
identity to each completed cycle; the physical provider normalizes and checks
its transport/freshness evidence; and the common validator plus Automa CLI
accept shared result meaning and reject mismatches. The local Automa worker
remains authoritative only for Chase. `manage.py drive` is a runtime assembly
owner, not a new decision-stage API, and PiRacer is simply the first physical
provider. This requires adding the missing source identity fields; no existing
component currently supplies the entire tuple.

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
provider-backed read-only HTTP route is in scope as transport, while an
operator-facing live decision page/URL is not. The logical boundary is the
common decision provider and CLI; the repository's vendor patch is only the
source-controlled transport boundary, even though its handler executes from
the generated DonkeyCar checkout.

### Explicit D1 deferral: host application

Final host or actuator application is not part of the D1 claim. The user-facing
D1 result only needs to show the decision result and the shadow authority that
keeps it from being presented as a vehicle command. This matches the existing
simulation contract:

- `proposed` may contain a nonzero shadow intent;
- `authorized_output` is the shadow-only idle output;
- `proposed_applied` is explicitly `false`; and
- `host_application` remains unavailable unless a host separately reports
  application.

D1 should expose those fields from the same correlated result rather than ask a
user or page to infer safety from mode, engine control, or source code. It does
not need to add `DriveMode` or actuator telemetry. If a later M006 evidence
package still requires host-side application or temporal mode evidence, that is
a separate evidence contract; it does not expand this D1 implementation.

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
| Add a vehicle provider to `vehicles stream decision` | High; recommended | It preserves one decision-facing CLI surface and one stage-level concept while adapting the physical publication. `--once` gives a bounded first live check; the physical observation endpoint can remain supporting evidence. | The provider must preserve the accepted decision-result meaning; current observation JSON cannot simply be relabeled as a decision frame, and its physical representation need not impersonate local worker state. |
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
| Timestamp age only | Strong existing baseline | A stopped or hung cycle becomes stale after no new publication arrives for more than 1,000 ms, without adding a new heartbeat protocol. | The current physical route can still return a stale payload with `ok=true`; clock skew, future timestamps, or a frozen process need explicit handling. |
| Producer heartbeat/PID | Medium | A producer identity and heartbeat can distinguish a runtime generation and support a stopped-process check. | A remote CLI cannot use local `os.kill` to verify a Pi PID; PID alone proves existence, not cycle progress, and PID reuse is possible. |
| Sequence advancement | Medium-high | Existing frame IDs, frame indices, and counters can show whether the latest cycle advances rather than merely being replayed. | Counters reset on process restart unless bound to a run/generation, and sequence advancement still needs a timeout for a slow or hung producer. |
| systemd/MainPID identity | Medium-high as binding evidence | The service is supervised with `Type=simple` and `Restart=always`, so service restart can define a new runtime generation. | Service existence does not prove the camera/decision loop advances, and the current publication does not expose remote `MainPID` or unit generation. |
| Correlated current publication: source/runtime identity + activation match + bounded timestamp | High; recommended for D1 | It covers the first meaningful stopped/stale check with one provider-owned publication and avoids applying local-worker process checks to a remote producer. | Continuous heartbeat, sequence progress, and systemd identity are not proved by this minimum and may be separate future evidence. |

The current recommendation is a provider publication carrying runtime-produced
source identity, an activation match, and bounded timestamp freshness. The
1,000 ms value can be a starting configuration derived from the observed
physical cadence; the implementation review may choose a better
cadence-relative threshold without changing the proposal's semantic boundary.
Missing, future-dated, mismatched, or stale data is `unavailable`. A stopped
producer is demonstrated by stopping it and then waiting beyond the freshness
bound; this does not claim direct remote process-stop detection. Continuous
heartbeat, sequence-advancement, and systemd/MainPID proof are deferred unless
the bounded check shows they are needed. The existing validator can be reused
for result semantics, cycle-alignment, activation, and timestamp checks, but
local `os.kill`/`state.json` checks stay in the local worker provider and are
not fabricated on the physical path.

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
The non-negotiable semantic minimum is vehicle identity, runtime/run identity,
activation/generation identity, frame/cycle identity, publication timestamp,
decision source/proposal/authority, and explicit shadow-only authority state.
Those claims may be nested or serialized differently if the accepted meaning
remains intact.

| Shape | Baseline plausibility | Case for it | Main limitation |
| --- | --- | --- | --- |
| Reuse the existing frame/result meaning through a provider-neutral validator | High; recommended | Preserves cycle/source/authority alignment and keeps the operator's decision concept stable while allowing local and physical providers to verify different liveness evidence. | Requires a deliberately small separation between shared result semantics and provider-specific source checks. |
| Publish the decision result plus a separately correlated host-control record | Medium; defer unless the zero-control claim needs it | Keeps decision output lossless and lets a host-control boundary own its own source semantics. | It adds correlation and another record before D1 needs a full final-output claim. |
| Revise the M006 frame schema/validator to add physical fields | Medium, but only through amendment | Gives physical producer fields first-class names and can unify local/physical acceptance. | It changes the accepted contract and requires amendment/re-review rather than being a fill-in task inside this proposal. |
| Add extra fields and let the current validator ignore them | None; reject | Minimal code change. | The current validator explicitly requires an exact key set, so this would fail acceptance and create an ambiguous contract. |

The draft therefore assumes a small provider-neutral result boundary: the
vehicle runtime produces identity and decision evidence, the physical provider
normalizes and verifies provider-specific freshness, and the common validator
checks shared semantics; local PID/state checks remain in the Chase adapter. A
separately correlated host-application record is not part of the first D1
claim. The existing shadow-authority fields remain explicit:
`proposed_applied=false` means the proposal was not applied as proposed, while
`host_application` is unavailable unless a host reports it. The CLI is only the
provider-aware consumer.

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
vehicle runtime/source → sensor/frame input → activated decision stage
        ↓
runtime attaches source/run/activation/frame identity to its result
        ↓
provider publication adapter normalizes and checks freshness
        ↓
common semantic acceptance → `vehicles update/info/stream decision` CLI
```

D1 owns the narrow missing provider/publication and common-acceptance seam:
expose one decision result whose source/runtime identity is produced by the
vehicle runtime, normalize and check bounded freshness in the provider, and
accept shared result meaning through the common decision surface. The result
must make proposed intent and shadow-only authority visible without claiming
host or actuator application. The existing shadow engine, activated cycle
execution, and provider-specific liveness checks remain owners of their
current responsibilities. `manage.py` vehicle-runtime wiring and a Donkey
route are in-scope implementation details behind the physical provider; a new
bridge process is not a new logical stage and remains out of scope.

The first useful result is one fresh correlated cycle and one stale/unavailable
result after a producer stop followed by the freshness wait. Missing,
mismatched, and incomplete publications are also unavailable. Left/right
decision behavior remains the offline semantic/reference behavior established
by #203; this proposal does not retune perception or claim vehicle avoidance.

## In scope

- Confirm whether the existing physical publication can carry the accepted full
  decision result; if not, add the smallest provider-backed read-only transport
  and adapter needed. The physical route is in scope; an operator-facing live
  page is not.
- Vehicle/source-provider, runtime/run, activation/generation, frame/cycle,
  input/publication timestamp, and bounded-freshness evidence sufficient to say
  what decision stage ran and to reject a mismatched result.
- Same-cycle observation/memory context and resulting plan, proposal, and
  authority data only when they are co-located in the publisher-owned snapshot;
  no separate reads or new memory semantics are required.
- Shadow-authority fields sufficient to distinguish proposed intent from the
  shadow-only authorized output; D1 does not claim host or actuator application.
- A narrow provider interface behind the existing decision-stage command
  family; the read-only same-cycle result accessor is allowed, but no
  general-purpose framework is required.
- Explicit unavailable behavior for a missing, mismatched, incomplete, or
  stale publication. A stopped producer is checked only after waiting beyond
  the freshness bound.
- A short operator procedure for checking the path while the vehicle remains
  stationary in action-idle user mode.
- If the existing semantic frame cannot be used truthfully, stop and return a
  versioned schema amendment to proposal review; do not silently land a new
  semantic contract during implementation.

## Out of scope

- Vehicle movement or applying proposed commands.
- Reimplementing or retuning the decision cycle, shadow engine, perception, memory,
  tracking, prediction, or steering policy.
- The offline toggle/page implemented by #203.
- An operator-facing live decision page/URL, retained-image overlay, or
  redesign of the operator page; those are separate questions. The
  provider-backed read-only HTTP route used by the CLI remains in scope.
- PiRacer-only commands, controls, or a second operator-facing decision
  concept.
- Privileged Chase evaluator inputs or state; provider-owned worker liveness
  evidence remains limited to the local producer path.
- A new unreviewed semantic decision contract parallel to
  `vehicle_decision_stream_frame_v0`. Provider wire envelopes, nesting, and
  normalization are allowed when they preserve the accepted meanings; a new
  semantic version requires proposal amendment/review.
- Continuous heartbeat, sequence-advancement, systemd/MainPID, unrelated host
  or actuator telemetry, or actuator-application proof. Required source/runtime
  identity, timestamps, shadow fields, and the unavailable
  `host_application` sentinel remain in scope; later evidence cannot expand
  this D1 claim.
- A universal future-proof abstraction or a second bridge lifecycle.
- CLI automation-launcher changes. The narrowly scoped `manage.py`
  vehicle-runtime activation/source/context wiring remains in scope; any
  launcher expansion is a separate scope decision.

The read-only same-cycle result accessor with fail-closed clearing is an
explicitly allowed publication seam; it does not reimplement or retune the
decision engine or policy.

## Independence from #203

There is no dependency on #203's branch, offline wrapper, or inspector page.
#203 loads a saved decision sequence and runs the real shadow engine for two
offline scenarios. D1 may use the accepted shared decision-result meaning and
the existing `vehicles stream decision` CLI as compatibility baselines, but it
should not import or turn the offline inspector into a live producer.

The branches should remain separate. D1 uses the live
`vehicles stream decision` provider; later replay or offline-page integration
is optional follow-on work, not D1 scope. A later live-page integration would
also be a separate review question. #203 supplies an offline semantic and
left/right reference baseline, not live proof and not a requirement to
reproduce its two scenarios on the physical provider. If physical provenance
requires a small versioned semantic amendment, that is a proposal-review
decision rather than an implementation surprise.

## Implementation impact and dependency schedule

This is planning material for sizing and sequencing, not an additional
contract or an implementation authorization. It records the file-level
dependency exercise in the same place as the proposal. Estimates exclude
proof-of-work reports, generated HTML, and unrelated existing contents.

### Baseline correction

PR #208 is intentionally based on `main` for feedback, but implementation
impact must be measured against the M006 branch after the accepted decision
surface work, currently represented by `m006/sync-main-after-207` at
`b356f60`. That branch already contains:

- `cli/automa_cli/decision.py` with decision frame construction, acceptance,
  publication, persistence, and `vehicles stream decision`;
- `cli/automa_cli/app.py` with the `vehicles stream decision` parser and
  handler;
- `cli/automa_cli/automation.py` with the Chase/local-worker latest-frame
  writer; and
- `implementations/decision/shadow_adapter.py` with a shadow engine that
  retains the typed cycle result while returning an idle `AutonomyControl`.

Consequently, `app.py`, `streaming.py`, and Chase `automation.py` are not D1
implementation targets. The physical-provider work belongs around the
existing decision owner, not in a second streaming implementation.

### Shared meanings to keep aligned

The implementation should carry one vehicle-runtime-produced,
provider-published result with these meanings, even if its physical wire
nesting differs. The runtime is authoritative for identity production; the
provider owns transport normalization and provider-specific freshness checks;
the common validator owns shared semantic acceptance:

```text
vehicle identity
source/provider identity
producer runtime/run identity
activation/generation identity
frame/cycle identity and input timestamp
producer publication/completion timestamp
decision source, proposal/plan, and authority
authority_mode = shadow_only
proposed_applied = false
authorized_output = shadow-only idle
host_application = unavailable unless explicitly host-reported
```

The existing `vehicle_decision_stream_frame_v0` acceptance path also has local
Chase-only evidence: `worker_pid`, `state.json`, and local PID liveness. Those
checks remain in the Chase provider. A physical provider must not fabricate
local-worker evidence just to fit the existing validator.

### Hidden result-provider seam

`AutonomyCycleHost` returns a generic `DecisionCycleResult`, while the active
`ShadowProposalsAutonomyEngine` retains the real
`ShadowDecisionCycleResult` as `last_cycle_result`. `AutonomyManager.step()`
returns only the idle control, so the onboard publisher cannot truthfully
publish the decision by reading the generic control or cycle dictionary.

The file-level pseudocode pass found a narrow illustrative solution: add an
optional read-only capability such as `get_current_cycle_result()` to the
current shadow adapter. It should return only the result from the most recent
successful step, clear on reset and failed entry, and never authorize the
inner control. The publisher can discover that capability at the runtime
boundary and correlate the result to the frame; it must not import or
hard-code the concrete shadow class as the permanent decision boundary. No
change to the generic `AutonomyEngine` protocol or `AutonomyManager` is
expected for D1, and the final method name is an implementation choice.

### File disposition and dependencies

| File | D1 disposition | Earliest useful work | Hard dependency | Parallelization verdict |
| --- | --- | --- | --- | --- |
| `implementations/decision/shadow_adapter.py` | Add the optional same-cycle result capability; preserve fail-closed clearing and idle control | Contract/seam wave | Result shape and ownership | Small serial seam lane |
| `autonomy/runtime/manager.py` / `autonomy/runtime/engine.py` | No change expected; retain `step -> AutonomyControl` | None | Revisit only if the optional capability is insufficient | Exclude from fanout |
| `implementations/runtime/donkeycar/donkey_part.py` | Attach one same-cycle decision publication to the onboard snapshot; preserve observation and user-mode behavior | After seam and identity shape | Result capability, source/runtime identity, envelope | One file lane |
| `deploy/targets/donkeycar/app/manage.py` | Pass authoritative activation/source/runtime context into the part | After the part API is fixed | `donkey_part.py` constructor/context | Draft in parallel; implement afterward |
| `deploy/targets/donkeycar/patches/waveshare-donkeycar-local.patch` | Add one read-only decision route or equivalent publisher-backed response | After envelope is named | Part publication method and error shape | Independent transport lane |
| `cli/automa_cli/physical_observation.py` | Fetch and normalize one physical decision response; do not join unrelated reads | After wire contract | Route and provider identity | Interface/fixture drafting can be parallel; implementation follows the route envelope |
| `cli/automa_cli/decision.py` | Separate shared semantic acceptance from Chase-only liveness and add physical provider dispatch | After normalized contract | Physical adapter shape | One high-coupling lane; implementation follows the normalized adapter |
| `cli/automa_cli/app.py`, `cli/automa_cli/streaming.py`, `cli/automa_cli/automation.py` | No D1 change on the actual M006 implementation base | None | Existing accepted decision surface | Do not schedule agents |
| `tests/implementations/decision/test_shadow_adapter.py` | Verify result accessor, clearing, and unchanged idle authority | After seam decision | Accessor shape | Small conditional test lane |
| `tests/implementations/runtime/test_observation_publication.py` | Verify atomic source publication and compatibility with observation output | After envelope is frozen | Part API and fixtures | Draft in parallel; run after source work |
| `tests/cli/decision/test_shadow_decision_surfaces.py` | Verify shared semantics, provider dispatch, and unavailable cases | After validator shape | Existing frame fixtures and physical adapter | Draft in parallel; primary decision tests |
| `tests/integration/physical_deploy/test_live_runner.py` | Verify the bounded physical check and stopped/stale unavailable result | Last | Deployed route, provider, and validator | Inherently final/live |

### Bottom-up schedule

**Wave 0 — serial seam and identity decisions.** Freeze the optional result
accessor, the provider-neutral meanings above, and the owner/lifecycle of
vehicle, runtime, and activation-generation identity. Without these, parallel
agents can produce individually plausible but incompatible payloads.

**Wave 1 — parallel file work.** Pseudocode, fixtures, and interfaces can be
drafted in disjoint lanes for `donkey_part.py`, the Donkey route patch,
`physical_observation.py`, `decision.py`, and deterministic tests.
Implementation of the physical read adapter follows the route envelope;
implementation of decision acceptance follows the normalized adapter shape.
`manage.py` can be drafted in parallel but should wait for the
`donkey_part.py` constructor/context API before implementation. `decision.py`
is one high-coupling lane because it owns frame construction, validation,
persistence, and streaming.

**Wave 2 — integration and deterministic validation.** Integrate the adapter
seam first, then the onboard publisher, runtime wiring, route/read adapter,
and decision acceptance. Run source-publication and decision-surface tests
against the same envelope. The route must expose one publisher-owned snapshot;
the CLI must not reconstruct a decision by joining observation, status, frame,
and local-worker reads.

**Wave 3 — bounded live check.** In the eventual implementation/evidence
review unit, run the physical readiness procedure and the ordinary
`vehicles stream decision --id <vehicle_id> --once --json` check while the
vehicle is stationary in action-idle user mode. Confirm one fresh correlated
result, then stop the producer and wait beyond its freshness bound to confirm
`unavailable` for the missing/stale publication. This is not direct process-stop
detection. This feedback draft authorizes no live capture and does not claim
host or actuator application from this D1 result.

This is not seven independent implementation lanes. It is one serial seam and
identity spine followed by roughly three parallel lanes: onboard source and
wiring, route and physical read adapter, and decision acceptance/tests. The
single-file coupling in `decision.py` and `donkey_part.py` is a reason to give
each file one owner, not to split regions of those files between agents.

### Revised sizing

| Area | Likely added/changed LOC |
| --- | ---: |
| Optional result-provider seam and shadow adapter | 5–20 |
| `donkey_part.py` source publication | 60–120 |
| `manage.py` wiring | 10–30 |
| Donkey route patch | 35–80 |
| Physical fetch/normalizer | 40–100 |
| Existing `decision.py` validator/provider integration | 80–180 |
| Deterministic tests, including the adapter seam | 160–320 |
| Live test/procedure updates | 30–70 |

The practical planning envelope is **230–530 production LOC plus 160–320
deterministic-test LOC and 30–70 live/procedure LOC**, or roughly **420–920
changed LOC** after allowing for overlap between refactoring and added lines.
The lower end assumes the optional adapter capability and provider split stay
small; the upper end includes a dedicated physical decision route and a clean
provider-neutral acceptance separation. This supersedes the earlier
`90–220` production / `50–120` test estimate, which counted already-present
CLI work and omitted the physical-provider integration.

The source-controlled `manage.py` vehicle-runtime/context wiring and vendor
patch are the repository boundary for a generated DonkeyCar checkout; the
patched handler executes from that generated checkout during deployment. This
is a deployment detail, not a second logical decision stage or a separately
sourced repository.

## Expected handoff

After feedback, the formal proposal review should decide the stage/provider
boundary and resolve the remaining proposal-level choices: source authority,
provider-level result acceptance, and the bounded procedure. It need not settle
a universal abstraction, freeze the mutable decision engine, or add
host-application proof. The concrete route choice remains implementation-owned
as stated above. Only after the proposal has an exact-head accepted contract
review, has merged, and the milestone workflow records
`ready_for_implementation` may implementation begin as a separate review unit
on its separate branch. Proposal and implementation may share a working
session, but not a branch.
