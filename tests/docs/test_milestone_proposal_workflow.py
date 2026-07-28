from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from docs.milestones.workflow import (
    PlanContractError,
    accept_proposal,
    start_implementation_branch,
    validate_merged_proposal_metadata,
    validate_plan_text,
    validate_proposal_text,
    validate_review_unit_transition,
    validate_review_unit_git_diff,
)
from tests.docs.milestone_workflow_fixtures import (
    CURRENT_CRITERION,
    CURRENT_FRONTIER,
    IMPLEMENTATION_BRANCH,
    MILESTONE_BRANCH,
    PLAN_RELATIVE,
    PROPOSAL_BRANCH,
    PROPOSAL_RELATIVE,
    proposal_text,
    ready_plan_text,
)

PLAN_REVISION_BRANCH = "m900/plan-shadow-proposals"
REVISED_FRONTIER = "Shadow action proposals"


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


class ProposalDocumentTests(unittest.TestCase):
    def test_required_proposal_shape_is_accepted(self) -> None:
        validate_proposal_text(proposal_text())

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
        )
        self.assertEqual(transition, "proposal")

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
        )
        self.assertEqual(transition, "implementation")

    def test_implementation_pr_normalizes_opened_branch_annotation(self) -> None:
        accepted = accept_proposal(
            self.proposal_head,
            proposal_pr=60,
            merge_commit="a" * 40,
            proposal_url="https://example.invalid/60",
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
        )

        self.assertEqual(transition, "implementation")

    def test_implementation_cannot_modify_accepted_proposal(self) -> None:
        accepted = accept_proposal(
            self.proposal_head,
            proposal_pr=60,
            merge_commit="a" * 40,
            proposal_url="https://example.invalid/60",
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
        return {
            "state": "MERGED",
            "baseRefName": MILESTONE_BRANCH,
            "headRefName": PROPOSAL_BRANCH,
            "mergeCommit": {"oid": "b" * 40},
            "url": "https://example.invalid/60",
            "files": [
                {"path": PLAN_RELATIVE},
                {"path": str(Path(PLAN_RELATIVE).with_suffix(".html"))},
                {"path": PROPOSAL_RELATIVE},
            ],
        }

    def test_merged_proposal_records_exact_commit(self) -> None:
        commit, url = validate_merged_proposal_metadata(
            self._payload(),
            self.state,
            proposal_pr=60,
            allowed_paths=self.allowed,
        )
        self.assertEqual(commit, "b" * 40)
        self.assertEqual(url, "https://example.invalid/60")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
