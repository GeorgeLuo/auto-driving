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

Can an operator run the accepted M007 perception-to-memory lifecycle sequence
from a local workbench and inspect the real CLI lifecycle, results, and cleanup
without shell commands, mock data, hidden simulator changes, or a second
execution authority?

## Proposed Contract

### Bounded composition assessment and selected slice

The implementation creates the durable assessment at
`docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md`.
It is the single evolving M008 assessment for M008-01, M008-02, and M008-07.
It records source revision and assesses small compositions of existing M007
sequences and CLI commands—not each command as a page control and not an
unbounded workflow graph. It must record the following disposition.

| Candidate / current surface | Disposition | Assessment conclusion |
| --- | --- | --- |
| M007 `continuity.memory_lifecycle` composed with current CLI `update perception`, long-running observation-only `automation run`, and current perception/memory presentation | **Selected** as `workbench.perception_memory_lifecycle.v1` | This is the smallest coherent journey: make live perception and memory available, inspect current perception and memory state, verify the bounded lifecycle, and clean up. The components share one vehicle, observation-only authority, and a single operator question. |
| M007 `continuity.offline_perception`: recorded source, then apply variants | Deferred | It requires a generated record directory and asks an offline comparison question; it does not complete a live perception-to-memory journey. Do not turn its directory picker or algorithm matrix into a workbench feature in this unit. |
| M007 `continuity.live_config_swap`: restage, observe a perception view, restore | Retained only as a prerequisite/recovery pattern | Its useful confirmation is perception readiness and restoration, not memory lifecycle inspection. Its restorable activation treatment informs this slice's cleanup but is not separately exposed as a feature. |
| Existing loopback `/` perception view and `/memory` map | Reuse inputs, not independent authorities | They render current correlated publication and memory records, but their server follows a worker generation. The workbench reuses their signal meanings through shared server-side state and never makes copied browser logic authoritative. |

The assessment compares the selected composition with the standalone lifecycle
and the two rejected/deferred candidates against operator usefulness,
composability, required inputs and state, signal quality, CLI completeness,
side effects, recovery, cleanup, and workbench fit. It names which steps are
user-visible versus internal preparation/cleanup, identifies source paths and
exact commands, distinguishes product gaps from observations, and gives every
retained gap a disposition: later product decision, external owner, or no
follow-up. It is not an all-leaf CLI inventory or a second M007 evidence run.

### One shared sequence and structured state

Implementation adds one server-side owner for
`workbench.perception_memory_lifecycle.v1`.
The owner alone orders the selected lifecycle, maintains run identity and
state transitions, and chooses permitted existing CLI operations. Both
entrypoints call that owner rather than invoking different command flows:

- a local `automa vehicles workbench` CLI surface that can serve the page and
  run the selected slice with complete machine-readable state; and
- the loopback workbench HTTP API used by the page.

The browser may request the one declared operation, refresh structured state,
ask to cancel or clean up its own run, and render named status, result, failure,
recovery, and cleanup signals. It may not accept arbitrary argv, choose an
algorithm, call simulator APIs, synthesize lifecycle results, or directly
invoke perception, memory, worker, or Metrics UI behavior. CLI human output
names the same progress, outcome, and recovery facts that the API reports; its
machine-readable output is the machine surface. Raw stdout, raw JSON, a report
directory, or a transcript alone is not the workbench display.

For a supported locally available Chase input, the runner composes these
existing lifecycle operations into one declared journey:

1. Inspect the targeted vehicle and passive-capture prerequisites without
   starting, configuring, or preparing a simulator.
2. Snapshot the relevant local perception and memory activation state before
   the workbench changes either activation.
3. Stage only `lightweight_observer` perception and `bounded_evidence` memory,
   then start one new observation-only worker generation and wait for its
   current correlated publication.
4. Reuse the existing live perception and memory state/view seams while the
   page remains open, then execute the bounded-memory lifecycle check and
   report its named phase outcomes.
5. On success, declared failure, cancellation, or page-requested cleanup, stop
   only the generation the runner started and restore the recoverable local
   activation snapshot before reporting terminal cleanup state.

The assessment records the exact command/function boundary used for each step.
A pre-existing worker is visible `blocked` state with its existing operator
recovery; this runner neither adopts nor stops it. Snapshot failure, staging
failure, unavailable passive capture, startup failure, lifecycle-check failure,
cancellation, restore failure, and cleanup failure each produce a named state
and next action. A failed restore or worker cleanup cannot be reported as a
clean completed run.

### Long-lived local workbench boundary

The local workbench server is independent from a particular automation-worker
generation. Its page and last structured run state remain reachable across
completed, failed, and cleaned-up runs, so the operator can inspect the result
and begin another declared run without reopening the page. The server binds to
loopback only and serves plain HTML, CSS, and JavaScript; React, remote hosting,
authentication, and a general workflow builder are out of scope.

The structured state contract has at least a server identity, selected sequence
id, run id, phase, allowed action, human-visible summary, machine detail,
view/memory signal references, failure boundary, recovery action, and
cleanup/restore outcome. The page consumes that contract. Existing perception
and memory presentations may be linked, embedded only through the same loopback
origin, or adapted by the shared server, but no feature meaning is maintained
separately in the CLI page and workbench.

### Observation-only and lifecycle boundary

The slice is observation-only: it must not invoke `simulators ensure`, select
or change a simulator scenario, playback, control source, or input, issue a
movement/operation pulse, use non-observation authority, or enable recording
by default. The only local mutations are declared staged activation,
workbench-owned worker generation, and selected memory lifecycle state; their
before/after and cleanup outcomes are visible. The workbench does not silently
normalize a blocked prerequisite by reconfiguring the simulator.

The implementation may add only the one CLI-useful workbench entrypoint and
shared composed journey. It does not extract a generic workflow framework,
redefine the accepted M007 continuity catalog, or treat M007's historical
result as a current M008 acceptance run. A later
operator-acceptance/evidence unit owns M008-03, M008-05, and M008-06 proof;
this unit supplies the selected contract, assessment, and one-source page
alignment needed for that review.

## Ownership

| Concern | Owner | Required result |
| --- | --- | --- |
| Selected journey ordering, run identity, transition/state contract, cancellation, and cleanup | Workbench scenario runner | One `workbench.perception_memory_lifecycle.v1` composition is shared by CLI and HTTP requests. |
| CLI parse, human output, and `--json` machine boundary | `cli/automa_cli/app.py` plus workbench command module | CLI cannot diverge from the browser's sequence or expose arbitrary execution. |
| Page rendering and loopback routes | Workbench loopback server and plain page | Render server-owned state and named next actions; no business-logic duplication in JavaScript. |
| Perception, memory, worker, status, and existing views | Existing CLI owners | Reuse public/function contracts; do not fork lifecycle behavior. |
| Candidate assessment and gap disposition | M008 assessment artifact | Preserve the bounded source-grounded selection and residuals through closeout. |

## Affected Paths

| Path | Purpose |
| --- | --- |
| `cli/automa_cli/app.py` | Register the bounded workbench CLI surface. |
| `cli/automa_cli/workbench.py` | Shared sequence runner, loopback server/API, and structured state owner. |
| `cli/automa_cli/workbench.html` | Plain local workbench presentation consuming the API contract. |
| `cli/automa_cli/automation.py`, `memory.py`, `memory_check.py`, `perception_view.py` | Reuse only public/function seams required by the selected runner; changes are limited to an explicit one-source adaptation boundary. |
| `tests/cli/test_workbench.py` and focused adjacent CLI tests | Deterministic runner, CLI/API parity, lifecycle, and refusal coverage. |
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
| Current vehicle/passive capture prerequisite is unavailable | Report `blocked`, failed boundary, and named recovery. Do not start a worker, stage activation, or call simulator preparation. |
| A worker already runs | Preserve it and report `blocked` with targeted recovery; never adopt or stop it. |
| Activation snapshot cannot be made recoverable | Refuse before staging or worker start. |
| Worker start, lifecycle check, cancellation, or a terminal step fails | Stop only a workbench-owned generation, restore activation, retain failure/recovery state, and keep the page available. |
| Restore or worker cleanup fails | Show failed cleanup and next action; do not claim a clean run or enable a replacement run that could hide the mutation. |
| A stale worker/view/run result is rendered as current | Bind state and page updates to active run identity; expose stale/unavailable status rather than current success. |
| Page refreshes after a run ends | Long-lived server returns latest terminal structured state and remains able to start a new declared run. |
| Implementation reconfigures simulator state, sends movement/control, or turns on recording to make the slice work | Reject as out of contract; deterministic tests cover allowed operations and observation-only parameters. |
| Existing `/` or `/memory` pages are copied into a second feature definition | Reuse state meaning through the common server contract or document the single adaptation boundary; duplicate lifecycle logic fails review. |
| Generated report path, stdout, or JSON is the only result signal | Fail review: page and CLI provide concise progress, outcome, cleanup, and recovery signals. |

## External Assumptions

- The current local Metrics UI/Chase passive-capture contract remains available
  for `chase-sim-chaser`. A change is visible `blocked` state, not authority to
  change the simulator.
- M007's accepted continuity catalog and captured result remain historical
  evidence for candidate selection. Commands may be reused only through current
  CLI-owned interfaces; the M008 runner is not a second session runner.
- The host can bind a local loopback server and open its URL. Browser-launch
  failure may leave a manually usable URL, but it does not make page health or
  the lifecycle successful.
- `lightweight_observer` and `bounded_evidence` remain installed packaged
  options. A missing option is a named blocked prerequisite, not a fallback.

## Non-Goals

- Full M006 shadow-decision evidence, decision-engine redesign, or movement
  authority.
- Every M007 continuity family, all CLI leaves, arbitrary recorded-source
  upload, algorithm selection, a workflow builder, or a second slice.
- React adoption, a frontend design system, remote hosting, authentication, or
  a Metrics UI redesign.
- Replacing the M007 session runner, rewriting perception/memory business logic
  in JavaScript, or claiming the historical M007 run proves M008.
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
  and contains no alternate lifecycle authority;
- API rejects malformed/unknown requests and never accepts raw argv;
- the selected CLI/API lifecycle has ordered state transitions and visible
  progress/result/recovery/cleanup summaries, without mock result sources;
- missing prerequisite, pre-existing worker, snapshot refusal, start failure,
  lifecycle failure, cancellation, worker cleanup failure, and restore failure
  have declared states and do not claim clean completion;
- the workbench starts no worker or activation mutation before a successful
  recoverable snapshot, stops only its own generation, and restores the snapshot
  on every terminal route;
- observation-only command construction excludes simulator preparation,
  movement/control, non-observation authority, and default recording;
- server/page persistence returns the latest terminal state after a run and
  allows the next declared run without relaunching the server;
- page/API state is run-identity aware and renders stale/unavailable information
  instead of a current success; and
- perception/memory page semantics are consumed through the documented
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
    "durable_evidence": "PR #{pr} selects workbench.perception_memory_lifecycle.v1 through the durable M008 assessment, composes only the necessary existing CLI operations behind one shared server-side CLI/workbench runner, and documents the selected perception/memory page adaptation boundary.",
  "criterion_updates": {
    "M008-01": {
      "status": "Met",
      "evidence": "PR #{pr} records the bounded assessment of the relevant M007 perception-memory candidates, current CLI capabilities, and existing loopback pages."
    },
    "M008-02": {
      "status": "Met",
      "evidence": "PR #{pr} selects workbench.perception_memory_lifecycle.v1 as the one CLI-useful reusable composed journey with declared inputs, signals, safety, recovery, and cleanup."
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
      "risk": "The selected workbench lifecycle has not yet received a recorded operator acceptance across repeated runs and declared failure/recovery cases",
      "consequence": "The selected implementation can establish the shared sequence and page boundary without proving M008-03, M008-05, or M008-06",
      "resolution": "Use the durable assessment and implementation contract to select one bounded live/operator evidence review unit when the operator is ready."
    }
  ],
  "next_frontier": {
    "state": "none",
    "reason": "No later frontier is contracted until the selected workbench implementation establishes its assessment and one-source lifecycle boundary.",
    "revisit_when": "After implementation, select a bounded operator-acceptance/evidence frontier for the remaining workbench persistence, usefulness, and lifecycle proof if the implementation contract is intact."
  }
}
```

This success handoff applies only when the implementation delivers the shared
runner, assessment, and selected page-alignment boundary without an unresolved
cleanup/restore defect. A conclusive blocked or failed implementation retains
the frontier and records the actual limitation rather than promoting these
criteria.
