from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from docs.milestones.workflow import (
    PlanContractError,
    apply_handoff,
    start_proposal_branch,
    validate_merged_pr_metadata,
    validate_plan_text,
    verify_handoff_git_state,
)


ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "milestones" / "005-evidence-memory-foundation" / "plan.md"


def _plan_with_current_pr(text: str) -> str:
    marker = "**Conflicting evidence semantics**\n\n"
    if marker not in text:
        raise AssertionError("current frontier marker is missing")
    updated = text.replace(
        marker,
        marker + "- PR: [#59](https://example.invalid/59)\n",
        1,
    )
    updated = updated.replace(
        "- Workflow state: ready_for_proposal\n",
        "- Workflow state: implementation_in_review\n",
        1,
    )
    updated = updated.replace(
        "- Proposal path: `docs/milestones/005-evidence-memory-foundation/proposals/conflicting-evidence.md`\n",
        "- Proposal path: `docs/milestones/005-evidence-memory-foundation/proposals/conflicting-evidence.md`\n"
        "- Accepted proposal: [#60](https://example.invalid/60) at `abc1234`\n",
        1,
    )
    history_row = (
        "| Conflicting evidence semantics | ready_for_proposal | #58 froze the minimal "
        "frontier contract; draft implementation PR #59 is paused because no independent "
        "proposal has been reviewed and accepted |"
    )
    return updated.replace(
        history_row,
        history_row
        + "\n| Conflicting evidence semantics | proposal_in_review | Proposal branch started. |"
        + "\n| Conflicting evidence semantics | ready_for_implementation | Proposal PR #60 accepted. |"
        + "\n| Conflicting evidence semantics | implementation_in_review | Implementation branch started. |",
        1,
    )


def _receipt(*, merge_commit: str = "deadbee") -> dict[str, object]:
    return {
        "schema": "milestone_handoff_v1",
        "accepted_pr": 59,
        "accepted_merge_commit": merge_commit,
        "outcome": "advance",
        "result": "Accepted",
        "durable_evidence": "Focused conflict and replay tests",
        "criterion_updates": {
            "M005-08": {
                "status": "Met",
                "evidence": "Deterministic conflicting-evidence contract accepted in #59",
            },
        },
        "risk_remove": [
            "Conflicting evidence from one source/key has no explicit compatibility or replacement contract"
        ],
        "risk_upsert": [],
        "next_frontier": {
            "state": "none",
            "reason": "Milestone closeout is current.",
            "revisit_when": "Closeout decides whether to activate milestone 006.",
        },
    }


class MilestonePlanContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan_text = PLAN.read_text(encoding="utf-8")
        self.open_plan_text = _plan_with_current_pr(self.plan_text)

    def test_invalid_exit_status_is_rejected_in_its_table_cell(self) -> None:
        invalid = self.plan_text.replace(
            "| M005-13 | Closeout states what memory representation proved useful, what remains unverified, and whether later pattern or action work is justified | Blocked |",
            "| M005-13 | Closeout states what memory representation proved useful, what remains unverified, and whether later pattern or action work is justified | READY |",
        )

        with self.assertRaisesRegex(PlanContractError, "M005-13.*invalid status"):
            validate_plan_text(invalid)

    def test_missing_current_frontier_owner_is_rejected(self) -> None:
        invalid = self.plan_text.replace(
            "- Acceptance owner: `BoundedEvidenceLedger` update policy plus focused implementation and replay fixtures\n",
            "",
        )

        with self.assertRaisesRegex(
            PlanContractError,
            "Current Frontier.*acceptance owner",
        ):
            validate_plan_text(invalid)

    def test_frontier_criteria_must_be_known_explicit_ids(self) -> None:
        invalid = self.plan_text.replace(
            "- Exit criteria affected: M005-08\n",
            "- Exit criteria affected: M005-08 through M005-09\n",
            1,
        )

        with self.assertRaisesRegex(
            PlanContractError,
            "comma-separated list of IDs",
        ):
            validate_plan_text(invalid)

    def test_next_frontier_branch_must_use_milestone_prefix(self) -> None:
        invalid = self.plan_text.replace(
            "- Implementation branch: `m005/closeout` (planned; not opened)\n",
            "- Implementation branch: `agent/closeout`\n",
        )

        with self.assertRaisesRegex(
            PlanContractError,
            "Next-Frontier Candidate implementation branch",
        ):
            validate_plan_text(invalid)

    def test_milestone_branch_must_match_milestone_number(self) -> None:
        invalid = self.plan_text.replace(
            "`milestone/005-evidence-memory-foundation`",
            "`milestone/006-wrong-milestone`",
            1,
        )

        with self.assertRaisesRegex(
            PlanContractError,
            "Milestone branch must start with 'milestone/005-'",
        ):
            validate_plan_text(invalid)

    def test_mid_milestone_adoption_requires_cutover_and_baseline_ledger(self) -> None:
        missing_cutover = self.plan_text.replace(
            "| Cutover | #57 merged to `main`; #58 recorded its accepted result and established the remaining conflict frontier; the milestone branch was created from resulting `main` |\n",
            "",
        )
        with self.assertRaisesRegex(PlanContractError, "baseline and Cutover"):
            validate_plan_text(missing_cutover)

        missing_baseline_row = self.plan_text.replace(
            "| Baseline #34–#50 (`22cfff9`) | Are the pre-contract M005 memory foundation, operator paths, Pi lifecycle proof, and integration-branch decision accepted as historical starting state? | Accepted before compact-contract adoption | M005-01–M005-12 at the statuses recorded above | Mainline history through `22cfff9`; tracked Pi and replay evidence referenced by the criteria |\n",
            "",
        )
        with self.assertRaisesRegex(PlanContractError, "Contract baseline row"):
            validate_plan_text(missing_baseline_row)

    def test_mid_milestone_adoption_names_grandfathered_prs(self) -> None:
        missing_field = self.plan_text.replace(
            "| Grandfathered PRs | #57 (accepted evidence unit), #58 (contract migration); both retain their existing `main` targets |\n",
            "",
        )
        with self.assertRaisesRegex(PlanContractError, "Grandfathered PRs"):
            validate_plan_text(missing_field)

    def test_handoff_promotes_closeout_and_allows_terminal_next_slot(self) -> None:
        updated = apply_handoff(self.open_plan_text, _receipt())
        state = validate_plan_text(updated)

        self.assertEqual(state.status, "Active")
        self.assertEqual(state.current.name, "Milestone closeout")
        self.assertTrue(state.next_frontier.is_empty)
        self.assertIn(
            ("#59",),
            tuple((row[0],) for row in state.ledger.rows),
        )
        statuses = {row[0]: row[2] for row in state.criteria.rows}
        self.assertEqual(statuses["M005-08"], "Met")
        self.assertEqual(statuses["M005-09"], "Met")
        self.assertNotIn(
            "Conflicting evidence from one source/key has no explicit",
            updated,
        )

    def test_handoff_rejects_before_implementation_review(self) -> None:
        with self.assertRaisesRegex(
            PlanContractError,
            "requires workflow state implementation_in_review",
        ):
            apply_handoff(self.plan_text, _receipt())

    def test_handoff_rejects_duplicate_ledger_entry(self) -> None:
        marker = "\n\nThe baseline row is the explicit adoption boundary"
        duplicate_plan = self.open_plan_text.replace(
            marker,
            "\n| #59 | Already accepted | Accepted | M005-08 | duplicate |" + marker,
        )

        with self.assertRaisesRegex(PlanContractError, "already in the accepted ledger"):
            apply_handoff(duplicate_plan, _receipt())

    def test_plan_validation_rejects_duplicate_ledger_pr_rows(self) -> None:
        marker = "\n\nThe baseline row is the explicit adoption boundary"
        duplicate_plan = self.plan_text.replace(
            marker,
            "\n| #57 | Duplicate accepted result | Accepted | M005-09 | duplicate |"
            + marker,
        )

        with self.assertRaisesRegex(PlanContractError, "duplicate accepted ledger PR"):
            validate_plan_text(duplicate_plan)

    def test_handoff_rejects_criterion_updates_outside_current_frontier(self) -> None:
        receipt = _receipt()
        receipt["criterion_updates"]["M005-03"] = {
            "status": "Met",
            "evidence": "unowned update",
        }

        with self.assertRaisesRegex(
            PlanContractError,
            "outside the current frontier: M005-03",
        ):
            apply_handoff(self.open_plan_text, receipt)

    def test_handoff_cannot_invent_next_candidate(self) -> None:
        receipt = _receipt()
        receipt["next_frontier"] = {
            "state": "candidate",
            "name": "Unreviewed work",
        }

        with self.assertRaisesRegex(
            PlanContractError,
            "cannot invent an unreviewed next candidate",
        ):
            apply_handoff(self.open_plan_text, receipt)

    def test_handoff_rejects_premature_closeout_promotion(self) -> None:
        incomplete = self.open_plan_text.replace(
            "| M005-07 | Default execution writes no logs, frames, or memory history; recording is explicit and bounded | Met |",
            "| M005-07 | Default execution writes no logs, frames, or memory history; recording is explicit and bounded | Partial |",
        )

        with self.assertRaisesRegex(
            PlanContractError,
            "cannot promote milestone closeout.*M005-07",
        ):
            apply_handoff(incomplete, _receipt())

    def test_closeout_handoff_requires_and_records_all_criteria_met(self) -> None:
        promoted = apply_handoff(self.open_plan_text, _receipt())
        promoted = promoted.replace(
            "**Milestone closeout**\n",
            "**Milestone closeout**\n\n- PR: [#60](https://example.invalid/60)\n",
            1,
        )
        promoted = promoted.replace(
            "- Workflow state: ready_for_proposal\n",
            "- Workflow state: implementation_in_review\n",
            1,
        )
        promoted = promoted.replace(
            "- Proposal path: `docs/milestones/005-evidence-memory-foundation/proposals/closeout.md`\n",
            "- Proposal path: `docs/milestones/005-evidence-memory-foundation/proposals/closeout.md`\n"
            "- Accepted proposal: [#61](https://example.invalid/61) at `cab1234`\n",
            1,
        )
        promoted = promoted.replace(
            "\n\n## Accepted Review Units",
            "\n| Milestone closeout | proposal_in_review | Proposal branch started. |"
            "\n| Milestone closeout | ready_for_implementation | Proposal PR #61 accepted. |"
            "\n| Milestone closeout | implementation_in_review | Implementation branch started. |"
            "\n\n## Accepted Review Units",
            1,
        )
        close_receipt = {
            "schema": "milestone_handoff_v1",
            "accepted_pr": 60,
            "accepted_merge_commit": "feedbee",
            "outcome": "close",
            "result": "Accepted",
            "durable_evidence": "closeout.md",
            "criterion_updates": {},
            "risk_remove": [],
            "risk_upsert": [],
        }
        with self.assertRaisesRegex(
            PlanContractError,
            "cannot close milestone.*M005-13",
        ):
            apply_handoff(promoted, close_receipt)

        close_receipt["criterion_updates"] = {
            "M005-13": {
                "status": "Met",
                "evidence": "Milestone closeout accepted",
            }
        }
        closed = validate_plan_text(apply_handoff(promoted, close_receipt))
        self.assertEqual(closed.status, "closed")
        self.assertTrue(closed.current.is_empty)
        self.assertTrue(closed.next_frontier.is_empty)

    def test_github_metadata_must_match_merge_and_milestone_branch(self) -> None:
        state = validate_plan_text(self.open_plan_text)
        receipt = _receipt(merge_commit="abc1234")
        valid = {
            "state": "MERGED",
            "baseRefName": "milestone/005-evidence-memory-foundation",
            "headRefName": "m005/conflicting-evidence",
            "mergeCommit": {"oid": "abc123456789"},
        }
        validate_merged_pr_metadata(valid, state, receipt)

        wrong_base = {**valid, "baseRefName": "main"}
        with self.assertRaisesRegex(PlanContractError, "did not target"):
            validate_merged_pr_metadata(wrong_base, state, receipt)

        wrong_sha = {**valid, "mergeCommit": {"oid": "def567890"}}
        with self.assertRaisesRegex(PlanContractError, "does not match"):
            validate_merged_pr_metadata(wrong_sha, state, receipt)


class MilestoneHandoffGitOrderingTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def test_handoff_requires_clean_matching_branch_with_merge_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "docs" / "milestones" / "005-evidence-memory-foundation" / "plan.md"
            plan.parent.mkdir(parents=True)
            plan.write_text(
                _plan_with_current_pr(PLAN.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            self._git(root, "init", "-b", "milestone/005-evidence-memory-foundation")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "accepted review unit",
            )
            merge_commit = self._git(root, "rev-parse", "HEAD")
            state = validate_plan_text(plan.read_text(encoding="utf-8"))
            receipt = _receipt(merge_commit=merge_commit)

            verify_handoff_git_state(plan, state, receipt, repo_root=root)

            (root / "uncommitted.txt").write_text("dirty", encoding="utf-8")
            with self.assertRaisesRegex(PlanContractError, "clean worktree"):
                verify_handoff_git_state(plan, state, receipt, repo_root=root)

    def test_handoff_rejects_branch_before_milestone_integration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "docs" / "milestones" / "005-evidence-memory-foundation" / "plan.md"
            plan.parent.mkdir(parents=True)
            plan.write_text(
                _plan_with_current_pr(PLAN.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            self._git(root, "init", "-b", "m005/incorrect-review-unit")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "not merged to milestone branch",
            )
            merge_commit = self._git(root, "rev-parse", "HEAD")
            state = validate_plan_text(plan.read_text(encoding="utf-8"))

            with self.assertRaisesRegex(PlanContractError, "handoff must run on"):
                verify_handoff_git_state(
                    plan,
                    state,
                    _receipt(merge_commit=merge_commit),
                    repo_root=root,
                )

    def test_start_creates_only_the_current_proposal_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "docs" / "milestones" / "005-evidence-memory-foundation" / "plan.md"
            plan.parent.mkdir(parents=True)
            current = PLAN.read_text(encoding="utf-8")
            plan.write_text(current, encoding="utf-8")
            self._git(root, "init", "-b", "milestone/005-evidence-memory-foundation")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "frontier handoff",
            )
            state = validate_plan_text(current)

            start_proposal_branch(
                plan,
                state,
                "m005/conflicting-evidence-proposal",
                repo_root=root,
            )

            self.assertEqual(
                self._git(root, "branch", "--show-current"),
                "m005/conflicting-evidence-proposal",
            )
            transitioned = validate_plan_text(plan.read_text(encoding="utf-8"))
            self.assertEqual(
                transitioned.current.fields["workflow state"],
                "proposal_in_review",
            )

    def test_proposal_start_rejects_frontier_past_proposal_state(self) -> None:
        state = validate_plan_text(
            _plan_with_current_pr(PLAN.read_text(encoding="utf-8"))
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "requires ready_for_proposal",
        ):
            start_proposal_branch(
                PLAN,
                state,
                "m005/conflicting-evidence-proposal",
                repo_root=ROOT,
            )

    def test_start_rejects_existing_remote_tracking_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plan = root / "docs" / "milestones" / "005-evidence-memory-foundation" / "plan.md"
            plan.parent.mkdir(parents=True)
            current = PLAN.read_text(encoding="utf-8")
            plan.write_text(current, encoding="utf-8")
            self._git(root, "init", "-b", "milestone/005-evidence-memory-foundation")
            self._git(root, "add", ".")
            self._git(
                root,
                "-c",
                "user.name=Milestone Test",
                "-c",
                "user.email=milestone@example.invalid",
                "commit",
                "-m",
                "frontier handoff",
            )
            self._git(
                root,
                "update-ref",
                "refs/remotes/origin/m005/conflicting-evidence-proposal",
                "HEAD",
            )
            state = validate_plan_text(current)

            with self.assertRaisesRegex(PlanContractError, "branch already exists"):
                start_proposal_branch(
                    plan,
                    state,
                    "m005/conflicting-evidence-proposal",
                    repo_root=root,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
