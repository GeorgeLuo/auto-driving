# Proposal: PiRacer host-boundary telemetry

## Milestone Context

- Milestone: M006 — Decision-Facing Perception Readiness
- Base branch: `milestone/006-decision-facing-perception-readiness`
- Proposal branch: `m006/piracer-host-boundary-telemetry-proposal`
- Implementation branch: `m006/piracer-host-boundary-telemetry`
- Frontier: PiRacer host-boundary telemetry
- Proposal artifact: `docs/milestones/006-decision-facing-perception-readiness/proposals/piracer-host-boundary-telemetry.md`
- Related implementation candidate: a separate prototype is being explored from PR #202's head while this proposal remains open; it is not canonical implementation or acceptance evidence

PR #202 has the accepted shadow-cycle proposal and the D1/D2 supporting
surfaces, but its physical evidence still needs a genuine observation of the
host boundary. The engine's proposed command and `ShadowAuthorityResult` are
not observations of Donkey's user mode, pilot outputs, or final host-selected
command. This frontier contracts the missing telemetry seam without accepting,
merging, or modifying #202.

## Review Kind

Deterministic invariant closure

## Review Question

Can the physical telemetry boundary prove, for each shadow cycle, which user
and pilot values and final pre-drivetrain command the Donkey host actually
observed, with exact source identity and fail-closed consumer joins, without
treating engine authority as host output?

## Proposed Contract

Add one read-only, generation-scoped host-boundary publication at the Donkey
host's `DriveMode` output boundary. The observer runs after `DriveMode` has
selected the `steering` and `throttle` values and before the drivetrain
consumes them. It observes the values already present in the host loop; it does
not participate in selection, clamp or rewrite them, and a telemetry failure
must not change vehicle outputs.

The additive latest-record route is `GET /autonomy/telemetry/latest` (with
`HEAD` having the same availability semantics). A healthy response uses the
typed schema `automa_host_boundary_telemetry_v0` and contains the following
fields. Exact JSON key spelling may be finalized by the implementation only if
the meanings and required fields remain unchanged.

```json
{
  "schema": "automa_host_boundary_telemetry_v0",
  "status": "healthy",
  "vehicle_id": "piracer",
  "source_id": "piracer-onboard-camera",
  "run_id": "donkey-run-...",
  "generation_id": "...",
  "activation": {
    "engine_id": "shadow-proposals",
    "activated_at_ms": 0
  },
  "source_frame": {
    "frame_id": "donkey_frame_000001",
    "frame_index": 1,
    "captured_at_ms": 0,
    "completed_at_ms": 0
  },
  "host_tick": {
    "sequence": 1,
    "observed_at_ms": 0,
    "published_at_ms": 0,
    "source_age_ms": 0,
    "gap_since_previous_ms": 0,
    "skipped_since_previous": 0
  },
  "mode": "user",
  "user_input": {"steering": 0.0, "throttle": 0.0},
  "pilot_output": {"steering": 0.0, "throttle": 0.0},
  "host_selected_output": {"steering": 0.0, "throttle": 0.0},
  "application": {
    "boundary": "post_drive_mode_pre_drivetrain",
    "actuator_feedback": "unavailable"
  }
}
```

The implementation must preserve these meanings:

- `mode` and `user_input` are the values supplied to the host drive-mode
  selection for the observed tick. `pilot_output` is the pilot value presented
  to `DriveMode` after preceding host parts such as `AiLaunch` have run.
  `host_selected_output` is the actual `steering`/`throttle` pair emitted by
  `DriveMode` for the drivetrain boundary.
- The source-frame and activation fields are copied from the runtime-owned
  publication identity. The observer may use the latest completed source frame
  only when it can report its identity and age; it must not mint a frame,
  attach a frame from another run, or silently turn a held/cadence-skipped
  value into a fresh cycle.
- `host_tick.sequence` is monotonic within a run. `observed_at_ms` describes
  the host observation, `published_at_ms` describes the publication, and
  `source_age_ms`, `gap_since_previous_ms`, and
  `skipped_since_previous` expose temporal coverage rather than hiding it.
- `status` is explicit. At minimum the producer and consumer distinguish
  `healthy`, `warming`, `stale`, `stopped`, `unavailable`, `mismatched`, and
  `error`. Missing values, invalid numeric values, future timestamps, stopped
  producers, and uncovered intervals are unavailable/incomplete—not zero.
- `application.boundary` states that the values are observed before the
  drivetrain. `actuator_feedback` is always `unavailable` in this frontier;
  neither the producer nor consumer may claim servo, motor, or physical
  movement feedback.

The existing observation and decision publications remain unchanged. The
consumer fetches and normalizes the telemetry independently, then joins it to
an accepted physical decision publication only when vehicle, source, run,
generation, activation, and exact source-frame identity agree. It rejects a
missing, malformed, stale, stopped, future-dated, non-monotonic, skipped, or
identity-mismatched record with a stable reason. A rejected record is not
replaced with engine `authorized_output`, an idle authority result, a zero
default, a cached prior success, or a value inferred from a stationary image.

The consumer may expose the normalized telemetry as a separate host-observed
panel in the existing physical decision view and capture path, but it must not
rewrite the accepted decision schema or conflate `proposed`, `authorized`,
`proposed_applied=false`, and `host_selected_output`.

While this proposal is open, the operator-authorized prototype may implement
the contract in an isolated worktree rooted at PR #202's exact head. Prototype
files, fixtures, and results are discovery material and do not make this
proposal accepted, do not authorize a canonical implementation branch, and do
not satisfy #202's physical evidence package. If prototype findings change a
required meaning, the drafted proposal must be amended before the prototype is
treated as matching it.

## Trust And Authority Model

| Fact or claim | Authoritative owner | Permitted interpretation |
| --- | --- | --- |
| Proposed command, selected proposal, and shadow authority | Accepted decision cycle and `ShadowAuthorityResult` | Decision intent and authority result only; never host output |
| User mode and user input observed by the host | Donkey host loop at the telemetry observer | Exact values seen by `DriveMode` for the recorded tick |
| Pilot output presented to `DriveMode` | Donkey host loop after preceding pilot-producing parts | Exact pilot values at the selection boundary; not actuator output |
| Final host-selected command | Donkey `DriveMode` output boundary before drivetrain | Exact pre-drivetrain host selection; not servo or motor feedback |
| Runtime and source identity | Runtime activation/publication owner | Join and freshness key; consumers may validate but not mint it |
| Capture-package authenticity and coverage | Deployed bundle, actual device, operator procedure, and review | Bounded evidence attribution; hashes prove bytes, not hardware origin |

The telemetry route is observational. It has no authority to apply movement,
change mode, alter the pilot, or override the decision engine. The explicit
pre-drivetrain boundary prevents the existing engine-authorized idle envelope
from being misreported as an observed host zero. Hardware authenticity,
actuator feedback, and throughout-interval claims remain outside this
contract's authority.

## Ownership

| Concern | Owner |
| --- | --- |
| Source-frame, run, activation, and generation identity | Existing Donkey autonomy publisher and activation lifecycle |
| Host mode, user input, pilot input, and final pre-drivetrain values | Read-only observer immediately after `DriveMode` |
| Latest-record atomicity, status, freshness, and route | Donkey runtime/deployment publication boundary |
| Telemetry schema validation and decision join | Physical observation/decision consumer |
| Host-observed panel and capture serialization | Existing physical decision view/capture adapter, additive only |
| M006-06 proposal evidence and final #202 acceptance | Current cross-environment evidence frontier and its review owner; unchanged |
| M006-07 criterion judgment | Current #202 evidence frontier; this capability is a supporting dependency only and does not mark it Met |

## Affected Paths

| Journey | Required observable result |
| --- | --- |
| Activate → source frame → `DriveMode` | One host tick records exact source/activation identity, mode, user input, pilot input, and final pre-drivetrain output |
| User-mode stationary PiRacer | Actual idle user values and zero pilot/output values are recorded separately; no zero is inferred from engine authority |
| Nonzero shadow intent with idle host | Decision proposed command, authorized idle, `proposed_applied=false`, and observed host output appear as distinct facts |
| Fresh → gap/skipped → stale/stopped | Coverage and liveness transition explicitly; cached values are not promoted to current success |
| Physical decision fetch/view | Telemetry joins only on exact identity and returns a stable unavailable/incomplete reason on failure |
| Observer/publisher failure | DriveMode and drivetrain inputs retain their existing behavior; telemetry failure is diagnostic only |
| Existing D1/D2 routes and offline decision commands | Existing schemas, routes, replay bytes, and shadow authority meanings remain compatible |

## Adversarial Matrix

| ID | Case | Required result |
| --- | --- | --- |
| T-01 | No telemetry publisher, warming runtime, or route unavailable | Return explicit unavailable/warming; never infer host zero from the decision cycle |
| T-02 | Malformed schema, missing required field, nonnumeric value, nonfinite value, or invalid mode | Reject the record with a stable error; do not publish a normalized success |
| T-03 | Run, generation, activation, vehicle, source, or frame identity mismatch | Reject the join; never attach telemetry from another runtime session |
| T-04 | Frame is older than the declared freshness bound, timestamp is future-dated, or publication stops | Mark stale/stopped/unavailable; do not serve cached success |
| T-05 | Host tick sequence regresses, repeats, skips, or has an uncovered time gap | Expose the gap/skipped count and fail the affected evidence interval closed |
| T-06 | User mode has nonzero input or PiRacer is not in `user` mode | Record the actual values but reject that interval as M006-07 zero-control evidence |
| T-07 | Engine control is nonzero while host output is zero | Preserve both observations and their distinct owners; do not call engine authority host telemetry |
| T-08 | Authorized idle is copied into `host_selected_output` | Reject the implementation/test; host output must come from the post-`DriveMode` boundary |
| T-09 | `AiLaunch`, `DriveMode`, or another host part transforms pilot/user values | Report the values at each contracted boundary; no consumer-side reconstruction or assumption |
| T-10 | Observer throws, locks, or is read by a slow client | Vehicle output remains unchanged; publication uses bounded immutable snapshots and no network I/O in the drive loop |
| T-11 | Concurrent latest-record reads observe a partial update | Return one complete old/new record or explicit unavailable; never a mixed identity |
| T-12 | POST, query mutation, path traversal, redirect, or remote request is attempted | Reject with no mode, decision, file, network, or actuation side effect |

## External Assumptions

- PR #202's D1 physical publication and D2 decision-view changes remain
  available as the implementation base, but their decision publication and
  view schemas are not reopened by this proposal.
- The supported Donkey deployment continues to route user/pilot values through
  `DriveMode` before drivetrain consumption, and the observer can be attached
  without changing that ordering.
- The runtime can provide activation and source-frame identity for the
  observed host tick. If it cannot, the producer must report unavailable and
  the physical evidence remains blocked; a synthetic identity is not an
  acceptable fallback.
- A later evidence operator can keep the PiRacer stationary, in `user` mode,
  with idle user inputs, and can preserve telemetry records covering the
  declared interval. This proposal does not authorize hardware operation.
- The deployed bundle, source revision, device/session identity, and capture
  procedure will be pinned when #202 refreshes its evidence package. A passing
  unit test or prototype receipt alone is not physical evidence.

## Non-Goals

- Accepting or merging this proposal, accepting #202, or changing the accepted
  #201 contract or the D1/D2 review receipts.
- Marking M006-07 Met; this frontier supplies a capability that a later
  evidence package must use.
- Changing decision proposal schemas, selector behavior, shadow authority,
  `proposed_applied`, engine control, mode selection, or drivetrain behavior.
- Reporting servo, motor, wheel, actuator, or physical movement feedback.
- Applying movement, commanding a vehicle, changing PiRacer mode, or adding a
  remote telemetry transport.
- Replacing existing observation/decision routes, replay formats, or the D2
  live decision view with a second decision protocol.
- Claiming hardware authenticity, safety, collision avoidance, object identity,
  navigation, or simulator/physical parity from telemetry alone.
- Committing canonical runtime implementation or evidence artifacts while the
  proposal is open; the operator-requested prototype remains isolated
  discovery work.

## File Impact

Expected implementation paths are the Donkey runtime observation/publication
module, `AutonomyPilotPart` identity handoff if required, the Donkey deployment
part wiring and local route patch, physical observation/decision normalization,
and focused runtime/CLI tests. The proposal PR itself adds only this artifact,
the canonical plan transition, and generated plan HTML. Prototype changes are
kept on a separate implementation-candidate worktree until proposal review
and any amendment cycle are complete.

## Validation Plan

- Validate the proposal and plan with the milestone workflow checker and
  verify the new frontier is a supporting M006-05 capability without
  overlapping the active #202 or D2 frontiers; #202 retains M006-06/M006-07
  ownership.
- In the prototype, exercise an in-process Donkey loop through the real
  `DriveMode` boundary. Assert that the observer sees the input/pilot/final
  values, preserves exact source identity, and never changes the returned
  drivetrain values.
- Run producer tests for atomic publication, run/activation/frame mismatch,
  stale/future/stopped state, gaps/skips, malformed values, observer failure,
  and explicit no-actuator-feedback status.
- Run consumer tests for schema normalization, exact decision joins, stable
  failure reasons, no cached-success fallback, nonzero-vs-zero separation, and
  compatibility of existing D1/D2 physical decision paths.
- Review a prototype record sequence for user mode, idle user input, zero
  pilot, and zero final host output. Treat this only as deterministic
  capability evidence; it cannot satisfy the later physical C1–C7 package.
- If a required boundary, identity, temporal rule, or ownership meaning is
  missing, stop implementation discovery, amend this drafted proposal, and
  revise the prototype to the amended contract before declaring #202
  telemetry-ready.
- The later #202 evidence owner must use the accepted contract to capture
  genuine Chase and stationary PiRacer records, including coverage and
  terminal-state receipts, before any M006-07 acceptance judgment.

## Evidence Topology And Capture Strategy

| Claim | Deterministic source | Prototype/evidence boundary | What it does not prove |
| --- | --- | --- | --- |
| Host observer is at the intended boundary | Source wiring and focused unit tests | In-process loop through `DriveMode` | A real device or actuator output |
| Record fields are actual host values | Observer tests with explicit input/pilot/final values | Bounded synthetic sequence with source identity | Throughout-interval physical behavior |
| Consumer cannot confuse decision and host authority | Normalizer/join tests for matching and mismatch cases | Physical view/capture projection showing separate panels | That a later capture is genuine or accepted |
| Liveness and coverage fail closed | Sequence tests for stop, stale, gap, skip, and future time | Prototype stop/restart receipt | Absence of failures in an unobserved interval |
| M006-07 physical zero-control claim | None in this proposal | Later pinned Chase/PiRacer evidence package under #202 | Unit tests, screenshots, engine idle, or stationary image alone |

No prototype record is copied into the canonical M006 evidence directory. After
proposal acceptance and implementation review, the #202 evidence owner may
capture raw telemetry next to the existing raw decision/source records, retain
derived joins separately, and render a human-reviewed package. Every accepted
interval must identify its deployed bundle, device/session, run/generation,
source frame, timestamps, mode, user input, pilot output, final host output,
and uncovered/gap status. Missing coverage is a disclosed limit or failure,
not a successful zero-control observation.

## Expected Handoff

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Host-boundary telemetry contract accepted in PR #{pr}; producer, consumer, identity, freshness, coverage, and authority-separation checks passed.",
  "criterion_updates": {
    "M006-05": {
      "status": "Met",
      "evidence": "The accepted M006-05 decision surfaces remain intact; this telemetry capability is an additive supporting operator-surface contract and does not replace the accepted baseline."
    },
    "M006-07": {
      "status": "Unmet",
      "evidence": "The telemetry capability is a supporting prerequisite for the #202 Chase/PiRacer evidence; no criterion is marked Met by deterministic or prototype checks."
    }
  },
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "Telemetry acceptance does not replace the #202 cross-environment evidence package or close M006.",
    "revisit_when": "The #202 evidence owner has a pinned availability receipt and genuine covered Chase/PiRacer records."
  }
}
```

Acceptance of this proposal returns the frontier to `ready_for_implementation`
and permits the implementation branch to start. It does not approve or merge
#202, authorize physical operation, or mark M006-07 Met. The implementation PR
must reconcile this proposal and every later accepted amendment, and the #202
evidence owner must refresh its exact-head capture package before M006-07 can
be reviewed.
