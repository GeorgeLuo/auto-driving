from __future__ import annotations

import copy
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
    RepairReviewMetadata,
    _fetch_pr_repair_review_metadata,
    _fetch_pr_review_metadata,
    accept_proposal,
    accept_proposal_amendment,
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


REPAIR_PR_URL = "https://github.com/example/repository/pull/60"
REPAIR_PR_AUTHOR = "repair-author"


def _repair_review_record(
    *,
    url: str,
    body: str,
    head_oid: str,
    submitted_at: str,
    actor: str,
    comments: list[dict[str, str]] | None = None,
    state: str = "COMMENTED",
    association: str = "COLLABORATOR",
    can_push: bool = True,
) -> dict[str, object]:
    comment_nodes = comments or []
    return {
        "url": url,
        "state": state,
        "body": body,
        "commit": {"oid": head_oid},
        "submittedAt": submitted_at,
        "author": {"login": actor},
        "authorAssociation": association,
        "authorCanPushToRepository": can_push,
        "includesCreatedEdit": False,
        "comments": {
            "nodes": comment_nodes,
            "totalCount": len(comment_nodes),
        },
    }


def _governed_repair_case(
    *,
    classifications: tuple[str, ...] = ("substantial",),
    severities: tuple[str, ...] = ("P1",),
    routes: dict[int, str] | None = None,
    risk_dispositions: dict[int, str] | None = None,
) -> tuple[str, RepairReviewMetadata]:
    if len(classifications) != len(severities):
        raise AssertionError("classification and severity fixtures must align")
    routes = routes or {}
    risk_dispositions = risk_dispositions or {}
    commits = [f"{index:x}" * 40 for index in range(1, len(classifications) + 2)]
    reviews: list[dict[str, object]] = []
    ledger_rows: list[str] = []
    findings: list[str] = []
    substantial_to_cycle: dict[int, int] = {}
    substantial_count = 0
    p0_cycles: set[int] = set()

    for cycle, (classification, severity) in enumerate(
        zip(classifications, severities, strict=True),
        start=1,
    ):
        reviewed_head = commits[cycle - 1]
        repair_head = commits[cycle]
        verdict_url = f"{REPAIR_PR_URL}#pullrequestreview-{100 + cycle}"
        finding_url = f"{REPAIR_PR_URL}#discussion_r{400 + cycle}"
        reviews.append(
            _repair_review_record(
                url=verdict_url,
                body=(
                    "Verdict: changes requested\n\n"
                    f"Classification: {classification}\n"
                    f"Highest severity: {severity}\n"
                ),
                head_oid=reviewed_head,
                submitted_at=f"2026-08-14T18:{cycle * 10:02d}:00Z",
                actor="workflow-reviewer",
                comments=[
                    {
                        "url": finding_url,
                        "body": f"[{severity}] Cycle {cycle} contract finding",
                    }
                ],
            )
        )
        findings.append(finding_url)
        ledger_rows.append(
            f"| {cycle} | {verdict_url} | {classification} | {severity} | "
            f"{repair_head} | Cycle {cycle} enforcement repair. |"
        )
        if classification == "substantial":
            substantial_count += 1
            substantial_to_cycle[substantial_count] = cycle
            if severity == "P0":
                p0_cycles.add(substantial_count)

    required_decisions = sorted(set(range(2, substantial_count + 1)) | p0_cycles)
    escalation_rows: list[str] = []
    audit_rows: list[str] = []
    finding_rows: list[str] = []
    for substantial_cycle in required_decisions:
        cycle = substantial_to_cycle[substantial_cycle]
        audited_head = commits[cycle]
        manifest = tuple(findings[:cycle])
        manifest_text = ", ".join(manifest)
        route = routes.get(
            substantial_cycle,
            "replan-current-unit" if substantial_cycle <= 2 else "continue-current-unit",
        )
        decision_url = f"{REPAIR_PR_URL}#pullrequestreview-{200 + substantial_cycle}"
        fresh_url = f"{REPAIR_PR_URL}#pullrequestreview-{300 + substantial_cycle}"
        decision_actor = "operator-reviewer"
        decision_time = f"2026-08-14T18:{cycle * 10 + 1:02d}:00Z"
        accepted_contract = "changed" if route == "proposal-amendment" else "unchanged"
        primary_question = (
            "not-singular" if route == "split-or-replace-review-unit" else "singular"
        )
        owner_abstraction = (
            "changed" if route in {"replan-current-unit", "split-or-replace-review-unit"}
            else "unchanged"
        )
        coherent_diff = "no" if route == "split-or-replace-review-unit" else "yes"
        replacement_lineage = (
            f"{REPAIR_PR_URL}#issuecomment-999"
            if route == "split-or-replace-review-unit"
            else "None"
        )
        cumulative_p0 = any(
            severities[index - 1] == "P0" for index in range(1, cycle + 1)
        )
        risk_disposition = risk_dispositions.get(
            substantial_cycle,
            (
                "P0 risk accepted for bounded re-review."
                if cumulative_p0
                else (
                    "Review unit abandoned with risk recorded."
                    if route == "abandon-review-unit"
                    else "None"
                )
            ),
        )
        disposition = f"Authorized {route} for the audited topology."
        decision_body = f"""## Repair Continuation Decision

- Substantial cycle: {substantial_cycle}
- Decision role: meta-manager
- Route: {route}
- Audited head: {audited_head}
- Accepted contract: {accepted_contract}
- Primary question: {primary_question}
- Enforcement owner/abstraction: {owner_abstraction}
- Coherent diff: {coherent_diff}
- Prior findings: all-disposed
- Cumulative history: visible-in-current-ledger
- Finding manifest: {manifest_text}
- Replacement lineage: {replacement_lineage}
- Risk disposition: {risk_disposition}
- Disposition: {disposition}"""
        fresh_body = f"""## Repair Fresh-Context Review

- Substantial cycle: {substantial_cycle}
- Audited head: {audited_head}
- Finding manifest: {manifest_text}
- Scope: totality
- Outcome: totality-reviewed"""
        reviews.extend(
            [
                _repair_review_record(
                    url=decision_url,
                    body=decision_body,
                    head_oid=audited_head,
                    submitted_at=decision_time,
                    actor=decision_actor,
                ),
                _repair_review_record(
                    url=fresh_url,
                    body=fresh_body,
                    head_oid=audited_head,
                    submitted_at=f"2026-08-14T18:{cycle * 10 + 2:02d}:00Z",
                    actor="fresh-reviewer",
                ),
            ]
        )
        escalation_rows.append(
            f"| {substantial_cycle} | {decision_url} | {decision_actor} | "
            f"meta-manager | {decision_time} | {route} | {audited_head} | "
            f"{fresh_url} | {manifest_text} | {disposition} |"
        )
        audit_rows.append(
            f"| {substantial_cycle} | {decision_url} | {accepted_contract} | "
            f"{primary_question} | {owner_abstraction} | {coherent_diff} | "
            "all-disposed | visible-in-current-ledger | "
            f"{replacement_lineage} | {risk_disposition} |"
        )
        for finding in manifest:
            finding_rows.append(
                f"| {substantial_cycle} | {finding} | resolved | {audited_head} | "
                f"{decision_url} |"
            )

    body = repair_cycle_governance_body(
        rows="\n".join(ledger_rows),
        escalation_rows=(
            "\n".join(escalation_rows)
            if escalation_rows
            else "| None | None | None | None | None | None | None | None | None | None |"
        ),
        audit_rows=(
            "\n".join(audit_rows)
            if audit_rows
            else "| None | None | None | None | None | None | None | None | None | None |"
        ),
        finding_rows=(
            "\n".join(finding_rows)
            if finding_rows
            else "| None | None | None | None | None |"
        ),
    )
    metadata = RepairReviewMetadata(
        pull_request_number=60,
        pull_request_url=REPAIR_PR_URL,
        pull_request_author=REPAIR_PR_AUTHOR,
        head_oid=commits[-1],
        commits=tuple(commits),
        reviews=tuple(reviews),
    )
    return body, metadata


def _metadata_review(
    metadata: RepairReviewMetadata,
    url_suffix: str,
) -> dict[str, object]:
    matches = [
        review for review in metadata.reviews if str(review.get("url", "")).endswith(url_suffix)
    ]
    if len(matches) != 1:
        raise AssertionError(f"fixture review not found: {url_suffix}")
    return matches[0]


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

    def test_declared_cycle_requires_github_metadata(self) -> None:
        body, _ = _governed_repair_case()
        with self.assertRaisesRegex(PlanContractError, "structured GitHub"):
            validate_repair_cycle_governance_body(body)

    def test_one_substantial_cycle_is_bound_to_exact_review_evidence(self) -> None:
        body, metadata = _governed_repair_case()
        self.assertEqual(
            validate_repair_cycle_governance_body(body, review_metadata=metadata),
            1,
        )

    def test_arbitrary_review_reference_is_rejected(self) -> None:
        body, metadata = _governed_repair_case()
        body = body.replace(
            f"{REPAIR_PR_URL}#pullrequestreview-101",
            "#1",
            1,
        )
        with self.assertRaisesRegex(PlanContractError, "review on PR"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_second_substantial_cycle_requires_decision_history(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        escalation_row = next(
            line for line in body.splitlines() if line.startswith("| 2 |") and "-202" in line
        )
        body = body.replace(
            escalation_row,
            "| None | None | None | None | None | None | None | None | None | None |",
            1,
        )
        with self.assertRaisesRegex(PlanContractError, "keyed|required substantial"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_second_substantial_cycle_accepts_authorized_exact_head_receipts(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        self.assertEqual(
            validate_repair_cycle_governance_body(body, review_metadata=metadata),
            2,
        )

    def test_minor_cycles_do_not_consume_substantial_cycle_budget(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("minor", "substantial"),
            severities=("P3", "P1"),
        )
        self.assertEqual(
            validate_repair_cycle_governance_body(body, review_metadata=metadata),
            1,
        )

    def test_minor_cycle_after_threshold_preserves_prior_exact_head_decision(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial", "minor"),
            severities=("P1", "P1", "P3"),
        )
        self.assertEqual(
            validate_repair_cycle_governance_body(body, review_metadata=metadata),
            2,
        )

    def test_later_substantial_cycles_preserve_every_decision_and_audit(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial", "substantial"),
            severities=("P1", "P1", "P1"),
        )
        self.assertEqual(
            validate_repair_cycle_governance_body(body, review_metadata=metadata),
            3,
        )

    def test_fourth_substantial_cycle_requires_another_distinct_renewal(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=(
                "substantial",
                "substantial",
                "substantial",
                "substantial",
            ),
            severities=("P1", "P1", "P1", "P1"),
        )
        self.assertEqual(
            validate_repair_cycle_governance_body(body, review_metadata=metadata),
            4,
        )

    def test_later_cycle_cannot_reuse_a_decision_receipt(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial", "substantial"),
            severities=("P1", "P1", "P1"),
        )
        body = body.replace("pullrequestreview-203", "pullrequestreview-202")
        with self.assertRaisesRegex(PlanContractError, "unique per cycle"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_later_cycle_cannot_drop_an_earlier_decision(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial", "substantial"),
            severities=("P1", "P1", "P1"),
        )
        row = next(
            line for line in body.splitlines() if line.startswith("| 2 |") and "-202" in line
        )
        body = body.replace(f"{row}\n", "", 1)
        with self.assertRaisesRegex(PlanContractError, "preserve one immutable row"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_authorized_actor_cannot_rewrite_a_cycle_decision(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        replacement = copy.deepcopy(
            _metadata_review(metadata, "pullrequestreview-202")
        )
        replacement["url"] = f"{REPAIR_PR_URL}#pullrequestreview-902"
        replacement["submittedAt"] = "2026-08-14T18:23:00Z"
        metadata = RepairReviewMetadata(
            pull_request_number=metadata.pull_request_number,
            pull_request_url=metadata.pull_request_url,
            pull_request_author=metadata.pull_request_author,
            head_oid=metadata.head_oid,
            commits=metadata.commits,
            reviews=metadata.reviews + (replacement,),
        )
        with self.assertRaisesRegex(PlanContractError, "rewritten or duplicate"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_dispositions_must_use_exact_reviewer_owned_findings(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        prefix, suffix = body.rsplit("discussion_r401", 1)
        body = prefix + "discussion_r999" + suffix
        with self.assertRaisesRegex(PlanContractError, "not reviewer-owned"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_dispositions_cannot_omit_a_prior_finding(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        row = next(
            line
            for line in body.splitlines()
            if line.startswith("| 2 |") and "discussion_r401" in line and "resolved" in line
        )
        body = body.replace(f"{row}\n", "", 1)
        with self.assertRaisesRegex(PlanContractError, "exact cumulative finding"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_split_route_fails_closed_pending_structured_lineage(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial", "substantial"),
            severities=("P1", "P1", "P1"),
            routes={3: "split-or-replace-review-unit"},
        )
        with self.assertRaisesRegex(PlanContractError, "issue #118"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_same_unit_continuation_rejects_changed_topology(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial", "substantial"),
            severities=("P1", "P1", "P1"),
        )
        body = body.replace(
            "| 3 | https://github.com/example/repository/pull/60#pullrequestreview-203 | unchanged | singular |",
            "| 3 | https://github.com/example/repository/pull/60#pullrequestreview-203 | unchanged | not-singular |",
            1,
        )
        metadata = copy.deepcopy(metadata)
        decision = _metadata_review(metadata, "pullrequestreview-203")
        decision["body"] = str(decision["body"]).replace(
            "- Primary question: singular",
            "- Primary question: not-singular",
        )
        with self.assertRaisesRegex(PlanContractError, "unchanged singular contract"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_proposal_amendment_route_requires_changed_contract(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial", "substantial"),
            severities=("P1", "P1", "P1"),
            routes={3: "proposal-amendment"},
        )
        body = body.replace(
            "| 3 | https://github.com/example/repository/pull/60#pullrequestreview-203 | changed |",
            "| 3 | https://github.com/example/repository/pull/60#pullrequestreview-203 | unchanged |",
            1,
        )
        metadata = copy.deepcopy(metadata)
        decision = _metadata_review(metadata, "pullrequestreview-203")
        decision["body"] = str(decision["body"]).replace(
            "- Accepted contract: changed",
            "- Accepted contract: unchanged",
        )
        with self.assertRaisesRegex(PlanContractError, "changed accepted contract"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_decision_actor_must_differ_from_repair_author(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        metadata = copy.deepcopy(metadata)
        decision = _metadata_review(metadata, "pullrequestreview-202")
        decision["author"] = {"login": REPAIR_PR_AUTHOR}
        with self.assertRaisesRegex(PlanContractError, "other than the repair author"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_fresh_review_requires_current_authority(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        metadata = copy.deepcopy(metadata)
        fresh = _metadata_review(metadata, "pullrequestreview-302")
        fresh["authorCanPushToRepository"] = False
        with self.assertRaisesRegex(PlanContractError, "not currently authorized"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_decision_must_be_attached_to_exact_audited_head(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        metadata = copy.deepcopy(metadata)
        decision = _metadata_review(metadata, "pullrequestreview-202")
        decision["commit"] = {"oid": metadata.commits[1]}
        with self.assertRaisesRegex(PlanContractError, "exact audited head"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_author_written_decision_time_cannot_override_github_time(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        body = body.replace("2026-08-14T18:21:00Z", "2026-99-99T99:99:99Z", 1)
        with self.assertRaisesRegex(PlanContractError, "must match GitHub"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_fresh_review_receipt_cannot_be_reused_as_decision(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        body = body.replace("pullrequestreview-302", "pullrequestreview-202", 1)
        with self.assertRaisesRegex(PlanContractError, "unique per cycle"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_fresh_review_must_follow_decision(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial", "substantial"),
            severities=("P1", "P1"),
        )
        metadata = copy.deepcopy(metadata)
        fresh = _metadata_review(metadata, "pullrequestreview-302")
        fresh["submittedAt"] = "2026-08-14T18:20:00Z"
        with self.assertRaisesRegex(PlanContractError, "must follow"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_reviewer_owned_p0_requires_risk_disposition(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial",),
            severities=("P0",),
            risk_dispositions={1: "None"},
        )
        with self.assertRaisesRegex(PlanContractError, "reviewer-owned P0"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_missing_reviewer_severity_fails_closed(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial",),
            severities=("P0",),
        )
        metadata = copy.deepcopy(metadata)
        verdict = _metadata_review(metadata, "pullrequestreview-101")
        comments = verdict["comments"]
        assert isinstance(comments, dict)
        nodes = comments["nodes"]
        assert isinstance(nodes, list)
        nodes[0]["body"] = "Unsafe boundary without a structured severity"
        with self.assertRaisesRegex(PlanContractError, "reviewer-owned.*severity"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_negated_p0_impact_does_not_create_reviewer_severity(self) -> None:
        body, metadata = _governed_repair_case()
        body = body.replace(
            "Cycle 1 enforcement repair.",
            "No P0 remains after the enforcement repair.",
        )
        self.assertEqual(
            validate_repair_cycle_governance_body(body, review_metadata=metadata),
            1,
        )

    def test_ledger_severity_must_match_reviewer_manifest(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial",),
            severities=("P0",),
        )
        body = body.replace("| substantial | P0 |", "| substantial | P1 |", 1)
        with self.assertRaisesRegex(PlanContractError, "highest severity"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_reviewer_severity_declaration_cannot_conflict_with_findings(self) -> None:
        body, metadata = _governed_repair_case(
            classifications=("substantial",),
            severities=("P0",),
        )
        metadata = copy.deepcopy(metadata)
        verdict = _metadata_review(metadata, "pullrequestreview-101")
        verdict["body"] = str(verdict["body"]).replace(
            "Highest severity: P0",
            "Highest severity: P1",
        )
        with self.assertRaisesRegex(PlanContractError, "conflicts"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)

    def test_cycle_numbers_must_be_consecutive(self) -> None:
        body, metadata = _governed_repair_case()
        body = body.replace("| 1 |", "| 2 |", 1)
        with self.assertRaisesRegex(PlanContractError, "consecutive from 1"):
            validate_repair_cycle_governance_body(body, review_metadata=metadata)


class RepairMetadataFetchTests(unittest.TestCase):
    def _response(self) -> dict[str, object]:
        body, metadata = _governed_repair_case()
        del body
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "number": metadata.pull_request_number,
                        "url": metadata.pull_request_url,
                        "headRefOid": metadata.head_oid,
                        "author": {"login": metadata.pull_request_author},
                        "commits": {
                            "totalCount": len(metadata.commits),
                            "nodes": [
                                {"commit": {"oid": oid}} for oid in metadata.commits
                            ],
                        },
                        "reviews": {
                            "totalCount": len(metadata.reviews),
                            "nodes": list(metadata.reviews),
                        },
                    }
                }
            }
        }

    def test_fetch_exposes_pr_identity_commits_reviews_and_findings(self) -> None:
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
                stdout=json.dumps(self._response()),
                stderr="",
            ),
        ]
        with mock.patch(
            "docs.milestones.workflow.subprocess.run",
            side_effect=completed,
        ):
            metadata = _fetch_pr_repair_review_metadata(60)

        self.assertEqual(metadata.pull_request_author, REPAIR_PR_AUTHOR)
        self.assertEqual(metadata.head_oid, metadata.commits[-1])
        verdict = _metadata_review(metadata, "pullrequestreview-101")
        comments = verdict["comments"]
        assert isinstance(comments, dict)
        self.assertEqual(comments["totalCount"], 1)

    def test_fetch_fails_closed_when_review_history_would_truncate(self) -> None:
        response = self._response()
        pull_request = response["data"]["repository"]["pullRequest"]  # type: ignore[index]
        pull_request["reviews"] = {"totalCount": 101, "nodes": []}  # type: ignore[index]
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
                _fetch_pr_repair_review_metadata(60)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
