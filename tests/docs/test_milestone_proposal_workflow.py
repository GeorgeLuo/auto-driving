from __future__ import annotations

import re
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


ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = (
    ROOT / "docs" / "milestones" / "005-evidence-memory-foundation" / "plan.md"
)
PLAN_RELATIVE = PLAN_PATH.relative_to(ROOT).as_posix()
PROPOSAL_RELATIVE = (
    "docs/milestones/005-evidence-memory-foundation/"
    "proposals/conflicting-evidence.md"
)


def _proposal_text() -> str:
    return """# Proposal: Conflicting evidence semantics

## Review Question

Is the conflict policy bounded and deterministic?

## Proposed Contract

One slot has one structural contract.

## Ownership

The bounded evidence ledger owns compatibility.

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
"""


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
        validate_proposal_text(_proposal_text())

    def test_missing_validation_plan_is_rejected(self) -> None:
        with self.assertRaisesRegex(PlanContractError, "Validation Plan"):
            validate_proposal_text(
                _proposal_text().replace("## Validation Plan", "## Checks")
            )


def _as_ready_for_proposal(text: str) -> str:
    """Rewind the live plan to ready_for_proposal with a consistent history tail.

    Proposal-transition tests need a ready_for_proposal base even after the live
    milestone has advanced. Preserve earlier history rows; drop later ones for the
    current frontier and force the current state line.
    """

    state = validate_plan_text(text)
    if state.current.is_empty or state.current.name is None:
        raise AssertionError("active current frontier required")
    frontier = state.current.name
    current_state = state.current.fields["workflow state"]
    updated = text
    updated = re.sub(r"^- PR: .+\n", "", updated, count=1, flags=re.M)
    updated = re.sub(r"^- Accepted proposal: .+\n", "", updated, count=1, flags=re.M)
    updated = updated.replace(
        f"- Workflow state: {current_state}\n",
        "- Workflow state: ready_for_proposal\n",
        1,
    )
    # Keep history through the latest ready_for_proposal row for this frontier.
    lines = updated.splitlines()
    history_start = None
    for index, line in enumerate(lines):
        if line.strip() == "## Workflow History":
            history_start = index
            break
    if history_start is None:
        raise AssertionError("workflow history missing")
    table_start = history_start + 1
    while table_start < len(lines) and not lines[table_start].startswith("|"):
        table_start += 1
    table_end = table_start
    while table_end < len(lines) and lines[table_end].startswith("|"):
        table_end += 1
    header = lines[table_start : table_start + 2]
    body = lines[table_start + 2 : table_end]
    kept: list[str] = []
    for row in body:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        row_frontier, row_state = cells[0], cells[1]
        kept.append(row)
        if row_frontier == frontier and row_state == "ready_for_proposal":
            # Drop any later same-frontier transitions after the ready row.
            # If the live plan has later rows for this frontier after this point,
            # stop including them by breaking after this match and ignoring rest
            # that share this frontier... we need to continue for other frontiers
            # but conflict is current only. Simpler: truncate after this row.
            # Actually history is only this frontier currently. Truncate.
            break
    rebuilt = lines[:table_start] + header + kept + lines[table_end:]
    return "\n".join(rebuilt) + ("\n" if text.endswith("\n") else "")


class WorkflowStateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = PLAN_PATH.read_text(encoding="utf-8")

    def test_implementation_ready_requires_accepted_proposal_receipt(self) -> None:
        base = _as_ready_for_proposal(self.plan)
        state = validate_plan_text(base)
        invalid = base.replace(
            f"- Workflow state: {state.current.fields['workflow state']}\n",
            "- Workflow state: ready_for_implementation\n",
            1,
        )
        # Keep history mismatched intentionally? Must match latest history for
        # the accepted-proposal rule to be evaluated after history checks.
        # Advance history state in place on the last row.
        invalid = invalid.replace(
            f"| {state.current.name} | ready_for_proposal |",
            f"| {state.current.name} | ready_for_implementation |",
            1,
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "requires an accepted proposal",
        ):
            validate_plan_text(invalid)

    def test_latest_history_must_match_current_state(self) -> None:
        state = validate_plan_text(self.plan)
        current = state.current.fields["workflow state"]
        # Flip only the current state line so history lags.
        other = (
            "proposal_in_review"
            if current == "ready_for_proposal"
            else "ready_for_proposal"
        )
        invalid = self.plan.replace(
            f"- Workflow state: {current}\n",
            f"- Workflow state: {other}\n",
            1,
        )
        with self.assertRaisesRegex(
            PlanContractError,
            "latest state does not match",
        ):
            validate_plan_text(invalid)


class ReviewUnitTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        live = PLAN_PATH.read_text(encoding="utf-8")
        self.base = _as_ready_for_proposal(live)
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
            head_branch="m005/conflicting-evidence-proposal",
            proposal_text=_proposal_text(),
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
                head_branch="m005/conflicting-evidence-proposal",
                proposal_text=_proposal_text(),
            )

    def test_proposal_pr_cannot_rewrite_frozen_non_goals(self) -> None:
        changed_contract = self.proposal_head.replace(
            "Semantic fusion, object identity, confidence aggregation, "
            "live-host re-proof, or action behavior",
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
                head_branch="m005/conflicting-evidence-proposal",
                proposal_text=_proposal_text(),
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
                head_branch="m005/conflicting-evidence",
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
            head_branch="m005/conflicting-evidence",
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
                head_branch="m005/conflicting-evidence",
            )


class ProposalAcceptanceMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        proposal_plan = _move_to_review(_as_ready_for_proposal(PLAN_PATH.read_text(encoding="utf-8")))
        self.state = validate_plan_text(proposal_plan)
        self.allowed = {
            PLAN_RELATIVE,
            str(Path(PLAN_RELATIVE).with_suffix(".html")),
            PROPOSAL_RELATIVE,
        }

    def _payload(self) -> dict[str, object]:
        return {
            "state": "MERGED",
            "baseRefName": "milestone/005-evidence-memory-foundation",
            "headRefName": "m005/conflicting-evidence-proposal",
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
            ready = _as_ready_for_proposal(PLAN_PATH.read_text(encoding="utf-8"))
            plan.write_text(ready, encoding="utf-8")
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
                "ready for proposal",
            )
            base_sha = self._git(root, "rev-parse", "HEAD")
            self._git(root, "switch", "-c", "m005/conflicting-evidence-proposal")
            plan.write_text(
                _move_to_review(plan.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            proposal = root / PROPOSAL_RELATIVE
            proposal.parent.mkdir(parents=True)
            proposal.write_text(_proposal_text(), encoding="utf-8")
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
                base_ref="milestone/005-evidence-memory-foundation",
                head_ref="m005/conflicting-evidence-proposal",
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
                _as_ready_for_proposal(PLAN_PATH.read_text(encoding="utf-8"))
            )
            accepted = accept_proposal(
                proposal_review,
                proposal_pr=60,
                merge_commit="c" * 40,
                proposal_url="https://example.invalid/60",
            )
            plan.write_text(accepted, encoding="utf-8")
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
                "accept proposal",
            )

            start_implementation_branch(
                plan,
                validate_plan_text(accepted),
                "m005/conflicting-evidence",
                repo_root=root,
            )

            self.assertEqual(
                self._git(root, "branch", "--show-current"),
                "m005/conflicting-evidence",
            )
            transitioned = validate_plan_text(plan.read_text(encoding="utf-8"))
            self.assertEqual(
                transitioned.current.fields["workflow state"],
                "implementation_in_review",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
