# Proposal: Perception-memory workbench journey

| Field | Value |
| --- | --- |
| Milestone | 008 Perception-Memory Workbench Feasibility |
| Frontier | Perception-memory workbench journey |
| Review kind | Behavioral feature slice |
| Proposal branch | `m008/perception-memory-workbench-proposal` |
| Implementation branch | `m008/perception-memory-workbench` |
| Exit criteria | M008-01, M008-02, M008-04 |

## Review Kind

Behavioral feature slice

## Review Question

Can an operator use a local workbench to replay a supported ordered image source
through the existing perception-to-`Observation`-to-memory pipeline, inspect
real capture overlays and memory effects, and control the bounded replay without
shell commands, mock data, hidden simulator changes, or a second execution
authority?

## Proposed Contract

### Bounded composition assessment and selected slice

The implementation creates the durable assessment at
`docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md`.
It is the single evolving M008 assessment for M008-01, M008-02, and M008-07.
It compares a small set of coherent command/pipeline compositions—not a page
for every CLI leaf and not a workflow graph—and records this disposition.

| Candidate / current surface | Disposition | Assessment conclusion |
| --- | --- | --- |
| Ordered image directory → existing perception-apply components → `Observation` → bounded memory | **Selected** as `workbench.image_replay.v1` | This is the smallest useful source-agnostic journey: inspect a real capture with perception overlays, advance its ordered feed, and see the resulting memory ledger change. It is deterministic and needs no vehicle, simulator, or worker. |
| Existing `automa_memory_observation_sequence_v0` → `memory replay` | Supported diagnostic/reference input, not the primary journey | It is a useful deterministic memory check but bypasses perception and has no capture/overlay story. It verifies the memory half of the common pipeline rather than replacing the selected visual journey. |
| Image directory → `perception apply` / candidate comparison | Deferred | The repository can already apply or compare perception across directory images, but a comparison matrix is a distinct operator question and does not show memory behavior. Do not promote algorithm selection into the first workbench. |
| M007 live continuity (`automation run`, live view, memory lifecycle) | Later feed adapter and evidence path | It remains useful once the workbench has a stable replay contract, but a live worker makes first-use and deterministic iteration depend on simulator availability. It is not the core input contract. |
| Video file | Later feed adapter | No owned CLI video decoder, frame-timestamp policy, or source identity contract exists yet. The normalized-feed seam makes it additive work later; it is not implemented here. |
| Existing perception `/` and memory `/memory` pages | Visual and semantic reuse input | Their overlay and ledger meanings inform the workbench presentation. The workbench consumes the same server-side perception, `Observation`, and memory values rather than copying browser business logic. |

The assessment names the source paths, current commands, composition seam,
inputs, signal quality, side effects, recovery, cleanup, and workbench fit for
each row. It distinguishes product gaps from observations and gives every
retained gap a disposition: later product decision, external owner, or no
follow-up. It is not an all-leaf CLI inventory or a second M007 evidence run.

### One shared sequence and structured state

Implementation adds one server-side owner for `workbench.image_replay.v1`.
The owner shares the same replay execution and structured state between:

- a local `automa vehicles workbench replay` CLI surface, with complete
  machine-readable output; and
- the loopback workbench API used by the page.

The common input is a normalized, ordered feed of frames. Every frame carries
source identity, frame id, sequence index, timestamp, image payload/reference,
and optional dropout/absence annotation. The first adapter accepts a bounded
directory of supported images: it honors an available manifest order, otherwise
uses documented lexical image order, derives stable frame identity from the
source and ordered position, and preserves an explicit source identity. It
rejects empty or unsupported input, duplicate frame identity, invalid ordering,
or declared limits before a run starts. Its state model does not assume that a
future video, live vehicle, or simulator adapter exists.

For each non-absent frame the runner creates the same `SensorSnapshot` and
request shape used by existing perception application, runs the selected
packaged perception implementation, converts its `PerceptionText` through the
existing `Observation` path, and updates an isolated bounded-memory stage
through the normal decision-cycle seam. An absence annotation creates the
declared no-input observation path; it does not fabricate a perception result.
The runner exposes the current frame, perception, observation, memory snapshot,
and transition reason as structured state. It does not route replay through a
browser-created observation, raw-JSON shortcut, or separate memory reducer.

The first slice fixes `lightweight_observer` perception and `bounded_evidence`
memory. The page cannot request an arbitrary argv, algorithm, mapper setting,
source adapter, simulator operation, or memory implementation. Those semantic
choices, the supported image-directory input, and the perception →
`Observation` → memory authority are fixed; presentation and the bounded replay
interface are not a pixel or command-spelling freeze.

### Useful visual workbench and controls

The long-lived loopback page presents the selected capture as its primary visual
surface. It renders server-produced perception things and signals as overlays
on the current image, with frame id, source identity, timestamp, processing
state, and an accessible textual summary. A timeline/progress surface and
memory ledger make each processed frame's effect legible; selecting a memory
record shows its server-provided retained evidence and source-frame linkage.
Raw JSON, stdout, a record path, or a transcript alone is never the display.

Initial controls are deliberately small and scalable:

- choose and validate one image-directory source through the declared
  server-side adapter;
- start, pause, resume, and step the ordered replay; reset isolated memory to
  its initial state before replaying again;
- choose a supported playback cadence; and
- show or hide presentation overlays and select an existing memory record
  without changing the pipeline result.

The page only requests declared runner actions and renders structured state.
It does not decode a source, derive overlays, construct an `Observation`,
mutate memory, or invoke a CLI command itself. CLI human output names the same
source, progress, outcome, failure, recovery, and cleanup facts as the API;
`--json` is the machine surface. A video or live-feed adapter later maps to the
same normalized-feed and state contract, never a parallel page workflow.

### Bounded POC-completion envelope

The implementation is expected to refine the visual workbench and may add
small supporting replay actions, CLI flags/subcommands, or internal sequence
steps that are not enumerated above when they make the one selected journey
workable and legible. Such an addition remains in this review unit only when it
satisfies every condition below:

1. It serves the same operator question: replay one supported ordered image
   source, inspect its real perception overlays and memory effects, and control
   that replay.
2. It keeps the image-directory adapter, fixed packaged perception and memory
   choices, normalized-feed contract, and server-owned perception →
   `Observation` → memory pipeline intact.
3. It is reachable through the same `automa vehicles workbench replay` command
   family and loopback state contract; it adds neither a second execution
   authority nor a browser-only feature.
4. It remains observation-only, introduces no vehicle/simulator/worker/Metrics
   dependency or mutation, and has a focused deterministic test plus an entry
   in the durable assessment describing why it was needed for the POC.

Permitted examples include refining overlay/ledger layout and accessibility,
adding a bounded seek or frame-navigation control, adding a replay-command
option that exposes an already-supported state/action, or extracting a
CLI-owned helper needed to traverse the same pipeline. They are implementation
choices in one command family, not independent product features.

An addition is outside this envelope if it adds video or live ingestion, a new
algorithm or comparison question, a different source/recording contract,
changed perception/observation/memory semantics, external authority, or a
second operator goal. It requires a later proposal rather than an expanding
implementation review. The assessment records every envelope addition and
every deferred request so reviewers can decide it by these conditions instead
of reopening the primary question.

### POC finish and evidence boundary

The implementation's deliverable is a POC-ready workbench: a supported source
can be replayed end to end; its server-produced capture overlays, ordered
progress, memory effect, failure/recovery state, and declared controls are
legible; the CLI and page share the same authoritative state; and the page
remains usable for another run. It does not by itself claim an operator finds
the POC satisfactory.

The queued `Replay workbench POC acceptance` evidence unit performs one guided
operator demonstration against those conditions. It records either one
affirmative minimal-usefulness judgment or a named blocker. A missing stated
condition returns as a repair; a POC-envelope addition is evaluated against the
four conditions above; any other request is a separately proposed frontier.
The evidence session is therefore an acceptance decision, not an unbounded
visual-polish queue.

### Long-lived, local lifecycle boundary

The server is loopback-only, remains available across completed, failed,
paused, reset, and cancelled runs, and returns the last terminal structured
state after refresh. It uses plain HTML, CSS, and JavaScript; React, remote
hosting, authentication, and a generic workflow framework are out of scope.

The structured state has at least server identity, selected sequence id, run id,
source identity, adapter kind, current frame/position, phase, allowed action,
human-visible summary, machine detail, perception/observation/memory
references, failure boundary, recovery action, and terminal cleanup outcome.
The workbench's isolated mapper/memory state is discarded or reset at the end
of a run; source files are read-only. No vehicle activation, worker, simulator
configuration, Metrics UI operation, movement, control authority, or default
recording is part of this slice.

The implementation adds only this one CLI-useful replay command family, common
normalized-feed seam, and visual workbench journey, including only the bounded
POC-completion additions defined above. It does not redefine the M007 catalog
or treat historical M007 results as current M008 acceptance. The queued
operator-acceptance/evidence unit owns M008-03, M008-05, and M008-06 proof;
this unit supplies the deterministic visual slice and one-source pipeline
boundary needed for that review.

## Ownership

| Concern | Owner | Required result |
| --- | --- | --- |
| Feed normalization, replay ordering, run identity, transition/state contract, and isolated reset/cleanup | Workbench replay runner | One `workbench.image_replay.v1` composition is shared by CLI and HTTP requests. |
| CLI parse, human output, and `--json` machine boundary | `cli/automa_cli/app.py` plus workbench command module | CLI cannot diverge from the browser's sequence or expose arbitrary execution. |
| Page rendering and loopback routes | Workbench loopback server and plain page | Render server-owned state and named next actions; no business-logic duplication in JavaScript. |
| Perception → `Observation` → memory pipeline and existing views | Existing CLI and decision-cycle owners | Reuse public/function contracts and visual semantics; do not fork business logic. |
| POC-completion additions and residual disposition | Workbench replay runner with the durable assessment | Admit only additions meeting all four envelope conditions; record each addition or defer it as a later frontier. |
| Candidate assessment and gap disposition | M008 assessment artifact | Preserve the bounded source-grounded selection and residuals through closeout. |

## Affected Paths

| Path | Purpose |
| --- | --- |
| `cli/automa_cli/app.py` | Register the bounded workbench CLI surface. |
| `cli/automa_cli/workbench.py` | Shared sequence runner, loopback server/API, and structured state owner. |
| `cli/automa_cli/workbench.html` | Plain local workbench presentation consuming the API contract. |
| `cli/automa_cli/perception_runs.py`, `memory.py`, `perception_view.py`, and the decision-cycle seam | Reuse or extract only the shared image, perception, observation, memory, and visual presentation contracts required by replay. |
| `tests/cli/test_workbench.py` and focused perception/memory replay tests | Deterministic feed, pipeline, CLI/API parity, visual-state, control, and refusal coverage. |
| `docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md` | Durable M008 bounded assessment, sequence contract, and gap disposition. |
| `docs/milestones/008-cli-decision-workbench/proposals/perception-memory-workbench.md` | This reviewed contract. |
| `docs/milestones/008-cli-decision-workbench/plan.md` and `plan.html` | Workflow transition and later accepted-unit handoff only. |

No product implementation, tests of unimplemented behavior, generated runtime
artifacts, or assessment result is included in this proposal PR.

## Adversarial Matrix

| Attempted bypass or failure | Required behavior |
| --- | --- |
| Browser sends arbitrary argv, an unknown sequence, a different algorithm, or an extra option | Reject before lifecycle work. The server exposes only the selected sequence and fixed supported configuration. |
| CLI and HTTP take different execution paths or report incompatible outcomes | Focused tests prove both invoke the same runner/state contract; divergence fails review. |
| Source is empty, contains unsupported/nonregular images, exceeds a declared bound, has duplicate frame identity, or has ambiguous ordering | Refuse before mapper or memory work and name the offending source boundary. |
| Source path attempts traversal or the browser asks for an unregistered adapter | The server validates its declared local source boundary and rejects it; the browser never gains arbitrary filesystem or adapter access. |
| An absence/dropout annotation, corrupted frame, mapper failure, or memory failure occurs | Do not fabricate perception. Record the frame/source boundary, terminal or recoverable state, and next action while the page remains available. |
| Pause, resume, step, or reset races with replay | Serialize runner actions; no frame is processed twice, reset reinitializes isolated memory deterministically, and stale actions report their run identity. |
| A stale run/frame/view result is rendered as current | Bind state and page updates to active run and frame identity; expose stale/unavailable status rather than current success. |
| Page refreshes after a run ends | Long-lived server returns latest terminal structured state and remains able to start a new declared run. |
| A video or live source is presented as though it were an image-directory adapter | Reject the unsupported adapter explicitly; do not silently change timestamp, ordering, or source-identity semantics. |
| Implementation reconfigures a vehicle/simulator, starts a worker, sends movement/control, uses Metrics, or turns on recording to make the slice work | Reject as out of contract; deterministic tests cover the permitted replay operations. |
| Existing `/` or `/memory` pages are copied into a second feature definition | Reuse state meaning through the common server contract or document the single adaptation boundary; duplicate lifecycle logic fails review. |
| Browser-produced overlays or ledger entries disagree with the processed frame | Render only server-produced perception/memory values and presentation shape; browser code cannot substitute pipeline results. |
| A purported POC refinement adds a new source, semantic pipeline, external authority, or a second operator goal | Reject it from this implementation review and record the named later-proposal disposition. |
| Generated report path, stdout, or JSON is the only result signal | Fail review: page and CLI provide concise progress, outcome, cleanup, and recovery signals. |

## External Assumptions

- The host can bind a local loopback server and open its URL. Browser-launch
  failure may leave a manually usable URL, but it does not make page health or
  replay processing successful.
- The chosen local image directory is readable and its files remain available
  for the declared replay. A missing or unreadable source is visible failure,
  not authority to substitute another source.
- `lightweight_observer` and `bounded_evidence` remain installed packaged
  options. A missing option is a named blocked prerequisite, not a fallback.
- Current image-directory perception application and the decision-cycle
  perception-to-`Observation` seam remain reusable through owned interfaces.
  Their removal is a named integration gap, not a reason to create browser-side
  substitutes.

## Non-Goals

- Full M006 shadow-decision evidence, decision-engine redesign, movement
  authority, or a live vehicle/simulator source adapter.
- Video decoding, video timestamp/identity policy, live-feed ingestion,
  arbitrary recorded-source upload, algorithm selection, a workflow builder,
  or a second slice.
- React adoption, a frontend design system, remote hosting, authentication, or
  a Metrics UI redesign.
- Rewriting perception/memory business logic in JavaScript, promoting a
  generated report as the visual result, or claiming historical M007 runs prove
  M008.
- An operator-acceptance verdict or the complete repeated-run/failure evidence
  needed to mark M008-03, M008-05, or M008-06 `Met`.

## File Impact

### Proposal PR

- `docs/milestones/008-cli-decision-workbench/proposals/perception-memory-workbench.md`
- `docs/milestones/008-cli-decision-workbench/plan.md`
- `docs/milestones/008-cli-decision-workbench/plan.html` (generated)

### Implementation PR after proposal acceptance

- The bounded code, test, and assessment paths listed in **Affected Paths**.
- No generated runtime session, raw stdout transcript, or live acceptance
  artifact unless a later evidence proposal explicitly contracts it.

## Validation Plan

### Proposal PR

- `python3 docs/milestones/workflow.py validate docs/milestones/008-cli-decision-workbench/plan.md`
- `python3 docs/milestones/workflow.py validate-pr` against this branch and a
  completed proposal PR body.
- `python3 docs/render_markdown.py --check`
- `git diff --check`

### Implementation PR after proposal acceptance

Deterministic coverage must prove:

- parser help and both CLI modes use the same runner; `--json` is structured
  and contains no alternate replay authority;
- API rejects malformed/unknown requests and never accepts raw argv;
- each visual/control or CLI addition claimed as POC completion is recorded in
  the assessment and demonstrably meets the envelope's same-journey,
  same-authority, fixed-semantics, and observation-only conditions;
- image-directory normalization honors manifest order when present and
  documented lexical order otherwise; it produces stable source/frame identity
  and rejects empty, invalid, ambiguous, duplicate, and over-limit input before
  invoking perception;
- identical bounded sources produce the same ordered frame progression and
  memory state progression, while absence annotations take only the declared
  no-input observation path;
- each processed image takes the existing `SensorSnapshot` → perception →
  `Observation` → bounded-memory path; browser code neither constructs an
  observation nor mutates memory;
- the selected CLI/API replay lifecycle has ordered state transitions and
  visible source/progress/result/failure/recovery/cleanup summaries, without
  mock result sources;
- corrupted input, mapper failure, memory failure, cancellation, stale action,
  pause/resume/step race, and reset each have declared state and do not claim
  successful completion;
- no permitted replay path starts a worker, mutates a vehicle activation,
  prepares a simulator, invokes Metrics, sends movement/control, or enables
  recording;
- server/page persistence returns the latest terminal state after a run and
  allows the next declared replay without relaunching the server;
- page/API state is run- and frame-identity aware, renders stale/unavailable
  information instead of a current success, and serves overlays/ledger values
  from the server-produced pipeline state; and
- perception/memory visual semantics are consumed through the documented
  server-side adaptation rather than copied lifecycle logic.

Run the focused CLI suite, affected adjacent CLI tests, normal default suite,
plan validation, Markdown rendering check, and `git diff --check`. The later
evidence unit—not this implementation PR—performs operator acceptance and live
lifecycle proof for the remaining criteria.

## Expected Handoff

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "PR #{pr} selects workbench.image_replay.v1 through the durable M008 assessment, composes the existing image-perception, Observation, and bounded-memory seams behind one shared server-side CLI/workbench runner, and documents the selected visual page adaptation boundary.",
  "criterion_updates": {
    "M008-01": {
      "status": "Met",
      "evidence": "PR #{pr} records the bounded assessment of the relevant M007 perception-memory candidates, current CLI capabilities, and existing loopback pages."
    },
    "M008-02": {
      "status": "Met",
      "evidence": "PR #{pr} selects workbench.image_replay.v1 as the one CLI-useful reusable composed journey with declared feed inputs, overlays, controls, safety, recovery, cleanup, and a bounded POC-completion envelope."
    },
    "M008-04": {
      "status": "Met",
      "evidence": "PR #{pr} establishes the selected slice's one-source adaptation boundary between existing CLI-launched perception/memory pages and the workbench."
    }
  },
  "risk_remove": [
    "M007 sequences were accepted as CLI journeys, not proven as workbench features",
    "Frontend pieces used by the selected slice may be split between long-running perception-memory routes and generated per-run review pages",
    "The smallest reliable CLI-to-workbench invocation boundary is not yet chosen"
  ],
  "risk_upsert": [
    {
      "risk": "The selected workbench has not yet received recorded operator acceptance across repeated replay runs and declared source or processing failures",
      "consequence": "The deterministic visual implementation can establish the shared sequence and page boundary without proving M008-03, M008-05, or M008-06",
      "resolution": "Use the durable assessment and implementation contract to select one bounded operator-evidence review unit, including any future video or live-adapter decision, when the operator is ready."
    }
  ],
  "next_frontier": {
    "state": "none",
    "reason": "The plan already queues the bounded Replay workbench POC acceptance evidence unit after implementation; the handoff must not invent another successor.",
    "revisit_when": "After the queued evidence unit, select closeout or a separately proposed residual only if its acceptance decision or durable assessment requires it."
  }
}
```

This success handoff applies only when the implementation delivers the shared
runner, assessment, and selected visual page-alignment boundary without an
unresolved source-state or processing-cleanup defect. A conclusive blocked or
failed implementation retains the frontier and records the actual limitation
rather than promoting these criteria.
