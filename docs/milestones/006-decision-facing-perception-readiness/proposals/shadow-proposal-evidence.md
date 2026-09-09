# Proposal: Cross-environment shadow proposal evidence

| Field | Value |
| --- | --- |
| Milestone | 006 Decision-Facing Perception Readiness |
| Frontier | Cross-environment shadow proposal evidence |
| Proposal branch | `m006/shadow-proposal-evidence-proposal` |
| Implementation branch | `m006/shadow-proposal-evidence` |
| Exit criteria | M006-06, M006-07 |
| Review kind | Live or external evidence |

Prerequisite (accepted, do not re-open):

- The accepted modular shadow proposal foundation in
  [shadow-proposals.md](shadow-proposals.md), proposal #73 at
  `7a585fc5eab8028b7454ad0040575cf4120c8e92`, implementation #74 at
  `7830cb0c509eb6c601bf74f707d8caeca177ed8d`.
- The accepted Automa shadow decision surfaces in
  [shadow-decision-surfaces.md](shadow-decision-surfaces.md), proposal #79 at
  `b9c1af61be8837ee6832fd2e33c706aa4ee84b44`, implementation #80 at
  `0206c860a7a3aad38045b570f31784c22dac00e8`.

This is one live/external evidence review unit for M006-06 and M006-07. It
does not implement the decision engine, the Automa surfaces, or either of the
capture-readiness capabilities discussed below.

## Review Kind

Live or external evidence

## Review Question

Does the staged `avoid_recent_obstruction` path produce provenance-complete
shadow action plans and the same correlated visual explanation on Chase and
stationary PiRacer inputs while applied control remains zero and privileged
simulator state stays outside controller inputs?

## Deliverable and readiness status

This PR delivers a bounded evidence contract/procedure and a fail-closed
capture/readiness gate for M006-06 and M006-07. Acceptance of this proposal
accepts that evidence contract only. It does not assert that either environment
can provide live capture, that D1 or D2 is available, or that live capture is
ready. D1 and D2 are provisional names for candidate capture-readiness
capability gaps or conditions: they are proposal-level, non-binding hypotheses,
not M006 exit criteria, contracted Frontier Map nodes, accepted dependencies,
or permission to implement them in this PR. Future work may introduce a
concrete capability or recovery frontier after this unit's output or a concrete
failure identifies the need; that work must be separately proposed and
reviewed, and this PR does not predeclare its branch, PR, external issue, or
implementation route.

## Operator Want

- Want: An operator can inspect one reviewable evidence package for each
  environment, see left and right obstruction intent plus
  retained-to-stale-to-inactive behavior, follow every selected source to its
  actual camera frame, reproduce the decision digest, and distinguish proposed
  intent from host-observed zero output.
- Reject if: Either environment cannot supply a live, correlated
  proposal/image/source/host-authority package through the accepted workflow;
  synthetic replay or an unavailable host-application envelope cannot stand in
  for the missing physical demonstration.

## Evidence rendering

- Derived HTML: yes
- Evidence directory:
  `docs/milestones/006-decision-facing-perception-readiness/evidence/shadow-proposal-evidence/`
- Skip reason: None. The implementation/evidence work must render derived HTML
  for sealed records next to the records they present. The records remain the
  authority; proposal-only work creates no runtime record and therefore adds no
  evidence artifact in this PR.

## Proposed Contract

### Package boundary

Only after an explicit capture assignment and verification of every applicable
candidate condition below may the evidence owner produce two separately
attributable packages beneath the stable evidence directory:
`chase/` and `piracer/`. Each package uses the same staged engine, accepted
operator path, and correlated decision/view fields. Each contains:

- left and right active `avoid_recent_obstruction` witnesses;
- one continuous side-evidence removal sequence, without reset during the
  sequence, witnessing fresh, retained, stale, and inactive states;
- an absent-supported-evidence witness;
- stop, failure, and replay receipts; and
- raw source frames/publications/configuration/host records plus derived
  sequences, replay results, digests, exact-frame HTML, screenshots, and a
  manifest that keeps raw, derived, visual, and host-authority evidence
  separate.

Witnesses may overlap in one run; a combinatorial scene matrix is not required.
Capture selects a bounded contiguous lifecycle window and its referenced source
frames. Dropped frames, uncovered intervals, and incomplete runs are recorded;
missing transitions are never inferred. Existing default input and record
bounds remain in force: at most 256 frames and 32 MiB of input per apply
sequence, and 8 MiB per record. No retuning is part of this contract.

### Environment and authority boundary

- Chase uses the supported packaged perception and memory path and a stationary,
  observe-only session where available. The evaluator/reference/map sibling is
  retained only as a separate comparison artifact and never enters controller
  inputs. Session identity, control source/input, and before/during/after
  preservation are recorded. No scenario reset, playback adjustment, or control
  takeover is part of the evidence procedure.
- PiRacer is stationary, in `user` mode, with user inputs idle and pilot
  steering/throttle zero. Physical publication, matched onboard frame/image
  identity, mode, and pilot telemetry are recorded for the declared interval.
- Proposed movement intent remains distinct from applied control. For every
  accepted interval, host-side applied/pilot output is joined to the capture
  generation and reported separately from the cycle's proposed command,
  authorized idle output, and `proposed_applied=false`. An unavailable
  `host_application` envelope is unavailable evidence, not an invented zero.

### Evidence claims and authoritative observations

| Claim | Authoritative observation | Derivation | Acceptance boundary |
| --- | --- | --- | --- |
| M006-06 active intent and lifecycle | Live full shadow cycle, original observation and `MemorySnapshot` exports, cycle timestamp, and activation configuration | Join candidate `source_refs` to record provenance; calculate age from source timestamp and provenance update time; classify with the accepted policy | Left/right active witnesses show fresh selection, retained continuity within `retained_max_age_ms`, stale command-null/idle selection, and inactive after eligible evidence expires. Selection and command signs agree. |
| M006-06 exact visual explanation | Original correlated image bytes, full source references, actual browser-rendered exact-frame page, and visual inspection receipt | Map each source reference to its actual image/frame and inspect the current and retained source frames | The rendered image, memory references, lifecycle, command, selection, authority, and text agree with the record. A template-only screenshot or JSON-only assertion is insufficient. The live URL and spatial retained-evidence overlay remain the D2 candidate condition and must be independently verified before a live-view claim. |
| M006-06 replay | Captured observations/memory, source cycle identities, replay sequence bytes, frozen activation, and code revision | Lossless projection into `automa_decision_apply_sequence_v0`, then accepted decision apply twice | Canonical digest bytes and SHA-256 match, and replay lifecycle/selection/command agrees with the captured cycles for identical replay-supported inputs/configuration. Full host-envelope or generation equality is not claimed by replay. |
| M006-07 Chase privilege exclusion | Controller input/cycle source exports and pinned capture-to-context code; evaluator state stored only as a separate sibling artifact | Inspect actual candidate input and source envelopes against camera/observation/memory provenance | No evaluator, map, reference-decision, or other privileged simulator state is present in controller inputs. This is bounded evidence for the declared capture, not a universal authenticity proof. |
| M006-06/M006-07 zero applied control | Host-side applied/pilot output and mode records covering the declared interval, joined to capture generation, plus cycle authority | Separate proposed, authorized idle, `proposed_applied=false`, and actual host output; summarize zero/nonzero observations and temporal gaps | Every accepted interval has host evidence; Pi mode is `user` and pilot outputs are zero. An engine-authorized idle result or stationary photograph alone is insufficient. |

Hashes establish byte consistency, not authenticated hardware origin. Session
records and operator capture provenance establish only bounded attribution.
Recurring evidence IDs do not establish physical-object identity; memory is the
latest evidence rather than a trajectory. No location, motion, prediction,
navigation, or movement-safety claim is part of this proposal.

### Candidate capture-readiness conditions (D1/D2)

D1 and D2 are provisional, proposal-level, non-binding hypotheses about
capability gaps or conditions that may matter to a later canonical capture.
They are not M006 exit criteria, contracted Frontier Map nodes, accepted
prerequisites, or implementation permission in this PR. They make the evidence
contract's fail-closed gate explicit. If a future capture attempt confirms a
condition is needed, the relevant owner must provide or verify it through a
separately reviewed capability or recovery proposal and implementation route
before the affected canonical evidence is attempted. This proposal does not
choose that route, create an external issue, or reserve a product path.

| ID | Candidate owner / boundary | Candidate minimum capability | Separate route and capture gate |
| --- | --- | --- | --- |
| D1 — PiRacer shadow-cycle publication/liveness | Existing PiRacer Donkey physical publication/control boundary together with the Automa automation/decision-stream publication boundary | A supported physical shadow-cycle publication/stream route with genuine run, activation, and liveness identity plus host mode/pilot observations, preserving the same accepted operator workflow | If later evidence confirms this condition is needed, the named owner must separately propose and review the bounded capability or recovery work, then provide an availability/verification receipt before physical automation or canonical Pi evidence. Existing physical observation publication is supporting input evidence only; it cannot stand in for the generation-scoped shadow publication or fabricate local PID/state. |
| D2 — Live decision URL/retained-evidence visual overlay | Existing Automa decision `info`/view boundary | A correlated live decision view reachable from `info` and the required visual retained-evidence explanation | If later evidence confirms this condition is needed, the named owner must separately propose and review the bounded capability or recovery work, then provide an availability/verification receipt before the live-view portion of canonical evidence. Existing exact-frame replay HTML is supporting visual evidence only; it does not resolve the required live URL or spatial retained-evidence overlay. |

The operator may authorize canonical capture only after the proposal is accepted,
the workflow is ready for implementation, the capture procedure, lossless
sequence mapping, bounded selection, and evidence checks are frozen, and every
applicable candidate condition has an explicit availability/verification
receipt. Proposal acceptance alone is never that receipt and does not make live
capture ready. If a candidate condition or authoritative observation is
unavailable, the package remains incomplete or records the failure; it is not
reclassified as successful through offline replay, stationary observation, or a
fabricated live publisher. A future proposal may introduce the concrete
capability or recovery unit after this contract's output or a concrete failure
identifies it.

### Bounded cases

| Case | Criteria | Evidence contract |
| --- | --- | --- |
| C1 — shared staged path | M006-06, M006-07 | In Chase and PiRacer, stage/inspect the same engine configuration and run the live automation/decision stream after the applicable candidate conditions are verified by the capture gate. Record matching vehicle/run/activation/frame identity, one `avoid_recent_obstruction` candidate, `deterministic_first_active`, engine `shadow-proposals`, and observed host output zero. |
| C2 — left/right active intent | M006-06, M006-07 | Each environment supplies one supported left and one supported right scene. Fresh accepted image-relative evidence selects steering `+m` for left and `-m` for right, with throttle `0`, gear hold, matching selected contribution and complete references. Proposed nonzero steering is visible beside idle authority and host observations. |
| C3 — retained-to-inactive lifecycle | M006-06, M006-07 | Each environment records one side-evidence removal sequence without reset: fresh, retained active, stale command-null/idle selection, then inactive after accepted evidence expires. Preserve the prior source image for retained/stale references and use real cycle timestamps and memory snapshots; timestamps are not edited to manufacture transitions. |
| C4 — absent supported evidence | M006-06 | Empty memory at start or after expiry yields inactive, no selected proposal, and no invented source or movement intent. Unsupported placement is recorded honestly and cannot satisfy C2. |
| C5 — integrity and stop boundary | M006-06, M006-07 | A mismatched frame/image, stale generation, or missing authoritative host observation is not counted as successful live evidence. After the owned worker stops, the decision stream must not return an accepted live frame; retain failure receipts. |
| C6 — replay and visual review | M006-06 | Replay each environment's bounded recorded inputs twice and inspect exact-frame HTML. Canonical digest bytes and SHA-256 match; captured lifecycle, selection, and proposed command agree for identical replay-supported inputs/configuration. Inspect actual rendered images and references. |
| C7 — environment authority interval | M006-07 | Chase preserves the declared session and keeps evaluator state outside controller inputs; Pi remains stationary in `user` mode with pilot steering/throttle zero. Missing temporal coverage is a limit or failure, not proof of throughout. |

## Ownership

| Concern | Owner / boundary |
| --- | --- |
| Evidence package, manifest, capture procedure, and acceptance judgment | Tracked exact-frame Chase and stationary PiRacer shadow evidence packages under the stable evidence directory; the evidence operator and reviewer own capture and acceptance. |
| Accepted proposal/plan/authority semantics | PR #74 proposal and implementation; this unit consumes the types, plugin policy, lifecycle rules, selector, and shadow authority without changing them. |
| Decision stage/info/stream/apply/view fields | Existing Automa decision surfaces from PR #80; this unit consumes them without reimplementing them. |
| D1 PiRacer publication/liveness | Existing PiRacer Donkey physical publication/control boundary and its Automa automation/decision-stream owner; this is the candidate boundary for any future capability or recovery proposal if a concrete gap is identified. |
| D2 live URL/retained-evidence overlay | Existing Automa decision `info`/view owner; this is the candidate boundary for any future capability or recovery proposal if a concrete gap is identified. |
| Chase session preservation | Existing Metrics UI/Chase session boundary; use its existing session-fingerprint/preserve-session capability or retain a structured refusal. No external issue is created by this proposal. |
| Visual correlation | Evidence review inspects actual rendered images, source frames, references, lifecycle, selection, and separate authority fields; JSON and template presence alone do not own this claim. |

## Affected Paths

| Path | Expected result |
| --- | --- |
| Shared stage → info → live automation → decision stream | Chase and stationary PiRacer use the same accepted engine configuration and correlated run/activation/frame identity after the applicable candidate conditions are verified by the capture gate. |
| Left/right supported scenes | Fresh accepted obstruction evidence produces the expected steer-away intent with throttle zero and gear hold; proposal and authority remain separate. |
| Continuous evidence removal | Fresh, retained, stale, and inactive lifecycle states are visible with real timestamps, memory snapshots, and retained source images. |
| Absent or unsupported evidence | Inactive or unavailable state is recorded without an invented source, command, or movement claim. |
| Exact-frame visual review | The actual current and retained source images, references, lifecycle, selection, authority, and live-view/overlay result are inspected and linked in the package. |
| Deterministic replay | The recorded supported inputs apply twice with byte-equal canonical digests and matching SHA-256; the comparison remains limited to replay-supported fields. |
| Chase controller inputs | Camera/observation/memory provenance is present; evaluator/map/reference state is retained only outside the controller input path. |
| PiRacer authority interval | Stationary `user` mode and zero pilot steering/throttle are recorded for every accepted interval; missing coverage remains a disclosed gap. |
| Stop and cleanup boundary | The owned worker's terminal state, errors, session preservation, mode/output observations, and any incomplete run are retained without deleting another run's captures. |

## Adversarial Matrix

| Adversarial case | Required result |
| --- | --- |
| D1 route is absent or exposes only physical observation | Capture readiness remains blocked; do not fabricate generation/run/liveness identity or count physical observation as live shadow-cycle evidence. |
| D2 route is absent, `info` has no live URL, or the view has no retained-evidence overlay | Capture readiness remains blocked; offline exact-frame HTML and JSON remain supporting evidence and do not satisfy the named live view. |
| Frame, image, source reference, or generation does not match | Reject the affected witness/package; no successful live claim is retained. |
| Host application or pilot/mode observation is missing for a claimed interval | Reject the affected zero-control claim or mark the interval incomplete; do not infer host output from engine authority. |
| Owned worker stops or the publication becomes stale | Stream must not return an accepted live frame; retain terminal/failure receipt and uncovered interval. |
| Side evidence is removed and only stale evidence remains | Show stale command-null/idle behavior with the prior source image; never edit timestamps or manufacture a lifecycle transition. |
| Memory is empty, expired, unavailable, or evidence kind is unsupported | Record inactive/unavailable without selected proposal, source, or movement intent; the absent case cannot satisfy the active case. |
| Fresh evidence competes with a higher-confidence stale sibling | Fresh evidence wins according to the accepted selection policy; stale confidence does not override freshness. |
| Chase evaluator, map, or reference-decision state enters controller inputs | Reject the Chase privilege-exclusion case; retain evaluator state only as a separate comparison artifact. |
| PiRacer is not stationary/in `user` mode or pilot output is nonzero | Reject the affected M006-07 authority interval; do not describe it as zero applied control. |
| Proposed nonzero steering is presented as applied output | Reject the authority interpretation; show proposed intent, authorized idle output, `proposed_applied=false`, and host observation separately. |
| Screenshot is a template, JSON-only assertion, or a page without actual image correlation | Reject the visual explanation case; inspect rendered current and retained source images and references. |
| Replay digests match but captured cycle records/source references are absent or disagree | Digest equality alone is insufficient; reject the live-parity claim while retaining the replay result as bounded supporting evidence. |
| Run is interrupted or only a partial sequence survives | Label it incomplete, preserve uncovered intervals and cleanup errors, and do not assemble a successful package from surviving frames alone. |

## External Assumptions

- The accepted PR #74 decision-data, proposal, selector, lifecycle, authority,
  and `avoid_recent_obstruction` contracts remain available without reopening
  their review questions.
- The accepted Automa decision surfaces remain available without renaming their
  schemas or changing their proposal/authority meaning. D1 and D2 are the
  provisional candidate conditions described above; this proposal neither
  asserts their availability nor waives the capture gate.
- Chase can provide the supported packaged perception/memory path and a
  reachable session whose identity and preservation can be observed. If the
  existing session-preservation capability refuses, the refusal and its impact
  remain external evidence rather than a local workaround.
- PiRacer hardware can be operated stationary in `user` mode with idle user
  inputs if the applicable D1 candidate condition is later verified; narrow
  supported placements and inactive physical output are acceptable evidence of
  a limit, not a reason to tune perception.
- A frozen repository/deployed-bundle identity, engine/plugin/selector and
  activation configuration, vehicle/run/publisher identity, frame/cycle and
  observation/memory/source records, image hashes, and environment-specific
  session/frame metadata can be retained by the operator.
- Existing memory settings can witness retained age at or below 1000 ms and
  stale age before expiry without changing those settings. Apply and record
  bounds and accepted default inputs remain available.
- Byte hashes establish consistency only. Recurring IDs do not prove object
  identity, memory is not a trajectory, image motion does not establish
  self-motion, and no prediction, localization, navigation, or movement-safety
  proof is assumed.

## Non-Goals

- Implementing D1 PiRacer shadow-cycle publication/liveness or D2 live decision
  URL/retained-evidence overlay in this evidence PR; a future proposal may
  introduce separately reviewed capability or recovery work if a concrete need
  is identified.
- Reimplementing or changing the accepted Automa decision surfaces, PR #74
  proposal policy, lifecycle matrix, selector, authority semantics, or source
  schema.
- Treating offline replay, the existing physical observation publication, a
  stationary photograph, engine-authorized idle, or a fabricated local
  publisher as a substitute for missing live capability.
- New perception algorithms, VLM products, perception retuning, prediction,
  trajectory, SLAM, metric motion, semantic identity, or object tracking.
- Applied vehicle movement, non-idle authority, collision avoidance,
  navigation, or safety certification claims.
- Consuming evaluator, map, reference-decision, or other privileged simulator
  state as controller input.
- A new CLI/workflow/policy surface, external issue, milestone advancement,
  criterion promotion, or closeout judgment.

## File Impact

### Proposal phase

- Create
  `docs/milestones/006-decision-facing-perception-readiness/proposals/shadow-proposal-evidence.md`
  as this reviewed contract.
- Modify only the canonical M006 `plan.md` to enter `proposal_in_review`,
  materialize the required empty `Frontier Map` with `Path: none` and
  `Cadence: linked-list`, preserve its history and existing Met criteria, and
  keep the successor empty.
- Regenerate the corresponding M006 `plan.html`; do not edit it by hand.
- No evidence records, product code, tests, accepted proposal/amendment
  artifacts, vehicle/simulator state, or workflow/contract/policy files are
  created by this proposal phase.

### Later evidence phase

- Create `docs/milestones/006-decision-facing-perception-readiness/evidence/shadow-proposal-evidence/README.md`
  with the reproducible procedure, case map, limits, and source references.
- Create `evidence/shadow-proposal-evidence/chase/` and
  `evidence/shadow-proposal-evidence/piracer/` raw frame/publication/cycle/
  configuration/host records and images, with derived `sequence.json`, replay
  results/digests, exact-frame HTML, and screenshots.
- Create `evidence/shadow-proposal-evidence/result.json` and adjacent
  `result.html` for case outcomes, provenance, interval coverage, cleanup, and
  capture-readiness receipts. Derived pages sit next to the sealed records they
  present, under the same stable per-frontier directory.
- Add evidence-local export, verification, or rendering helpers only if needed
  to reproduce these artifacts. They do not become product surfaces or policy
  implementation.

### Potential future capability work

D1 and D2 determine any later implementation file impact only if a concrete gap
is identified and a future proposal is accepted for that bounded capability or
recovery work. This proposal names their candidate owning boundaries and
minimum capabilities for capture readiness; it does not infer or reserve their
product paths.

## Validation Plan

### Proposal checks

Run only proposal/documentation checks for this PR:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py status \
  --plan docs/milestones/006-decision-facing-perception-readiness/plan.md
PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py validate \
  docs/milestones/006-decision-facing-perception-readiness/plan.md
python3 docs/render_markdown.py
python3 docs/render_markdown.py --check
PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/006-decision-facing-perception-readiness \
  --head-ref m006/shadow-proposal-evidence-proposal \
  --base-sha f050612446bf24fc3b04d301e1d68cb253f37b0c \
  --head-sha <actual-proposal-head> \
  --pr-body-file /Users/gluo/Projects/agent-handoffs/m006-next-frontier-proposal/PR-BODY.md
git diff --check
```

The path audit checks that the proposal, canonical M006 plan, and generated
M006 plan HTML are the only changed paths relative to the exact base; the
proposal and accepted-artifact links resolve; the stable evidence directory is
repository-relative; and the proposal contains exactly one valid handoff JSON
block. No product suite or live capture is part of proposal validation.

### Later live/external acceptance (not run in this proposal phase)

After proposal acceptance, explicit operator assignment, and independent
verification of every applicable candidate capture-readiness condition, the
evidence owner may run the accepted stage/info/automation/stream/replay/view
procedure for bounded Chase and stationary PiRacer packages, then inspect the
records and rendered images against C1-C7. Proposal acceptance alone does not
authorize these commands, assert D1/D2 availability, or make live capture
ready. Those commands and captures are not executed or represented as receipts
here.

## Expected Handoff

This is a later evidence-phase success template, not a proposal acceptance
result; M006-06 and M006-07 remain `Unmet` until the required evidence is
captured and accepted.

Post-merge evidence success template (merge-time identity is filled by the
governing completion workflow; do not predeclare PR/SHA fields):

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Tracked Chase and stationary PiRacer shadow evidence under evidence/shadow-proposal-evidence in PR #{pr}, with correlated frames/refs, lifecycle, replay, visual review and host-authority receipts.",
  "criterion_updates": {
    "M006-06": {
      "status": "Met",
      "evidence": "Accepted cross-environment live proposal, source, lifecycle, selection, intent, visual and zero-control evidence in PR #{pr}."
    },
    "M006-07": {
      "status": "Met",
      "evidence": "Accepted Chase controller-input isolation and Pi user-mode/zero-pilot interval evidence in PR #{pr}."
    }
  },
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "No successor is contracted in the governing remaining path; successful advance returns current to idle.",
    "revisit_when": "After M006-06/07 evidence acceptance, the operator may assign a separate closeout proposal."
  }
}
```
