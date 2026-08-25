# Proposal: Timeout input-envelope consistency

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Timeout input-envelope consistency |
| Proposal branch | `m007/timeout-input-envelope-proposal` |
| Implementation branch | `m007/timeout-input-envelope` |
| Exit criterion | M007-04 |
| Review finding | [P2] malformed timeout values leak `ValueError` on PR #81 ([inline finding](https://github.com/GeorgeLuo/auto-driving/pull/81#discussion_r3849733271)) |
| Review kind | Review repair |

## Review Question

Do all affected primary CLI commands reject non-positive and non-finite timeout
inputs through one stable input/error boundary before execution, without
tracebacks, while preserving valid timeout behavior?

This is a new product review unit because PR #81 is a closed-plan cumulative
review surface. The timeout finding must be repaired and reviewed on its owning
CLI boundary; PR #81 remains historical and is not edited by this unit.

The proposal is grounded in the exact Phase C review finding. At the reviewed
PR #81 head, `vehicles status --timeout-s -1` returned a bounded exit-2 error,
while the primary `vehicles automation run` and `vehicles update perception`
handlers allowed the shared discovery validator's `ValueError` to escape for
invalid timeout values. The affected input class is the finite-positive
contract already used by the shared readiness gate, not a request to change
the timeout budget or readiness phases.

## Proposed Contract

### Affected primary commands

The implementation owns the `--timeout-s` input boundary for these M007
journey consumers:

| Command | Human form | Existing machine-readable form |
| --- | --- | --- |
| `vehicles status` | Yes | `--json` |
| `vehicles automation run` | Yes | No `--json` flag exists; exit code and human error remain the machine boundary |
| `vehicles update perception` | Yes | `--json` |

`vehicles active` is the already-correct reference behavior. Other timeout
flags used by memory, physical, deployment, or unrelated operation commands
are not silently swept into this frontier.

### Input and error behavior

1. `--timeout-s` is valid only when its parsed value is finite and strictly
   greater than zero. The rejected set includes `0`, negative values, `nan`,
   `-nan`, `inf`, `+inf`, and `-inf` wherever the parser accepts them.
2. Each affected handler invokes the common validation/error boundary before
   discovery, local staging, worker launch, browser opening, or any other
   command work. An invalid timeout therefore cannot create or modify a local
   activation, worker, view, or runtime record and cannot trigger a simulator
   probe.
3. Human mode returns exit code `2`, writes one stable actionable diagnostic to
   the command's established error channel, and emits no traceback. The
   diagnostic identifies the command and `--timeout-s` as invalid and uses the
   existing finite-positive wording/category rather than exposing
   `ValueError`.
4. A command that already supports `--json` returns the same stable timeout
   error category in its machine-readable error envelope, with exit code `2`,
   and does not print a successful payload or traceback. The implementation may
   reuse the repository's existing `automa_cli_error_v1` category shape; it may
   not invent a command-specific timeout category.
5. A finite positive timeout is passed through unchanged to the existing
   downstream operation-level deadline/readiness logic. This unit does not
   change deadline duration, phase order, retries, or timeout exception meaning
   after command work has begun.
6. Parser failures for values that cannot be parsed as floats remain ordinary
   argparse input failures. This unit closes the accepted non-positive and
   non-finite value class and does not redesign all CLI parsing.

### Root cause and repair boundary

The current behavior validates the timeout in one top-level handler but lets
the same value reach a shared discovery validator from the other primary
handlers. The local/staged branch of perception update also makes validation
ordering important: invalid input must be rejected before an offline shortcut
can bypass discovery. The owning boundary is therefore the shared CLI
input/error adapter used by the `app.py` handlers, with downstream modules
remaining responsible for operation behavior rather than command-envelope
translation.

The implementation may place the helper in the clearest existing CLI owner,
but it must leave one authoritative validation rule and one error category for
all three commands. It must not catch every downstream `ValueError` and label
unrelated runtime failures as invalid user input.

## Ownership

| Boundary | Owner in this unit |
| --- | --- |
| CLI argument validation, command/error envelope, and exit code | `cli/automa_cli/app.py` handler boundary and its shared timeout helper |
| Existing operation-level timeout semantics | `cli/automa_cli/vehicles.py` discovery/readiness consumer; no budget redesign |
| Automation and perception work protection | `cli/automa_cli/automation.py` and `cli/automa_cli/perception.py` receive only validated values; their existing runtime error categories remain intact |
| Regression proof | Focused CLI parser/handler tests plus the default repository suite |
| Milestone transition | The reviewed implementation handoff for M007-04 |

No separately owned external repository or live simulator capability is needed
to close this input-envelope defect.

## Affected Paths

Proposal review changes only this proposal artifact, the canonical milestone
plan, and generated plan HTML. The later implementation may touch:

- `cli/automa_cli/app.py` for the common timeout input/error boundary and the
  three affected handlers;
- `cli/automa_cli/vehicles.py` only if the existing validator/error category
  must be shared or aligned, without changing discovery deadline semantics;
- `cli/automa_cli/automation.py` and `cli/automa_cli/perception.py` only if a
  narrow call-site adjustment is required to guarantee validation precedes
  work; and
- focused tests under `tests/cli/` covering parser, human, JSON, call-order,
  and valid-value regression behavior.

No product code, tests, runtime artifacts, or documentation implementation is
included in this proposal PR.

## Adversarial Matrix

| Attempted bypass | Required response |
| --- | --- |
| `--timeout-s 0` on each affected command | Exit 2 with the stable timeout-input error; no downstream call or side effect |
| `--timeout-s -1` on each affected command | Same as zero; no uncaught `ValueError` or traceback |
| `--timeout-s nan` or `--timeout-s -nan` | Same stable rejection; NaN must not reach deadline arithmetic |
| `--timeout-s inf`, `+inf`, or `-inf` | Same stable rejection; infinity must not reach discovery, staging, or worker startup |
| Invalid timeout with an unreachable or missing vehicle | Input error wins before discovery; the result must not become a vehicle/network error |
| Invalid timeout on an existing offline/staged perception bundle | Reject before the local shortcut, manifest write, or staging side effect |
| Invalid timeout on automation run with `--open-view` or an existing worker | Reject before worker launch/reuse, view health, browser launch, or runtime-file mutation |
| Invalid timeout with `--json` on status or perception update | Exit 2 and emit the stable machine-readable timeout category, not a success payload, mixed text, or traceback |
| Valid finite positive timeout, including a small positive value | Preserve the existing downstream value and operation-level deadline behavior |
| A downstream `ValueError` unrelated to timeout input | Preserve its owning runtime error handling; do not misclassify it as `timeout_invalid` |
| Omitted `--timeout-s` | Preserve each command's current default and existing behavior |
| Non-numeric timeout token | Preserve argparse's existing parse failure; do not broaden this unit into parser redesign |

## External Assumptions

- Python `argparse` continues to deliver `--timeout-s` values as floats for
  numeric, NaN, and infinity spellings; the boundary must still validate the
  post-parse value.
- The existing CLI launcher propagates handler exit code `2` and does not need
  a new process-level exception wrapper.
- Existing JSON-capable commands can carry a shared timeout error category
  without changing their successful payload schemas.
- No live simulator, browser, worker, or Pi device is required for the
  deterministic input-boundary proof. Existing live acceptance artifacts remain
  historical and are not recaptured by this unit.

## Non-Goals

- Changing the default timeout, deadline allocation, readiness phases, retry
  policy, or underlying network timeout implementation.
- Repairing the separate PiRacer staged-inspection P1 or Chase image
  dimension/encoding P2 from PR #81.
- Normalizing every timeout flag in the CLI, including memory, physical,
  deployment, or unrelated operation commands.
- Adding a new `--json` flag to automation run or redesigning successful JSON
  payloads.
- Catching arbitrary runtime exceptions at the process boundary.
- Editing cumulative PR #81, its closeout packet, or the completed-milestone
  ledger.
- Beginning implementation in this proposal PR.

## File Impact

| Path | Proposal change | Later implementation role |
| --- | --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/timeout-input-envelope.md` | Add this reviewed contract | Immutable accepted proposal |
| `docs/milestones/007-cli-operator-usability/plan.md` | Select the current frontier and record M007-04 ownership; proposal workflow forbids pre-claiming criterion or risk changes | Record proposal/implementation handoffs only |
| `docs/milestones/007-cli-operator-usability/plan.html` | Generated rendering of the plan transition | Regenerated with canonical plan changes |
| `cli/automa_cli/app.py` | None | Common timeout validation/error envelope at affected handlers |
| `cli/automa_cli/vehicles.py` | None | Shared validator/category alignment only if required by the owner boundary |
| `cli/automa_cli/automation.py` | None | No invalid timeout reaches worker startup; narrow adapter change only if required |
| `cli/automa_cli/perception.py` | None | No invalid timeout reaches staging/discovery; narrow adapter change only if required |
| `tests/cli/` | None | Focused invalid/valid timeout matrix, JSON/human output, and no-side-effect regressions |

## Validation Plan

### Proposal PR

The proposal PR must contain only this artifact, the canonical plan transition,
and generated plan HTML:

```sh
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 -m unittest \
  tests.docs.test_milestone_proposal_workflow \
  tests.docs.test_milestone_planning
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/timeout-input-envelope-proposal \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

The proposal review verifies the exact PR #81 finding link, one review
question, the M007-04 owner, the no-side-effect ordering, the complete invalid
value matrix, and the absence of implementation files.

### Implementation PR after proposal acceptance

Deterministic tests must:

- run all affected commands with `0`, negative, NaN, and infinity values in
  human mode and each existing JSON mode;
- assert exit code `2`, stable command/error category, no traceback, and no
  successful payload;
- patch discovery, local staging, worker launch, view/browser opening, and
  runtime writes to prove invalid values are rejected before command work;
- verify a finite positive value reaches the existing consumer unchanged and
  that the omitted default remains unchanged; and
- verify unrelated downstream failures are not relabeled as invalid timeout
  input.

Run the focused timeout tests, the affected CLI suites, the repository suite,
workflow validation, Markdown rendering check, and `git diff --check`. No live
simulator or browser run is required for this deterministic review repair.

## Expected Handoff

Post-merge successful implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Timeout input-envelope consistency in PR #{pr}: vehicles status, vehicles automation run, and vehicles update perception reject zero, negative, NaN, and infinite --timeout-s values before command work with stable exit-2 human or existing machine-readable errors and no traceback; finite positive and default timeout behavior remains unchanged; focused regressions and the repository suite pass.",
  "criterion_updates": {
    "M007-04": {
      "status": "Met",
      "evidence": "PR #{pr} closes the Phase C timeout input-envelope finding at the shared CLI boundary: every affected primary consumer rejects non-positive and non-finite timeout values before discovery, staging, or worker/view work, with stable exit-2 errors and no traceback, while valid bounded timeout behavior is preserved."
    }
  },
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "The timeout repair is promoted and the milestone remains idle; the separate Phase C PiRacer and Chase image-envelope findings remain outside this unit.",
    "revisit_when": "A later proposal is justified by the remaining Phase C finding or by a new milestone acceptance decision."
  }
}
```

This handoff applies only after the implementation review has verified the
entire matrix and the exact-head acceptance receipt. It does not mark M007-06
Met or authorize cumulative PR #81 to merge.

## Sequence After This Proposal Merges

1. Obtain the exact-head proposal review receipt and merge this proposal into
   `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal` for the proposal PR and confirm
   `ready_for_implementation` with the recorded reviewed head and merge commit.
3. Start `m007/timeout-input-envelope` and implement only this contract.
4. Review the implementation against the matrix, repair within this unit if
   required, then complete the implementation handoff.
5. Return M007 to idle. A later proposal may route the remaining Phase C
   finding; this unit does not select or implement it.

## Review Kind

**Review repair** — a separate owned product review unit is required because
the exact P2 was found during the rejected cumulative PR #81 review and the
closed-plan PR must remain unchanged. The unit is bounded to the timeout
input/error boundary and its regressions.
