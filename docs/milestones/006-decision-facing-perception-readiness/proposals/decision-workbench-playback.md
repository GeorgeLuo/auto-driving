# Proposal: Decision playback workbench

## Milestone Context

- Milestone: M006 — Decision-Facing Perception Readiness
- Base branch: `milestone/006-decision-facing-perception-readiness`
- Proposal branch: `m006/decision-workbench-playback-proposal`
- Implementation branch: `m006/decision-workbench-playback`
- Frontier: Decision playback workbench
- Proposal artifact: `docs/milestones/006-decision-facing-perception-readiness/proposals/decision-workbench-playback.md`
- Related surfaces: accepted M006 decision replay/apply and the closed M008 image-replay workbench

This is a new parallel frontier. It does not alter the current cross-environment
evidence frontier or D2 live decision-view implementation.

## Review Kind

Behavioral feature slice

## Review Question

Can the local workbench replay a bounded recorded decision input and present a
frame-correlated analysis of proposals, selection, evidence, and shadow authority
without changing decision semantics, depending on a live producer, or making
proposed movement appear applied?

## Operator Want

- Want: Use the workbench as the playback and analysis surface for a captured
  decision run, with the image/frame timeline as the primary navigation object
  and the decision explanation available at the selected frame.
- Reject if: Playback loses frame/image correlation, silently substitutes a later
  source, changes the existing decision result, requires a live Chase/PiRacer
  producer, or presents proposed intent as applied control.

## Evidence rendering

- Derived HTML: skip
- Evidence directory: None
- Skip reason: The proposal phase mints no sealed machine-readable signal. Any
  implementation or operator evidence will be scoped after the contract is
  finalized.

## Scope

### In Scope

- One offline playback mode in the existing local workbench.
- A bounded source adapter for an existing `automa_decision_apply_sequence_v0`
  input, or an existing recorded run that losslessly contains that sequence and
  its optional source images.
- Server-owned per-frame state exposing frame identity/timestamp, the correlated
  image when present, observation and memory inputs, proposal candidates,
  selected proposal/contributions, source references, freshness/lifecycle state,
  proposed command, authorized shadow-idle output, and
  `proposed_applied=false`.
- Workbench controls for loading, bounded replay, pause/seek, and selecting a
  frame for inspection, reusing the existing workbench lifecycle where it fits.
- A deterministic playback check against the existing decision engine and replay
  semantics without introducing a second decision authority.
- A small local prototype during proposal review to settle the source-bundle and
  projection details before the proposal receives its exact-head acceptance
  review.

### Out Of Scope

- Live polling, remote transport, or a workbench dependency on a running Chase
  or PiRacer producer.
- Changes to `RuntimeViewServer`, `/decision`, `/perception`, or `/memory` live
  routes; those remain the CLI live-view surface.
- New proposal policy, selector behavior, authority semantics, perception or
  memory behavior, or a replacement decision schema.
- Movement, vehicle-mode changes, simulator reconfiguration, physical evidence,
  navigation claims, video ingestion, multi-run comparison, or durable history.
- Making an exact-frame HTML artifact or digest-only payload stand in for missing
  source identity or image correlation.
- Product code, implementation tests, runtime artifacts, or evidence records in
  this proposal PR.

## Proposed Contract

### Playback boundary

The workbench receives a bounded offline decision input retaining the existing
sequence identity (`frame_id`, `frame_index`, and `timestamp_ms`) and the
normalized observation, observation error, and memory values consumed by the
accepted shadow engine. If source images are supplied, each image is joined by
exact frame identity. A missing or mismatched image is unavailable, never a
latest-directory image or a live camera frame.

The workbench does not query a live vehicle, automation worker, or runtime-view
publisher while loading or playing back. Engine configuration is frozen or
explicitly selected at the offline boundary before replay starts; a hidden live
activation lookup is not allowed. The prototype selects the smallest existing
configuration source that satisfies this rule.

### Decision meaning and presentation

The accepted `shadow-proposals` engine remains the only decision authority.
Playback invokes or consumes its existing cycle result and presents proposal
lifecycle, selection, source references, freshness, and authority without
rewriting their meanings. The page distinguishes proposed command, selected
proposal/contributions, authorized shadow-only idle, and any recorded host
observation. It shows `proposed_applied=false` and never infers host zero output
from authorized idle or from an absent host field.

The image/frame timeline is the playback navigation surface. Selecting a frame
shows server-produced decision state and exact inputs, with explicit unavailable
states for absent images, missing memory, failed observation, or incomplete host
observations. The browser remains a presentation client and does not parse the
source bundle, run the engine, derive references, or maintain a second history.

### Proposal-review prototype gate

Before the contract review receipt is submitted, a disposable local prototype
will test only these bounded questions:

1. Whether the smallest usable input is a directory with `sequence.json` and
   source frames, the output of `decision apply --record`, or a minimal adapter.
2. Which existing cycle/stream fields make the explanation useful without
   duplicating the full raw payload or losing source references.
3. Whether packaged shadow configuration or a frozen activation snapshot is the
   smallest offline configuration boundary.

The selected answer, rejected alternatives, and any contract delta will be
written into this proposal before acceptance. The prototype is contract
discovery, not implementation acceptance: it uses a throwaway worktree or local
scratch files, creates no canonical evidence, and contributes no product code
to this proposal PR. If it shows the question or authority boundary is wrong,
the proposal remains in review until corrected.

## Ownership

| Concern | Owner |
| --- | --- |
| Offline source normalization and run lifecycle | Existing workbench source/runner boundary |
| Decision semantics, selection, and authority | Accepted `shadow-proposals` engine and M006 decision contracts |
| Playback state projection and frame selection | Existing workbench server and page |
| Exact source-image correlation | Offline playback source adapter; no live fallback |
| Live decision display | Existing `RuntimeViewServer` and D2 `/decision`; unchanged |
| Cross-environment evidence | Current evidence frontier; unchanged by this unit |

## Affected Paths

| Journey | Observable result |
| --- | --- |
| Load recorded input | Bounded sequence identity and source/error state are validated before playback |
| Play, pause, seek, or select | Selected frame retains its own decision, image, and provenance; no later frame is substituted |
| Inspect decision state | Existing lifecycle, freshness, proposal, source, and authority meanings remain visible |
| Repeat playback | Existing deterministic decision semantics and digest-compatible results remain unchanged |
| Use live views | `RuntimeViewServer` remains the only live decision-view path and is not coupled to playback |

## Adversarial Matrix

| ID | Case | Required result |
| --- | --- | --- |
| DWB-01 | Missing, malformed, oversized, or wrong-schema source | Reject before engine startup with a bounded error; do not partially play or choose another source |
| DWB-02 | Duplicate, reordered, or inconsistent frame identity | Reject the input or frame; do not join by position when identity disagrees |
| DWB-03 | Missing, wrong-frame, unreadable, or later image | Show unavailable or reject the association; never substitute another image or live capture |
| DWB-04 | Live producer or activation unavailable | Valid frozen playback remains independent; no hidden live lookup or fabricated identity |
| DWB-05 | Missing, invalid, or changing configuration | Fail closed and retain frozen configuration identity; no mid-run policy change |
| DWB-06 | Absent, failed, stale, or incomplete observation/memory | Preserve accepted lifecycle and expose the reason; do not invent evidence or command |
| DWB-07 | Nonzero proposal with shadow idle authority | Show proposed intent separately with `proposed_applied=false`; never label it applied |
| DWB-08 | Seek bypasses earlier frames or runs concurrently with live view | Use bounded recorded inputs; prevent state leakage and keep live/playback identities separate |
| DWB-09 | Browser sends paths, commands, or write methods | Reject without filesystem traversal, live requests, engine mutation, or vehicle control |
| DWB-10 | Repeated playback differs | Report deterministic mismatch; visual similarity is not equality proof |

## External Assumptions

- Accepted M006 decision contracts and the `shadow-proposals` engine remain
  available without reopening policy or authority review.
- The closed M008 workbench implementation remains the supported local
  presentation and loopback boundary, with its observation-only lifecycle.
- `automa_decision_apply_sequence_v0` remains the supported bounded decision
  input and its observation/memory values remain decodable without schema change.
- A playback demonstration can use stable frame identity and, when visual
  correlation is claimed, source-image bytes for those exact frames.
- No Chase or PiRacer session is required for implementation acceptance.

## Non-Goals

- Completing or reworking PR #202's Chase/PiRacer evidence.
- Repairing or extending D2's live `RuntimeViewServer` contract.
- Reopening M008 or changing its closed criteria, assessment, or evidence.
- Treating playback as live evidence or proof of physical readiness.
- Changing engine inputs, proposal policy, freshness rules, selector, authority,
  or command-application semantics.
- Adding a second workbench execution authority or a second live decision page.

## File Impact

### Proposal phase

- Create this proposal artifact.
- Add this named frontier under the M006 `Parallel Frontiers` registry and
  record its `proposal_in_review` history row in the canonical M006 `plan.md`.
- Regenerate the canonical M006 `plan.html`.
- Do not add product code, tests, runtime artifacts, evidence, or prototype
  output to this PR.

### Later implementation phase

Expected implementation paths are the existing workbench contract, source,
runner, server, and page modules, plus focused decision/workbench tests and any
small shared replay adapter required by the finalized contract. The
implementation must not modify live `RuntimeViewServer` behavior.

## Validation Plan

### Proposal PR

Run only proposal/documentation checks:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py status \
  --plan docs/milestones/006-decision-facing-perception-readiness/plan.md
PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py validate \
  docs/milestones/006-decision-facing-perception-readiness/plan.md
python3 docs/render_markdown.py --check
git diff --check
```

The proposal PR must contain only this proposal, the canonical M006 plan, and
the generated plan HTML. It must have one exact-head contract receipt before
merge. The prototype runs separately before that receipt and its findings are
reconciled into this artifact.

### Later implementation

After proposal acceptance, validate the happy path first: load a bounded
recorded sequence, inspect an image-correlated frame, play/pause/seek, inspect
proposal/source/authority fields, and repeat playback for deterministic equality.
Then exercise the accepted adversarial cases and existing workbench/decision
regression checks. No live vehicle or physical evidence is required.

## Expected Handoff

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Decision playback workbench is accepted in PR #{pr}; bounded recorded inputs, correlated frame analysis, existing shadow authority semantics, and separation from live CLI views are preserved.",
  "criterion_updates": {
    "M006-05": {
      "status": "Met",
      "evidence": "Decision playback extends the accepted replay/view surface with offline frame-correlated workbench analysis while preserving existing live-view and shadow-authority semantics."
    }
  },
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "Completion does not promote another frontier or close M006.",
    "revisit_when": "M006 evidence completion or an explicit closeout proposal establishes the next judgment."
  }
}
```
