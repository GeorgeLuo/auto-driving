from __future__ import annotations

import json


MILESTONE_NUMBER = "900"
MILESTONE_BRANCH = "milestone/900-workflow-fixture"
PLAN_RELATIVE = "docs/milestones/900-workflow-fixture/plan.md"
PROPOSAL_RELATIVE = (
    "docs/milestones/900-workflow-fixture/proposals/evidence-policy.md"
)
PROPOSAL_BRANCH = "m900/evidence-policy-proposal"
IMPLEMENTATION_BRANCH = "m900/evidence-policy"
PROPOSAL_AMENDMENT_BRANCH = "m900/amend-evidence-policy-lag"
PROPOSAL_AMENDMENT_RELATIVE = (
    "docs/milestones/900-workflow-fixture/proposals/"
    "evidence-policy-lag-amendment.md"
)
NEXT_PROPOSAL_BRANCH = "m900/closeout-proposal"
NEXT_IMPLEMENTATION_BRANCH = "m900/closeout"
CURRENT_FRONTIER = "Evidence policy"
NEXT_FRONTIER = "Milestone closeout"
CURRENT_CRITERION = "M900-01"
CLOSEOUT_CRITERION = "M900-03"
RESOLVED_RISK = "Evidence recurrence has no explicit compatibility contract"
BASELINE_SHA = "abc1234"


def ready_plan_text() -> str:
    return f"""# Milestone 900 - Workflow fixture

| Field | Value |
| --- | --- |
| Status | Active |
| Milestone branch | `{MILESTONE_BRANCH}` |
| Current frontier | {CURRENT_FRONTIER} |
| Contract baseline | `{BASELINE_SHA}` |
| Grandfathered PRs | #1 |
| Cutover | Synthetic mid-milestone workflow fixture |

## Exit Criteria

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |
| {CURRENT_CRITERION} | Evidence conflicts are deterministic | Partial | Policy remains open |
| M900-02 | Existing operator path remains stable | Met | Deterministic fixture |
| {CLOSEOUT_CRITERION} | Milestone closeout is accepted | Blocked | Requires current frontier |

## Current Delivery

### Current Frontier

**{CURRENT_FRONTIER}**

- Workflow state: ready_for_proposal
- Proposal branch: `{PROPOSAL_BRANCH}`
- Implementation branch: `{IMPLEMENTATION_BRANCH}`
- Proposal path: `{PROPOSAL_RELATIVE}`
- Review kind: Deterministic invariant closure
- Review question: Does repeated evidence follow one deterministic contract?
- Acceptance owner: Synthetic evidence ledger
- Exit criteria affected: {CURRENT_CRITERION}
- Prerequisite: Baseline behavior is accepted
- Milestone-level non-goal: Semantic identity

### Next-Frontier Candidate

**{NEXT_FRONTIER}**

- Proposal branch: `{NEXT_PROPOSAL_BRANCH}`
- Implementation branch: `{NEXT_IMPLEMENTATION_BRANCH}`
- Proposal path: `docs/milestones/900-workflow-fixture/proposals/closeout.md`
- Review kind: Milestone closeout
- Review question: Is the synthetic milestone complete?
- Acceptance owner: Synthetic closeout
- Exit criteria affected: {CLOSEOUT_CRITERION}
- Prerequisite: Every other criterion is Met
- Milestone-level non-goal: New runtime behavior

## Workflow History

| Frontier | State | Evidence |
| --- | --- | --- |
| {CURRENT_FRONTIER} | ready_for_proposal | Synthetic frontier is ready. |

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |
| Baseline #1 (`{BASELINE_SHA}`) | Is the fixture baseline accepted? | Accepted before compact-contract adoption | M900-01-M900-03 | Synthetic baseline |

The baseline row is the explicit adoption boundary.

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |
| {RESOLVED_RISK} | Recurrence may silently overwrite meaning | Current frontier |
| Process state is local | Restart continuity is absent | Explicit non-goal |
"""


def proposal_review_plan_text() -> str:
    text = ready_plan_text().replace(
        "- Workflow state: ready_for_proposal\n",
        "- Workflow state: proposal_in_review\n",
        1,
    )
    text = text.replace(
        f"**{CURRENT_FRONTIER}**\n\n",
        f"**{CURRENT_FRONTIER}**\n\n- PR: [#58](https://example.invalid/58)\n",
        1,
    )
    return text.replace(
        "\n\n## Accepted Review Units",
        f"\n| {CURRENT_FRONTIER} | proposal_in_review | Proposal branch started. |"
        "\n\n## Accepted Review Units",
        1,
    )


def implementation_review_plan_text() -> str:
    text = ready_plan_text().replace(
        "- Workflow state: ready_for_proposal\n",
        "- Workflow state: implementation_in_review\n",
        1,
    )
    text = text.replace(
        f"**{CURRENT_FRONTIER}**\n\n",
        f"**{CURRENT_FRONTIER}**\n\n- PR: [#59](https://example.invalid/59)\n",
        1,
    )
    text = text.replace(
        f"- Proposal path: `{PROPOSAL_RELATIVE}`\n",
        f"- Proposal path: `{PROPOSAL_RELATIVE}`\n"
        "- Accepted proposal: [#58](https://example.invalid/58) at `def5678`\n",
        1,
    )
    return text.replace(
        "\n\n## Accepted Review Units",
        f"\n| {CURRENT_FRONTIER} | proposal_in_review | Proposal branch started. |"
        f"\n| {CURRENT_FRONTIER} | ready_for_implementation | Proposal PR #58 accepted. |"
        f"\n| {CURRENT_FRONTIER} | implementation_in_review | Implementation branch started. |"
        "\n\n## Accepted Review Units",
        1,
    )


def handoff_template() -> dict[str, object]:
    return {
        "schema": "milestone_handoff_template_v1",
        "outcome": "advance",
        "result": "Accepted",
        "durable_evidence": "Focused evidence tests in PR #{pr}",
        "criterion_updates": {
            CURRENT_CRITERION: {
                "status": "Met",
                "evidence": "Evidence policy accepted in PR #{pr}",
            }
        },
        "risk_remove": [RESOLVED_RISK],
        "risk_upsert": [],
        "next_frontier": {
            "state": "none",
            "reason": "Closeout is current.",
            "revisit_when": "Closeout decides what follows.",
        },
    }


def proposal_text() -> str:
    template = json.dumps(handoff_template(), indent=2, sort_keys=True)
    return f"""# Proposal: Evidence policy

## Review Question

Is the evidence policy bounded and deterministic?

## Proposed Contract

One slot has one structural contract.

## Ownership

The synthetic evidence ledger owns compatibility.

## Affected Paths

Update, expiry, reset, and replay.

## Adversarial Matrix

| Case | Expected |
| --- | --- |
| Conflict | Invalidate |

## External Assumptions

Plugin IDs are stable within a source.

## Non-Goals

Semantic truth selection.

## File Impact

Memory implementation and focused tests.

## Validation Plan

Unit and replay tests.

## Expected Handoff

```json
{template}
```
"""


def proposal_amendment_text() -> str:
    return """# Proposal Amendment: Evidence policy lag tolerance

## Review Question

Is bounded lag accepted without weakening attributable evidence?

## Reason For Amendment

Live observation proved that exact-current correlation rejects known-good lag.

## Contract Delta

Accept current or bounded-stale observations with an explicit lag value.

## Ownership

The evidence validator owns the bounded-lag decision.

## Affected Paths

Live validation and operator-visible diagnostics.

## Adversarial Matrix

| Case | Expected |
| --- | --- |
| Beyond bound | Reject with observed lag |

## External Assumptions

Sequence identifiers are monotonic within one run.

## Non-Goals

Unbounded eventual consistency.

## File Impact

Validator, focused tests, and command catalog expectations.

## Validation Plan

Exercise current, bounded-stale, beyond-bound, and malformed observations.
"""


def handoff_receipt(*, merge_commit: str = "deadbee") -> dict[str, object]:
    receipt = handoff_template()
    receipt["schema"] = "milestone_handoff_v1"
    receipt["accepted_pr"] = 59
    receipt["accepted_merge_commit"] = merge_commit
    return receipt
