# Proposal: Replay workbench POC acceptance

| Field | Value |
| --- | --- |
| Milestone | 008 Perception-Memory Workbench Feasibility |
| Frontier | Replay workbench POC acceptance |
| Review kind | Live or external evidence |
| Proposal branch | `m008/replay-workbench-acceptance-proposal` |
| Implementation branch | `m008/replay-workbench-acceptance` |
| Exit criteria | M008-03, M008-05, M008-06 |

## Review Kind

Live or external evidence

## Review Question

Can an operator use the implemented image-replay workbench to inspect real
perception overlays and memory effects, control the declared replay, and affirm
that this one local workflow is minimally useful at its delivered display
granularity?

This is a user-led acceptance unit, not another product implementation. The
workbench from PR #174 is the system under test. Hands-on comments from that
implementation are context; they do not replace a committed operator verdict.

## Proposed Contract

### What this unit decides

One guided local session records either:

- one affirmative judgment that the primary demonstration is minimally useful
  at the delivered granularity; or
- a named blocker that falsifies a stated condition of the accepted workbench
  contract.

A missing stated condition is a workbench repair, not a visual-polish queue.
A new source, adapter, algorithm question, or second operator goal is a later
proposal. Closeout is not this unit.

The durable assessment at
`docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md`
stays the single M008-01 / M008-02 / M008-07 artifact. This unit may cite it
and note residuals; it must not write a second assessment.

### Environment

Run from a clean checkout of the M008 milestone tip that contains merged PR
#174. Record:

- UTC start time;
- OS and browser name/version;
- exact `auto-driving` commit and clean/dirty state;
- the named local image-directory source (supported JPEG/PNG/WebP/BMP, with
  optional `manifest.json` / `run.json` order);
- the plugin root used (`lab/plugins/perception` or the packaged default);
- the printed loopback URL and server identity from the page's `/api/health` or
  `/api/state` response.

The source must be a real readable image directory. The primary demonstration
must use a long capture with at least 10 frames spanning at least 10 seconds,
and no more than the command's bounded 1024 normalized frames. It must use a
recorded `manifest.json` / `run.json` with strictly increasing `timestamp_ms`
values so realtime pacing exercises captured timing, and a ready plugin/source
combination that produces at least one server-reported perception item and
memory record. A source without recorded timestamps may be used only for a
separate failure/recovery retry and cannot be claimed as realtime evidence.
Fixture-generated or mock frames are not the primary demonstration. Local
absolute paths and unrelated browser content are redacted in tracked
artifacts.

### Exact procedure

Start the long-lived page from the repository root:

```sh
./cli/automa vehicles workbench replay <source_dir> \
  --plugin-dir lab/plugins/perception \
  --plugin classical_regions \
  --pace realtime \
  --max-frames 1024 \
  --open
```

The command intentionally names a ready plugin, requests recorded-timestamp
pacing, and raises the bounded frame limit for a long capture. Omitting
`--plugin-dir` is allowed when the packaged `frame` + `floor_plane` default is
the session's catalog; in that case omit `--plugin` as well and record the
packaged selection. `--json` is a recorder aid; it is never the operator
display.

The operator, not a batch script, then:

1. Confirms the page opened and shows source identity, plugin catalog, and
   declared next actions without shell commands as the product surface. Turn
   the loop control off so this run reaches a terminal state instead of
   wrapping forever.
2. The command has already started replay. Inspects the current capture,
   server-produced overlays, progress, and memory ledger on a processed frame;
   wait for the next frame if the page opened before the first frame arrived.
3. Pauses. Toggles to another ready set, then to empty raw-capture, and back
   to a non-empty ready set. After each selection response, records the held
   frame's server-produced plugin runs, overlays or explicit raw-capture state,
   and memory state. Leave the non-empty set selected before resuming. The
   evidence README cites the deterministic invalid-selection coverage (or
   records an explicit loopback rejection response) rather than treating a
   checkbox that is never rendered as an invalid-ID test.
4. Before this running-toggle check, temporarily choose the `1000 ms` fixed
   cadence so there is enough delay to observe one held frame. Resume.
   While the phase is running, toggle the ready set to empty raw-capture (or to
   another ready set if empty was the last paused selection). Record the state
   immediately after the selection and after the next frame: the current
   still's processed evidence must remain unchanged at the selection boundary,
   then the next frame must reflect the new set. Restore `realtime` before the
   first run is allowed to complete.
5. After the first run reaches its terminal state, resets isolated memory and
   reselects `classical_regions` (or another ready non-empty set) if the
   running-toggle check left an empty selection, then starts a second run
   **without** restarting the server. Keep loop off so the second run also
   reaches a terminal state. The page remains available; prior run identity
   does not leak as current success. Record the server identity and distinct
   first and second run IDs; the server identity and loopback URL must be the
   same for both runs.
6. Once the second run is terminal and the source field is editable, replaces
   it with an empty, missing, or unsupported directory and presses Start. The
   named failure and next action stay visible; expand the `Failure boundary`
   disclosure and record its boundary, message, and suggested next action.
   Then replaces it with an operator-chosen valid directory and presses Start
   again, recording a successful recovery on the same server. No source,
   simulator, or worker is silently substituted or started.
7. After the recovered run completes (or is explicitly cancelled if still
   running), resets. No vehicle, worker, simulator, Metrics operation,
   movement, or recording was started. Isolated mapper/memory state is reset.

CLI human output or one `--json` snapshot may corroborate phase, failure
boundary, recovery, and cleanup. They cannot replace the page inspection.
The evidence README must also cite the existing deterministic CLI/API and
selection-boundary coverage for the invalid-ID and shared-runner claims; a
direct `/api/action` probe may supplement that citation but is not the product
display. The citation names
`tests/cli/test_workbench.py::test_explicit_catalog_allows_raw_capture_and_live_replacement`
for atomic invalid-ID refusal and
`tests/cli/test_workbench.py::test_loopback_api_exposes_and_applies_plugin_selection`
for the loopback selection boundary,
`tests/cli/test_workbench.py::test_loopback_api_persists_after_terminal_state_and_rejects_raw_argv`
for server persistence, distinct run identity, and stale-run rejection, and
`tests/cli/test_workbench.py::test_cli_replay_machine_readable_boundary` plus
`tests/cli/test_workbench.py::test_cli_replay_accepts_realtime_pace` for the
CLI boundary and pace. If a direct probe is used, it records the HTTP 422
`plugin_catalog` response and the unchanged effective selection.

### Operator verdict

The operator records one of:

- `accepted`: the primary demonstration is minimally useful at this
  granularity; listed residuals do not falsify it;
- `blocked`: a stated contract condition failed; name the step and required
  repair;
- `incomplete`: the declared environment was not available or identifiable.

`accepted` is sufficient for M008-05. Later visual refinement is residual
unless it falsifies that judgment. An operator who already used the workbench
during #174 still performs this session and writes the verdict into the
evidence packet.

### Evidence artifacts

The implementation PR owns:

`docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/`

| Path | Contract |
| --- | --- |
| `README.md` | Environment receipt, procedure log (including loop-off, second-run, failure, and valid-retry transitions), operator observations, verdict, limitations, deterministic-boundary citations, and links |
| `result.json` | `m008_replay_workbench_acceptance_v1` with timestamps, commit, server identity, distinct first/second/failed/recovered run IDs, source/plugin identity, effective pace/loop controls, step outcomes, observation-only/cleanup checks, findings, and `accepted` / `blocked` / `incomplete` |
| `result.html` | Derived HTML of that committed `result.json` |
| `browser-view.png` | One cropped screenshot of the inspected workbench still (capture + overlays or explicit raw-capture) |
| `cli-transcript.txt` | Optional exact launch/help/status transcript; redacted local prefixes |

`result.json` lists each retained non-record artifact with a SHA-256 digest over
its exact committed bytes. It intentionally excludes itself and derived
`result.html` to avoid a self-reference/circular digest; the page is verified
by regeneration from the committed record, and the record's own integrity is
the Git commit. The README records the digest and regeneration commands.
Runtime caches, full frame dumps, and unrelated browser chrome stay untracked.

### Findings

A stated-condition miss (`acceptance_blocker`) keeps this unit from the
success handoff until the workbench is repaired on its own PR and the session
is rerun. Preference, layout taste, video/live, or a second journey is an
`enhancement_candidate` residual, not a blocker. An unavailable machine or
source is `incomplete`, not a product pass or fail.

No product, CLI, or assessment rewrite lands in the evidence PR.

## Ownership

| Concern | Owner | Required result |
| --- | --- | --- |
| Procedure completeness and artifact integrity | Evidence README and `result.json` | Reviewer can replay what was done from committed files. |
| Minimal-usefulness and display-granularity judgment | Named operator in the evidence receipt | One `accepted` or a named blocker. |
| Page persistence, controls, overlays, and memory | Operator inspection of the live workbench plus screenshot | M008-03 and M008-05 are not inferred from CLI JSON alone. |
| Failure, recovery, cleanup, observation-only | Named procedure steps plus result checks | M008-06 stays inside the selected slice's declared cases. |
| Residual gaps | Existing M008 assessment | No second assessment. |
| Product repair | Outside this unit | Later workbench PR, then a full session rerun. |

## Affected Paths

Proposal review changes only this artifact, canonical `plan.md`, and generated
`plan.html`.

After acceptance, the evidence implementation changes only the declared
evidence directory plus the plan handoff and rendered HTML. It uses, and does
not modify, the workbench runner, page, CLI, plugins, or assessment except to
cite residuals already recorded there.

## Adversarial Matrix

| Attempt | Required behavior |
| --- | --- |
| Treat #174 review comments as the operator verdict | Fail: the committed evidence packet must contain the named verdict. |
| Use mock frames or screenshots without a live page session | Fail: the primary demonstration needs a real local image directory and the running workbench. |
| Make stdout, JSON, or a transcript the only display | Fail: the page must show capture, overlays or raw-capture, progress, and memory. |
| Restart the server between the two runs and claim persistence | Fail M008-03. |
| Empty selection while running hides leftover server overlays on the current still | Fail the #189 running rule; record an acceptance blocker. |
| Paused plugin toggle leaves the held still unchanged | Fail the #189 paused rule; record an acceptance blocker. |
| Invalid plugin IDs change the effective set | Fail; cite the deterministic selection-boundary test or record a direct loopback rejection with the effective set unchanged. |
| Primary source is too short, exceeds the bounded frame limit, or lacks recorded timestamps | Fail the long realtime demonstration; the session is not accepted on a synthetic-timestamp or truncated source. |
| Failed source is silently replaced | Fail M008-06. |
| Server identity changes, run IDs are reused, or the recovery retry uses a new server | Fail M008-03; repeated-run persistence was not established. |
| Artifact digest is self-referential or `result.html` cannot be regenerated from `result.json` | Fail artifact integrity; repair the evidence packet before handoff. |
| Session starts a worker, simulator, Metrics operation, movement, or recording | Fail M008-06; reject as out of contract. |
| Expand into video, live ingest, or a second operator goal | Out of this unit; residual or later proposal. |
| Rewrite the M008 assessment as a new inventory | Fail: cite and keep the existing assessment. |

## External Assumptions

- The host can bind loopback and the operator can open the printed URL. Browser
  launch failure may leave a usable URL; that is not page-health success.
- The named image directory stays readable for the session. A missing source is
  visible failure, not authority to pick another directory automatically.
- Ready packaged or `lab/plugins/perception` plugins can run in core Python.
  Isolated-runtime entries (for example FastSAM) remain visible as unavailable.
- No vehicle, simulator, or Metrics UI deployment is required or permitted.

## Non-Goals

- Product expansion beyond the accepted POC-completion envelope.
- Video or live adapters, new processing semantics, or a second workbench
  journey.
- A substitute for an actual operator acceptance.
- Closeout, cumulative-PR readiness, or marking M008-07 / M008-08 `Met`.
- React, remote hosting, authentication, or a Metrics UI redesign.
- Repairing the workbench inside this evidence PR.

## File Impact

### Proposal PR

- `docs/milestones/008-cli-decision-workbench/proposals/replay-workbench-acceptance.md`
- `docs/milestones/008-cli-decision-workbench/plan.md`
- `docs/milestones/008-cli-decision-workbench/plan.html`

### Implementation PR after proposal acceptance

- `docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/`
- canonical plan handoff and generated HTML only

## Validation Plan

### Proposal PR

- `python3 docs/milestones/workflow.py validate docs/milestones/008-cli-decision-workbench/plan.md`
- `python3 docs/milestones/workflow.py validate-pr` against this branch and a
  completed proposal PR body
- `python3 docs/render_markdown.py --check`
- `git diff --check`

### Evidence implementation after proposal acceptance

The session is the proof. CI of deterministic tests is not sufficient.

Commit the artifacts above with `result.json` status `accepted`, `blocked`, or
`incomplete`. Derived `result.html` must be regenerable from that committed
`result.json`. Plan validation and `git diff --check` still run.

A success handoff requires `accepted` plus the procedure steps for persistence,
inspection, failure/recovery, and observation-only cleanup. The packet records
one server identity, distinct first/second/failed/recovered run IDs, effective
pace/loop controls, and verified artifact digests/regeneration. A blocker or
incomplete session does not promote M008-03, M008-05, or M008-06.

## Expected Handoff

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "PR #{pr} records one operator-accepted local image-replay workbench session: one loopback server identity stayed available across distinct first, second, failed, and recovered runs, overlays and memory were inspected from server state, declared failure/recovery/cleanup stayed observation-only, and the operator affirmed minimal usefulness at the delivered granularity.",
  "criterion_updates": {
    "M008-03": {
      "status": "Met",
      "evidence": "PR #{pr} records one loopback server identity and distinct run IDs across the first, second, failed, and recovered runs without relaunching the server, and cites the deterministic CLI/API runner contract and persistence coverage."
    },
    "M008-05": {
      "status": "Met",
      "evidence": "PR #{pr} records one affirmative operator acceptance of the image-replay primary demonstration at the delivered display granularity."
    },
    "M008-06": {
      "status": "Met",
      "evidence": "PR #{pr} records declared source-failure recovery and cleanup with no worker, simulator, Metrics operation, movement, or recording."
    }
  },
  "risk_remove": [
    "The selected workbench has not yet received recorded operator acceptance across repeated replay runs and declared source or processing failures",
    "Human-friendly presentation and the minimally useful display granularity are operator judgments"
  ],
  "risk_upsert": [
    {
      "risk": "Later visual refinement or a video/live source may still be wanted after this POC acceptance",
      "consequence": "M008 can close on one accepted local slice without those capabilities",
      "resolution": "Keep those wants as residuals in the existing assessment; select closeout or a later proposal only if the operator is ready."
    }
  ],
  "next_frontier": {
    "state": "none",
    "reason": "No remaining work-order node is contracted. Closeout is selected when the operator is ready, not because this unit queued it.",
    "revisit_when": "Select closeout when M008-03, M008-05, and M008-06 are Met and the operator is ready to close."
  }
}
```

This success handoff applies only to an `accepted` evidence packet. A blocked
or incomplete session retains the frontier and does not mark these criteria
`Met`.
