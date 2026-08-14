from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from docs.milestones.workflow import (
    ContractReviewReceipt,
    PlanContractError,
    _fetch_pr_review_metadata,
    accept_proposal,
    accept_proposal_amendment,
    abandon_proposal_amendment,
    format_paused_implementation,
    parse_paused_implementation,
    start_implementation_branch,
    start_proposal_amendment_branch,
    validate_merged_proposal_amendment_metadata,
    validate_merged_proposal_metadata,
    validate_implementation_adjunct_body,
    validate_plan_text,
    validate_proposal_amendment_text,
    validate_proposal_text,
    validate_repair_cycle_governance_body,
    validate_review_unit_transition,
    validate_review_unit_git_diff,
    verify_implementation_pause_against_github,
)
from tests.docs.milestone_workflow_fixtures import (
    CURRENT_CRITERION,
    CURRENT_FRONTIER,
    IMPLEMENTATION_ADJUNCT_BRANCH,
    IMPLEMENTATION_BRANCH,
    MILESTONE_BRANCH,
    PLAN_RELATIVE,
    PROPOSAL_AMENDMENT_BRANCH,
    PROPOSAL_AMENDMENT_RELATIVE,
    PROPOSAL_BRANCH,
    PROPOSAL_RELATIVE,
    implementation_adjunct_body,
    implementation_review_plan_text,
    proposal_amendment_text,
    proposal_text,
    ready_plan_text,
    repair_cycle_governance_body,
)

PLAN_REVISION_BRANCH = "m900/plan-shadow-proposals"
REVISED_FRONTIER = "Shadow action proposals"
REVIEW_KIND = "Deterministic invariant closure"
IMPL_PR_URL = "https://github.com/example/auto-driving/pull/59"
IMPL_ESCALATION_URL = (
    "https://github.com/example/auto-driving/pull/59#pullrequestreview-9001"
)


def _review_unit_body(review_kind: str = REVIEW_KIND) -> str:
    return (
        "# Synthetic review unit\n\n"
        "## Review Kind\n\n"
        f"{review_kind}\n\n"
        "## Review Question\n\n"
        "Is the bounded contract acceptable?\n\n"
        f"{repair_cycle_governance_body()}\n"
    )


def _contract_review(
    *,
    head_oid: str,
    state: str = "COMMENTED",
    outcome: str = "accepted",
    submitted_at: str = "2026-08-12T18:00:00Z",
) -> dict[str, object]:
    body = (
        "## Contract Review Receipt\n\n"
        f"- Outcome: `{outcome}`\n"
        if state == "COMMENTED"
        else ""
    )
    return {
        "state": state,
        "body": body,
        "commit": {"oid": head_oid},
        "submittedAt": submitted_at,
        "author": {"login": "workflow-reviewer"},
        "authorAssociation": "COLLABORATOR",
        "authorCanPushToRepository": True,
        "includesCreatedEdit": False,
    }


def _accepted_review_receipt(
    head_oid: str = "f" * 40,
) -> ContractReviewReceipt:
    return ContractReviewReceipt(
        head_oid=head_oid,
        reviewer="workflow-reviewer",
        reviewer_association="COLLABORATOR",
        submitted_at="2026-08-12T18:00:00Z",
    )


def _replace_section_body(text: str, heading: str, body: str) -> str:
    pattern = rf"(?ms)^{re.escape(heading)}\n.*?(?=^## |\Z)"
    replacement = f"{heading}\n\n{body.strip()}\n\n"
    updated, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise AssertionError(f"missing fixture section: {heading}")
    return updated


def _remove_section(text: str, heading: str) -> str:
    pattern = rf"(?ms)^{re.escape(heading)}\n.*?(?=^## |\Z)"
    updated, count = re.subn(pattern, "", text, count=1)
    if count != 1:
        raise AssertionError(f"missing fixture section: {heading}")
    return updated


def _move_to_review(text: str, *, implementation: bool = False) -> str:
    state = validate_plan_text(text)
    old_state = state.current.fields["workflow state"]
    new_state = (
        "implementation_in_review" if implementation else "proposal_in_review"
    )
    updated = text.replace(
        f"- Workflow state: {old_state}\n",
        f"- Workflow state: {new_state}\n",
        1,
    )
    return updated.replace(
        "\n\n## Accepted Review Units",
        f"\n| {state.current.name} | {new_state} | Review branch started. |"
        "\n\n## Accepted Review Units",
        1,
    )


def _revise_plan(text: str) -> str:
    revised = text.replace(
        f"| Current frontier | {CURRENT_FRONTIER} |",
        f"| Current frontier | {REVISED_FRONTIER} |",
        1,
    ).replace(
        f"**{CURRENT_FRONTIER}**",
        f"**{REVISED_FRONTIER}**",
        1,
    ).replace(
        "Does repeated evidence follow one deterministic contract?",
        "Can independent plugins emit attributable shadow action proposals?",
        1,
    )
    return revised.replace(
        "\n\n## Accepted Review Units",
        f"\n| {REVISED_FRONTIER} | ready_for_proposal | "
        "Plan revision: scope replaced before proposal authoring. |"
        "\n\n## Accepted Review Units",
        1,
    )


def _impl_github_payload(
    head_oid: str,
    *,
    state: str = "OPEN",
    is_draft: bool = True,
    merged: bool = False,
    url: str = IMPL_PR_URL,
    number: int = 59,
    head_ref: str = IMPLEMENTATION_BRANCH,
    base_ref: str = MILESTONE_BRANCH,
) -> dict[str, object]:
    return {
        "number": number,
        "url": url,
        "state": state,
        "isDraft": is_draft,
        "merged": merged,
        "baseRefName": base_ref,
        "headRefName": head_ref,
        "headRefOid": head_oid,
    }


def _impl_escalation_receipt(
    *,
    pr: int = 59,
    association: str = "OWNER",
    body: str = (
        "Durable route: `proposal-amendment`. Pause the implementation PR "
        "and amend the accepted contract."
    ),
) -> dict[str, object]:
    return {
        "id": "9001",
        "pr": pr,
        "authorAssociation": association,
        "body": body,
        "state": "COMMENTED",
        "login": "operator",
    }


def _paused_impl_value(
    head: str,
    *,
    disposition: str = "paused",
    resume: str = "reconcile",
) -> str:
    return format_paused_implementation(
        pr=59,
        url=IMPL_PR_URL,
        branch=IMPLEMENTATION_BRANCH,
        head=head,
        disposition=disposition,
        resume=resume,
        receipt=IMPL_ESCALATION_URL,
    )


def _mock_impl_github(
    head_oid: str,
    *,
    payload: dict[str, object] | None = None,
    receipt: dict[str, object] | None = None,
):
    return mock.patch.multiple(
        "docs.milestones.workflow",
        fetch_github_pr_view=mock.Mock(
            return_value=payload or _impl_github_payload(head_oid)
        ),
        fetch_github_review_receipt=mock.Mock(
            return_value=receipt or _impl_escalation_receipt()
        ),
    )


def _move_impl_to_amendment_review(
    text: str,
    *,
    paused: str,
    branch: str = PROPOSAL_AMENDMENT_BRANCH,
    path: str = PROPOSAL_AMENDMENT_RELATIVE,
) -> str:
    state = validate_plan_text(text)
    accepted = state.current.fields["accepted proposal"]
    updated = text.replace(
        "- Workflow state: implementation_in_review\n",
        "- Workflow state: proposal_amendment_in_review\n",
        1,
    )
    updated = updated.replace(
        "- PR: [#59](https://example.invalid/59)\n",
        "",
        1,
    )
    updated = updated.replace(
        f"- Accepted proposal: {accepted}\n",
        f"- Accepted proposal: {accepted}\n"
        f"- Proposal amendment branch: `{branch}`\n"
        f"- Proposal amendment path: `{path}`\n"
        "- Amendment source state: implementation_in_review\n"
        f"- Paused implementation: {paused}\n",
        1,
    )
    return updated.replace(
        "\n\n## Accepted Review Units",
        f"\n| {CURRENT_FRONTIER} | proposal_amendment_in_review | "
        "Proposal amendment branch started. |"
        "\n\n## Accepted Review Units",
        1,
    )


def _accepted_plan() -> str:
    return accept_proposal(
        _move_to_review(ready_plan_text()),
        proposal_pr=60,
        merge_commit="a" * 40,
        proposal_url="https://example.invalid/60",
        review_receipt=_accepted_review_receipt(),
    )


def _move_to_amendment_review(
    text: str,
    *,
    branch: str = PROPOSAL_AMENDMENT_BRANCH,
    path: str = PROPOSAL_AMENDMENT_RELATIVE,
) -> str:
    state = validate_plan_text(text)
    accepted = state.current.fields["accepted proposal"]
    updated = text.replace(
        "- Workflow state: ready_for_implementation\n",
        "- Workflow state: proposal_amendment_in_review\n",
        1,
    )
    if state.current.fields.get("proposal amendment branch"):
        old_branch = state.current.fields["proposal amendment branch"]
        old_path = state.current.fields["proposal amendment path"]
        updated = updated.replace(
            f"- Proposal amendment branch: {old_branch}\n",
            f"- Proposal amendment branch: `{branch}`\n",
            1,
        ).replace(
            f"- Proposal amendment path: {old_path}\n",
            f"- Proposal amendment path: `{path}`\n",
            1,
        )
    else:
        updated = updated.replace(
            f"- Accepted proposal: {accepted}\n",
            f"- Accepted proposal: {accepted}\n"
            f"- Proposal amendment branch: `{branch}`\n"
            f"- Proposal amendment path: `{path}`\n",
            1,
        )
    return updated.replace(
        "\n\n## Accepted Review Units",
        f"\n| {CURRENT_FRONTIER} | proposal_amendment_in_review | "
        "Proposal amendment branch started. |"
        "\n\n## Accepted Review Units",
        1,
    )


class ProposalDocumentTests(unittest.TestCase):
    def test_required_proposal_shape_is_accepted(self) -> None:
        validate_proposal_text(proposal_text())

    def test_universal_claim_requires_trust_and_authority_model(self) -> None:
        invalid = proposal_text().replace(
            "## Trust And Authority Model",
            "## Trust Notes",
        )
        with self.assertRaisesRegex(PlanContractError, "Trust And Authority Model"):
            validate_proposal_text(invalid)

    def test_universal_claim_requires_evidence_topology_and_capture(self) -> None:
        invalid = proposal_text().replace(
            "## Evidence Topology And Capture Strategy",
            "## Evidence Notes",
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "Evidence Topology And Capture Strategy",
        ):
            validate_proposal_text(invalid)

    def test_universal_claim_rejects_an_empty_contractability_section(self) -> None:
        invalid = _replace_section_body(
            proposal_text(),
            "## Trust And Authority Model",
            "<!-- model pending -->",
        )
        with self.assertRaisesRegex(PlanContractError, "must be completed"):
            validate_proposal_text(invalid)

    def test_non_universal_proposal_does_not_require_contractability_sections(
        self,
    ) -> None:
        ordinary = proposal_text().replace(
            "Is the evidence policy bounded and deterministic?",
            "Does the evidence policy assign one structural contract?",
        )
        ordinary = _remove_section(
            ordinary,
            "## Trust And Authority Model",
        )
        ordinary = _remove_section(
            ordinary,
            "## Evidence Topology And Capture Strategy",
        )
        validate_proposal_text(ordinary)

    def test_universal_language_in_proposed_contract_triggers_the_gate(self) -> None:
        invalid = proposal_text().replace(
            "Is the evidence policy bounded and deterministic?",
            "Does the evidence policy assign one structural contract?",
        ).replace(
            "One slot has one structural contract.",
            "One slot has one exact structural contract.",
        )
        invalid = _remove_section(invalid, "## Trust And Authority Model")
        with self.assertRaisesRegex(PlanContractError, "Trust And Authority Model"):
            validate_proposal_text(invalid)

    def test_missing_validation_plan_is_rejected(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "Validation Plan"):
            validate_proposal_text(
                proposal_text().replace("## Validation Plan", "## Checks")
            )

    def test_missing_expected_handoff_is_rejected(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "Expected Handoff"):
            validate_proposal_text(
                proposal_text().replace("## Expected Handoff", "## Later State")
            )

    def test_required_proposal_amendment_shape_is_accepted(self) -> None:
        validate_proposal_amendment_text(proposal_amendment_text())

    def test_universal_amendment_requires_contractability_delta(self) -> None:
        invalid = _remove_section(
            proposal_amendment_text(),
            "## Evidence Topology And Capture Strategy",
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "Evidence Topology And Capture Strategy",
        ):
            validate_proposal_amendment_text(invalid)

    def test_non_universal_amendment_does_not_require_contractability_sections(
        self,
    ) -> None:
        ordinary = proposal_amendment_text().replace(
            "Is bounded lag accepted without weakening attributable evidence?",
            "Is attributable lag accepted without weakening evidence?",
        ).replace(
            "Accept current or bounded-stale observations with an explicit lag value.",
            "Accept current or recent observations with an explicit lag value.",
        )
        ordinary = _remove_section(ordinary, "## Trust And Authority Model")
        ordinary = _remove_section(
            ordinary,
            "## Evidence Topology And Capture Strategy",
        )
        validate_proposal_amendment_text(ordinary)

    def test_proposal_amendment_requires_contract_delta(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "Contract Delta"):
            validate_proposal_amendment_text(
                proposal_amendment_text().replace(
                    "## Contract Delta",
                    "## Revised Idea",
                )
            )

    def test_required_implementation_adjunct_body_is_accepted(self) -> None:
        validate_implementation_adjunct_body(
            implementation_adjunct_body(),
            base_branch=IMPLEMENTATION_BRANCH,
        )

    def test_implementation_adjunct_requires_implement_now_direction(self) -> None:
        invalid = implementation_adjunct_body().replace(
            "Requested disposition: `implement-now`",
            "Requested disposition: `later`",
        )
        with self.assertRaisesRegex(PlanContractError, "implement-now"):
            validate_implementation_adjunct_body(
                invalid,
                base_branch=IMPLEMENTATION_BRANCH,
            )

    def test_implementation_adjunct_requires_checked_compatibility(self) -> None:
        invalid = implementation_adjunct_body().replace(
            "- [x] The parent contract remains true without this adjunct.",
            "- [ ] The parent contract remains true without this adjunct.",
        )
        with self.assertRaisesRegex(PlanContractError, "not checked"):
            validate_implementation_adjunct_body(
                invalid,
                base_branch=IMPLEMENTATION_BRANCH,
            )

    def test_implementation_adjunct_names_its_actual_base(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "must match its PR base"):
            validate_implementation_adjunct_body(
                implementation_adjunct_body(),
                base_branch="m900/different-parent",
            )


class RepairCycleGovernanceTests(unittest.TestCase):
    def test_initial_review_receipt_is_accepted(self) -> None:
        self.assertEqual(
            validate_repair_cycle_governance_body(
                repair_cycle_governance_body()
            ),
            0,
        )

    def test_one_substantial_cycle_does_not_require_escalation(self) -> None:
        body = repair_cycle_governance_body(
            rows=(
                "| 1 | https://github.example/review/1 | substantial | "
                "abc1234 | Replaced the enforcement boundary. |"
            ),
        )

        self.assertEqual(validate_repair_cycle_governance_body(body), 1)

    def test_second_substantial_cycle_requires_completed_escalation(self) -> None:
        body = repair_cycle_governance_body(
            rows=(
                "| 1 | https://github.example/review/1 | substantial | "
                "abc1234 | Replaced the enforcement boundary. |\n"
                "| 2 | https://github.example/review/2 | substantial | "
                "def5678 | Reframed the failure class. |"
            ),
        )

        with self.assertRaisesRegex(
            PlanContractError,
            "require a completed repair escalation",
        ):
            validate_repair_cycle_governance_body(body)

    def test_second_substantial_cycle_accepts_human_decision_receipt(self) -> None:
        body = repair_cycle_governance_body(
            rows=(
                "| 1 | https://github.example/review/1 | substantial | "
                "abc1234 | Replaced the enforcement boundary. |\n"
                "| 2 | https://github.example/review/2 | substantial | "
                "def5678 | Reframed the failure class. |"
            ),
            status="completed",
            decision_receipt="https://github.example/decision/2",
            route="replan-current-unit",
            disposition="Keep the singular question with a reset owner.",
        )

        self.assertEqual(validate_repair_cycle_governance_body(body), 2)

    def test_minor_cycles_do_not_consume_substantial_cycle_budget(self) -> None:
        body = repair_cycle_governance_body(
            rows=(
                "| 1 | https://github.example/review/1 | minor | "
                "abc1234 | Corrected evidence formatting. |\n"
                "| 2 | https://github.example/review/2 | substantial | "
                "def5678 | Replaced the enforcement boundary. |"
            ),
        )

        self.assertEqual(validate_repair_cycle_governance_body(body), 1)

    def test_third_substantial_cycle_must_leave_review_unit(self) -> None:
        body = repair_cycle_governance_body(
            rows=(
                "| 1 | https://github.example/review/1 | substantial | "
                "abc1234 | Replaced the enforcement boundary. |\n"
                "| 2 | https://github.example/review/2 | substantial | "
                "def5678 | Reframed the failure class. |\n"
                "| 3 | https://github.example/review/3 | substantial | "
                "fed9876 | Expanded material scope again. |"
            ),
            status="completed",
            decision_receipt="https://github.example/decision/2",
            route="replan-current-unit",
            disposition="Keep the singular question with a reset owner.",
        )

        with self.assertRaisesRegex(
            PlanContractError,
            "third substantial repair cycle",
        ):
            validate_repair_cycle_governance_body(body)

    def test_cycle_numbers_must_be_consecutive(self) -> None:
        body = repair_cycle_governance_body(
            rows=(
                "| 2 | https://github.example/review/2 | minor | "
                "def5678 | Corrected evidence formatting. |"
            ),
        )

        with self.assertRaisesRegex(PlanContractError, "consecutive from 1"):
            validate_repair_cycle_governance_body(body)


class WorkflowStateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = ready_plan_text()

    def test_implementation_ready_requires_accepted_proposal_receipt(self) -> None:
        invalid = self.plan.replace(
            "- Workflow state: ready_for_proposal\n",
            "- Workflow state: ready_for_implementation\n",
            1,
        ).replace(
            f"| {CURRENT_FRONTIER} | ready_for_proposal |",
            f"| {CURRENT_FRONTIER} | ready_for_implementation |",
            1,
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "requires an accepted proposal",
        ):
            validate_plan_text(invalid)

    def test_latest_history_must_match_current_state(self) -> None:
        invalid = self.plan.replace(
            "- Workflow state: ready_for_proposal\n",
            "- Workflow state: proposal_in_review\n",
            1,
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "latest state does not match",
        ):
            validate_plan_text(invalid)

    def test_ready_for_implementation_requires_durable_review_receipt(self) -> None:
        accepted = _accepted_plan()
        without_receipt = accepted.replace(
            " (reviewed head `ffffffffffffffffffffffffffffffffffffffff` by "
            "`workflow-reviewer` as `COLLABORATOR` at "
            "`2026-08-12T18:00:00Z`)",
            "",
            1,
        )
        with self.assertRaisesRegex(PlanContractError, "contract review receipt"):
            validate_plan_text(without_receipt)

    def test_frontier_rejects_unsupported_review_kind(self) -> None:
        invalid = self.plan.replace(
            f"- Review kind: {REVIEW_KIND}\n",
            "- Review kind: Exploratory bundle\n",
            1,
        )
        with self.assertRaisesRegex(PlanContractError, "unsupported review kind"):
            validate_plan_text(invalid)

    def test_preproposal_plan_revision_preserves_history(self) -> None:
        state = validate_plan_text(_revise_plan(self.plan))

        self.assertEqual(state.current.name, REVISED_FRONTIER)
        self.assertEqual(
            state.workflow_history.rows[-2:],
            (
                (
                    CURRENT_FRONTIER,
                    "ready_for_proposal",
                    "Synthetic frontier is ready.",
                ),
                (
                    REVISED_FRONTIER,
                    "ready_for_proposal",
                    "Plan revision: scope replaced before proposal authoring.",
                ),
            ),
        )


class ReviewUnitTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = ready_plan_text()
        self.proposal_head = _move_to_review(self.base)

    def test_proposal_pr_is_documentation_only(self) -> None:
        transition = validate_review_unit_transition(
            self.base,
            self.proposal_head,
            plan_path=PLAN_RELATIVE,
            changed_paths={
                PLAN_RELATIVE,
                str(Path(PLAN_RELATIVE).with_suffix(".html")),
                PROPOSAL_RELATIVE,
            },
            head_branch=PROPOSAL_BRANCH,
            proposal_text=proposal_text(),
            pr_body=_review_unit_body(),
        )
        self.assertEqual(transition, "proposal")

    def test_proposal_pr_review_kind_must_match_plan(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "does not match"):
            validate_review_unit_transition(
                self.base,
                self.proposal_head,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    str(Path(PLAN_RELATIVE).with_suffix(".html")),
                    PROPOSAL_RELATIVE,
                },
                head_branch=PROPOSAL_BRANCH,
                proposal_text=proposal_text(),
                pr_body=_review_unit_body("Behavioral feature slice"),
            )

    def test_proposal_pr_requires_one_completed_review_kind(self) -> None:
        duplicate = _review_unit_body() + "\n## Review Kind\n\nReview repair\n"
        with self.assertRaisesRegex(PlanContractError, "exactly one"):
            validate_review_unit_transition(
                self.base,
                self.proposal_head,
                plan_path=PLAN_RELATIVE,
                changed_paths={PLAN_RELATIVE, PROPOSAL_RELATIVE},
                head_branch=PROPOSAL_BRANCH,
                proposal_text=proposal_text(),
                pr_body=duplicate,
            )

    def test_proposal_pr_requires_pr_body(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "requires the PR body"):
            validate_review_unit_transition(
                self.base,
                self.proposal_head,
                plan_path=PLAN_RELATIVE,
                changed_paths={PLAN_RELATIVE, PROPOSAL_RELATIVE},
                head_branch=PROPOSAL_BRANCH,
                proposal_text=proposal_text(),
            )

    def test_milestone_review_unit_pr_body_requires_repair_receipts(self) -> None:
        # Keep a valid Review Kind so the repair-cycle gate is the failing check.
        body = (
            "# Synthetic review unit\n\n"
            f"## Review Kind\n\n{REVIEW_KIND}\n\n"
            "## Review Question\n\n"
            "Is the bounded contract acceptable?\n"
        )
        with self.assertRaisesRegex(PlanContractError, "Repair Cycle Ledger"):
            validate_review_unit_transition(
                self.base,
                self.proposal_head,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    str(Path(PLAN_RELATIVE).with_suffix(".html")),
                    PROPOSAL_RELATIVE,
                },
                head_branch=PROPOSAL_BRANCH,
                proposal_text=proposal_text(),
                pr_body=body,
            )

    def test_plan_revision_can_replace_unstarted_frontier(self) -> None:
        transition = validate_review_unit_transition(
            self.base,
            _revise_plan(self.base),
            plan_path=PLAN_RELATIVE,
            changed_paths={
                PLAN_RELATIVE,
                str(Path(PLAN_RELATIVE).with_suffix(".html")),
            },
            head_branch=PLAN_REVISION_BRANCH,
        )

        self.assertEqual(transition, "plan_revision")

    def test_plan_revision_normalizes_markdown_formatted_workflow_state(self) -> None:
        formatted_base = self.base.replace(
            "- Workflow state: ready_for_proposal\n",
            "- Workflow state: `ready_for_proposal`\n",
            1,
        )
        transition = validate_review_unit_transition(
            formatted_base,
            _revise_plan(formatted_base),
            plan_path=PLAN_RELATIVE,
            changed_paths={
                PLAN_RELATIVE,
                str(Path(PLAN_RELATIVE).with_suffix(".html")),
            },
            head_branch=PLAN_REVISION_BRANCH,
        )

        self.assertEqual(transition, "plan_revision")

    def test_plan_revision_rejects_non_plan_files(self) -> None:
        with self.assertRaisesRegex(
            PlanContractError,
            "contains non-plan changes",
        ):
            validate_review_unit_transition(
                self.base,
                _revise_plan(self.base),
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    str(Path(PLAN_RELATIVE).with_suffix(".html")),
                    "implementations/decision/proposals.py",
                },
                head_branch=PLAN_REVISION_BRANCH,
            )

    def test_plan_revision_requires_rendered_html(self) -> None:
        with self.assertRaisesRegex(
            PlanContractError,
            "must update canonical plan and rendered HTML",
        ):
            validate_review_unit_transition(
                self.base,
                _revise_plan(self.base),
                plan_path=PLAN_RELATIVE,
                changed_paths={PLAN_RELATIVE},
                head_branch=PLAN_REVISION_BRANCH,
            )

    def test_plan_revision_is_unavailable_after_review_starts(self) -> None:
        started = _move_to_review(self.base)
        with self.assertRaisesRegex(
            PlanContractError,
            "requires ready_for_proposal before and after review",
        ):
            validate_review_unit_transition(
                started,
                started,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    str(Path(PLAN_RELATIVE).with_suffix(".html")),
                },
                head_branch=PLAN_REVISION_BRANCH,
            )

    def test_plan_revision_cannot_rewrite_accepted_ledger(self) -> None:
        revised = _revise_plan(self.base).replace(
            "Synthetic baseline",
            "Reinterpreted baseline",
            1,
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "cannot rewrite accepted review-unit evidence",
        ):
            validate_review_unit_transition(
                self.base,
                revised,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    str(Path(PLAN_RELATIVE).with_suffix(".html")),
                },
                head_branch=PLAN_REVISION_BRANCH,
            )

    def test_plan_revision_cannot_preclaim_met_criterion(self) -> None:
        revised = _revise_plan(self.base).replace(
            f"| {CURRENT_CRITERION} | Evidence conflicts are deterministic "
            "| Partial | Policy remains open |",
            f"| {CURRENT_CRITERION} | Evidence conflicts are deterministic "
            "| Met | Plan says so |",
            1,
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "cannot add or rewrite a Met exit criterion",
        ):
            validate_review_unit_transition(
                self.base,
                revised,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    str(Path(PLAN_RELATIVE).with_suffix(".html")),
                },
                head_branch=PLAN_REVISION_BRANCH,
            )

    def test_plan_revision_requires_reserved_branch(self) -> None:
        with self.assertRaisesRegex(
            PlanContractError,
            "cannot replace the current frontier",
        ):
            validate_review_unit_transition(
                self.base,
                _revise_plan(self.base),
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    str(Path(PLAN_RELATIVE).with_suffix(".html")),
                },
                head_branch="m900/shadow-proposals",
            )

    def test_proposal_pr_normalizes_opened_branch_annotation(self) -> None:
        annotated = f"`{PROPOSAL_BRANCH}` (planned; not opened)"
        base = self.base.replace(f"`{PROPOSAL_BRANCH}`", annotated, 1)
        head = _move_to_review(base).replace(annotated, f"`{PROPOSAL_BRANCH}`", 1)

        transition = validate_review_unit_transition(
            base,
            head,
            plan_path=PLAN_RELATIVE,
            changed_paths={
                PLAN_RELATIVE,
                str(Path(PLAN_RELATIVE).with_suffix(".html")),
                PROPOSAL_RELATIVE,
            },
            head_branch=PROPOSAL_BRANCH,
            proposal_text=proposal_text(),
            pr_body=_review_unit_body(),
        )

        self.assertEqual(transition, "proposal")

    def test_proposal_pr_cannot_change_opened_branch_identity(self) -> None:
        annotated = f"`{PROPOSAL_BRANCH}` (planned; not opened)"
        base = self.base.replace(f"`{PROPOSAL_BRANCH}`", annotated, 1)
        head = _move_to_review(base).replace(
            annotated,
            "`m900/different-proposal`",
            1,
        )

        with self.assertRaisesRegex(
            PlanContractError,
            "changed frozen proposal branch identity",
        ):
            validate_review_unit_transition(
                base,
                head,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    str(Path(PLAN_RELATIVE).with_suffix(".html")),
                    PROPOSAL_RELATIVE,
                },
                head_branch=PROPOSAL_BRANCH,
                proposal_text=proposal_text(),
            )

    def test_proposal_pr_rejects_implementation_file(self) -> None:
        with self.assertRaisesRegex(
            PlanContractError,
            "contains implementation changes",
        ):
            validate_review_unit_transition(
                self.base,
                self.proposal_head,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    PROPOSAL_RELATIVE,
                    "implementations/memory/bounded_evidence.py",
                },
                head_branch=PROPOSAL_BRANCH,
                proposal_text=proposal_text(),
            )

    def test_proposal_pr_cannot_rewrite_frozen_non_goals(self) -> None:
        changed_contract = self.proposal_head.replace(
            "Semantic identity",
            "Anything the implementer chooses",
            1,
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "changed frozen frontier field 'non-goals'",
        ):
            validate_review_unit_transition(
                self.base,
                changed_contract,
                plan_path=PLAN_RELATIVE,
                changed_paths={PLAN_RELATIVE, PROPOSAL_RELATIVE},
                head_branch=PROPOSAL_BRANCH,
                proposal_text=proposal_text(),
            )

    def test_proposal_amendment_is_additive_contract_only(self) -> None:
        accepted = _accepted_plan()
        amendment_head = _move_to_amendment_review(accepted)

        transition = validate_review_unit_transition(
            accepted,
            amendment_head,
            plan_path=PLAN_RELATIVE,
            changed_paths={
                PLAN_RELATIVE,
                str(Path(PLAN_RELATIVE).with_suffix(".html")),
                PROPOSAL_AMENDMENT_RELATIVE,
            },
            head_branch=PROPOSAL_AMENDMENT_BRANCH,
            proposal_amendment_text=proposal_amendment_text(),
            pr_body=_review_unit_body(),
        )

        self.assertEqual(transition, "proposal_amendment")

    def test_implementation_source_amendment_cannot_drop_source_and_pause(
        self,
    ) -> None:
        base = implementation_review_plan_text()
        paused = _paused_impl_value("a" * 40)
        head = _move_impl_to_amendment_review(base, paused=paused)
        dropped = head.replace(
            "- Amendment source state: implementation_in_review\n",
            "",
            1,
        ).replace(
            f"- Paused implementation: {paused}\n",
            "",
            1,
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "implementation-source amendment must record amendment "
            "source state implementation_in_review",
        ):
            validate_review_unit_transition(
                base,
                dropped,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    str(Path(PLAN_RELATIVE).with_suffix(".html")),
                    PROPOSAL_AMENDMENT_RELATIVE,
                },
                head_branch=PROPOSAL_AMENDMENT_BRANCH,
                proposal_amendment_text=proposal_amendment_text(),
                pr_body=_review_unit_body(),
            )

    def test_implementation_source_amendment_cannot_downgrade_source(self) -> None:
        base = implementation_review_plan_text()
        paused = _paused_impl_value("a" * 40)
        head = _move_impl_to_amendment_review(base, paused=paused)
        downgraded = head.replace(
            "- Amendment source state: implementation_in_review\n",
            "- Amendment source state: ready_for_implementation\n",
            1,
        ).replace(
            f"- Paused implementation: {paused}\n",
            "",
            1,
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "implementation-source amendment must record amendment "
            "source state implementation_in_review",
        ):
            validate_review_unit_transition(
                base,
                downgraded,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    str(Path(PLAN_RELATIVE).with_suffix(".html")),
                    PROPOSAL_AMENDMENT_RELATIVE,
                },
                head_branch=PROPOSAL_AMENDMENT_BRANCH,
                proposal_amendment_text=proposal_amendment_text(),
                pr_body=_review_unit_body(),
            )

    def test_implementation_source_amendment_keeps_source_and_pause(self) -> None:
        base = implementation_review_plan_text()
        paused = _paused_impl_value("a" * 40)
        head = _move_impl_to_amendment_review(base, paused=paused)
        transition = validate_review_unit_transition(
            base,
            head,
            plan_path=PLAN_RELATIVE,
            changed_paths={
                PLAN_RELATIVE,
                str(Path(PLAN_RELATIVE).with_suffix(".html")),
                PROPOSAL_AMENDMENT_RELATIVE,
            },
            head_branch=PROPOSAL_AMENDMENT_BRANCH,
            proposal_amendment_text=proposal_amendment_text(),
            pr_body=_review_unit_body(),
        )
        self.assertEqual(transition, "proposal_amendment")

    def test_proposal_amendment_cannot_rewrite_accepted_proposal(self) -> None:
        accepted = _accepted_plan()
        amendment_head = _move_to_amendment_review(accepted)

        with self.assertRaisesRegex(PlanContractError, "non-contract changes"):
            validate_review_unit_transition(
                accepted,
                amendment_head,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    PROPOSAL_RELATIVE,
                    PROPOSAL_AMENDMENT_RELATIVE,
                },
                head_branch=PROPOSAL_AMENDMENT_BRANCH,
                proposal_amendment_text=proposal_amendment_text(),
            )

    def test_accepted_amendment_unlocks_implementation(self) -> None:
        amendment_review = _move_to_amendment_review(_accepted_plan())
        accepted = accept_proposal_amendment(
            amendment_review,
            amendment_pr=61,
            merge_commit="b" * 40,
            amendment_url="https://example.invalid/61",
            review_receipt=_accepted_review_receipt("e" * 40),
        )
        state = validate_plan_text(accepted)
        self.assertEqual(
            state.current.fields["workflow state"],
            "ready_for_implementation",
        )
        self.assertIn("#61", state.current.fields["accepted proposal amendments"])
        self.assertIn(
            PROPOSAL_AMENDMENT_RELATIVE,
            state.current.fields["accepted proposal amendments"],
        )

        implementation_head = _move_to_review(accepted, implementation=True)
        transition = validate_review_unit_transition(
            accepted,
            implementation_head,
            plan_path=PLAN_RELATIVE,
            changed_paths={
                PLAN_RELATIVE,
                "implementations/memory/bounded_evidence.py",
            },
            head_branch=IMPLEMENTATION_BRANCH,
            pr_body=_review_unit_body(),
        )
        self.assertEqual(transition, "implementation")

    def test_implementation_pr_review_kind_must_match_plan(self) -> None:
        accepted = accept_proposal(
            self.proposal_head,
            proposal_pr=60,
            merge_commit="a" * 40,
            proposal_url="https://example.invalid/60",
            review_receipt=_accepted_review_receipt(),
        )
        implementation_head = _move_to_review(accepted, implementation=True)
        with self.assertRaisesRegex(PlanContractError, "does not match"):
            validate_review_unit_transition(
                accepted,
                implementation_head,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    "implementations/memory/bounded_evidence.py",
                },
                head_branch=IMPLEMENTATION_BRANCH,
                pr_body=_review_unit_body("Review repair"),
            )

    def test_implementation_cannot_modify_accepted_amendment(self) -> None:
        amendment_review = _move_to_amendment_review(_accepted_plan())
        accepted = accept_proposal_amendment(
            amendment_review,
            amendment_pr=61,
            merge_commit="b" * 40,
            amendment_url="https://example.invalid/61",
            review_receipt=_accepted_review_receipt("e" * 40),
        )
        implementation_head = _move_to_review(accepted, implementation=True)

        with self.assertRaisesRegex(
            PlanContractError,
            "cannot modify the accepted proposal or its amendments",
        ):
            validate_review_unit_transition(
                accepted,
                implementation_head,
                plan_path=PLAN_RELATIVE,
                changed_paths={PLAN_RELATIVE, PROPOSAL_AMENDMENT_RELATIVE},
                head_branch=IMPLEMENTATION_BRANCH,
            )

    def test_proposal_amendments_are_cumulative(self) -> None:
        first_review = _move_to_amendment_review(_accepted_plan())
        first_accepted = accept_proposal_amendment(
            first_review,
            amendment_pr=61,
            merge_commit="b" * 40,
            amendment_url="https://example.invalid/61",
            review_receipt=_accepted_review_receipt("e" * 40),
        )
        second_branch = "m900/amend-evidence-policy-timeout"
        second_path = (
            "docs/milestones/900-workflow-fixture/proposals/"
            "evidence-policy-timeout-amendment.md"
        )
        second_review = _move_to_amendment_review(
            first_accepted,
            branch=second_branch,
            path=second_path,
        )
        transition = validate_review_unit_transition(
            first_accepted,
            second_review,
            plan_path=PLAN_RELATIVE,
            changed_paths={PLAN_RELATIVE, second_path},
            head_branch=second_branch,
            proposal_amendment_text=proposal_amendment_text(),
            pr_body=_review_unit_body(),
        )
        self.assertEqual(transition, "proposal_amendment")

        second_accepted = accept_proposal_amendment(
            second_review,
            amendment_pr=62,
            merge_commit="c" * 40,
            amendment_url="https://example.invalid/62",
            review_receipt=_accepted_review_receipt("d" * 40),
        )
        receipts = validate_plan_text(second_accepted).current.fields[
            "accepted proposal amendments"
        ]
        self.assertIn("#61", receipts)
        self.assertIn("#62", receipts)
        self.assertIn(PROPOSAL_AMENDMENT_RELATIVE, receipts)
        self.assertIn(second_path, receipts)

    def test_implementation_requires_accepted_proposal(self) -> None:
        premature = _move_to_review(self.base, implementation=True)
        with self.assertRaises(PlanContractError):
            validate_review_unit_transition(
                self.base,
                premature,
                plan_path=PLAN_RELATIVE,
                changed_paths={
                    PLAN_RELATIVE,
                    "implementations/memory/bounded_evidence.py",
                },
                head_branch=IMPLEMENTATION_BRANCH,
            )

    def test_accepted_proposal_unlocks_implementation(self) -> None:
        accepted = accept_proposal(
            self.proposal_head,
            proposal_pr=60,
            merge_commit="a" * 40,
            proposal_url="https://example.invalid/60",
            review_receipt=_accepted_review_receipt(),
        )
        implementation_head = _move_to_review(accepted, implementation=True)
        transition = validate_review_unit_transition(
            accepted,
            implementation_head,
            plan_path=PLAN_RELATIVE,
            changed_paths={
                PLAN_RELATIVE,
                str(Path(PLAN_RELATIVE).with_suffix(".html")),
                "implementations/memory/bounded_evidence.py",
                "tests/implementations/memory/test_bounded_evidence.py",
            },
            head_branch=IMPLEMENTATION_BRANCH,
            pr_body=_review_unit_body(),
        )
        self.assertEqual(transition, "implementation")

    def test_implementation_pr_normalizes_opened_branch_annotation(self) -> None:
        accepted = accept_proposal(
            self.proposal_head,
            proposal_pr=60,
            merge_commit="a" * 40,
            proposal_url="https://example.invalid/60",
            review_receipt=_accepted_review_receipt(),
        )
        annotated = f"`{IMPLEMENTATION_BRANCH}` (planned; not opened)"
        accepted = accepted.replace(f"`{IMPLEMENTATION_BRANCH}`", annotated, 1)
        implementation_head = _move_to_review(
            accepted,
            implementation=True,
        ).replace(annotated, f"`{IMPLEMENTATION_BRANCH}`", 1)

        transition = validate_review_unit_transition(
            accepted,
            implementation_head,
            plan_path=PLAN_RELATIVE,
            changed_paths={
                PLAN_RELATIVE,
                str(Path(PLAN_RELATIVE).with_suffix(".html")),
                "implementations/memory/bounded_evidence.py",
            },
            head_branch=IMPLEMENTATION_BRANCH,
            pr_body=_review_unit_body(),
        )

        self.assertEqual(transition, "implementation")

    def test_implementation_cannot_modify_accepted_proposal(self) -> None:
        accepted = accept_proposal(
            self.proposal_head,
            proposal_pr=60,
            merge_commit="a" * 40,
            proposal_url="https://example.invalid/60",
            review_receipt=_accepted_review_receipt(),
        )
        implementation_head = _move_to_review(accepted, implementation=True)
        with self.assertRaisesRegex(
            PlanContractError,
            "cannot modify the accepted proposal",
        ):
            validate_review_unit_transition(
                accepted,
                implementation_head,
                plan_path=PLAN_RELATIVE,
                changed_paths={PLAN_RELATIVE, PROPOSAL_RELATIVE},
                head_branch=IMPLEMENTATION_BRANCH,
            )


class ProposalAcceptanceMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        proposal_plan = _move_to_review(ready_plan_text())
        self.state = validate_plan_text(proposal_plan)
        self.allowed = {
            PLAN_RELATIVE,
            str(Path(PLAN_RELATIVE).with_suffix(".html")),
            PROPOSAL_RELATIVE,
        }

    def _payload(self) -> dict[str, object]:
        head_oid = "c" * 40
        return {
            "state": "MERGED",
            "baseRefName": MILESTONE_BRANCH,
            "headRefName": PROPOSAL_BRANCH,
            "headRefOid": head_oid,
            "mergeCommit": {"oid": "b" * 40},
            "mergedAt": "2026-08-12T18:02:00Z",
            "url": "https://example.invalid/60",
            "body": _review_unit_body(),
            "reviews": [_contract_review(head_oid=head_oid)],
            "files": [
                {"path": PLAN_RELATIVE},
                {"path": str(Path(PLAN_RELATIVE).with_suffix(".html"))},
                {"path": PROPOSAL_RELATIVE},
            ],
        }

    def test_merged_proposal_records_exact_commit(self) -> None:
        commit, url, review_receipt = validate_merged_proposal_metadata(
            self._payload(),
            self.state,
            proposal_pr=60,
            allowed_paths=self.allowed,
        )
        self.assertEqual(commit, "b" * 40)
        self.assertEqual(url, "https://example.invalid/60")
        self.assertEqual(review_receipt.head_oid, "c" * 40)
        self.assertEqual(review_receipt.reviewer, "workflow-reviewer")
        self.assertEqual(review_receipt.reviewer_association, "COLLABORATOR")

    def test_formal_approval_on_exact_head_is_accepted(self) -> None:
        payload = self._payload()
        head_oid = payload["headRefOid"]
        assert isinstance(head_oid, str)
        payload["reviews"] = [
            _contract_review(head_oid=head_oid, state="APPROVED")
        ]

        _, _, review_receipt = validate_merged_proposal_metadata(
            payload,
            self.state,
            proposal_pr=60,
            allowed_paths=self.allowed,
        )

        self.assertEqual(review_receipt.head_oid, head_oid)

    def test_review_on_stale_head_does_not_accept_final_head(self) -> None:
        payload = self._payload()
        payload["headRefOid"] = "d" * 40
        with self.assertRaisesRegex(PlanContractError, "no decisive.*exact head"):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )

    def test_latest_decisive_exact_head_review_owns_outcome(self) -> None:
        payload = self._payload()
        head_oid = payload["headRefOid"]
        assert isinstance(head_oid, str)
        reviews = payload["reviews"]
        assert isinstance(reviews, list)
        reviews.append(
            _contract_review(
                head_oid=head_oid,
                state="CHANGES_REQUESTED",
                submitted_at="2026-08-12T18:01:00Z",
            )
        )

        with self.assertRaisesRegex(PlanContractError, "outstanding.*changes"):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )

    def test_one_reviewer_cannot_clear_another_reviewers_changes(self) -> None:
        payload = self._payload()
        head_oid = payload["headRefOid"]
        reviews = payload["reviews"]
        assert isinstance(head_oid, str)
        assert isinstance(reviews, list)
        first = reviews[0]
        assert isinstance(first, dict)
        first.update(
            {
                "state": "CHANGES_REQUESTED",
                "body": "",
                "author": {"login": "reviewer-a"},
            }
        )
        accepted = _contract_review(
            head_oid=head_oid,
            state="APPROVED",
            submitted_at="2026-08-12T18:01:00Z",
        )
        accepted["author"] = {"login": "reviewer-b"}
        reviews.append(accepted)

        with self.assertRaisesRegex(PlanContractError, "reviewer-a"):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )

    def test_same_reviewer_can_clear_their_own_changes_request(self) -> None:
        payload = self._payload()
        head_oid = payload["headRefOid"]
        reviews = payload["reviews"]
        assert isinstance(head_oid, str)
        assert isinstance(reviews, list)
        first = reviews[0]
        assert isinstance(first, dict)
        first.update({"state": "CHANGES_REQUESTED", "body": ""})
        reviews.append(
            _contract_review(
                head_oid=head_oid,
                submitted_at="2026-08-12T18:01:00Z",
            )
        )

        _, _, receipt = validate_merged_proposal_metadata(
            payload,
            self.state,
            proposal_pr=60,
            allowed_paths=self.allowed,
        )

        self.assertEqual(receipt.reviewer, "workflow-reviewer")

    def test_edited_comment_receipt_is_rejected(self) -> None:
        payload = self._payload()
        reviews = payload["reviews"]
        assert isinstance(reviews, list)
        review = reviews[0]
        assert isinstance(review, dict)
        review["includesCreatedEdit"] = True
        with self.assertRaisesRegex(PlanContractError, "malformed or edited"):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )

    def test_fetch_fails_closed_when_review_window_would_truncate(self) -> None:
        response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "mergedAt": "2026-08-12T18:02:00Z",
                        "reviews": {"nodes": [], "totalCount": 101},
                    }
                }
            }
        }
        completed = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"nameWithOwner":"example/repository"}',
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(response),
                stderr="",
            ),
        ]
        with mock.patch(
            "docs.milestones.workflow.subprocess.run",
            side_effect=completed,
        ):
            with self.assertRaisesRegex(PlanContractError, "100-review"):
                _fetch_pr_review_metadata(60)

    def test_untrusted_review_cannot_accept_contract(self) -> None:
        payload = self._payload()
        reviews = payload["reviews"]
        assert isinstance(reviews, list)
        review = reviews[0]
        assert isinstance(review, dict)
        review["authorAssociation"] = "CONTRIBUTOR"
        with self.assertRaisesRegex(PlanContractError, "no decisive authorized"):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )

    def test_reviewer_without_current_push_authority_cannot_accept(self) -> None:
        payload = self._payload()
        reviews = payload["reviews"]
        assert isinstance(reviews, list)
        review = reviews[0]
        assert isinstance(review, dict)
        review["authorCanPushToRepository"] = False
        with self.assertRaisesRegex(PlanContractError, "no decisive authorized"):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )

    def test_malformed_receipt_from_unauthorized_reviewer_is_ignored(self) -> None:
        payload = self._payload()
        head_oid = payload["headRefOid"]
        reviews = payload["reviews"]
        assert isinstance(head_oid, str)
        assert isinstance(reviews, list)
        malformed = _contract_review(
            head_oid=head_oid,
            submitted_at="2026-08-12T18:01:00Z",
        )
        malformed.update(
            {
                "body": "## Contract Review Receipt\n\n- Maybe: `accepted`\n",
                "author": {"login": "drive-by-reviewer"},
                "authorAssociation": "CONTRIBUTOR",
                "authorCanPushToRepository": False,
            }
        )
        reviews.append(malformed)

        _, _, receipt = validate_merged_proposal_metadata(
            payload,
            self.state,
            proposal_pr=60,
            allowed_paths=self.allowed,
        )

        self.assertEqual(receipt.reviewer, "workflow-reviewer")

    def test_review_submitted_after_merge_is_rejected(self) -> None:
        payload = self._payload()
        reviews = payload["reviews"]
        assert isinstance(reviews, list)
        review = reviews[0]
        assert isinstance(review, dict)
        review["submittedAt"] = "2026-08-12T18:03:00Z"
        with self.assertRaisesRegex(PlanContractError, "no decisive authorized"):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )

    def test_embedded_example_receipt_cannot_accept_contract(self) -> None:
        payload = self._payload()
        reviews = payload["reviews"]
        assert isinstance(reviews, list)
        review = reviews[0]
        assert isinstance(review, dict)
        review["body"] = (
            "I request changes; the following is only an example.\n\n"
            "## Contract Review Receipt\n\n"
            "- Outcome: `accepted`\n"
        )
        with self.assertRaisesRegex(PlanContractError, "malformed or edited"):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )

    def test_malformed_comment_receipt_is_rejected(self) -> None:
        payload = self._payload()
        reviews = payload["reviews"]
        assert isinstance(reviews, list)
        review = reviews[0]
        assert isinstance(review, dict)
        review["body"] = (
            "## Contract Review Receipt\n\n"
            "- Outcome: `accepted`\n"
            "- Caveat: maybe\n"
        )
        with self.assertRaisesRegex(PlanContractError, "malformed or edited"):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )

    def test_later_unedited_receipt_replaces_same_reviewers_malformed_one(self) -> None:
        payload = self._payload()
        head_oid = payload["headRefOid"]
        reviews = payload["reviews"]
        assert isinstance(head_oid, str)
        assert isinstance(reviews, list)
        malformed = reviews[0]
        assert isinstance(malformed, dict)
        malformed["body"] = (
            "## Contract Review Receipt\n\n- Outcome: `accepted`\nextra\n"
        )
        reviews.append(
            _contract_review(
                head_oid=head_oid,
                submitted_at="2026-08-12T18:01:00Z",
            )
        )

        _, _, receipt = validate_merged_proposal_metadata(
            payload,
            self.state,
            proposal_pr=60,
            allowed_paths=self.allowed,
        )

        self.assertEqual(receipt.reviewer, "workflow-reviewer")

    def test_merged_proposal_rejects_mismatched_review_kind(self) -> None:
        payload = self._payload()
        payload["body"] = _review_unit_body("Behavioral feature slice")
        with self.assertRaisesRegex(PlanContractError, "does not match"):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )

    def test_merged_proposal_rejects_code_changes(self) -> None:
        payload = self._payload()
        payload["files"].append(
            {"path": "implementations/memory/bounded_evidence.py"}
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "contains implementation changes",
        ):
            validate_merged_proposal_metadata(
                payload,
                self.state,
                proposal_pr=60,
                allowed_paths=self.allowed,
            )


class ProposalAmendmentAcceptanceMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        amendment_plan = _move_to_amendment_review(_accepted_plan())
        self.state = validate_plan_text(amendment_plan)
        self.allowed = {
            PLAN_RELATIVE,
            str(Path(PLAN_RELATIVE).with_suffix(".html")),
            PROPOSAL_AMENDMENT_RELATIVE,
        }

    def _payload(self) -> dict[str, object]:
        head_oid = "d" * 40
        return {
            "state": "MERGED",
            "baseRefName": MILESTONE_BRANCH,
            "headRefName": PROPOSAL_AMENDMENT_BRANCH,
            "headRefOid": head_oid,
            "mergeCommit": {"oid": "b" * 40},
            "mergedAt": "2026-08-12T18:02:00Z",
            "url": "https://example.invalid/61",
            "body": _review_unit_body(),
            "reviews": [_contract_review(head_oid=head_oid)],
            "files": [
                {"path": PLAN_RELATIVE},
                {"path": str(Path(PLAN_RELATIVE).with_suffix(".html"))},
                {"path": PROPOSAL_AMENDMENT_RELATIVE},
            ],
        }

    def test_merged_amendment_records_exact_commit(self) -> None:
        commit, url, review_receipt = (
            validate_merged_proposal_amendment_metadata(
                self._payload(),
                self.state,
                amendment_pr=61,
                allowed_paths=self.allowed,
            )
        )
        self.assertEqual(commit, "b" * 40)
        self.assertEqual(url, "https://example.invalid/61")
        self.assertEqual(review_receipt.head_oid, "d" * 40)

    def test_amendment_requires_exact_head_review(self) -> None:
        payload = self._payload()
        payload["reviews"] = []
        with self.assertRaisesRegex(PlanContractError, "no decisive.*exact head"):
            validate_merged_proposal_amendment_metadata(
                payload,
                self.state,
                amendment_pr=61,
                allowed_paths=self.allowed,
            )

    def test_amendment_acceptance_records_review_authority(self) -> None:
        amendment_review = _move_to_amendment_review(_accepted_plan())
        receipt = ContractReviewReceipt(
            head_oid="d" * 40,
            reviewer="workflow-reviewer",
            reviewer_association="COLLABORATOR",
            submitted_at="2026-08-12T18:00:00Z",
        )
        accepted = accept_proposal_amendment(
            amendment_review,
            amendment_pr=61,
            merge_commit="b" * 40,
            amendment_url="https://example.invalid/61",
            review_receipt=receipt,
        )

        state = validate_plan_text(accepted)
        record = state.current.fields["accepted proposal amendments"]
        self.assertIn("reviewed head", record)
        self.assertIn("workflow-reviewer", record)

    def test_merged_amendment_rejects_mismatched_review_kind(self) -> None:
        payload = self._payload()
        payload["body"] = _review_unit_body("Review repair")
        with self.assertRaisesRegex(PlanContractError, "does not match"):
            validate_merged_proposal_amendment_metadata(
                payload,
                self.state,
                amendment_pr=61,
                allowed_paths=self.allowed,
            )

    def test_merged_amendment_rejects_code_changes(self) -> None:
        payload = self._payload()
        payload["files"].append(
            {"path": "implementations/memory/bounded_evidence.py"}
        )
        with self.assertRaisesRegex(PlanContractError, "non-contract changes"):
            validate_merged_proposal_amendment_metadata(
                payload,
                self.state,
                amendment_pr=61,
                allowed_paths=self.allowed,
            )


class ReviewUnitGitDiffTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _create_implementation_parent(self, root: Path) -> tuple[Path, str]:
        plan = root / PLAN_RELATIVE
        plan.parent.mkdir(parents=True)
        plan.write_text(implementation_review_plan_text(), encoding="utf-8")
        parent_file = root / "implementations" / "evidence" / "policy.py"
        parent_file.parent.mkdir(parents=True)
        parent_file.write_text("POLICY = 'accepted'\n", encoding="utf-8")
        self._git(root, "init", "-b", IMPLEMENTATION_BRANCH)
        self._git(root, "add", ".")
        self._git(
            root,
            "-c",
            "user.name=Milestone Test",
            "-c",
            "user.email=milestone@example.invalid",
            "commit",
            "-m",
            "start implementation review",
        )
        return plan, self._git(root, "rev-parse", "HEAD")

    def test_git_diff_gate_recognizes_hitl_implementation_adjunct(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, base_sha = self._create_implementation_parent(root)
            self._git(root, "switch", "-c", IMPLEMENTATION_ADJUNCT_BRANCH)
            adjunct = root / "implementations" / "evidence" / "inspection.py"
            adjunct.write_text("OPTIONAL_VIEW = True\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "add optional evidence inspection",
            )
            head_sha = self._git(root, "rev-parse", "HEAD")

            transition = validate_review_unit_git_diff(
                base_ref=IMPLEMENTATION_BRANCH,
                head_ref=IMPLEMENTATION_ADJUNCT_BRANCH,
                base_sha=base_sha,
                head_sha=head_sha,
                pr_body=implementation_adjunct_body(),
                repo_root=root,
            )

            self.assertEqual(transition, "implementation_adjunct")

    def test_implementation_adjunct_rejects_contract_artifact_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan, base_sha = self._create_implementation_parent(root)
            self._git(root, "switch", "-c", IMPLEMENTATION_ADJUNCT_BRANCH)
            plan.write_text(
                plan.read_text(encoding="utf-8") + "\nUnreviewed plan note.\n",
                encoding="utf-8",
            )
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "change plan from adjunct",
            )

            with self.assertRaisesRegex(
                PlanContractError,
                "cannot change the canonical milestone plan",
            ):
                validate_review_unit_git_diff(
                    base_ref=IMPLEMENTATION_BRANCH,
                    head_ref=IMPLEMENTATION_ADJUNCT_BRANCH,
                    base_sha=base_sha,
                    head_sha=self._git(root, "rev-parse", "HEAD"),
                    pr_body=implementation_adjunct_body(),
                    repo_root=root,
                )

    def test_implementation_adjunct_requires_reserved_child_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, base_sha = self._create_implementation_parent(root)
            wrong_branch = "m900/evidence-inspection"
            self._git(root, "switch", "-c", wrong_branch)
            adjunct = root / "implementations" / "evidence" / "inspection.py"
            adjunct.write_text("OPTIONAL_VIEW = True\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "use wrong adjunct branch",
            )

            with self.assertRaisesRegex(PlanContractError, "must match"):
                validate_review_unit_git_diff(
                    base_ref=IMPLEMENTATION_BRANCH,
                    head_ref=wrong_branch,
                    base_sha=base_sha,
                    head_sha=self._git(root, "rev-parse", "HEAD"),
                    pr_body=implementation_adjunct_body(),
                    repo_root=root,
                )

    def test_reserved_adjunct_branch_requires_pr_body(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, base_sha = self._create_implementation_parent(root)
            self._git(root, "switch", "-c", IMPLEMENTATION_ADJUNCT_BRANCH)
            adjunct = root / "implementations" / "evidence" / "inspection.py"
            adjunct.write_text("OPTIONAL_VIEW = True\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "omit adjunct metadata",
            )

            with self.assertRaisesRegex(PlanContractError, "requires.*body"):
                validate_review_unit_git_diff(
                    base_ref=IMPLEMENTATION_BRANCH,
                    head_ref=IMPLEMENTATION_ADJUNCT_BRANCH,
                    base_sha=base_sha,
                    head_sha=self._git(root, "rev-parse", "HEAD"),
                    repo_root=root,
                )

    def test_implementation_adjunct_must_include_current_parent_head(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._create_implementation_parent(root)
            self._git(root, "switch", "-c", IMPLEMENTATION_ADJUNCT_BRANCH)
            adjunct = root / "implementations" / "evidence" / "inspection.py"
            adjunct.write_text("OPTIONAL_VIEW = True\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "add inspection from old parent",
            )
            head_sha = self._git(root, "rev-parse", "HEAD")
            self._git(root, "switch", IMPLEMENTATION_BRANCH)
            parent_file = root / "implementations" / "evidence" / "policy.py"
            parent_file.write_text("POLICY = 'advanced'\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "advance parent implementation",
            )
            current_base_sha = self._git(root, "rev-parse", "HEAD")

            with self.assertRaisesRegex(PlanContractError, "current parent"):
                validate_review_unit_git_diff(
                    base_ref=IMPLEMENTATION_BRANCH,
                    head_ref=IMPLEMENTATION_ADJUNCT_BRANCH,
                    base_sha=current_base_sha,
                    head_sha=head_sha,
                    pr_body=implementation_adjunct_body(),
                    repo_root=root,
                )

    def test_non_adjunct_child_does_not_claim_hitl_lane(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, base_sha = self._create_implementation_parent(root)
            repair_branch = f"{IMPLEMENTATION_BRANCH}--repair-policy"
            self._git(root, "switch", "-c", repair_branch)
            repair = root / "implementations" / "evidence" / "repair.py"
            repair.write_text("REPAIR = True\n", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "repair parent review finding",
            )

            transition = validate_review_unit_git_diff(
                base_ref=IMPLEMENTATION_BRANCH,
                head_ref=repair_branch,
                base_sha=base_sha,
                head_sha=self._git(root, "rev-parse", "HEAD"),
                repo_root=root,
            )

            self.assertIsNone(transition)

    def test_adjunct_branch_cannot_be_a_pr_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(
                PlanContractError,
                "cannot be used as a PR base",
            ):
                validate_review_unit_git_diff(
                    base_ref=IMPLEMENTATION_ADJUNCT_BRANCH,
                    head_ref=f"{IMPLEMENTATION_ADJUNCT_BRANCH}--adjunct-nested",
                    base_sha="0" * 40,
                    head_sha="1" * 40,
                    repo_root=Path(temp_dir),
                )

    def test_git_diff_gate_recognizes_proposal_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            plan.parent.mkdir(parents=True)
            plan.write_text(ready_plan_text(), encoding="utf-8")
            self._git(root, "init", "-b", MILESTONE_BRANCH)
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "ready for proposal",
            )
            base_sha = self._git(root, "rev-parse", "HEAD")
            self._git(root, "switch", "-c", PROPOSAL_BRANCH)
            plan.write_text(
                _move_to_review(plan.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            proposal = root / PROPOSAL_RELATIVE
            proposal.parent.mkdir(parents=True)
            proposal.write_text(proposal_text(), encoding="utf-8")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "propose conflict policy",
            )
            head_sha = self._git(root, "rev-parse", "HEAD")

            transition = validate_review_unit_git_diff(
                base_ref=MILESTONE_BRANCH,
                head_ref=PROPOSAL_BRANCH,
                base_sha=base_sha,
                head_sha=head_sha,
                pr_body=_review_unit_body(),
                repo_root=root,
            )

            self.assertEqual(transition, "proposal")

    def test_git_diff_gate_recognizes_plan_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            plan.parent.mkdir(parents=True)
            plan.write_text(ready_plan_text(), encoding="utf-8")
            plan_html = plan.with_suffix(".html")
            plan_html.write_text("base", encoding="utf-8")
            self._git(root, "init", "-b", MILESTONE_BRANCH)
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "ready for proposal",
            )
            base_sha = self._git(root, "rev-parse", "HEAD")
            self._git(root, "switch", "-c", PLAN_REVISION_BRANCH)
            plan.write_text(_revise_plan(ready_plan_text()), encoding="utf-8")
            plan_html.write_text("revised", encoding="utf-8")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "revise unstarted frontier",
            )
            head_sha = self._git(root, "rev-parse", "HEAD")

            transition = validate_review_unit_git_diff(
                base_ref=MILESTONE_BRANCH,
                head_ref=PLAN_REVISION_BRANCH,
                base_sha=base_sha,
                head_sha=head_sha,
                repo_root=root,
            )

            self.assertEqual(transition, "plan_revision")

    def test_git_diff_gate_recognizes_proposal_amendment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            plan.parent.mkdir(parents=True)
            plan.write_text(_accepted_plan(), encoding="utf-8")
            plan_html = plan.with_suffix(".html")
            plan_html.write_text("accepted", encoding="utf-8")
            self._git(root, "init", "-b", MILESTONE_BRANCH)
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "accept proposal",
            )
            base_sha = self._git(root, "rev-parse", "HEAD")
            self._git(root, "switch", "-c", PROPOSAL_AMENDMENT_BRANCH)
            plan.write_text(
                _move_to_amendment_review(plan.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            plan_html.write_text("amendment review", encoding="utf-8")
            amendment = root / PROPOSAL_AMENDMENT_RELATIVE
            amendment.parent.mkdir(parents=True, exist_ok=True)
            amendment.write_text(proposal_amendment_text(), encoding="utf-8")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "amend accepted proposal",
            )
            head_sha = self._git(root, "rev-parse", "HEAD")

            transition = validate_review_unit_git_diff(
                base_ref=MILESTONE_BRANCH,
                head_ref=PROPOSAL_AMENDMENT_BRANCH,
                base_sha=base_sha,
                head_sha=head_sha,
                pr_body=_review_unit_body(),
                repo_root=root,
            )

            self.assertEqual(transition, "proposal_amendment")

    def test_proposal_amendment_branch_starts_after_proposal_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            plan.parent.mkdir(parents=True)
            accepted = _accepted_plan()
            plan.write_text(accepted, encoding="utf-8")
            self._git(root, "init", "-b", MILESTONE_BRANCH)
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "accept proposal",
            )

            start_proposal_amendment_branch(
                plan,
                validate_plan_text(accepted),
                PROPOSAL_AMENDMENT_BRANCH,
                PROPOSAL_AMENDMENT_RELATIVE,
                repo_root=root,
            )

            self.assertEqual(
                self._git(root, "branch", "--show-current"),
                PROPOSAL_AMENDMENT_BRANCH,
            )
            transitioned = validate_plan_text(plan.read_text(encoding="utf-8"))
            self.assertEqual(
                transitioned.current.fields["workflow state"],
                "proposal_amendment_in_review",
            )
            self.assertEqual(
                transitioned.current.fields["proposal amendment path"],
                f"`{PROPOSAL_AMENDMENT_RELATIVE}`",
            )

    def test_implementation_branch_starts_only_after_proposal_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            plan.parent.mkdir(parents=True)
            proposal_review = _move_to_review(
                ready_plan_text().replace(
                    f"- Implementation branch: `{IMPLEMENTATION_BRANCH}`\n",
                    f"- Implementation branch: `{IMPLEMENTATION_BRANCH}` "
                    "(planned; not opened)\n",
                    1,
                )
            )
            accepted = accept_proposal(
                proposal_review,
                proposal_pr=60,
                merge_commit="c" * 40,
                proposal_url="https://example.invalid/60",
                review_receipt=_accepted_review_receipt("d" * 40),
            )
            plan.write_text(accepted, encoding="utf-8")
            self._git(root, "init", "-b", MILESTONE_BRANCH)
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "accept proposal",
            )

            start_implementation_branch(
                plan,
                validate_plan_text(accepted),
                IMPLEMENTATION_BRANCH,
                repo_root=root,
            )

            self.assertEqual(
                self._git(root, "branch", "--show-current"),
                IMPLEMENTATION_BRANCH,
            )
            transitioned = validate_plan_text(plan.read_text(encoding="utf-8"))
            self.assertEqual(
                transitioned.current.fields["workflow state"],
                "implementation_in_review",
            )
            self.assertEqual(
                transitioned.current.fields["implementation branch"],
                f"`{IMPLEMENTATION_BRANCH}`",
            )

    def _init_plan(self, root: Path, text: str, message: str = "seed plan") -> None:
        plan = root / PLAN_RELATIVE
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(text, encoding="utf-8")
        self._git(root, "init", "-b", MILESTONE_BRANCH)
        self._git(root, "add", ".")
        self._commit(root, message)

    def _commit(self, root: Path, message: str) -> str:
        self._git(root, "add", ".")
        self._git(
            root,
            "-c",
            "user.name=Milestone Test",
            "-c",
            "user.email=milestone@example.invalid",
            "commit",
            "-m",
            message,
        )
        return self._git(root, "rev-parse", "HEAD")

    def _start_impl_amendment(
        self,
        root: Path,
        plan: Path,
        *,
        head: str | None = None,
        disposition: str = "paused",
        resume: str = "reconcile",
        payload: dict[str, object] | None = None,
        receipt: dict[str, object] | None = None,
    ) -> str:
        head = head or self._git(root, "rev-parse", "HEAD")
        with _mock_impl_github(head, payload=payload, receipt=receipt):
            start_proposal_amendment_branch(
                plan,
                validate_plan_text(plan.read_text(encoding="utf-8")),
                PROPOSAL_AMENDMENT_BRANCH,
                PROPOSAL_AMENDMENT_RELATIVE,
                repo_root=root,
                implementation_pr=59,
                implementation_url=IMPL_PR_URL,
                implementation_head=head,
                implementation_disposition=disposition,
                resume_policy=resume,
                escalation_receipt=IMPL_ESCALATION_URL,
            )
        return head

    def _accept_amendment_on_milestone(
        self,
        root: Path,
        plan: Path,
        *,
        merge_commit: str | None = None,
    ) -> tuple[str, str]:
        amendment = root / PROPOSAL_AMENDMENT_RELATIVE
        amendment.parent.mkdir(parents=True, exist_ok=True)
        amendment.write_text(proposal_amendment_text(), encoding="utf-8")
        self._commit(root, "start amendment")
        self._git(root, "switch", MILESTONE_BRANCH)
        self._git(
            root,
            "merge",
            "--no-ff",
            "-m",
            "merge amendment",
            PROPOSAL_AMENDMENT_BRANCH,
        )
        merge_sha = merge_commit or self._git(root, "rev-parse", "HEAD")
        accepted = accept_proposal_amendment(
            plan.read_text(encoding="utf-8"),
            amendment_pr=70,
            merge_commit=merge_sha,
            amendment_url="https://github.com/example/auto-driving/pull/70",
            review_receipt=_accepted_review_receipt(),
        )
        plan.write_text(accepted, encoding="utf-8")
        self._commit(root, "accept amendment")
        return accepted, merge_sha

    def test_amendment_from_first_implementation_review_requires_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            with self.assertRaises(PlanContractError) as ctx:
                start_proposal_amendment_branch(
                    plan,
                    validate_plan_text(plan.read_text(encoding="utf-8")),
                    PROPOSAL_AMENDMENT_BRANCH,
                    PROPOSAL_AMENDMENT_RELATIVE,
                    repo_root=root,
                )
            self.assertIn("implementation_in_review", str(ctx.exception))
            self.assertIn("escalation", str(ctx.exception))

    def test_second_cycle_escalation_can_start_amendment_from_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            head = self._start_impl_amendment(root, plan)
            transitioned = validate_plan_text(plan.read_text(encoding="utf-8"))
            self.assertEqual(
                transitioned.current.fields["workflow state"],
                "proposal_amendment_in_review",
            )
            self.assertEqual(
                transitioned.current.fields["amendment source state"],
                "implementation_in_review",
            )
            paused = transitioned.current.fields["paused implementation"]
            self.assertIn("#59", paused)
            self.assertIn(head, paused)
            self.assertIn("paused", paused)
            self.assertIn("reconcile", paused)
            self.assertIn(IMPL_PR_URL, paused)

    def test_open_implementation_pr_cannot_amend_without_pause_or_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            with self.assertRaises(PlanContractError):
                start_proposal_amendment_branch(
                    plan,
                    validate_plan_text(plan.read_text(encoding="utf-8")),
                    PROPOSAL_AMENDMENT_BRANCH,
                    PROPOSAL_AMENDMENT_RELATIVE,
                    repo_root=root,
                )

    def test_start_amendment_rejects_non_github_implementation_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            with self.assertRaisesRegex(PlanContractError, "github.com"):
                start_proposal_amendment_branch(
                    plan,
                    validate_plan_text(plan.read_text(encoding="utf-8")),
                    PROPOSAL_AMENDMENT_BRANCH,
                    PROPOSAL_AMENDMENT_RELATIVE,
                    repo_root=root,
                    implementation_pr=59,
                    implementation_url="https://example.invalid/59",
                    implementation_head="a" * 40,
                    implementation_disposition="paused",
                    resume_policy="reconcile",
                    escalation_receipt=IMPL_ESCALATION_URL,
                )

    def test_start_amendment_rejects_github_head_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            recorded = self._git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(PlanContractError, "does not match the recorded pause head"):
                self._start_impl_amendment(
                    root,
                    plan,
                    head=recorded,
                    payload=_impl_github_payload("b" * 40),
                )

    def test_abandoned_amendment_restores_implementation_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            self._start_impl_amendment(root, plan)
            self._commit(root, "start amendment")
            self.assertEqual(
                self._git(root, "branch", "--show-current"),
                PROPOSAL_AMENDMENT_BRANCH,
            )
            abandon_proposal_amendment(
                plan,
                validate_plan_text(plan.read_text(encoding="utf-8")),
                reason="Amendment rejected; resume the implementation unit.",
                repo_root=root,
            )
            self.assertEqual(
                self._git(root, "branch", "--show-current"),
                MILESTONE_BRANCH,
            )
            restored = validate_plan_text(plan.read_text(encoding="utf-8"))
            self.assertEqual(
                restored.current.fields["workflow state"],
                "implementation_in_review",
            )
            self.assertIn("#59", restored.current.fields.get("pr", ""))
            self.assertFalse(restored.current.fields.get("proposal amendment branch"))
            self.assertFalse(restored.current.fields.get("paused implementation"))

    def test_abandoned_closed_amendment_keeps_replace_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            self._git(root, "branch", IMPLEMENTATION_BRANCH)
            head = self._git(root, "rev-parse", "HEAD")
            self._start_impl_amendment(
                root,
                plan,
                head=head,
                disposition="closed",
                resume="replace",
                payload=_impl_github_payload(head, state="CLOSED", is_draft=False),
            )
            self._commit(root, "start amendment")
            abandon_proposal_amendment(
                plan,
                validate_plan_text(plan.read_text(encoding="utf-8")),
                reason="Amendment rejected; open a replacement implementation.",
                repo_root=root,
            )
            restored = validate_plan_text(plan.read_text(encoding="utf-8"))
            self.assertEqual(
                restored.current.fields["workflow state"],
                "ready_for_implementation",
            )
            self.assertFalse(restored.current.fields.get("pr"))
            paused = restored.current.fields["paused implementation"]
            self.assertIn("closed", paused)
            self.assertIn("replace", paused)
            self._commit(root, "abandon closed amendment")
            with _mock_impl_github(
                head,
                payload=_impl_github_payload(head, state="CLOSED", is_draft=False),
            ):
                start_implementation_branch(
                    plan,
                    restored,
                    IMPLEMENTATION_BRANCH,
                    repo_root=root,
                )
            resumed = validate_plan_text(plan.read_text(encoding="utf-8"))
            self.assertEqual(
                resumed.current.fields["workflow state"],
                "implementation_in_review",
            )

    def test_accepted_amendment_from_implementation_preserves_paused_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            head = self._start_impl_amendment(root, plan)
            amending = plan.read_text(encoding="utf-8")
            accepted = accept_proposal_amendment(
                amending,
                amendment_pr=70,
                merge_commit="e" * 40,
                amendment_url="https://github.com/example/auto-driving/pull/70",
                review_receipt=_accepted_review_receipt("f" * 40),
            )
            state = validate_plan_text(accepted)
            self.assertEqual(
                state.current.fields["workflow state"],
                "ready_for_implementation",
            )
            self.assertIn("#59", state.current.fields["paused implementation"])
            self.assertIn(head, state.current.fields["paused implementation"])
            self.assertIn("#70", state.current.fields["accepted proposal amendments"])
            self.assertFalse(state.current.fields.get("amendment source state"))

    def test_resume_after_amendment_reconciles_paused_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            self._git(root, "branch", IMPLEMENTATION_BRANCH)
            head = self._start_impl_amendment(root, plan)
            accepted, merge_sha = self._accept_amendment_on_milestone(root, plan)
            with _mock_impl_github(head):
                start_implementation_branch(
                    plan,
                    validate_plan_text(accepted),
                    IMPLEMENTATION_BRANCH,
                    repo_root=root,
                )
            resumed = validate_plan_text(plan.read_text(encoding="utf-8"))
            self.assertEqual(
                resumed.current.fields["workflow state"],
                "implementation_in_review",
            )
            self.assertIn("#59", resumed.current.fields.get("pr", ""))
            self.assertFalse(resumed.current.fields.get("paused implementation"))
            self.assertEqual(
                self._git(root, "branch", "--show-current"),
                IMPLEMENTATION_BRANCH,
            )
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", merge_sha, "HEAD"],
                cwd=root,
                check=False,
            )
            self.assertEqual(ancestor.returncode, 0)
            self.assertTrue((root / PROPOSAL_AMENDMENT_RELATIVE).is_file())

    def test_resume_rejects_implementation_head_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            self._git(root, "branch", IMPLEMENTATION_BRANCH)
            head = self._start_impl_amendment(root, plan)
            accepted, _merge_sha = self._accept_amendment_on_milestone(root, plan)
            self._git(root, "switch", IMPLEMENTATION_BRANCH)
            extra = root / "implementations" / "drift.py"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text("DRIFT = True\n", encoding="utf-8")
            self._commit(root, "drift implementation")
            self._git(root, "switch", MILESTONE_BRANCH)
            with _mock_impl_github(head):
                with self.assertRaisesRegex(PlanContractError, "head drifted"):
                    start_implementation_branch(
                        plan,
                        validate_plan_text(accepted),
                        IMPLEMENTATION_BRANCH,
                        repo_root=root,
                    )

    def test_resume_rejects_missing_amendment_ancestry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            self._git(root, "branch", IMPLEMENTATION_BRANCH)
            head = self._start_impl_amendment(root, plan)
            accepted, _merge_sha = self._accept_amendment_on_milestone(
                root,
                plan,
                merge_commit="e" * 40,
            )
            with _mock_impl_github(head):
                with self.assertRaisesRegex(PlanContractError, "not an ancestor"):
                    start_implementation_branch(
                        plan,
                        validate_plan_text(accepted),
                        IMPLEMENTATION_BRANCH,
                        repo_root=root,
                    )

    def test_resume_rejects_merge_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            conflict = root / "docs" / "conflict.txt"
            self._git(root, "switch", "-c", IMPLEMENTATION_BRANCH)
            conflict.write_text("implementation side\n", encoding="utf-8")
            impl_head = self._commit(root, "implementation conflict")
            self._git(root, "switch", MILESTONE_BRANCH)
            conflict.write_text("milestone side\n", encoding="utf-8")
            self._commit(root, "milestone conflict")
            self._start_impl_amendment(root, plan, head=impl_head)
            accepted, _merge_sha = self._accept_amendment_on_milestone(root, plan)
            with _mock_impl_github(impl_head):
                with self.assertRaisesRegex(PlanContractError, "cannot merge"):
                    start_implementation_branch(
                        plan,
                        validate_plan_text(accepted),
                        IMPLEMENTATION_BRANCH,
                        repo_root=root,
                    )

    def test_git_diff_gate_recognizes_implementation_source_amendment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            self._init_plan(root, implementation_review_plan_text())
            base_sha = self._git(root, "rev-parse", "HEAD")
            head = self._start_impl_amendment(root, plan)
            amendment = root / PROPOSAL_AMENDMENT_RELATIVE
            amendment.parent.mkdir(parents=True, exist_ok=True)
            amendment.write_text(proposal_amendment_text(), encoding="utf-8")
            head_sha = self._commit(root, "amend from implementation review")
            with _mock_impl_github(head):
                transition = validate_review_unit_git_diff(
                    base_ref=MILESTONE_BRANCH,
                    head_ref=PROPOSAL_AMENDMENT_BRANCH,
                    base_sha=base_sha,
                    head_sha=head_sha,
                    pr_body=_review_unit_body(),
                    repo_root=root,
                )
            self.assertEqual(transition, "proposal_amendment")


class ImplementationPauseGithubTests(unittest.TestCase):
    def _verify(
        self,
        head: str = "a" * 40,
        *,
        payload: dict[str, object] | None = None,
        receipt: dict[str, object] | None = None,
        paused: str | None = None,
    ) -> None:
        parsed = parse_paused_implementation(paused or _paused_impl_value(head))
        with _mock_impl_github(head, payload=payload, receipt=receipt):
            verify_implementation_pause_against_github(
                parsed,
                planned_branch=IMPLEMENTATION_BRANCH,
                milestone_branch=MILESTONE_BRANCH,
                repo_root=Path("."),
            )

    def test_matching_draft_pause_is_accepted(self) -> None:
        self._verify()

    def test_url_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "does not match GitHub"):
            self._verify(
                payload=_impl_github_payload(
                    "a" * 40,
                    url="https://github.com/example/auto-driving/pull/60",
                )
            )

    def test_head_drift_is_rejected(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "recorded pause head"):
            self._verify(payload=_impl_github_payload("b" * 40))

    def test_paused_pr_must_be_draft(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "converted to draft"):
            self._verify(payload=_impl_github_payload("a" * 40, is_draft=False))

    def test_paused_pr_must_stay_open(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "must remain OPEN"):
            self._verify(
                payload=_impl_github_payload("a" * 40, state="CLOSED", is_draft=False)
            )

    def test_closed_pr_must_not_be_merged(self) -> None:
        paused = _paused_impl_value("a" * 40, disposition="closed", resume="replace")
        with self.assertRaisesRegex(PlanContractError, "must not already be merged"):
            self._verify(
                paused=paused,
                payload=_impl_github_payload(
                    "a" * 40,
                    state="CLOSED",
                    is_draft=False,
                    merged=True,
                ),
            )

    def test_wrong_base_is_rejected(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "base must be the milestone"):
            self._verify(payload=_impl_github_payload("a" * 40, base_ref="main"))

    def test_wrong_head_branch_is_rejected(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "planned implementation"):
            self._verify(
                payload=_impl_github_payload("a" * 40, head_ref="m900/other")
            )

    def test_escalation_must_belong_to_implementation_pr(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "belong to the implementation"):
            self._verify(receipt=_impl_escalation_receipt(pr=70))

    def test_escalation_must_be_authorized(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "OWNER, MEMBER, or COLLABORATOR"):
            self._verify(receipt=_impl_escalation_receipt(association="CONTRIBUTOR"))

    def test_escalation_must_select_proposal_amendment(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "proposal-amendment"):
            self._verify(receipt=_impl_escalation_receipt(body="Please replan."))


if __name__ == "__main__":
    unittest.main(verbosity=2)
