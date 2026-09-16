# Proposal: D2 live decision view

## Milestone Context

- Milestone: M006 — Decision-Facing Perception Readiness
- Base branch: `milestone/006-decision-facing-perception-readiness`
- Proposal branch: `m006/live-decision-view-proposal`
- Implementation branch: `m006/live-decision-view`
- Frontier: D2 live decision view
- Proposal artifact: `docs/milestones/006-decision-facing-perception-readiness/proposals/live-decision-view.md`
- Related implementation candidate: PR #203, retained open and not accepted by this proposal

## Review Question

Does `RuntimeViewServer` host a generation-bound `/decision` page in the same
CLI loopback class as `/perception` and `/memory`, with the cycle's actual
image as the primary object?

## Proposed Contract

The accepted decision cycle is authoritative for decision facts. D2 presents a
correlated envelope assembled through the real automation and
`RuntimeViewServer` seam; the decision stream itself does not need to carry
image bytes or become the transport for every presentation input. The existing
`RuntimeViewServer` owns a read-only `/decision` page and the generation-bound
data and image routes beside its existing perception and memory views.

- The first implementation slice must work through a real local automation
  cycle: the captured image, accepted decision cycle, and runtime-view
  publication must join at the product boundary. A standalone inspector or
  projection fixture alone does not establish this seam.
- `info decision` preserves its staged/offline behavior. It may add an
  optional, bounded probe of the actual loopback producer and reports a live
  URL only after that producer is verified. It never starts a worker, captures
  a frame, or writes a runtime artifact.
- The page renders the accepted cycle, including its selected proposal,
  contributions, source references, proposed command, authorized idle output,
  and `proposed_applied=false`. Host output is a separately attributable
  observed or explicitly unavailable panel; authorized idle is not evidence
  that observed host output was zero.
- The live session is bound to vehicle/run, producer generation, and
  activation identity as applicable. Each snapshot, decision frame, image,
  and overlay is bound to its exact transaction/source identity. A diagnostic
  page may explain warming or unavailable state before a valid cycle exists;
  valid data must not cross those bindings. Restarts, restaging, activation
  changes, stale or malformed publications, and port reuse invalidate the old
  session and require explicit reconnection.
- The current image is the image captured for the exact decision transaction,
  not a faster later camera frame. Retention must use explicit bounded count or
  byte limits and preserve the in-flight image and latest completed matched
  transaction within those limits, or report capacity failure explicitly.
  Historical matched transactions may remain inspectable while frozen or
  stale, but they must expose capture age separately from publication age and
  must never appear as current success. Missing or evicted source images keep
  raw provenance and are unavailable rather than substituted.
- Retained evidence is shown only when its generation, transaction/frame,
  observation, provenance, and supported image-relative geometry agree.
  Snapshot, image, and overlay updates are installed atomically with respect
  to that association; superseded or expired responses are discarded and no
  guessed overlay is drawn.
- Presentation publication is observational and nonfatal. Bounded copying and
  retention, short worker-lock ownership, immutable snapshots, and no network
  I/O while holding a worker lock must keep slow browser readers or publication
  failures from aborting automation or changing authority outcomes.
- Layout, panel order, and exact human-facing reason wording are implementation
  hypotheses. The implementation may use side-by-side source/current images or
  a text-first degraded view when that makes the decision easier to explain,
  while keeping availability and reason categories distinguishable.
- Routes are GET/HEAD-only, loopback-scoped, no-store, and cannot read a file
  path, follow a supplied URL, start a decision, or enter the authority path.
  A missing or mismatched component produces a bounded partial/unavailable
  response with a stable reason.
- The D2 implementation must preserve existing offline decision commands,
  replay bytes, schemas, authority meanings, and M006's shadow-only action
  policy.

This proposal contracts the live surface needed for later M006 evidence. It
does not make a physical producer available, and it does not claim that the
current standalone inspector code in PR #203 already satisfies this contract.
That branch may be adapted as the implementation candidate after this proposal
is accepted and the canonical implementation state is started.

## Ownership

| Concern | Owner |
| --- | --- |
| Runtime-view lifetime, session identity, and image ingress | Existing `RuntimeViewServer` / automation lifecycle |
| Accepted decision snapshot and authority meanings | Existing decision producer and shadow-cycle contracts |
| Transaction correlation, provenance joins, bounded retention, and rendering | Focused D2 decision-view component |
| Optional live URL probe and staged/offline compatibility | Existing `info decision` boundary |
| Host observations | Their attributable producer; D2 does not manufacture telemetry |
| Cross-environment evidence and M006-06/M006-07 status | Current A, PR #202; unchanged by D2 |

## Affected Paths

| Journey | Observable result |
| --- | --- |
| Stage/activate → `info decision` | Actual generation URL or explicit producer-unavailable reason; no startup side effect |
| Automation cycle → `/decision` | The real product seam publishes one correlated transaction containing the cycle's actual image, decision, evidence, and authority facts |
| Fresh → retained → stale/unavailable | Retained sources remain attributable; capture and publication age are distinct; stale or missing sources never become fresh success |
| Slow processing or paused inspection | The exact in-flight/latest completed transaction remains useful within declared bounds, or reports explicit capacity failure; a matched historical view is labeled frozen/stale |
| Stop/restart/restage | Old session URL and images refuse the new generation and do not silently attach to a replacement |
| Publication failure or slow browser reader | Automation and authority outcomes continue; presentation reads use bounded immutable snapshots without worker backpressure |
| Existing offline apply/replay/record | Existing schemas, digest meanings, and no-default-write behavior remain intact |
| Any browser or route write | No actuation, mutation, arbitrary file read, redirect-follow, or authority side effect |

## Adversarial Matrix

| ID | Case | Required result |
| --- | --- | --- |
| D2-01 | Old URL after worker restart, activation edit, run change, restage, or port reuse | Session/generation mismatch or unavailable; never attach to the new producer |
| D2-02 | Current image differs from the decision transaction or a later camera frame is newer | Refuse the association; do not substitute the latest image |
| D2-03 | Slow processing exceeds the capture cache, or a transaction is paused for inspection | Preserve the exact in-flight/latest completed image within declared bounds, or report capacity failure; matched history is visibly frozen/stale and never promoted to current |
| D2-04 | Missing, malformed, future-dated, stale, oversized, or partially published cycle | Stable partial/unavailable response; no cached success, silent fallback, or truncated cycle |
| D2-05 | Retained memory source is missing, ambiguous, evicted, or has unsupported geometry | Preserve raw provenance and report the component unavailable; draw no guessed overlay |
| D2-06 | Host output is absent, malformed, wrong-frame, or nonzero | Keep host observation separate; never infer zero from authorized idle output |
| D2-07 | Publication raises, worker lock is contended, or browser readers are slow | Automation continues with unchanged authority outcome; copying/retention is bounded and network I/O stays outside worker locks |
| D2-08 | Path traversal, URL-shaped image ID, redirect, non-loopback request, or write method | Reject without filesystem, network, decision, or control side effect |
| D2-09 | Poll responses, image loads, and expiry arrive out of order | Install one matched non-expired transaction; discard superseded or expired content |
| D2-10 | Existing stage/info/apply/stream/replay callers | Regression behavior, schemas, and exact replay bytes remain stable |

## External Assumptions

- PR #207's `RuntimeViewServer` remains the supported local hosting boundary.
- The accepted `vehicle_decision_stream_frame_v0` and shadow-authority
  contracts remain the source of decision and proposed/applied meanings.
- The real local automation seam can supply the exact captured image associated
  with its accepted decision cycle; implementation must demonstrate that join
  rather than assume the existing decision stream carries image bytes.
- A D1 physical producer, remote liveness contract, and throughout-zero host
  witness are not assumed. Where they do not exist, D2 reports unavailability
  and deterministic fixtures are labeled as fixtures.
- Source geometry is image-relative and may be rendered only after exact source
  image association; detector IDs do not establish physical identity.
- Retention limits and a representative processing delay are implementation
  choices that must be recorded with the implementation evidence, not frozen
  as speculative proposal constants.

## Non-Goals

- Marking M006-06 or M006-07 `Met`, changing their ownership, or completing
  PR #202's evidence package.
- Implementing D1 physical publication, remote transport, deployment, or host
  telemetry sampling.
- Applying movement, changing proposal policy, changing authority semantics,
  adding prediction/tracking, or making navigation or safety claims.
- Replacing the accepted decision/replay schemas, adding a second server, or
  serving arbitrary files or remote URLs.
- Making the current PR #203 standalone inspector mergeable without review of
  its conformance to this live `RuntimeViewServer` contract.
- Milestone closeout or workflow-tooling changes.

## File Impact

Expected implementation paths are the existing automation and
runtime-view/perception ingress, the decision-info boundary, a focused D2
projection/retention component, the live decision page asset, and their
focused deterministic/browser tests. This proposal adds no product code,
runtime artifact, or implementation test.

## Validation Plan

- Begin with one real `stage`/automation/`RuntimeViewServer` happy-path cycle.
  An operator must be able to identify the selected proposal, decisive source
  frame, and whether anything was applied; a standalone inspector or fixture
  does not count for this seam.
- Verify actual current-image and retained-source joins, session and
  transaction mismatches, stale/invalid publication, explicit retention
  limits, no-side-effect URL probing, and separate proposed/authorized/host
  facts.
- Exercise representative slow processing, paused inspection, restart,
  missing-source, publication-error, slow-reader, image-failure, expiry, and
  out-of-order response boundaries; confirm no stale overlay survives and
  record capture age separately from publication age.
- Run the focused existing workflow, decision, and browser/view checks, and
  verify staged/offline `info` and replay compatibility.
- Keep any fixture/browser receipt scoped to local deterministic behavior and
  do not treat it as Chase/PiRacer evidence for M006-06/M006-07.

## Expected Handoff

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "D2 live decision view accepted in PR #{pr}; generation, image, provenance, authority, and no-side-effect checks passed.",
  "criterion_updates": {
    "M006-08": {
      "status": "Unmet",
      "evidence": "D2 live decision view is accepted as closeout input; M006 closeout judgment remains outstanding."
    }
  },
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "D2 completion does not promote another frontier or close M006.",
    "revisit_when": "M006 evidence completion or an explicit closeout proposal establishes the next judgment."
  }
}
```
