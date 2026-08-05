# Proposal: Live CLI operator acceptance

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Live CLI operator acceptance |
| Proposal branch | `m007/live-cli-acceptance-proposal` |
| Implementation branch | `m007/live-cli-acceptance` |
| Exit criterion | M007-05 |

## Review Question

Does the accepted simulator-to-perception CLI journey work end to end against
the current local Metrics UI deployment with one processed observation-only
frame, a healthy browser view, truthful layer states, no applied movement, and
no default recording?

This is a user-led acceptance unit, not another deterministic implementation
unit. The operator follows the public help and durable guide, runs the primary
commands one at a time, inspects both the Metrics UI and Automa perception
views, and reports discrepancies in the implementation PR as they are found.
The evidence recorder turns those observations into a tracked transcript,
machine-readable status captures, a bounded screenshot, and a finding ledger.
PR comments are useful live review dialogue, but they do not replace committed
evidence.

An affirmative result requires the whole bounded journey to pass. A product or
external-contract discrepancy may emerge from the session, but it is evidence,
not authorization to repair product behavior in this review unit.

## Proposed Contract

### Review-unit lifecycle

After this proposal is accepted, the implementation branch opens a draft PR
with the evidence README and session checklist. The live session then proceeds
in that PR:

1. The evidence recorder captures the exact repository and browser baseline.
2. The operator audits help and the durable guide before running the journey.
3. The operator runs each command separately and evaluates its output before
   continuing.
4. The recorder commits the command results, browser evidence, and every
   confirmed discrepancy to the same evidence PR.
5. The PR ends as either a passing acceptance unit or an accepted findings
   unit. An incomplete environment or abandoned session remains draft and does
   not make a milestone claim.

The operator owns judgments about discoverability, wording, visual health, and
whether the observed flow matches the documented intent. Automation may run
commands, collect JSON, and capture the browser, but it may not infer a visual
pass without the operator's inspection.

### Environment receipt

Before the first acceptance command, record:

- UTC start time and local timezone;
- operating system and browser name/version;
- exact `auto-driving` commit and clean/dirty state;
- exact `metrics-ui` commit, branch or PR/release identity, and clean/dirty
  state;
- the tested Metrics UI origin, with credentials or query secrets removed;
- whether a Chase frontend is visibly open and registered;
- the visible game/scenario, playback state, control source, and input when the
  UI exposes them;
- the existing worker/deployment state for `chase-sim-chaser`; and
- the names already present under
  `runtime/vehicles/chase-sim-chaser/bundle/runtime/automation/runs/`.

Acceptance requires committed, reviewable repository identities. A dirty
external checkout is allowed only when its exact diff or linked PR is named;
otherwise the session is `incomplete` because another reviewer cannot identify
the contract that was exercised. Local absolute paths, usernames, tokens, and
unrelated browser content are redacted from tracked artifacts.

If an earlier Automa worker is running, stop it before taking the baseline and
record that precondition cleanup. This does not count as the acceptance stop.
The Metrics UI must already expose Chase; the primary demonstration may not
select a scenario or call `simulators ensure` to manufacture its starting
state.

### Help, documentation, and flow audit

The operator starts from the public surfaces rather than from implementation
knowledge. Capture the output of:

```sh
./cli/automa help
./cli/automa vehicles help
./cli/automa vehicles automation help
./cli/automa vehicles automation run --help
```

Then compare those surfaces with
`docs/reference/cli-simulator-perception-journey.md`. The audit passes only when
an operator can discover the exact primary commands, understand that `active`
means vehicle discovery rather than worker liveness, distinguish deployment,
worker, and view state, find `--observe-only` and `--open-view`, understand that
`--frames 0` requires an explicit stop, and see that history recording is
opt-in through `--record`.

The audit is bounded to the primary success path and any recovery actually
encountered. It does not manufacture every failure mode already covered by
deterministic tests. Stale or contradictory help, documentation, next actions,
or output terminology is a finding even when an experienced contributor could
guess the intended command.

### Exact live procedure

Run from the `auto-driving` repository root. The six primary human commands are
the frozen milestone sequence:

```sh
./cli/automa vehicles status --chase-url http://localhost:5050

./cli/automa vehicles update perception \
  --id chase-sim-chaser \
  --algorithm lightweight_observer

./cli/automa vehicles automation run \
  --id chase-sim-chaser \
  --observe-only \
  --frames 0 \
  --open-view

./cli/automa vehicles status --id chase-sim-chaser

./cli/automa vehicles automation stop --id chase-sim-chaser

./cli/automa vehicles status --id chase-sim-chaser
```

Supplemental read-only captures do not replace the human commands:

```sh
./cli/automa vehicles status \
  --id chase-sim-chaser \
  --chase-url http://localhost:5050 \
  --json

./cli/automa vehicles status --id chase-sim-chaser --json
```

Capture the targeted JSON immediately after initial status, while the worker is
running, and after stop. Preserve exact argv, stdout, stderr, exit code, and
wall-clock ordering for every command. Run no command silently in a batch. If a
command fails, record and classify that result before any retry.

A retry is allowed only for an operator input error or the exact recovery
printed by the CLI. Both attempts remain in the transcript. Do not switch to an
internal command, hand-edit runtime state, add `--record`, take control, prepare
a scenario, or extend a timeout merely to turn a finding into a pass.

### Browser and frontend inspection

`automation run --open-view` must print a loopback URL only after its first
correlated publication is healthy. Inspect the opened Automa view and record a
single bounded screenshot that proves:

- a nonblank current front-camera image is visible;
- perception/observation content is rendered and intelligible;
- the displayed frame and perception publication are correlated under the
  lag-bounded machine rule (exact `current` match or proven lag within budget);
- the view identifies `chase-sim-chaser` and the overlay source; and
- no visual state contradicts the separately captured observation-only,
  no-applied-control, and recording-off machine evidence.

Also capture the view's `/api/latest` publication while the worker is running.
Cycle authority must report `action_policy=observe_only` and
`control_application=not_applied`. Correlation of camera and perception must
satisfy the **lag-bounded** rule in Machine acceptance gates (exact `current`
match, or `stale` with proven frame lag within the accepted budget). The
screenshot supplies human display evidence; `/api/latest` supplies machine
correlation evidence. Neither alone is sufficient.

Under continuous Chase, the product may publish a newer camera frame before
the previous perception result is ready (pipeline lag, typically tens to low
hundreds of milliseconds). That appears in the UI as Live green/red flicker
and as `overlay.status=stale` with a positive `frame_lag`. Bounded lag is
expected continuous-sim behavior; it is not by itself evidence that the view
is blank or that control was applied. Unbounded lag, missing perception, or
authority violations remain findings.

The Metrics UI remains open throughout. Opening the Automa view normally puts
the Metrics UI tab in the background, so the running targeted status is an
explicit background-tab probe: simulator frontend, Chase game, vehicle, and
passive capture must remain healthy. The operator then returns to Metrics UI
and confirms that the visible scenario, playback mode, control source, and
input were not changed by Automa. A background-only
`frontend_unresponsive`, disconnected frontend, blank view, lag **above** the
accepted budget, or misleading display is a product/external-contract finding,
not an acceptable fallback.

Browser-launch failure is classified separately from view health. If the CLI
reports a healthy view plus its URL but the operating system cannot open it,
record the warning and inspect the printed URL manually. That may pass the
view contract while retaining the existing platform-dependent launch risk. A
view that is unhealthy or lacks a usable URL cannot pass.

### Machine acceptance gates

The captured machine evidence must establish all of the following:

| Gate | Required evidence |
| --- | --- |
| Initial layers | Server reachable, frontend connected, Chase ready, vehicle discoverable, passive capture available, and a preservation receipt with equal before/after fields |
| Staging | `update perception` exits zero, identifies the packaged `lightweight_observer`, and reports the next runnable automation command without starting a worker |
| Startup | `automation run` exits zero only after at least one camera frame, its perception result, and a current-generation healthy loopback view |
| Running layers | Deployment `deployed`, worker `running`, view `available`, passive capture available, and human/JSON meanings agree |
| Correlation | View `/api/latest` reports lag-bounded camera/perception correlation: either (a) `overlay.status=current` and `frame.frame_id == overlay.source_frame_id`, or (b) `overlay.status=stale` with nonempty source frame id, typed camera and source frame indexes, derived lag `(frame.frame_index − overlay.source_frame_index)` equal to claimed `overlay.frame_lag`, and that lag in `1..MAX_FRAME_LAG` (accepted default **24**). `pending`, missing indexes, inconsistent claimed lag, or lag above budget fails. Poll-until-green is diagnostic only, not the pass criterion. `MAX_FRAME_LAG` is part of the reviewed acceptance surface (catalog/pin), not a free implementer constant |
| Authority | Worker and view report observation-only action policy and no applied control; the required session fingerprint remains equal to baseline |
| Default recording | `--record` is absent, worker state reports recording false, and the before/after history-directory listing contains no new run |
| Cleanup | Stop exits zero; final status reports worker `stopped`, no available current-generation view, deployment still staged, preserved authority/session state, and no Automa worker process remains |

Human output and JSON do not need identical formatting, but they must describe
the same layer states, authority, next action, failure boundary, and view
availability. Any contradiction is a finding.

### Evidence artifacts

The implementation PR owns this tracked directory:

`docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/`

It contains only bounded review evidence:

| Path | Contract |
| --- | --- |
| `README.md` | Human-readable environment receipt, chronological procedure, operator observations, finding ledger, verdict, limitations, and links to every artifact |
| `result.json` | `m007_live_cli_acceptance_v1` result with timestamps, repository identities, browser identity, ordered command outcomes, layer snapshots, fingerprints, frame correlation, authority, recording scan, cleanup, findings, and final status |
| `help-transcript.txt` | Exact help output and exit status for the four audited help levels |
| `cli-transcript.txt` | Ordered human CLI stdout/stderr and exit status, with local-only path prefixes redacted consistently |
| `initial-status.json` | Sanitized initial `automa_vehicle_status_v1` capture |
| `running-status.json` | Sanitized running `automa_vehicle_status_v1` capture |
| `stopped-status.json` | Sanitized final `automa_vehicle_status_v1` capture |
| `view-publication.json` | Sanitized loopback `/api/latest` payload proving frame/perception correlation and authority |
| `browser-view.png` | One cropped screenshot of the inspected Automa view without unrelated tabs or private content |
| `finding-<id>.png` | Optional cropped screenshot only when needed to make a recorded visual finding reviewable |

`result.json` records `pass`, `findings`, or `incomplete`; it never records
`skipped`. It lists each artifact with a SHA-256 digest. Generated runtime
directories, bundle archives, worker logs, caches, full browsing history, and
duplicate frame sequences remain untracked.

### Finding ledger and discrepancy routing

Each confirmed discrepancy receives a stable `M007-LIVE-###` id and these
fields in the README and result:

- classification: `acceptance_blocker`, `usability_defect`,
  `enhancement_candidate`, or `environment_blocker`;
- severity and affected surface;
- exact procedure step and command;
- expected and observed behavior;
- minimal reproduction and artifact links;
- owning repository and boundary, when known;
- operator impact; and
- disposition and required recheck.

The classifications have these consequences:

- An `acceptance_blocker` violates M007-05: unhealthy or uncorrelated view,
  false layer state, applied movement, changed protected simulator state,
  default history recording, unsafe cleanup, or a primary help/flow defect that
  prevents a reasonable operator from completing or interpreting the journey.
- A `usability_defect` is acceptance-blocking when it makes the supported path
  undiscoverable, contradictory, misleading, or unrecoverable. Smaller wording
  or layout defects may be nonblocking only when the operator can still execute
  and correctly interpret the frozen journey.
- An `enhancement_candidate` is a new preference or capability outside the
  accepted journey. It is preserved for the later CLI audit/disposition or
  another proposal but does not expand this PR.
- An `environment_blocker` means the declared starting environment was not
  available or identifiable. It yields `incomplete`, not a pass or a product
  verdict, unless the evidence proves a specific external contract failure.

Immediate PR comments can describe a raw observation. Before review, the
recorder must either confirm it into the committed ledger with evidence or mark
it `not_reproduced` with the attempted recheck. Comments alone are not durable
milestone evidence.

No product code, CLI help, reference documentation, Metrics UI code, or tests
are repaired in the evidence PR. A blocking finding keeps the PR draft or
changes-requested while a separately authorized repair is scoped at the owning
repository. After repair, the entire bounded session is rerun against exact new
commits; isolated screenshots or a retry of only the failed command cannot
retroactively produce a pass.

If the maintainer chooses to merge a conclusive findings unit before repair,
the normal successful handoff is not used. An exceptional reviewed `block`
receipt records result `Findings`, keeps M007-05 non-Met with the evidence PR,
does not promote the next frontier, and names the evidence required to
reactivate work.
The repair scope is derived and reviewed separately; this proposal does not
pre-authorize it.

### Cleanup is unconditional

The recorder runs `vehicles automation stop --id chase-sim-chaser` in a cleanup
path after startup succeeds or partially succeeds, including when browser
inspection or a later status gate fails. Final status and process liveness are
captured after cleanup. A failed stop is itself an acceptance blocker and must
be escalated until no repository-owned worker remains; it may not be hidden by
ending the transcript early.

## Ownership

| Boundary | Owner in this unit |
| --- | --- |
| Procedure and evidence completeness | Evidence README and `m007_live_cli_acceptance_v1` result |
| Human discoverability and output judgment | Operator named in the evidence receipt |
| Layer, preservation, authority, and cleanup truth | Captured `automa_vehicle_status_v1` snapshots |
| Browser rendering and frame correlation | Cropped screenshot plus `view-publication.json` |
| Discrepancy durability | Stable finding ledger committed in the evidence PR |
| Product repair | Explicitly outside this unit; later proposal/review at the owning repository |
| Milestone transition | Reviewed successful handoff template or an exceptional findings receipt |

## Affected Paths

Proposal review changes only:

- `docs/milestones/007-cli-operator-usability/proposals/live-cli-acceptance.md`;
- `docs/milestones/007-cli-operator-usability/plan.md`; and
- `docs/milestones/007-cli-operator-usability/plan.html`.

The later evidence implementation changes only the declared evidence directory
plus the canonical plan transition and rendered HTML. It reads, but does not
modify, the CLI, live test, operator reference, Metrics UI checkout, runtime
state, and loopback view implementation.

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| Help omits the runnable leaf or contradicts the guide | Record a usability defect; block when the primary journey is not discoverable or correctly interpretable |
| Human output says ready while JSON names a failed layer | Record an acceptance blocker; no layer may be inferred from the friendlier surface |
| Metrics UI is visible but its registered frontend stops answering when backgrounded | Record the exact `frontend_unresponsive` or disconnect evidence; do not foreground it merely to claim a pass |
| Startup prints a URL before correlated camera/perception health | Record an acceptance blocker even if the page later recovers |
| Browser launcher fails but the printed loopback URL is healthy | Inspect manually, retain launch warning, and judge view health separately |
| Screenshot looks healthy but `/api/latest` reports lag above budget, pending overlay, or unproven/inconsistent frame indexes | Machine correlation wins; record an acceptance blocker |
| Screenshot looks healthy and `/api/latest` reports stale with proven lag within budget | Machine correlation may pass under the lag-bounded gate; do not require exact `current` alone under continuous Chase |
| Claimed `frame_lag` does not equal derived index difference, or indexes are missing | Fail closed; do not trust a self-reported lag integer alone |
| JSON is healthy but the rendered image is blank, unreadable, or misleading | Human display evidence wins; record an acceptance blocker |
| Evaluator reference is absent while sensor identity is valid | Observation-only perception may pass; do not claim reference-dependent scoring |
| CLI changes scenario, playback mode, control source, or input | Record an acceptance blocker and stop; do not normalize the mutation |
| Playback advances naturally while its mode/authority stay unchanged | Record the exposed fingerprint values and distinguish normal simulation time from an Automa mutation |
| Worker reports observe-only but view or status reports applied control | Record an acceptance blocker and run cleanup immediately |
| A new timestamped history directory appears without `--record` | Record an acceptance blocker even if frames and view are otherwise healthy |
| Stop succeeds but the worker or current-generation view remains available | Record an acceptance blocker and continue cleanup/escalation |
| Operator mistypes a command | Preserve the attempt, classify it as operator input, and rerun only the intended step |
| External checkout is dirty but its diff is not identified | Mark the session incomplete; do not claim a reproducible contract |
| Product defect suggests an obvious one-line fix | Record the finding and owning boundary; do not edit product code in this PR |
| New feature idea is useful but outside M007-05 | Record an enhancement candidate and defer scope judgment to the later CLI audit/disposition or another proposal |
| Session cannot complete before operator leaves | Stop the worker, mark `incomplete`, and retain no partial acceptance claim |

## External Assumptions

- A local Metrics UI is reachable at `http://localhost:5050`, has its frontend
  visibly open, and currently exposes a Chase-compatible chaser and front
  camera.
- The exercised Metrics UI commit contains the passive-observation contract
  intended for this integration; the evidence records its exact identity
  rather than assuming a linked PR is deployed.
- The browser permits inspection of both the Metrics UI and loopback Automa
  view. Browser launch itself remains platform-dependent.
- The operator can judge whether the displayed camera and perception output
  are current and useful. Machine correlation supports but does not replace
  that judgment.
- The environment allows local background workers and loopback HTTP ports.
- One local session proves conformance only for the recorded commits, browser,
  and environment. It does not eliminate future Metrics UI drift or prove
  every browser/platform combination.

## Non-Goals

- Repairing auto-driving or Metrics UI product behavior.
- Redesigning CLI names, output, help, documentation, or browser presentation.
- Selecting or preparing a simulator scenario as part of the primary path.
- Taking vehicle control, applying idle or movement commands, or evaluating
  non-idle decisions.
- PiRacer, remote-view, authentication, public hosting, performance, soak,
  stress, multi-browser, or cross-platform qualification.
- Recording frame history with `--record` or committing full runtime output.
- Re-proving every deterministic failure and timeout matrix.
- CLI journey-coverage instrumentation, full CLI-leaf inventory, or capability
  disposition; those belong to the reviewed successor frontier chain.
- Milestone closeout or activation of another milestone.

## File Impact

### Proposal phase

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/live-cli-acceptance.md` | Add this reviewed live-evidence contract |
| `docs/milestones/007-cli-operator-usability/plan.md` | Record `proposal_in_review` through the workflow helper |
| `docs/milestones/007-cli-operator-usability/plan.html` | Regenerate the canonical rendering |

### Evidence implementation phase

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/README.md` | Add the environment receipt, chronological operator account, findings, and verdict |
| `docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/result.json` | Add the bounded machine-readable result and artifact digests |
| `docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/help-transcript.txt` | Add exact public help output |
| `docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/cli-transcript.txt` | Add exact ordered CLI output with local path redaction |
| `docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/*-status.json` | Add initial, running, and stopped status captures |
| `docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/view-publication.json` | Add current frame/perception and authority capture |
| `docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/browser-view.png` | Add one bounded successful-view screenshot |
| `docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/finding-*.png` | Add only screenshots required by confirmed visual findings |
| `docs/milestones/007-cli-operator-usability/plan.md` and `plan.html` | Record the implementation review transition; later handoff is mechanical |

No product, test, durable reference, or external-repository path changes in the
evidence implementation PR.

## Validation Plan

### Proposal validation

```sh
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/live-cli-acceptance-proposal \
  --base-sha <base-sha> \
  --head-sha <head-sha>
git diff --check
```

### Evidence validation

The implementation review runs:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
git diff --check
```

In addition, reviewers verify that all evidence files parse, every digest
matches, required result fields are present, command timestamps are ordered,
the three status documents retain `automa_vehicle_status_v1`, the screenshot
and `/api/latest` support the same view judgment, no new default run directory
appears, cleanup is terminal, and changed paths match the implementation file
impact. The manual live procedure above is the required external validation;
the default deterministic suite cannot substitute for it.

A passing implementation PR must be reviewed against the exact recorded
`auto-driving` and `metrics-ui` commits. A repair or rebase after the session
invalidates the pass until the whole procedure is rerun and the evidence is
replaced with one internally consistent session.

## Expected Handoff

Post-merge successful evidence template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "User-led live CLI operator acceptance against the recorded current Metrics UI commit, with help and flow audit, human/JSON transcript, correlated browser publication, observation-only authority, unchanged protected simulator state, no default recording, terminal cleanup, and tracked evidence in PR #{pr}",
  "criterion_updates": {
    "M007-05": {
      "status": "Met",
      "evidence": "Tracked live acceptance in PR #{pr} proves lag-bounded correlated camera/perception publication (exact current or proven frame lag within the accepted MAX_FRAME_LAG), healthy loopback rendering, truthful layer states, observation-only no-applied-control authority, protected-state preservation, no default run history, and stopped-worker cleanup against exact recorded auto-driving and Metrics UI commits"
    }
  },
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "CLI journey coverage foundation is promoted after successful tracked live CLI operator acceptance.",
    "revisit_when": "The coverage frontier proposes reproducible per-command and multi-command journey attribution before the full CLI-leaf audit."
  }
}
```

This template applies only to a `pass` result with no unresolved
acceptance-blocking finding. A conclusive findings PR uses a separately
reviewed exceptional block receipt and does not promote the next frontier or
mark M007-05 `Met`. An incomplete session has no handoff.

### Sequence after this proposal merges

1. Accept and merge this proposal PR into
   `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal`; verify
   `ready_for_implementation` and the exact proposal merge commit.
3. Start `m007/live-cli-acceptance`, add the evidence scaffold, and open its
   draft evidence PR before the operator session.
4. Conduct the user-led session one command at a time, reconcile PR comments
   into committed findings, and perform unconditional cleanup.
5. Accept the evidence PR only as a complete pass or a conclusive findings
   unit. Product repair remains separate.
6. On a pass, complete the normal handoff and promote **CLI journey coverage
   foundation**. On findings, stop before that promotion and review the next
   repair or reactivation decision explicitly.
