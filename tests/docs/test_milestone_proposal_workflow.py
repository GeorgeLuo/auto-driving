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
    CURRENT_FRONTIER,
    IMPLEMENTATION_BRANCH,
    MILESTONE_BRANCH,
    PLAN_RELATIVE,
    PROPOSAL_BRANCH,
    PROPOSAL_RELATIVE,
    proposal_text,
    ready_plan_text,
)

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

    def test_implementation_branch_starts_only_after_proposal_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / PLAN_RELATIVE
            plan.parent.mkdir(parents=True)
            proposal_review = _move_to_review(
                ready_plan_text()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
