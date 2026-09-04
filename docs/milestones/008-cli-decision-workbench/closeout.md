# M008 closeout: Perception-Memory Workbench Feasibility

| Field | Value |
| --- | --- |
| Milestone | 008 Perception-Memory Workbench Feasibility |
| Closeout phase | Phase A packet; terminal handoff and cumulative review remain separate |
| Prepared | 2026-09-04 |
| Current plan status | Closed after Phase B handoff; M008-01 through M008-08 are `Met`; Phase C cumulative review remains pending |
| Cumulative review | [PR #167](https://github.com/GeorgeLuo/auto-driving/pull/167), ready for Phase C review, `milestone/008-cli-decision-workbench` -> `main` |

## Whole-milestone judgment

M008 answered its bounded feasibility question with one useful local,
observation-only journey: `workbench.image_replay.v1`. The selected journey
replays an ordered image directory through the server-owned perception mapper,
`Observation`, and bounded decision-cycle memory, and presents the resulting
signals through a long-lived loopback workbench. The CLI and page use the same
runner and structured state; the browser remains a presentation client.

The accepted workbench implementation is PR #174. The accepted operator POC
is PR #191, whose recorded Chrome session used a long real image capture,
realtime timestamp pacing, meaningful `classical_regions` perception, plugin
selection, memory inspection, repeated runs, failure/recovery, and cleanup.
The operator judgment recorded in that evidence is that the delivered slice
is functionally satisfactory and minimally useful at its current display
granularity. That judgment closes the selected product question; visual polish
and other consumer wants remain residuals rather than hidden acceptance work.

This document was created as Phase A documentation. Phase A did not mark
M008-07 or M008-08 `Met`, remove plan risks, close the plan, make PR #167
ready, merge anything to `main`, or create a milestone tag. Phase B has now
applied the mechanical `workflow.py complete-implementation` handoff. Phase C
is still a separate whole-milestone review of PR #167.

## Post-Phase B handoff

The accepted closeout implementation PR #193 was squash-merged as
`9d3fa1d1334e747656e5874dc19921a062616bce`. The workflow handoff recorded in
`0872c624d2a8cd622eab7a3727c2a0ce0b8662dc` marked M008-07 and M008-08
`Met`, removed the four closeout-owned risks, regenerated `plan.html`, and
closed the plan with no remaining frontier. Cumulative PR #167 is still the
separate Phase C review surface; it has not merged to `main` and no
`milestone-008` tag exists.

## Accepted evidence identity

| Artifact | Frozen identity |
| --- | --- |
| M008 assessment | `docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md`; selected sequence `workbench.image_replay.v1` |
| Workbench implementation | PR #174, squash merge `27b3c343de311e60219abc9b18b4ef293a28b445` |
| POC proposal | PR #190, squash merge `8dca162ee776267091b1bf4ac23f188e19d471b6` |
| POC implementation | PR #191, squash merge `6c2f26a2ce34a5f38431e6b21d1269ea306f526d` |
| Evidence packet head | `654649281dc5e732d01c58cdce2935839cabd835` |
| Accepted result commit | `e3572d2c875d166efc2d6011384810169e3ce3cb` |
| Evidence location | `docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/` |
| Browser / host | Chrome `152.0.7977.76` headed on macOS `26.6.2` |
| Loopback server | `workbench-2d29d6df9d2f` |
| Recorded runs | first `run-af702ee8f0974eabb15bb5bdfa4fff4f`; second `run-3fa4314708804bacbafe9675fff24037`; failed `run-0969dd4fc26d4f3e9ccd04d217c4d156`; recovered `run-614187c4db7e445294420fa5fc4022f2` |

The closeout cites these identities rather than recapturing evidence merely
to make dates current. The recorded proof is bounded to the local browser,
source, server, and run identities above. It does not claim video or live
ingestion, remote hosting, movement, recording, Metrics UI operation, or
arbitrary isolated/model-dependent plugin support.

## Durable contract and decisions

The selected contract is:

~~~text
ordered image directory
  -> selected ready server-side perception plugins
  -> Observation
  -> bounded DecisionCycle memory
  -> structured loopback state and workbench page
~~~

The public CLI/workbench invocation is:

~~~text
./cli/automa vehicles workbench replay <source_dir> \
  --plugin-dir lab/plugins/perception \
  --plugin classical_regions \
  --pace realtime \
  --max-frames 1024 \
  --open
~~~

The durable decisions are:

- One ordered image replay is the authoritative feasibility slice. Supported
  image files, manifest/run ordering, source identity, frame identity, and
  recorded timestamps are validated before mapper or memory creation.
- The CLI and workbench share the ImageReplayRunner, loopback server, and
  structured run state. The browser does not read directories, decode images,
  derive observations, mutate memory, or invoke raw commands.
- A declared plugin root is recursively cataloged by manifest. Every package
  remains visible with readiness and error metadata; only selected ready
  entrypoints run. Malformed, duplicate, escaped, unavailable, isolated, and
  model-dependent entries do not silently run or install dependencies.
- Selection is an operator control throughout replay. A valid change applies
  on the next feed frame while running and reprocesses the held frame while
  paused. An empty selection is explicit raw capture: the image and observation
  lifecycle continue with empty perception and no plugin runs.
- Fixed-delay, fastest, and realtime timestamp-paced replay are exposed by the
  same CLI, API, and page. Realtime waits only for remaining recorded-time
  delta after processing; it is not a claim of live capture.
- Failure, cancellation, reset, terminal cleanup, and repeated runs stay
  observation-only. Stage instances reset at terminal cleanup; terminal history
  remains inspectable until source validation, reset, a new run, or server
  shutdown. History is process-local and is never recorded or persisted
  implicitly.
- The accepted operator display is sufficient for this feasibility slice.
  Further hierarchy, typography, overlay, and page-identity refinement is a
  later product decision, not a retroactive M008 blocker.

## Exit-criterion map

| Criterion | Authority | Closeout judgment |
| --- | --- | --- |
| M008-01 | Assessment and PR #174 | The relevant M007 candidates, CLI seams, existing pages, inputs, signals, side effects, recovery, cleanup, and workbench fit were bounded. |
| M008-02 | Assessment and PR #174 | `workbench.image_replay.v1` is the one independently CLI-useful, reusable sequence with explicit inputs, signals, safety, recovery, and cleanup. |
| M008-03 | PR #191 and replay evidence | One loopback server identity remained available across distinct first, second, failed, and recovered runs; the page consumed shared server state. |
| M008-04 | PR #174 | Existing CLI-launched perception/memory seams and selected page meanings have one server-owned adaptation boundary. |
| M008-05 | PR #191 and operator verdict | A real long capture produced meaningful overlays and memory; the operator accepted the delivered display granularity as minimally useful. |
| M008-06 | PR #191 and operator verdict | Source failure, valid retry, repeated runs, reset, and cleanup were observed without worker, simulator, Metrics, movement, or recording side effects. |
| M008-07 | Assessment continuity plus this packet | The assessment remains one evolving authority and classifies durable gaps as later decisions or no-follow-up observations. The Phase B handoff marked this criterion `Met`. |
| M008-08 | This packet plus Phase B handoff | The selected contract, accepted slice, CLI/page alignment, safe lifecycle, and residual limits are durable. The Phase B handoff marked this criterion `Met`. |

## Gap and residual disposition

| Residual | Durable disposition |
| --- | --- |
| `run_id` is not shown on the page | Enhancement candidate `M008-POC-E-001`; the server `/api/state` identity is accepted for this slice. A page identity change needs a bounded review if selected. |
| Visual hierarchy and display refinement | The operator accepted current functionality and granularity. Further visual refinement is residual and does not reopen M008 by preference. |
| Video or live ingestion | Deferred until a source contract defines ordering, timestamps, identity, decoding, and lifecycle. |
| Arbitrary algorithms and isolated/model-dependent plugins | Ready manifest packages may be selected; unavailable entries remain visible. Install, network fetch, model loading, and silent fallback are unsupported. |
| M006 shadow decision surfaces | M006 remains separately owned. M008 closeout does not edit its plan, branch, or evidence. |
| Browser, loopback transport, timing, and external Metrics UI | Acceptance covers the recorded local environment and server-owned state only. Public hosting, browser compatibility beyond the recorded session, and external contract drift remain bounded assumptions. |
| Movement, vehicle, simulator, and recording authority | The journey is read-only and observation-only; no control or simulator reconfiguration is claimed. |
| History persistence and export | State is process-local. Terminal history is retained only until source validation, reset, a new run, or shutdown; durable export requires explicit consent and a later proposal. |

## Failure, recovery, and safety record

| Case | Accepted behavior |
| --- | --- |
| Invalid, empty, unsupported, unreadable, or unsafe source | Validation fails before mapper or memory creation; structured failure and recovery remain available. |
| Mapper or memory health error | The run fails closed with a named failure boundary and no successful completion. |
| Pause, resume, step, cadence change, cancel, reset | Actions are typed and allow-listed; held-frame selection and next-frame plugin changes remain deterministic. |
| Repeated run on one server | The loopback server stays available; each run has a distinct identity and does not grant worker, simulator, vehicle, or movement authority. |
| Terminal cleanup | Mapper and memory stage instances reset; the page remains available for inspection and a separately declared next run. |
| Raw capture | Empty active-plugin selection advances the source and observation lifecycle with `status=empty`, no plugin runs, and no fabricated perception. |

## Validation

The implementation handoff must pass the focused workbench tests, the full
deterministic suite, milestone/workflow validation, Markdown rendering, and
whitespace checks. The exact results are recorded below before the PR is
opened:

~~~text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.cli.test_workbench
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
python3 docs/milestones/workflow.py validate \
  docs/milestones/008-cli-decision-workbench/plan.md
python3 docs/render_markdown.py --check
git diff --check
~~~

Result:

~~~text
focused workbench: Ran 30 tests in 21.283s — OK
full deterministic suite: Ran 894 tests in 348.464s — OK
workflow validate: Valid milestone plan
render --check: pass
git diff --check: pass
~~~

## Cumulative review topology

PR #167 is the cumulative M008 review surface, targeting `main` from
`milestone/008-cli-decision-workbench`, and is ready for Phase C review. Its
body cites this packet, accepted review units #174, #191, and #193, their exact
identities and validation, the residuals above, and the correct child-PR
topology. Phase B has applied the proposal's expected handoff with
`workflow.py complete-implementation`. A packet-only finding is repaired on
#167; a finding that falsifies an already accepted criterion follows the
append-only reject/restore workflow and a new proposal.

The next product focus is the separately owned M006 frontier (or a later
proposal selected from the residuals) after whole-milestone acceptance. M008
does not claim that M006 is complete or that any milestone tag exists.

## References

- [M008 plan](plan.md)
- [M008 assessment](assessment/perception-memory-workbench.md)
- [M008 closeout proposal](proposals/closeout.md)
- [Replay workbench acceptance evidence](evidence/replay-workbench-acceptance/)
- [PR #174](https://github.com/GeorgeLuo/auto-driving/pull/174), [PR #190](https://github.com/GeorgeLuo/auto-driving/pull/190), [PR #191](https://github.com/GeorgeLuo/auto-driving/pull/191), and [PR #167](https://github.com/GeorgeLuo/auto-driving/pull/167)
