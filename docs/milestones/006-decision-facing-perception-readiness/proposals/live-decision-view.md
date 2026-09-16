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

The accepted decision cycle remains the sole producer of D2 data. The existing
`RuntimeViewServer` owns a read-only `/decision` page and generation-bound data
and image routes beside its existing perception and memory views.

- `info decision` reports a decision-view URL only after a bounded probe of the
  actual loopback producer. It never starts a worker, captures a frame, or
  writes a runtime artifact.
- The page renders the accepted cycle, including its selected proposal,
  contributions, source references, proposed command, authorized idle output,
  and `proposed_applied=false`. Host output is a separate observed or
  explicitly unavailable panel.
- The current image is the image captured for that exact decision cycle, not
  the fastest later camera frame. Retained evidence is shown only when its
  generation, frame, observation, provenance, and supported image-relative
  geometry agree; missing or evicted sources are unavailable rather than
  guessed.
- Every read is bound to the producer's vehicle, run, generation, activation,
  frame, and freshness identity. Restarts, activation changes, stale or
  malformed publications, and port reuse refuse the old view.
- Routes are GET/HEAD-only, loopback-scoped, no-store, and cannot read a file
  path, follow a supplied URL, start a decision, or enter the authority path.
  A missing or mismatched component produces a bounded partial/unavailable
  response with a stable reason.
- The D2 implementation must preserve existing offline decision commands,
  replay bytes, authority meanings, and M006's shadow-only action policy.

This proposal contracts the live surface needed for later M006 evidence. It
does not make a physical producer available, and it does not claim that the
current standalone inspector code in PR #203 already satisfies this contract.
That branch may be adapted as the implementation candidate after this proposal
is accepted and the canonical implementation state is started.

## Ownership

| Concern | Owner |
| --- | --- |
| Runtime view lifetime, generation identity, and image ingress | Existing `RuntimeViewServer` / automation lifecycle |
| Accepted decision snapshot and authority meanings | Existing decision producer and shadow-cycle contracts |
| Decision projection, provenance joins, and bounded retention | Focused D2 decision-view component |
| CLI URL probe and human-facing availability | Existing `info decision` boundary |
| Cross-environment evidence and M006-06/M006-07 status | Current A, PR #202; unchanged by D2 |

## Affected Paths

| Journey | Observable result |
| --- | --- |
| Stage/activate → `info decision` | Actual generation URL or explicit producer-unavailable reason; no startup side effect |
| Automation cycle → `/decision` | The cycle's actual image, decision, evidence, and authority facts remain correlated |
| Fresh → retained → stale/unavailable | Retained sources remain attributable; stale or missing sources never become fresh success |
| Stop/restart/restage | Old URL and images refuse the new generation |
| Existing offline apply/replay/record | Existing schemas, digest meanings, and no-default-write behavior remain intact |
| Any browser or route write | No actuation, mutation, arbitrary file read, redirect-follow, or authority side effect |

## Adversarial Matrix

| ID | Case | Required result |
| --- | --- | --- |
| D2-01 | Old URL after worker restart, activation edit, run change, or port reuse | Generation mismatch or unavailable; never attach to the new producer |
| D2-02 | Current image differs from the decision frame or a later camera frame is newer | Refuse the association; do not substitute the latest image |
| D2-03 | Missing, malformed, future-dated, stale, or oversized cycle | Stable unavailable response; no cached success or truncated cycle |
| D2-04 | Retained memory source is missing, ambiguous, evicted, or has unsupported geometry | Preserve raw provenance and report the component unavailable; draw no guessed overlay |
| D2-05 | Host output is absent, malformed, wrong-frame, or nonzero | Keep host observation separate; never infer zero from authorized idle output |
| D2-06 | Path traversal, URL-shaped image ID, redirect, non-loopback request, or write method | Reject without filesystem, network, decision, or control side effect |
| D2-07 | Poll responses, image loads, and expiry arrive out of order | Install one matched non-expired transaction; discard superseded or expired content |
| D2-08 | Existing stage/info/apply/stream/replay callers | Regression behavior and exact replay bytes remain stable |

## External Assumptions

- PR #207's `RuntimeViewServer` remains the supported local hosting boundary.
- The accepted `vehicle_decision_stream_frame_v0` and shadow-authority
  contracts remain the source of decision and proposed/applied meanings.
- A D1 physical producer, remote liveness contract, and throughout-zero host
  witness are not assumed. Where they do not exist, D2 reports unavailability
  and deterministic fixtures are labeled as fixtures.
- Source geometry is image-relative and may be rendered only after exact source
  image association; detector IDs do not establish physical identity.

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

Expected implementation paths are the existing runtime-view/automation and
decision-info boundaries, a focused D2 projection/retention component, the
live decision page asset, and their deterministic/browser tests. This proposal
adds no product code, runtime artifact, or implementation test.

## Validation Plan

- Validate the canonical plan and proposal artifact, then run existing focused
  workflow and decision test suites.
- Exercise the public `stage`/`info`/automation path with a deterministic local
  producer and compare the served snapshot with the producer's accepted cycle.
- Verify actual current-image and retained-source joins, generation mismatch,
  stale/invalid publication, bounded retention, no-side-effect URL probing,
  and separate proposed/authorized/host facts.
- Exercise the page at resize, restart, expiry, image failure, and out-of-order
  response boundaries; confirm no stale overlay survives.
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
