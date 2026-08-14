#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[2]
MILESTONES = ROOT / "docs" / "milestones"
ALLOWED_MILESTONE_STATUSES = {"Active", "Blocked", "pre-plan", "closed"}
ALLOWED_CRITERION_STATUSES = {"Unmet", "Partial", "Met", "Blocked"}
COMMON_FRONTIER_FIELDS = (
    "review kind",
    "review question",
    "acceptance owner",
    "exit criteria affected",
    "prerequisite",
)
CURRENT_FRONTIER_FIELDS = (
    *COMMON_FRONTIER_FIELDS,
    "workflow state",
    "proposal branch",
    "implementation branch",
    "proposal path",
)
NEXT_FRONTIER_FIELDS = (
    *COMMON_FRONTIER_FIELDS,
    "proposal branch",
    "implementation branch",
    "proposal path",
)
WORKFLOW_STATES = {
    "ready_for_proposal",
    "proposal_in_review",
    "ready_for_implementation",
    "proposal_amendment_in_review",
    "implementation_in_review",
}
CONTRACT_REVIEW_RECEIPT_HEADING = "## Contract Review Receipt"
CONTRACT_REVIEW_RECEIPT_OUTCOMES = {"accepted", "changes_requested"}
AUTHORIZED_REVIEW_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
ALLOWED_REVIEW_KINDS = {
    "deterministic invariant closure",
    "behavioral feature slice",
    "broad mechanical rollout",
    "live or external evidence",
    "review repair",
    "milestone closeout",
}
PROPOSAL_REQUIRED_HEADINGS = (
    "## Review Question",
    "## Proposed Contract",
    "## Ownership",
    "## Affected Paths",
    "## Adversarial Matrix",
    "## External Assumptions",
    "## Non-Goals",
    "## File Impact",
    "## Validation Plan",
    "## Expected Handoff",
)
UNIVERSAL_CONTRACT_REQUIRED_HEADINGS = (
    "## Trust And Authority Model",
    "## Evidence Topology And Capture Strategy",
)
UNIVERSAL_CLAIM_PATTERN = re.compile(
    r"\b(?:bounded|detached|deterministic|exact|fresh)\b"
    r"|\bfail(?:-|\s+)closed\b"
    r"|\bno movement\b",
    re.IGNORECASE,
)
PROPOSAL_AMENDMENT_REQUIRED_HEADINGS = (
    "## Review Question",
    "## Reason For Amendment",
    "## Contract Delta",
    "## Ownership",
    "## Affected Paths",
    "## Adversarial Matrix",
    "## External Assumptions",
    "## Non-Goals",
    "## File Impact",
    "## Validation Plan",
)
IMPLEMENTATION_ADJUNCT_REQUIRED_HEADINGS = (
    "## Parent Implementation",
    "## Operator Request",
    "## HITL Authorization",
    "## Review Question",
    "## Compatibility",
    "## Scope",
    "## Evidence Impact",
    "## Validation",
)
IMPLEMENTATION_ADJUNCT_COMPATIBILITY_CHECKS = (
    "The parent contract remains true without this adjunct.",
    "The change serves the same current frontier and operator journey.",
    "The behavior is additive or optional and weakens no existing outcome.",
    (
        "No exit criterion, safety authority, schema, external assumption, "
        "expected handoff, or explicit non-goal changes."
    ),
    "No milestone plan, accepted proposal, or accepted amendment changes.",
    "There is one bounded review question and the base is the parent branch.",
)
REPAIR_CYCLE_LEDGER_HEADING = "## Repair Cycle Ledger"
REPAIR_CYCLE_LEDGER_HEADER = (
    "Cycle",
    "Review receipt",
    "Classification",
    "Repair revision",
    "Contract impact",
)
REPAIR_CYCLE_CLASSIFICATIONS = {"minor", "substantial"}
REPAIR_ESCALATION_HEADING = "## Repair Escalation"
REPAIR_ESCALATION_STATUSES = {"not-required", "completed"}
REPAIR_ESCALATION_ROUTES = {
    "replan-current-unit",
    "proposal-amendment",
    "split-or-replace-review-unit",
    "abandon-review-unit",
}
HANDOFF_TEMPLATE_SCHEMA = "milestone_handoff_template_v1"
EXAMPLE_RECEIPT: dict[str, Any] = {
    "schema": "milestone_handoff_v1",
    "accepted_pr": 123,
    "accepted_merge_commit": "0123456789abcdef",
    "outcome": "advance",
    "result": "Accepted",
    "durable_evidence": "tests and/or tracked evidence",
    "criterion_updates": {
        "M000-01": {
            "status": "Met",
            "evidence": "accepted result",
        }
    },
    "risk_remove": [],
    "risk_upsert": [],
    "next_frontier": {
        "state": "none",
        "reason": "No additional contract is justified yet.",
        "revisit_when": "Named evidence changes the frontier.",
    },
}


class PlanContractError(ValueError):
    pass


@dataclass(frozen=True)
class MarkdownTable:
    heading: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ContractReviewReceipt:
    head_oid: str
    reviewer: str
    reviewer_association: str
    submitted_at: str


@dataclass(frozen=True)
class ProposalReviewMetadata:
    merged_at: str
    reviews: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Frontier:
    name: str | None
    fields: dict[str, str]

    @property
    def is_empty(self) -> bool:
        return self.name is None


@dataclass(frozen=True)
class PlanState:
    milestone_number: str
    status: str
    milestone_branch: str
    current: Frontier
    next_frontier: Frontier
    criteria: MarkdownTable
    ledger: MarkdownTable
    workflow_history: MarkdownTable
    risks: MarkdownTable


def _workflow_state(frontier: Frontier) -> str:
    return frontier.fields.get("workflow state", "").strip().strip("`")


def _heading_level(heading: str) -> int:
    return len(heading) - len(heading.lstrip("#"))


def _section_bounds(lines: list[str], heading: str) -> tuple[int, int]:
    try:
        start_heading = lines.index(heading)
    except ValueError as exc:
        raise PlanContractError(f"missing section {heading}") from exc
    level = _heading_level(heading)
    end = len(lines)
    for index in range(start_heading + 1, len(lines)):
        line = lines[index]
        if line.startswith("#") and _heading_level(line) <= level:
            end = index
            break
    return start_heading + 1, end


def _split_table_row(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise PlanContractError(f"invalid Markdown table row: {line}")
    return tuple(cell.strip() for cell in stripped[1:-1].split("|"))


def _is_separator_row(cells: Iterable[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell) is not None for cell in cells)


def parse_table(text: str, heading: str) -> MarkdownTable:
    lines = text.splitlines()
    section_start, section_end = _section_bounds(lines, heading)
    table_start: int | None = None
    for index in range(section_start, section_end):
        if lines[index].strip().startswith("|"):
            table_start = index
            break
    if table_start is None:
        raise PlanContractError(f"{heading} must contain a Markdown table")

    table_lines: list[str] = []
    for index in range(table_start, section_end):
        if not lines[index].strip().startswith("|"):
            break
        table_lines.append(lines[index])
    if len(table_lines) < 2:
        raise PlanContractError(f"{heading} table is incomplete")

    header = _split_table_row(table_lines[0])
    separator = _split_table_row(table_lines[1])
    if len(separator) != len(header) or not _is_separator_row(separator):
        raise PlanContractError(f"{heading} table separator is invalid")

    rows: list[tuple[str, ...]] = []
    for line in table_lines[2:]:
        row = _split_table_row(line)
        if len(row) != len(header):
            raise PlanContractError(f"{heading} table row has the wrong column count")
        rows.append(row)
    return MarkdownTable(heading=heading, header=header, rows=tuple(rows))


def _normalize_field(label: str) -> str:
    normalized = re.sub(r"\s+", " ", label.strip().lower())
    if "non-goal" in normalized:
        return "non-goals"
    return normalized


def _normalize_review_kind(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().strip("`*_ ").lower())


def parse_frontier(text: str, heading: str) -> Frontier:
    lines = text.splitlines()
    start, end = _section_bounds(lines, heading)
    name: str | None = None
    fields: dict[str, str] = {}
    for line in lines[start:end]:
        stripped = line.strip()
        name_match = re.fullmatch(r"\*\*(.+)\*\*", stripped)
        if name_match and name is None:
            raw_name = name_match.group(1).strip()
            name = None if raw_name.lower() == "none" else raw_name
            continue
        field_match = re.match(r"-\s+\*\*?([^*:]+)\*\*?:\s*(.*)", stripped)
        if field_match is None:
            field_match = re.match(r"-\s+([^:]+):\s*(.*)", stripped)
        if field_match:
            fields[_normalize_field(field_match.group(1))] = field_match.group(2).strip()

    if name is None and not any(line.strip() == "**None**" for line in lines[start:end]):
        raise PlanContractError(f"{heading} must identify a frontier name or **None**")
    return Frontier(name=name, fields=fields)


def _header_values(text: str) -> dict[str, str]:
    table = parse_table(text, "# " + text.splitlines()[0].removeprefix("# ").strip())
    if table.header != ("Field", "Value"):
        raise PlanContractError("milestone header table must use Field and Value columns")
    values: dict[str, str] = {}
    for field, value in table.rows:
        if field in values:
            raise PlanContractError(f"duplicate milestone header field: {field}")
        values[field] = value
    return values


def _require_frontier_fields(
    frontier: Frontier,
    *,
    heading: str,
    current: bool,
) -> None:
    if frontier.is_empty:
        for field in ("reason", "revisit when"):
            if not frontier.fields.get(field):
                raise PlanContractError(f"{heading} empty state is missing {field!r}")
        return
    required = CURRENT_FRONTIER_FIELDS if current else NEXT_FRONTIER_FIELDS
    for field in required:
        if not frontier.fields.get(field):
            raise PlanContractError(f"{heading} is missing {field!r}")
    review_kind = _normalize_review_kind(frontier.fields["review kind"])
    if review_kind not in ALLOWED_REVIEW_KINDS:
        raise PlanContractError(
            f"{heading} has unsupported review kind {frontier.fields['review kind']!r}"
        )
    if not frontier.fields.get("non-goals"):
        raise PlanContractError(f"{heading} is missing 'non-goals'")


def _frontier_criterion_ids(
    frontier: Frontier,
    *,
    heading: str,
    known_ids: set[str],
) -> set[str]:
    if frontier.is_empty:
        return set()
    raw_value = frontier.fields["exit criteria affected"]
    criterion_ids = {
        value.strip().strip("`")
        for value in raw_value.split(",")
        if value.strip()
    }
    if not criterion_ids or any(
        re.fullmatch(r"M\d{3}-\d{2}", value) is None
        for value in criterion_ids
    ):
        raise PlanContractError(
            f"{heading} exit criteria affected must be a comma-separated list of IDs"
        )
    unknown = criterion_ids - known_ids
    if unknown:
        raise PlanContractError(
            f"{heading} references unknown exit criteria: {', '.join(sorted(unknown))}"
        )
    return criterion_ids


def _frontier_branch(frontier: Frontier, *, heading: str, field: str) -> str:
    raw_value = frontier.fields[field]
    quoted = re.search(r"`([^`]+)`", raw_value)
    branch = quoted.group(1) if quoted else raw_value.split(maxsplit=1)[0]
    if re.fullmatch(r"[A-Za-z0-9._/-]+", branch) is None:
        raise PlanContractError(f"{heading} has an invalid {field}")
    return branch


def _frontier_proposal_path(frontier: Frontier, *, heading: str) -> str:
    return _proposal_document_path(
        frontier.fields["proposal path"],
        heading=heading,
        field="proposal path",
    )


def _proposal_document_path(raw_value: str, *, heading: str, field: str) -> str:
    quoted = re.search(r"`([^`]+)`", raw_value)
    path = quoted.group(1) if quoted else raw_value.split(maxsplit=1)[0]
    if (
        re.fullmatch(
            r"docs/milestones/\d{3}-[A-Za-z0-9._-]+/proposals/[A-Za-z0-9._-]+\.md",
            path,
        )
        is None
    ):
        raise PlanContractError(
            f"{heading} {field} must be "
            "docs/milestones/<number>-<slug>/proposals/<name>.md"
        )
    return path


def _frontier_proposal_amendment_path(
    frontier: Frontier,
    *,
    heading: str,
) -> str:
    raw_value = frontier.fields.get("proposal amendment path")
    if not raw_value:
        raise PlanContractError(f"{heading} is missing 'proposal amendment path'")
    path = _proposal_document_path(
        raw_value,
        heading=heading,
        field="proposal amendment path",
    )
    if not path.endswith("-amendment.md"):
        raise PlanContractError(
            f"{heading} proposal amendment path must end with '-amendment.md'"
        )
    return path


def _accepted_proposal(frontier: Frontier, *, heading: str) -> tuple[int, str] | None:
    raw_value = frontier.fields.get("accepted proposal")
    if not raw_value:
        return None
    match = re.fullmatch(
        r"\[#(\d+)\]\([^)]+\) at `([0-9a-f]{7,40})` "
        r"\(reviewed head `([0-9a-f]{40})` by `([^`]+)` as "
        r"`(OWNER|MEMBER|COLLABORATOR)` at `([^`]+)`\)",
        raw_value,
    )
    if match is None:
        raise PlanContractError(
            f"{heading} accepted proposal must identify a PR, merge commit, "
            "and authorized exact-head contract review receipt"
        )
    _github_timestamp(
        match.group(6),
        label=f"{heading} accepted proposal contract review",
    )
    if match.group(3) == match.group(2):
        raise PlanContractError(
            f"{heading} accepted proposal review head must be the pre-merge PR "
            "head, not the merge commit"
        )
    return int(match.group(1)), match.group(2)


def _accepted_proposal_amendments(
    frontier: Frontier,
    *,
    heading: str,
) -> tuple[tuple[int, str, str], ...]:
    raw_value = frontier.fields.get("accepted proposal amendments")
    if not raw_value:
        return ()
    records: list[tuple[int, str, str]] = []
    for raw_record in raw_value.split("; "):
        match = re.fullmatch(
            r"\[#(\d+)\]\([^)]+\) at `([0-9a-f]{7,40})` "
            r"\(`([^`]+)`\) "
            r"\(reviewed head `([0-9a-f]{40})` by `([^`]+)` as "
            r"`(OWNER|MEMBER|COLLABORATOR)` at `([^`]+)`\)",
            raw_record,
        )
        if match is None:
            raise PlanContractError(
                f"{heading} accepted proposal amendments must identify each "
                "PR, merge commit, amendment path, and authorized exact-head "
                "contract review receipt"
            )
        _github_timestamp(
            match.group(7),
            label=f"{heading} accepted proposal amendment contract review",
        )
        if match.group(4) == match.group(2):
            raise PlanContractError(
                f"{heading} accepted proposal amendment review head must be the "
                "pre-merge PR head, not the merge commit"
            )
        path = _proposal_document_path(
            f"`{match.group(3)}`",
            heading=heading,
            field="accepted proposal amendment path",
        )
        if not path.endswith("-amendment.md"):
            raise PlanContractError(
                f"{heading} accepted proposal amendment path must end with "
                "'-amendment.md'"
            )
        records.append((int(match.group(1)), match.group(2), path))
    if len({record[0] for record in records}) != len(records):
        raise PlanContractError(
            f"{heading} accepted proposal amendments contain a duplicate PR"
        )
    if len({record[2] for record in records}) != len(records):
        raise PlanContractError(
            f"{heading} accepted proposal amendments contain a duplicate path"
        )
    return tuple(records)


AMENDMENT_ESCALATION_ROUTE = "proposal-amendment"
PAUSED_IMPLEMENTATION_RE = re.compile(
    r"\[#(?P<pr>\d+)\]\((?P<url>https://[^)\s]+)\) on "
    r"`(?P<branch>m\d{3}/[^`]+)` at `(?P<head>[0-9a-f]{7,40})` "
    r"\((?P<disposition>paused|closed)\); resume "
    r"`(?P<resume>reconcile|replace)`; escalation "
    r"`(?P<route>proposal-amendment)` (?P<receipt>https://\S+)"
)


def format_paused_implementation(
    *,
    pr: int,
    url: str,
    branch: str,
    head: str,
    disposition: str,
    resume: str,
    receipt: str,
) -> str:
    return (
        f"[#{pr}]({url}) on `{branch}` at `{head}` ({disposition}); "
        f"resume `{resume}`; escalation `{AMENDMENT_ESCALATION_ROUTE}` {receipt}"
    )


def parse_paused_implementation(
    value: str,
    *,
    heading: str = "Current Frontier",
) -> dict[str, str]:
    match = PAUSED_IMPLEMENTATION_RE.fullmatch(value.strip())
    if match is None:
        raise PlanContractError(
            f"{heading} paused implementation must identify PR, URL, branch, "
            "head, paused|closed disposition, reconcile|replace resume policy, "
            "and a durable proposal-amendment escalation receipt"
        )
    disposition = match.group("disposition")
    resume = match.group("resume")
    if disposition == "paused" and resume != "reconcile":
        raise PlanContractError(
            "paused implementation must use resume policy `reconcile`"
        )
    if disposition == "closed" and resume != "replace":
        raise PlanContractError(
            "closed implementation must use resume policy `replace`"
        )
    return match.groupdict()


def _validate_paused_implementation_fields(
    frontier: Frontier,
    *,
    workflow_state: str,
) -> None:
    raw = frontier.fields.get("paused implementation")
    source = frontier.fields.get("amendment source state")
    if workflow_state == "proposal_amendment_in_review":
        if not source:
            source = "ready_for_implementation"
        if source not in {
            "ready_for_implementation",
            "implementation_in_review",
        }:
            raise PlanContractError(
                "proposal_amendment_in_review requires amendment source state "
                "ready_for_implementation or implementation_in_review"
            )
        if source == "implementation_in_review":
            if not raw:
                raise PlanContractError(
                    "amendment from implementation_in_review requires a paused "
                    "implementation receipt"
                )
            parse_paused_implementation(raw)
        elif raw:
            raise PlanContractError(
                "ready_for_implementation-sourced amendment cannot name a "
                "paused implementation"
            )
        return
    if source:
        raise PlanContractError(
            f"{workflow_state} cannot retain amendment source state"
        )
    if raw and workflow_state != "ready_for_implementation":
        raise PlanContractError(
            f"{workflow_state} cannot retain a paused implementation receipt"
        )
    if raw:
        parse_paused_implementation(raw)


def validate_plan_text(text: str) -> PlanState:
    title_match = re.match(r"# Milestone (\d{3})\b", text)
    if title_match is None:
        raise PlanContractError("plan must start with '# Milestone '")
    milestone_number = title_match.group(1)

    header = _header_values(text)
    status = header.get("Status", "").strip("`")
    if status not in ALLOWED_MILESTONE_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_MILESTONE_STATUSES))
        raise PlanContractError(f"invalid milestone Status {status!r}; expected one of {allowed}")

    branch_value = header.get("Milestone branch", "")
    branch_match = re.search(r"`(milestone/[^`]+)`", branch_value)
    if branch_match is None:
        raise PlanContractError("Milestone branch must contain `milestone/<number>-<slug>`")
    milestone_branch = branch_match.group(1)
    expected_milestone_prefix = f"milestone/{milestone_number}-"
    if not milestone_branch.startswith(expected_milestone_prefix):
        raise PlanContractError(
            f"Milestone branch must start with {expected_milestone_prefix!r}"
        )

    baseline_value = header.get("Contract baseline")
    cutover_value = header.get("Cutover")
    grandfathered_value = header.get("Grandfathered PRs")
    if (baseline_value is None) != (cutover_value is None):
        raise PlanContractError(
            "mid-milestone adoption requires both Contract baseline and Cutover"
        )
    if baseline_value is not None and grandfathered_value is None:
        raise PlanContractError(
            "mid-milestone adoption requires a Grandfathered PRs header field"
        )
    grandfathered_prs = (
        {int(value) for value in re.findall(r"#(\d+)", grandfathered_value)}
        if grandfathered_value
        else set()
    )
    if grandfathered_value is not None and not grandfathered_prs:
        raise PlanContractError("Grandfathered PRs must contain at least one PR number")

    criteria = parse_table(text, "## Exit Criteria")
    if criteria.header != ("ID", "Criterion", "Status", "Evidence / remaining gap"):
        raise PlanContractError("Exit Criteria table has an unexpected header")
    seen_ids: set[str] = set()
    for criterion_id, _, criterion_status, _ in criteria.rows:
        if not re.fullmatch(r"M\d{3}-\d{2}", criterion_id):
            raise PlanContractError(f"invalid exit criterion ID: {criterion_id!r}")
        if criterion_id in seen_ids:
            raise PlanContractError(f"duplicate exit criterion ID: {criterion_id}")
        seen_ids.add(criterion_id)
        if criterion_status not in ALLOWED_CRITERION_STATUSES:
            raise PlanContractError(
                f"{criterion_id} has invalid status {criterion_status!r}"
            )

    current = parse_frontier(text, "### Current Frontier")
    next_frontier = parse_frontier(text, "### Next-Frontier Candidate")
    _require_frontier_fields(
        current,
        heading="Current Frontier",
        current=True,
    )
    _require_frontier_fields(
        next_frontier,
        heading="Next-Frontier Candidate",
        current=False,
    )
    _frontier_criterion_ids(
        current,
        heading="Current Frontier",
        known_ids=seen_ids,
    )
    _frontier_criterion_ids(
        next_frontier,
        heading="Next-Frontier Candidate",
        known_ids=seen_ids,
    )

    expected_review_prefix = f"m{milestone_number}/"
    if not current.is_empty:
        proposal_branch = _frontier_branch(
            current,
            heading="Current Frontier",
            field="proposal branch",
        )
        implementation_branch = _frontier_branch(
            current,
            heading="Current Frontier",
            field="implementation branch",
        )
        if proposal_branch == implementation_branch:
            raise PlanContractError(
                "Current Frontier proposal and implementation branches must differ"
            )
        _frontier_proposal_path(current, heading="Current Frontier")
        workflow_state = _workflow_state(current)
        if workflow_state not in WORKFLOW_STATES:
            raise PlanContractError(
                f"Current Frontier has invalid workflow state {workflow_state!r}"
            )
        accepted_proposal = _accepted_proposal(
            current,
            heading="Current Frontier",
        )
        accepted_amendments = _accepted_proposal_amendments(
            current,
            heading="Current Frontier",
        )
        if workflow_state in {"ready_for_proposal", "proposal_in_review"}:
            if accepted_proposal is not None:
                raise PlanContractError(
                    f"{workflow_state} cannot already identify an accepted proposal"
                )
            if accepted_amendments:
                raise PlanContractError(
                    f"{workflow_state} cannot identify accepted proposal amendments"
                )
        elif accepted_proposal is None:
            raise PlanContractError(
                f"{workflow_state} requires an accepted proposal PR and merge commit"
            )
        amendment_branch_value = current.fields.get("proposal amendment branch")
        amendment_path_value = current.fields.get("proposal amendment path")
        if bool(amendment_branch_value) != bool(amendment_path_value):
            raise PlanContractError(
                "Current Frontier must contain both proposal amendment branch and path"
            )
        if amendment_branch_value:
            amendment_branch = _frontier_branch(
                current,
                heading="Current Frontier",
                field="proposal amendment branch",
            )
            if not re.fullmatch(
                rf"m{re.escape(milestone_number)}/amend-[a-z0-9][a-z0-9-]*",
                amendment_branch,
            ):
                raise PlanContractError(
                    "Current Frontier proposal amendment branch must match "
                    f"m{milestone_number}/amend-<slug>"
                )
            amendment_path = _frontier_proposal_amendment_path(
                current,
                heading="Current Frontier",
            )
            accepted_amendment_paths = {
                record[2] for record in accepted_amendments
            }
            if workflow_state == "proposal_amendment_in_review":
                if amendment_path in accepted_amendment_paths:
                    raise PlanContractError(
                        "proposal amendment review must use a new additive artifact"
                    )
            elif amendment_path not in accepted_amendment_paths:
                raise PlanContractError(
                    f"{workflow_state} proposal amendment path lacks an acceptance receipt"
                )
        elif workflow_state == "proposal_amendment_in_review":
            raise PlanContractError(
                "proposal_amendment_in_review requires an amendment branch and path"
            )
        if workflow_state.startswith("ready_for_") and current.fields.get("pr"):
            raise PlanContractError(
                f"{workflow_state} cannot identify an active review PR"
            )
        _validate_paused_implementation_fields(current, workflow_state=workflow_state)
        for branch_kind, branch in (
            ("proposal", proposal_branch),
            ("implementation", implementation_branch),
        ):
            if not branch.startswith(expected_review_prefix):
                raise PlanContractError(
                    f"Current Frontier {branch_kind} branch must start with "
                    f"{expected_review_prefix!r}"
                )
    if not next_frontier.is_empty:
        forbidden_next_fields = {
            field
            for field in ("workflow state", "accepted proposal", "pr")
            if next_frontier.fields.get(field)
        }
        if forbidden_next_fields:
            raise PlanContractError(
                "Next-Frontier Candidate is queued and cannot contain "
                + ", ".join(sorted(forbidden_next_fields))
            )
        next_proposal_branch = _frontier_branch(
            next_frontier,
            heading="Next-Frontier Candidate",
            field="proposal branch",
        )
        next_implementation_branch = _frontier_branch(
            next_frontier,
            heading="Next-Frontier Candidate",
            field="implementation branch",
        )
        if next_proposal_branch == next_implementation_branch:
            raise PlanContractError(
                "Next-Frontier Candidate proposal and implementation branches must differ"
            )
        _frontier_proposal_path(
            next_frontier,
            heading="Next-Frontier Candidate",
        )
        for branch_kind, branch in (
            ("proposal", next_proposal_branch),
            ("implementation", next_implementation_branch),
        ):
            if not branch.startswith(expected_review_prefix):
                raise PlanContractError(
                    f"Next-Frontier Candidate {branch_kind} branch must start with "
                    f"{expected_review_prefix!r}"
                )

    if status == "Active" and current.is_empty:
        raise PlanContractError("Active milestone must have a current frontier")
    if status == "Blocked" and not current.is_empty:
        raise PlanContractError("Blocked milestone must use an empty current frontier")
    if status in {"pre-plan", "closed"} and not current.is_empty:
        raise PlanContractError(f"{status} milestone cannot have an active current frontier")
    if status in {"Blocked", "closed"} and not next_frontier.is_empty:
        raise PlanContractError(f"{status} milestone cannot have a next candidate")

    header_current = header.get("Current frontier", "")
    expected_current = current.name or "None"
    if not header_current.startswith(expected_current):
        raise PlanContractError(
            "header Current frontier does not match the Current Delivery section"
        )

    ledger = parse_table(text, "## Accepted Review Units")
    if ledger.header != (
        "PR",
        "Accepted review question",
        "Result",
        "Exit criteria",
        "Durable evidence",
    ):
        raise PlanContractError("Accepted Review Units table has an unexpected header")
    accepted_prs: set[str] = set()
    for row in ledger.rows:
        pr_match = re.fullmatch(r"#(\d+)", row[0])
        if pr_match is None:
            continue
        pr_number = pr_match.group(1)
        if pr_number in accepted_prs:
            raise PlanContractError(f"duplicate accepted ledger PR: #{pr_number}")
        accepted_prs.add(pr_number)

    workflow_history = parse_table(text, "## Workflow History")
    if workflow_history.header != ("Frontier", "State", "Evidence"):
        raise PlanContractError("Workflow History table has an unexpected header")
    allowed_history_states = WORKFLOW_STATES | {"accepted"}
    prior_history_row: tuple[str, str, str] | None = None
    expected_transitions = {
        "ready_for_proposal": {"proposal_in_review"},
        "proposal_in_review": {"ready_for_implementation"},
        "ready_for_implementation": {
            "proposal_amendment_in_review",
            "implementation_in_review",
        },
        "proposal_amendment_in_review": {
            "ready_for_implementation",
            "implementation_in_review",
        },
        "implementation_in_review": {
            "accepted",
            "proposal_amendment_in_review",
        },
    }
    for history_frontier, history_state, history_evidence in workflow_history.rows:
        if not history_frontier or not history_evidence:
            raise PlanContractError(
                "Workflow History frontier and evidence must be non-empty"
            )
        if history_state not in allowed_history_states:
            raise PlanContractError(
                f"Workflow History has invalid state {history_state!r}"
            )
        if prior_history_row is None:
            if history_state != "ready_for_proposal":
                raise PlanContractError(
                    "Workflow History must begin at ready_for_proposal"
                )
        else:
            prior_frontier, prior_state, _ = prior_history_row
            if history_frontier == prior_frontier:
                is_plan_revision = (
                    prior_state == "ready_for_proposal"
                    and history_state == "ready_for_proposal"
                    and history_evidence.startswith("Plan revision:")
                )
                if (
                    not is_plan_revision
                    and history_state
                    not in expected_transitions.get(prior_state, set())
                ):
                    raise PlanContractError(
                        "Workflow History has an invalid same-frontier transition "
                        f"{prior_state} -> {history_state}"
                    )
            elif (
                prior_state == "ready_for_proposal"
                and history_state == "ready_for_proposal"
                and history_evidence.startswith("Plan revision:")
            ):
                pass
            elif prior_state != "accepted" or history_state != "ready_for_proposal":
                raise PlanContractError(
                    "Workflow History can change frontier only after accepted, "
                    "or through an explicit pre-proposal Plan revision, and must "
                    "restart at ready_for_proposal"
                )
        prior_history_row = (
            history_frontier,
            history_state,
            history_evidence,
        )
    if status == "Active" and not current.is_empty:
        if not workflow_history.rows:
            raise PlanContractError("Active milestone requires workflow history")
        last_frontier, last_state, _ = workflow_history.rows[-1]
        if last_frontier != current.name:
            raise PlanContractError(
                "Workflow History latest frontier does not match Current Frontier"
            )
        expected_state = _workflow_state(current)
        if last_state != expected_state:
            raise PlanContractError(
                "Workflow History latest state does not match Current Frontier"
            )

    if baseline_value is not None:
        baseline_match = re.search(r"`([0-9a-f]{7,40})`", baseline_value)
        if baseline_match is None:
            raise PlanContractError("Contract baseline must contain a commit SHA")
        if not cutover_value or not cutover_value.strip():
            raise PlanContractError("Cutover must describe the topology transition")
        baseline_sha = baseline_match.group(1)
        if not any(
            row[0].startswith("Baseline") and baseline_sha in row[0]
            for row in ledger.rows
        ):
            raise PlanContractError(
                "Accepted Review Units must contain the Contract baseline row"
            )

    risks = parse_table(text, "## Open Risks And Unverified Assumptions")
    if risks.header != ("Risk or assumption", "Consequence", "Resolution path"):
        raise PlanContractError("Open Risks table has an unexpected header")

    return PlanState(
        milestone_number=milestone_number,
        status=status,
        milestone_branch=milestone_branch,
        current=current,
        next_frontier=next_frontier,
        criteria=criteria,
        ledger=ledger,
        workflow_history=workflow_history,
        risks=risks,
    )


def validate_plan_path(path: Path) -> PlanState:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanContractError(f"cannot read {path}: {exc}") from exc
    state = validate_plan_text(text)
    resolved_path = path.resolve()
    repo_root = resolved_path.parents[3]
    expected_proposal_parent = resolved_path.parent / "proposals"
    for heading, frontier in (
        ("Current Frontier", state.current),
        ("Next-Frontier Candidate", state.next_frontier),
    ):
        if frontier.is_empty:
            continue
        proposal_path = repo_root / _frontier_proposal_path(
            frontier,
            heading=heading,
        )
        if proposal_path.parent.resolve() != expected_proposal_parent.resolve():
            raise PlanContractError(
                f"{heading} proposal path must be inside "
                f"{expected_proposal_parent.relative_to(repo_root)}"
            )
        if heading != "Current Frontier":
            continue
        amendment_paths = [
            record[2]
            for record in _accepted_proposal_amendments(
                frontier,
                heading=heading,
            )
        ]
        if frontier.fields.get("proposal amendment path"):
            amendment_paths.append(
                _frontier_proposal_amendment_path(frontier, heading=heading)
            )
        for amendment_relative in amendment_paths:
            amendment_path = repo_root / amendment_relative
            if amendment_path.parent.resolve() != expected_proposal_parent.resolve():
                raise PlanContractError(
                    f"{heading} proposal amendment path must be inside "
                    f"{expected_proposal_parent.relative_to(repo_root)}"
                )
    return state


def _safe_cell(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanContractError(f"{field} must be a non-empty string")
    normalized = " ".join(value.split())
    if "|" in normalized:
        raise PlanContractError(f"{field} cannot contain '|'")
    return normalized


def _replace_table(text: str, heading: str, rows: list[list[str]]) -> str:
    lines = text.splitlines()
    section_start, section_end = _section_bounds(lines, heading)
    table_start: int | None = None
    for index in range(section_start, section_end):
        if lines[index].strip().startswith("|"):
            table_start = index
            break
    if table_start is None:
        raise PlanContractError(f"{heading} must contain a table")
    table_end = table_start
    while table_end < section_end and lines[table_end].strip().startswith("|"):
        table_end += 1

    existing = parse_table(text, heading)
    rendered = [
        "| " + " | ".join(existing.header) + " |",
        "| " + " | ".join("---" for _ in existing.header) + " |",
    ]
    for row in rows:
        if len(row) != len(existing.header):
            raise PlanContractError(f"{heading} replacement row has wrong width")
        rendered.append("| " + " | ".join(row) + " |")
    return "\n".join(lines[:table_start] + rendered + lines[table_end:]) + "\n"


def _replace_frontier(text: str, heading: str, body_lines: list[str]) -> str:
    lines = text.splitlines()
    section_start, section_end = _section_bounds(lines, heading)
    heading_index = section_start - 1
    replacement = [heading, "", *body_lines, ""]
    return "\n".join(lines[:heading_index] + replacement + lines[section_end:]) + "\n"


def _replace_header_value(text: str, field: str, value: str) -> str:
    table = parse_table(text, "# " + text.splitlines()[0].removeprefix("# ").strip())
    rows = [list(row) for row in table.rows]
    for row in rows:
        if row[0] == field:
            row[1] = value
            return _replace_table(
                text,
                "# " + text.splitlines()[0].removeprefix("# ").strip(),
                rows,
            )
    raise PlanContractError(f"milestone header is missing {field!r}")


def _append_workflow_history(
    text: str,
    *,
    frontier: str,
    state: str,
    evidence: str,
) -> str:
    table = parse_table(text, "## Workflow History")
    rows = [list(row) for row in table.rows]
    rows.append(
        [
            _safe_cell(frontier, field="workflow_history.frontier"),
            _safe_cell(state, field="workflow_history.state"),
            _safe_cell(evidence, field="workflow_history.evidence"),
        ]
    )
    return _replace_table(text, "## Workflow History", rows)


def _frontier_body(frontier: Frontier, *, current: bool) -> list[str]:
    if frontier.is_empty:
        return [
            "**None**",
            "",
            f"- Reason: {frontier.fields['reason']}",
            f"- Revisit when: {frontier.fields['revisit when']}",
        ]
    lines = [f"**{frontier.name}**", ""]
    preferred = (
        ("pr", "PR"),
        ("workflow state", "Workflow state"),
        ("proposal branch", "Proposal branch"),
        ("implementation branch", "Implementation branch"),
        ("proposal path", "Proposal path"),
        ("accepted proposal", "Accepted proposal"),
        ("proposal amendment branch", "Proposal amendment branch"),
        ("proposal amendment path", "Proposal amendment path"),
        ("accepted proposal amendments", "Accepted proposal amendments"),
        ("amendment source state", "Amendment source state"),
        ("paused implementation", "Paused implementation"),
        ("review kind", "Review kind"),
        ("review question", "Review question"),
        ("acceptance owner", "Acceptance owner"),
        ("exit criteria affected", "Exit criteria affected"),
        ("prerequisite", "Prerequisite"),
        ("non-goals", "Milestone-level non-goal" if current else "Non-goals"),
    )
    for key, label in preferred:
        value = frontier.fields.get(key)
        if value:
            lines.append(f"- {label}: {value}")
    return lines


def _empty_next_frontier_from_receipt(payload: Any) -> Frontier:
    if not isinstance(payload, dict):
        raise PlanContractError("next_frontier must be an object")
    state = payload.get("state")
    if state != "none":
        raise PlanContractError(
            "handoff cannot invent an unreviewed next candidate; "
            "next_frontier.state must be 'none'"
        )
    return Frontier(
        name=None,
        fields={
            "reason": _safe_cell(payload.get("reason"), field="next_frontier.reason"),
            "revisit when": _safe_cell(
                payload.get("revisit_when"),
                field="next_frontier.revisit_when",
            ),
        },
    )


def _normalize_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    if receipt.get("schema") != "milestone_handoff_v1":
        raise PlanContractError("receipt schema must be milestone_handoff_v1")
    accepted_pr = receipt.get("accepted_pr")
    if not isinstance(accepted_pr, int) or accepted_pr <= 0:
        raise PlanContractError("accepted_pr must be a positive integer")
    merge_commit = receipt.get("accepted_merge_commit")
    if not isinstance(merge_commit, str) or re.fullmatch(r"[0-9a-f]{7,40}", merge_commit) is None:
        raise PlanContractError("accepted_merge_commit must be a 7-40 character SHA")
    outcome = receipt.get("outcome")
    if outcome not in {"advance", "block", "close"}:
        raise PlanContractError("outcome must be advance, block, or close")
    _safe_cell(receipt.get("result"), field="result")
    _safe_cell(receipt.get("durable_evidence"), field="durable_evidence")
    if not isinstance(receipt.get("criterion_updates", {}), dict):
        raise PlanContractError("criterion_updates must be an object")
    if not isinstance(receipt.get("risk_remove", []), list):
        raise PlanContractError("risk_remove must be a list")
    if not isinstance(receipt.get("risk_upsert", []), list):
        raise PlanContractError("risk_upsert must be a list")
    if outcome == "advance":
        _empty_next_frontier_from_receipt(receipt.get("next_frontier"))
    return receipt


def apply_handoff(text: str, receipt_payload: dict[str, Any]) -> str:
    receipt = _normalize_receipt(receipt_payload)
    state = validate_plan_text(text)
    accepted_pr = receipt["accepted_pr"]
    if state.status != "Active":
        raise PlanContractError("frontier handoff requires an Active milestone")
    if _workflow_state(state.current) != "implementation_in_review":
        raise PlanContractError(
            "frontier handoff requires workflow state implementation_in_review"
        )
    if any(row[0] == f"#{accepted_pr}" for row in state.ledger.rows):
        raise PlanContractError(f"PR #{accepted_pr} is already in the accepted ledger")

    criterion_rows = [list(row) for row in state.criteria.rows]
    criterion_by_id = {row[0]: row for row in criterion_rows}
    owned_criteria = _frontier_criterion_ids(
        state.current,
        heading="Current Frontier",
        known_ids=set(criterion_by_id),
    )
    unexpected_updates = set(receipt.get("criterion_updates", {})) - owned_criteria
    if unexpected_updates:
        raise PlanContractError(
            "receipt updates criteria outside the current frontier: "
            + ", ".join(sorted(unexpected_updates))
        )
    for criterion_id, update in receipt.get("criterion_updates", {}).items():
        if criterion_id not in criterion_by_id:
            raise PlanContractError(f"unknown criterion update: {criterion_id}")
        if not isinstance(update, dict):
            raise PlanContractError(f"criterion update {criterion_id} must be an object")
        status = update.get("status")
        if status not in ALLOWED_CRITERION_STATUSES:
            raise PlanContractError(
                f"criterion update {criterion_id} has invalid status {status!r}"
            )
        criterion_by_id[criterion_id][2] = status
        criterion_by_id[criterion_id][3] = _safe_cell(
            update.get("evidence"), field=f"{criterion_id}.evidence"
        )
    text = _replace_table(text, "## Exit Criteria", criterion_rows)

    accepted_question = state.current.fields["review question"]
    affected_criteria = state.current.fields["exit criteria affected"]
    ledger_rows = [list(row) for row in state.ledger.rows]
    ledger_rows.append(
        [
            f"#{accepted_pr}",
            accepted_question,
            _safe_cell(receipt["result"], field="result"),
            affected_criteria,
            _safe_cell(receipt["durable_evidence"], field="durable_evidence"),
        ]
    )
    text = _replace_table(text, "## Accepted Review Units", ledger_rows)

    risk_rows = [list(row) for row in state.risks.rows]
    remove_names = {
        _safe_cell(value, field="risk_remove entry")
        for value in receipt.get("risk_remove", [])
    }
    missing_risks = remove_names - {row[0] for row in risk_rows}
    if missing_risks:
        raise PlanContractError(
            "receipt removes unknown risks: " + ", ".join(sorted(missing_risks))
        )
    risk_rows = [row for row in risk_rows if row[0] not in remove_names]
    for item in receipt.get("risk_upsert", []):
        if not isinstance(item, dict):
            raise PlanContractError("risk_upsert entries must be objects")
        row = [
            _safe_cell(item.get("risk"), field="risk_upsert.risk"),
            _safe_cell(item.get("consequence"), field="risk_upsert.consequence"),
            _safe_cell(item.get("resolution"), field="risk_upsert.resolution"),
        ]
        risk_rows = [existing for existing in risk_rows if existing[0] != row[0]]
        risk_rows.append(row)
    text = _replace_table(text, "## Open Risks And Unverified Assumptions", risk_rows)
    text = _append_workflow_history(
        text,
        frontier=state.current.name or "Unknown frontier",
        state="accepted",
        evidence=(
            f"Implementation PR #{accepted_pr} merged at "
            f"{receipt['accepted_merge_commit']}."
        ),
    )

    outcome = receipt["outcome"]
    if outcome == "advance":
        if state.next_frontier.is_empty:
            raise PlanContractError(
                "advance requires a reviewed next-frontier candidate to promote"
            )
        new_current = Frontier(
            name=state.next_frontier.name,
            fields={
                **state.next_frontier.fields,
                "workflow state": "ready_for_proposal",
            },
        )
        if new_current.fields["review kind"].lower() == "milestone closeout":
            closeout_criteria = _frontier_criterion_ids(
                new_current,
                heading="Next-Frontier Candidate",
                known_ids=set(criterion_by_id),
            )
            blocking = [
                row[0]
                for row in criterion_rows
                if row[0] not in closeout_criteria and row[2] != "Met"
            ]
            if blocking:
                raise PlanContractError(
                    "cannot promote milestone closeout while criteria remain unmet: "
                    + ", ".join(blocking)
                )
        new_next = _empty_next_frontier_from_receipt(receipt.get("next_frontier"))
        text = _replace_header_value(text, "Status", "Active")
        text = _replace_header_value(text, "Current frontier", new_current.name or "None")
        text = _append_workflow_history(
            text,
            frontier=new_current.name or "Unknown frontier",
            state="ready_for_proposal",
            evidence=f"Promoted after implementation PR #{accepted_pr}.",
        )
    elif outcome == "block":
        reason = _safe_cell(receipt.get("blocked_reason"), field="blocked_reason")
        revisit = _safe_cell(receipt.get("revisit_when"), field="revisit_when")
        new_current = Frontier(
            name=None,
            fields={"reason": reason, "revisit when": revisit},
        )
        new_next = Frontier(
            name=None,
            fields={"reason": reason, "revisit when": revisit},
        )
        text = _replace_header_value(text, "Status", "Blocked")
        text = _replace_header_value(text, "Current frontier", "None (blocked)")
    else:
        if state.current.fields.get("review kind", "").lower() != "milestone closeout":
            raise PlanContractError("close outcome requires a milestone closeout frontier")
        non_met = [row[0] for row in criterion_rows if row[2] != "Met"]
        if non_met:
            raise PlanContractError(
                "cannot close milestone while criteria remain unmet: "
                + ", ".join(non_met)
            )
        reason = f"Milestone closed after PR #{accepted_pr}."
        new_current = Frontier(
            name=None,
            fields={"reason": reason, "revisit when": "No in-milestone work remains."},
        )
        new_next = Frontier(
            name=None,
            fields={
                "reason": "Cross-milestone activation is decided by closeout.",
                "revisit when": "The next milestone is activated separately.",
            },
        )
        text = _replace_header_value(text, "Status", "closed")
        text = _replace_header_value(text, "Current frontier", "None (closed)")

    text = _replace_frontier(
        text,
        "### Current Frontier",
        _frontier_body(new_current, current=True),
    )
    text = _replace_frontier(
        text,
        "### Next-Frontier Candidate",
        _frontier_body(new_next, current=False),
    )
    validate_plan_text(text)
    return text


def _run_git(
    args: list[str],
    *,
    cwd: Path = ROOT,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def verify_handoff_git_state(
    plan: Path,
    state: PlanState,
    receipt: dict[str, Any],
    *,
    repo_root: Path = ROOT,
) -> None:
    repo_root = repo_root.resolve()
    _validate_plan_location(plan, repo_root=repo_root)
    branch = _run_git(["branch", "--show-current"], cwd=repo_root).stdout.strip()
    if branch != state.milestone_branch:
        raise PlanContractError(
            f"handoff must run on {state.milestone_branch!r}, currently {branch!r}"
        )
    dirty = _run_git(["status", "--porcelain"], cwd=repo_root).stdout.strip()
    if dirty:
        raise PlanContractError("handoff requires a clean worktree")
    merge_commit = receipt["accepted_merge_commit"]
    ancestor = _run_git(
        ["merge-base", "--is-ancestor", merge_commit, "HEAD"],
        cwd=repo_root,
        check=False,
    )
    if ancestor.returncode != 0:
        raise PlanContractError(
            f"accepted merge commit {merge_commit} is not an ancestor of HEAD"
        )


def _validate_plan_location(plan: Path, *, repo_root: Path) -> Path:
    try:
        relative_plan = plan.resolve().relative_to(repo_root)
    except ValueError as exc:
        raise PlanContractError("plan must be inside the repository") from exc
    if not relative_plan.match("docs/milestones/*/plan.md"):
        raise PlanContractError(
            "milestone plan must be docs/milestones/<slug>/plan.md"
        )
    return relative_plan


def validate_merged_pr_metadata(
    payload: dict[str, Any],
    state: PlanState,
    receipt: dict[str, Any],
) -> None:
    if payload.get("state") != "MERGED":
        raise PlanContractError(f"PR #{receipt['accepted_pr']} is not merged")
    if payload.get("baseRefName") != state.milestone_branch:
        raise PlanContractError(
            f"PR #{receipt['accepted_pr']} did not target {state.milestone_branch}"
        )
    expected_head = _frontier_branch(
        state.current,
        heading="Current Frontier",
        field="implementation branch",
    )
    if payload.get("headRefName") != expected_head:
        raise PlanContractError(
            f"PR #{receipt['accepted_pr']} did not use {expected_head}"
        )
    _validate_pr_review_kind(
        payload.get("body"),
        expected=state.current.fields["review kind"],
    )
    merge_commit = payload.get("mergeCommit")
    merge_oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    expected = receipt["accepted_merge_commit"]
    if not isinstance(merge_oid, str) or not merge_oid.startswith(expected):
        raise PlanContractError(
            f"PR #{receipt['accepted_pr']} merge commit does not match {expected}"
        )


def verify_handoff_github_state(
    state: PlanState,
    receipt: dict[str, Any],
    *,
    repo_root: Path = ROOT,
) -> None:
    payload = _fetch_pr_metadata(receipt["accepted_pr"], repo_root=repo_root)
    validate_merged_pr_metadata(payload, state, receipt)


def _fetch_pr_metadata(
    pr_number: int,
    *,
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "state,mergeCommit,baseRefName,headRefName,body",
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PlanContractError("GitHub CLI `gh` is required for handoff") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise PlanContractError(
            f"cannot verify accepted PR on GitHub: {detail}"
        ) from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PlanContractError("GitHub CLI returned invalid PR metadata") from exc
    if not isinstance(payload, dict):
        raise PlanContractError("GitHub CLI returned invalid PR metadata")
    return payload


def _replace_current_frontier_state(
    text: str,
    *,
    expected_state: str,
    new_state: str,
    evidence: str,
    accepted_proposal: str | None = None,
    opened_branch_field: str | None = None,
    field_updates: dict[str, str] | None = None,
    field_removes: tuple[str, ...] = (),
) -> str:
    state = validate_plan_text(text)
    if state.status != "Active" or state.current.is_empty:
        raise PlanContractError("workflow transition requires an active current frontier")
    actual_state = _workflow_state(state.current)
    if actual_state != expected_state:
        raise PlanContractError(
            f"workflow transition requires {expected_state}, currently {actual_state}"
        )
    fields = dict(state.current.fields)
    fields["workflow state"] = new_state
    fields.pop("pr", None)
    for key in field_removes:
        fields.pop(key, None)
    if opened_branch_field is not None:
        opened_branch = _frontier_branch(
            state.current,
            heading="Current Frontier",
            field=opened_branch_field,
        )
        fields[opened_branch_field] = f"`{opened_branch}`"
    if accepted_proposal is not None:
        fields["accepted proposal"] = accepted_proposal
    if field_updates:
        fields.update(field_updates)
    updated = _replace_frontier(
        text,
        "### Current Frontier",
        _frontier_body(
            Frontier(name=state.current.name, fields=fields),
            current=True,
        ),
    )
    updated = _append_workflow_history(
        updated,
        frontier=state.current.name or "Unknown frontier",
        state=new_state,
        evidence=evidence,
    )
    validate_plan_text(updated)
    return updated


def _start_frontier_branch(
    plan: Path,
    state: PlanState,
    requested_branch: str,
    *,
    branch_field: str,
    expected_state: str,
    new_state: str,
    repo_root: Path = ROOT,
) -> str:
    repo_root = repo_root.resolve()
    _validate_plan_location(plan, repo_root=repo_root)
    if state.status != "Active" or state.current.is_empty:
        raise PlanContractError("branch start requires an active current frontier")
    if _workflow_state(state.current) != expected_state:
        raise PlanContractError(
            f"branch start requires {expected_state}, currently "
            f"{state.current.fields.get('workflow state')}"
        )
    if state.current.fields.get("pr"):
        raise PlanContractError(
            "current frontier already has a PR; complete its handoff before starting"
        )
    planned_value = state.current.fields.get(branch_field, "")
    planned_match = re.search(r"`?(m\d{3}/[A-Za-z0-9._/-]+)`?", planned_value)
    if planned_match is None:
        raise PlanContractError(
            f"current frontier must contain a planned {branch_field}"
        )
    planned_branch = planned_match.group(1)
    expected_prefix = f"m{state.milestone_number}/"
    if not planned_branch.startswith(expected_prefix):
        raise PlanContractError(
            f"planned branch must start with {expected_prefix!r}"
        )
    if requested_branch != planned_branch:
        raise PlanContractError(
            f"requested branch {requested_branch!r} does not match {planned_branch!r}"
        )

    current_branch = _run_git(
        ["branch", "--show-current"],
        cwd=repo_root,
    ).stdout.strip()
    if current_branch != state.milestone_branch:
        raise PlanContractError(
            f"branch start must run on {state.milestone_branch!r}, "
            f"currently {current_branch!r}"
        )
    if _run_git(["status", "--porcelain"], cwd=repo_root).stdout.strip():
        raise PlanContractError("branch start requires a clean worktree")
    existing_refs = _run_git(
        ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"],
        cwd=repo_root,
    ).stdout.splitlines()
    if any(
        ref == f"refs/heads/{requested_branch}"
        or (
            ref.startswith("refs/remotes/")
            and ref.endswith(f"/{requested_branch}")
        )
        for ref in existing_refs
    ):
        raise PlanContractError(f"branch already exists: {requested_branch}")
    _run_git(["switch", "-c", requested_branch], cwd=repo_root)
    original = plan.read_text(encoding="utf-8")
    updated = _replace_current_frontier_state(
        original,
        expected_state=expected_state,
        new_state=new_state,
        evidence=f"Started {requested_branch}.",
        opened_branch_field=branch_field,
    )
    plan.write_text(updated, encoding="utf-8")
    return updated


def start_proposal_branch(
    plan: Path,
    state: PlanState,
    requested_branch: str,
    *,
    repo_root: Path = ROOT,
) -> str:
    return _start_frontier_branch(
        plan,
        state,
        requested_branch,
        branch_field="proposal branch",
        expected_state="ready_for_proposal",
        new_state="proposal_in_review",
        repo_root=repo_root,
    )


def start_implementation_branch(
    plan: Path,
    state: PlanState,
    requested_branch: str,
    *,
    repo_root: Path = ROOT,
) -> str:
    paused_raw = state.current.fields.get("paused implementation")
    if paused_raw and _workflow_state(state.current) == "ready_for_implementation":
        return _resume_implementation_after_amendment(
            plan,
            state,
            requested_branch,
            paused_raw=paused_raw,
            repo_root=repo_root,
        )
    return _start_frontier_branch(
        plan,
        state,
        requested_branch,
        branch_field="implementation branch",
        expected_state="ready_for_implementation",
        new_state="implementation_in_review",
        repo_root=repo_root,
    )


def _resume_implementation_after_amendment(
    plan: Path,
    state: PlanState,
    requested_branch: str,
    *,
    paused_raw: str,
    repo_root: Path = ROOT,
) -> str:
    repo_root = repo_root.resolve()
    _validate_plan_location(plan, repo_root=repo_root)
    paused = parse_paused_implementation(paused_raw)
    planned = _frontier_branch(
        state.current,
        heading="Current Frontier",
        field="implementation branch",
    )
    if requested_branch != planned:
        raise PlanContractError(
            f"requested branch {requested_branch!r} does not match {planned!r}"
        )
    current_branch = _run_git(
        ["branch", "--show-current"],
        cwd=repo_root,
    ).stdout.strip()
    if current_branch != state.milestone_branch:
        raise PlanContractError(
            f"implementation resume must run on {state.milestone_branch!r}, "
            f"currently {current_branch!r}"
        )
    if _run_git(["status", "--porcelain"], cwd=repo_root).stdout.strip():
        raise PlanContractError("implementation resume requires a clean worktree")
    existing = _run_git(
        ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"],
        cwd=repo_root,
    ).stdout.splitlines()
    if not any(
        ref == f"refs/heads/{requested_branch}"
        or (
            ref.startswith("refs/remotes/")
            and ref.endswith(f"/{requested_branch}")
        )
        for ref in existing
    ):
        raise PlanContractError(
            f"paused implementation branch is missing: {requested_branch}"
        )
    field_updates: dict[str, str] = {}
    if paused["resume"] == "reconcile":
        field_updates["pr"] = f"[#{paused['pr']}]({paused['url']})"
        evidence = (
            f"Resumed implementation {requested_branch} after proposal amendment "
            f"(reconcile PR #{paused['pr']})."
        )
    else:
        evidence = (
            f"Reopened implementation {requested_branch} after proposal amendment "
            f"(replace closed PR #{paused['pr']})."
        )
    original = plan.read_text(encoding="utf-8")
    updated = _replace_current_frontier_state(
        original,
        expected_state="ready_for_implementation",
        new_state="implementation_in_review",
        evidence=evidence,
        field_updates=field_updates,
        field_removes=("paused implementation",),
    )
    _run_git(["switch", requested_branch], cwd=repo_root)
    plan.write_text(updated, encoding="utf-8")
    return updated


def start_proposal_amendment_branch(
    plan: Path,
    state: PlanState,
    requested_branch: str,
    requested_path: str,
    *,
    repo_root: Path = ROOT,
    implementation_pr: int | None = None,
    implementation_url: str | None = None,
    implementation_head: str | None = None,
    implementation_disposition: str | None = None,
    resume_policy: str | None = None,
    escalation_receipt: str | None = None,
) -> str:
    repo_root = repo_root.resolve()
    relative_plan = _validate_plan_location(plan, repo_root=repo_root)
    if state.status != "Active" or state.current.is_empty:
        raise PlanContractError(
            "proposal amendment start requires an active current frontier"
        )
    source_state = _workflow_state(state.current)
    if source_state not in {
        "ready_for_implementation",
        "implementation_in_review",
    }:
        raise PlanContractError(
            "proposal amendment start requires ready_for_implementation or "
            "implementation_in_review"
        )
    impl_branch_name = _frontier_branch(
        state.current,
        heading="Current Frontier",
        field="implementation branch",
    )
    existing_refs = _run_git(
        ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"],
        cwd=repo_root,
    ).stdout.splitlines()
    implementation_branch_exists = any(
        ref == f"refs/heads/{impl_branch_name}"
        or (
            ref.startswith("refs/remotes/")
            and ref.endswith(f"/{impl_branch_name}")
        )
        for ref in existing_refs
    )
    in_flight = (
        source_state == "implementation_in_review" or implementation_branch_exists
    )
    extra = {
        implementation_pr,
        implementation_url,
        implementation_head,
        implementation_disposition,
        resume_policy,
        escalation_receipt,
    }
    if in_flight:
        if None in extra or implementation_pr is None:
            raise PlanContractError(
                "amendment from implementation_in_review requires "
                "--implementation-pr, --implementation-url, "
                "--implementation-head, --implementation-disposition, "
                "--resume-policy, and --escalation-receipt"
            )
        if implementation_disposition == "paused" and resume_policy != "reconcile":
            raise PlanContractError(
                "paused implementation amendment must use resume policy reconcile"
            )
        if implementation_disposition == "closed" and resume_policy != "replace":
            raise PlanContractError(
                "closed implementation amendment must use resume policy replace"
            )
        if implementation_disposition not in {"paused", "closed"}:
            raise PlanContractError(
                "implementation disposition must be paused or closed"
            )
        if resume_policy not in {"reconcile", "replace"}:
            raise PlanContractError("resume policy must be reconcile or replace")
        if not str(escalation_receipt).startswith("https://"):
            raise PlanContractError(
                "amendment escalation receipt must be a durable https URL"
            )
        if not str(implementation_url).startswith("https://"):
            raise PlanContractError(
                "implementation URL must be a durable https URL"
            )
        if re.fullmatch(r"[0-9a-f]{7,40}", implementation_head or "") is None:
            raise PlanContractError(
                "implementation head must be a git commit id"
            )
        impl_branch = _frontier_branch(
            state.current,
            heading="Current Frontier",
            field="implementation branch",
        )
        paused_value = format_paused_implementation(
            pr=implementation_pr,
            url=implementation_url,
            branch=impl_branch,
            head=implementation_head or "",
            disposition=implementation_disposition,
            resume=resume_policy or "",
            receipt=escalation_receipt or "",
        )
        parse_paused_implementation(paused_value)
    else:
        if any(value is not None for value in extra):
            raise PlanContractError(
                "ready_for_implementation amendment cannot name a paused "
                "implementation or escalation receipt"
            )
        if state.current.fields.get("pr"):
            raise PlanContractError(
                "current frontier already has a PR; complete its handoff "
                "before amending"
            )
    if re.fullmatch(
        rf"m{re.escape(state.milestone_number)}/amend-[a-z0-9][a-z0-9-]*",
        requested_branch,
    ) is None:
        raise PlanContractError(
            "proposal amendment branch must match "
            f"m{state.milestone_number}/amend-<slug>"
        )
    amendment_path = _proposal_document_path(
        requested_path,
        heading="Current Frontier",
        field="proposal amendment path",
    )
    if not amendment_path.endswith("-amendment.md"):
        raise PlanContractError(
            "proposal amendment path must end with '-amendment.md'"
        )
    expected_parent = relative_plan.parent / "proposals"
    if Path(amendment_path).parent != expected_parent:
        raise PlanContractError(
            f"proposal amendment path must be inside {expected_parent}"
        )
    proposal_path = _frontier_proposal_path(
        state.current,
        heading="Current Frontier",
    )
    accepted_paths = {
        record[2]
        for record in _accepted_proposal_amendments(
            state.current,
            heading="Current Frontier",
        )
    }
    if amendment_path == proposal_path or amendment_path in accepted_paths:
        raise PlanContractError(
            "proposal amendment must use a new additive artifact path"
        )

    current_branch = _run_git(
        ["branch", "--show-current"],
        cwd=repo_root,
    ).stdout.strip()
    if current_branch != state.milestone_branch:
        raise PlanContractError(
            f"proposal amendment start must run on {state.milestone_branch!r}, "
            f"currently {current_branch!r}"
        )
    if _run_git(["status", "--porcelain"], cwd=repo_root).stdout.strip():
        raise PlanContractError("proposal amendment start requires a clean worktree")
    existing_refs = _run_git(
        ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"],
        cwd=repo_root,
    ).stdout.splitlines()
    if any(
        ref == f"refs/heads/{requested_branch}"
        or (
            ref.startswith("refs/remotes/")
            and ref.endswith(f"/{requested_branch}")
        )
        for ref in existing_refs
    ):
        raise PlanContractError(f"branch already exists: {requested_branch}")

    _run_git(["switch", "-c", requested_branch], cwd=repo_root)
    original = plan.read_text(encoding="utf-8")
    field_updates = {
        "proposal amendment branch": f"`{requested_branch}`",
        "proposal amendment path": f"`{amendment_path}`",
        "amendment source state": (
            "implementation_in_review" if in_flight else "ready_for_implementation"
        ),
    }
    if in_flight:
        field_updates["paused implementation"] = paused_value
        evidence = (
            f"Started proposal amendment {requested_branch} from "
            f"implementation_in_review via proposal-amendment escalation "
            f"{escalation_receipt}."
        )
    else:
        evidence = f"Started proposal amendment {requested_branch}."
    updated = _replace_current_frontier_state(
        original,
        expected_state=source_state,
        new_state="proposal_amendment_in_review",
        evidence=evidence,
        field_updates=field_updates,
    )
    plan.write_text(updated, encoding="utf-8")
    return updated


def _contract_sections_claim_universal(
    text: str,
    headings: tuple[str, ...],
) -> bool:
    lines = text.splitlines()
    claim_sections: list[str] = []
    for heading in headings:
        start, end = _section_bounds(lines, heading)
        claim_sections.extend(lines[start:end])
    claims = re.sub(
        r"<!--.*?-->",
        "",
        "\n".join(claim_sections),
        flags=re.DOTALL,
    )
    return UNIVERSAL_CLAIM_PATTERN.search(claims) is not None


def validate_proposal_text(text: str) -> None:
    if not text.startswith("# Proposal:"):
        raise PlanContractError("proposal must start with '# Proposal:'")
    for heading in PROPOSAL_REQUIRED_HEADINGS:
        if heading not in text:
            raise PlanContractError(f"proposal is missing {heading}")
    if _contract_sections_claim_universal(
        text,
        ("## Review Question", "## Proposed Contract"),
    ):
        for heading in UNIVERSAL_CONTRACT_REQUIRED_HEADINGS:
            if heading not in text:
                raise PlanContractError(
                    f"universal proposal claim requires {heading}"
                )
            _required_section_body(
                text,
                heading,
                document="universal proposal",
            )
    load_handoff_template(text)


def validate_proposal_amendment_text(text: str) -> None:
    if not text.startswith("# Proposal Amendment:"):
        raise PlanContractError(
            "proposal amendment must start with '# Proposal Amendment:'"
        )
    for heading in PROPOSAL_AMENDMENT_REQUIRED_HEADINGS:
        if heading not in text:
            raise PlanContractError(f"proposal amendment is missing {heading}")
    if _contract_sections_claim_universal(
        text,
        ("## Review Question", "## Contract Delta"),
    ):
        for heading in UNIVERSAL_CONTRACT_REQUIRED_HEADINGS:
            if heading not in text:
                raise PlanContractError(
                    f"universal proposal amendment claim requires {heading}"
                )
            _required_section_body(
                text,
                heading,
                document="universal proposal amendment",
            )


def _repair_field(text: str, heading: str, label: str) -> str:
    lines = text.splitlines()
    start, end = _section_bounds(lines, heading)
    section = "\n".join(lines[start:end])
    matches = re.findall(
        rf"(?m)^-\s+{re.escape(label)}:\s*(.*?)\s*$",
        section,
    )
    if len(matches) != 1 or not matches[0].strip():
        raise PlanContractError(
            f"{heading} must provide exactly one {label!r} field"
        )
    return matches[0].strip()


def _repair_value(value: str) -> str:
    return value.strip().strip("`*_ ").lower()


def _is_placeholder(value: str) -> bool:
    return _repair_value(value) in {"", "-", "none", "n/a", "tbd"}


def _has_durable_reference(value: str) -> bool:
    return re.search(r"https?://\S+|#[1-9]\d*", value) is not None


def validate_repair_cycle_governance_body(text: str) -> int:
    """Validate declared repair rounds and the hard escalation threshold.

    The PR body is the durable index for repair review receipts. GitHub review
    discussion still owns the finding and classification; this validator makes
    the declared count and required escalation machine-checkable.
    """

    try:
        ledger = parse_table(text, REPAIR_CYCLE_LEDGER_HEADING)
    except PlanContractError as exc:
        raise PlanContractError(
            f"PR body must contain {REPAIR_CYCLE_LEDGER_HEADING}"
        ) from exc
    if ledger.header != REPAIR_CYCLE_LEDGER_HEADER:
        raise PlanContractError(
            "Repair Cycle Ledger must use columns: "
            + ", ".join(REPAIR_CYCLE_LEDGER_HEADER)
        )
    if not ledger.rows:
        raise PlanContractError("Repair Cycle Ledger must contain a row")

    first_values = tuple(_repair_value(value) for value in ledger.rows[0])
    if first_values[0] == "none":
        if len(ledger.rows) != 1 or any(
            value != "none" for value in first_values
        ):
            raise PlanContractError(
                "the no-repair ledger must contain exactly one all-None row"
            )
        substantial_cycles = 0
    else:
        substantial_cycles = 0
        for expected_cycle, row in enumerate(ledger.rows, start=1):
            cycle, review_receipt, classification, repair_revision, impact = row
            if _repair_value(cycle) != str(expected_cycle):
                raise PlanContractError(
                    "Repair Cycle Ledger cycle numbers must be consecutive from 1"
                )
            if not _has_durable_reference(review_receipt):
                raise PlanContractError(
                    f"repair cycle {expected_cycle} needs a durable review receipt"
                )
            normalized_classification = _repair_value(classification)
            if normalized_classification not in REPAIR_CYCLE_CLASSIFICATIONS:
                raise PlanContractError(
                    f"repair cycle {expected_cycle} classification must be one of: "
                    + ", ".join(sorted(REPAIR_CYCLE_CLASSIFICATIONS))
                )
            normalized_revision = _repair_value(repair_revision)
            if re.fullmatch(r"[0-9a-f]{7,40}", normalized_revision) is None:
                raise PlanContractError(
                    f"repair cycle {expected_cycle} must name its repair revision SHA"
                )
            if _is_placeholder(impact):
                raise PlanContractError(
                    f"repair cycle {expected_cycle} must summarize contract impact"
                )
            if normalized_classification == "substantial":
                substantial_cycles += 1

    if substantial_cycles > 2:
        raise PlanContractError(
            "a third substantial repair cycle cannot remain in the same review unit; "
            "split, replace, amend, or abandon it"
        )

    try:
        status = _repair_value(
            _repair_field(text, REPAIR_ESCALATION_HEADING, "Status")
        )
        decision_receipt = _repair_field(
            text,
            REPAIR_ESCALATION_HEADING,
            "Decision receipt",
        )
        route = _repair_value(
            _repair_field(text, REPAIR_ESCALATION_HEADING, "Route")
        )
        disposition = _repair_field(
            text,
            REPAIR_ESCALATION_HEADING,
            "Disposition",
        )
    except PlanContractError as exc:
        raise PlanContractError(
            f"PR body must contain a completed {REPAIR_ESCALATION_HEADING} section"
        ) from exc

    if status not in REPAIR_ESCALATION_STATUSES:
        raise PlanContractError(
            "Repair Escalation Status must be `not-required` or `completed`"
        )
    if substantial_cycles == 2 and status != "completed":
        raise PlanContractError(
            "two substantial repair cycles require a completed repair escalation "
            "before re-review"
        )
    if status == "not-required":
        if not _is_placeholder(decision_receipt) or route != "none":
            raise PlanContractError(
                "a not-required repair escalation must keep receipt and route as None"
            )
    else:
        if not _has_durable_reference(decision_receipt):
            raise PlanContractError(
                "a completed repair escalation needs a durable decision receipt"
            )
        if route not in REPAIR_ESCALATION_ROUTES:
            raise PlanContractError(
                "Repair Escalation Route must be one of: "
                + ", ".join(sorted(REPAIR_ESCALATION_ROUTES))
            )
        if _is_placeholder(disposition):
            raise PlanContractError(
                "a completed repair escalation must record its disposition"
            )
    return substantial_cycles


def _required_section_body(
    text: str,
    heading: str,
    *,
    document: str = "implementation adjunct",
) -> str:
    lines = text.splitlines()
    start, end = _section_bounds(lines, heading)
    body = "\n".join(lines[start:end]).strip()
    without_comments = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    meaningful = [
        line.strip()
        for line in without_comments.splitlines()
        if line.strip()
        and line.strip() not in {"-", "```", "```text", "```sh"}
        and not line.strip().startswith("### ")
    ]
    if not meaningful:
        raise PlanContractError(f"{document} section {heading} must be completed")
    return body


def _comment_review_receipt_outcome(body: str) -> str | None:
    if CONTRACT_REVIEW_RECEIPT_HEADING not in body:
        return None
    normalized = body.replace("\r\n", "\n").strip()
    match = re.fullmatch(
        rf"{re.escape(CONTRACT_REVIEW_RECEIPT_HEADING)}\n\n"
        r"- Outcome: `(accepted|changes_requested)`",
        normalized,
    )
    if match is None:
        raise PlanContractError(
            "contract COMMENT review must contain only the canonical receipt "
            "with Outcome `accepted` or `changes_requested`"
        )
    outcome = match.group(1)
    if outcome not in CONTRACT_REVIEW_RECEIPT_OUTCOMES:
        raise PlanContractError("contract review receipt outcome is invalid")
    return outcome


def _github_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise PlanContractError(f"{label} is missing its timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanContractError(f"{label} has an invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise PlanContractError(f"{label} timestamp must include a timezone")
    return parsed


def _validate_exact_head_contract_review(
    payload: dict[str, Any],
    *,
    review_metadata: ProposalReviewMetadata,
    label: str,
) -> ContractReviewReceipt:
    head_oid = payload.get("headRefOid")
    if not isinstance(head_oid, str) or re.fullmatch(r"[0-9a-f]{40}", head_oid) is None:
        raise PlanContractError(f"{label} has no full head commit")
    merged_at = _github_timestamp(
        review_metadata.merged_at,
        label=f"{label} merge",
    )
    reviews = review_metadata.reviews

    decisive_by_reviewer: dict[
        str,
        tuple[datetime, int, str, str, str, str],
    ] = {}
    for index, review in enumerate(reviews):
        if not isinstance(review, dict):
            continue
        commit = review.get("commit")
        review_oid = commit.get("oid") if isinstance(commit, dict) else None
        if review_oid != head_oid:
            continue
        author = review.get("author")
        reviewer = author.get("login") if isinstance(author, dict) else None
        if not isinstance(reviewer, str) or not reviewer:
            continue
        association = str(review.get("authorAssociation") or "").upper()
        can_push = review.get("authorCanPushToRepository") is True
        if (
            association not in AUTHORIZED_REVIEW_ASSOCIATIONS
            or not can_push
        ):
            continue
        state = str(review.get("state") or "").upper()
        outcome: str | None = None
        if state == "APPROVED":
            outcome = "accepted"
        elif state == "CHANGES_REQUESTED":
            outcome = "changes_requested"
        elif state == "COMMENTED":
            body = review.get("body")
            if not isinstance(body, str):
                raise PlanContractError(f"{label} review body must be text")
            try:
                outcome = _comment_review_receipt_outcome(body)
            except PlanContractError:
                outcome = "malformed"
            if outcome is not None and review.get("includesCreatedEdit") is not False:
                outcome = "malformed"
        if outcome is None:
            continue
        submitted_at = review.get("submittedAt")
        submitted = _github_timestamp(
            submitted_at,
            label=f"{label} decisive review",
        )
        if submitted > merged_at:
            continue
        assert isinstance(submitted_at, str)
        decision = (
            submitted,
            index,
            outcome,
            reviewer,
            association,
            submitted_at,
        )
        previous = decisive_by_reviewer.get(reviewer)
        if previous is None or decision[:2] > previous[:2]:
            decisive_by_reviewer[reviewer] = decision

    if not decisive_by_reviewer:
        raise PlanContractError(
            f"{label} has no decisive authorized GitHub review on exact head "
            f"{head_oid}"
        )
    outstanding = sorted(
        reviewer
        for reviewer, decision in decisive_by_reviewer.items()
        if decision[2] == "changes_requested"
    )
    if outstanding:
        raise PlanContractError(
            f"{label} has outstanding authorized changes requested on exact head "
            f"{head_oid} by {', '.join(outstanding)}"
        )
    malformed = sorted(
        reviewer
        for reviewer, decision in decisive_by_reviewer.items()
        if decision[2] == "malformed"
    )
    if malformed:
        raise PlanContractError(
            f"{label} has malformed or edited COMMENT receipt on exact head "
            f"{head_oid} by {', '.join(malformed)}"
        )
    accepted = [
        decision
        for decision in decisive_by_reviewer.values()
        if decision[2] == "accepted"
    ]
    if not accepted:
        raise PlanContractError(
            f"{label} has no accepted authorized review on exact head {head_oid}"
        )
    _, _, _, reviewer, association, submitted_at = max(accepted)
    return ContractReviewReceipt(
        head_oid=head_oid,
        reviewer=reviewer,
        reviewer_association=association,
        submitted_at=submitted_at,
    )


def _contract_review_receipt_suffix(
    receipt: ContractReviewReceipt,
) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", receipt.head_oid) is None:
        raise PlanContractError("contract review receipt requires a full head commit")
    if (
        not receipt.reviewer
        or receipt.reviewer_association not in AUTHORIZED_REVIEW_ASSOCIATIONS
    ):
        raise PlanContractError("contract review receipt has invalid reviewer authority")
    _github_timestamp(
        receipt.submitted_at,
        label="contract review receipt",
    )
    return (
        f" (reviewed head `{receipt.head_oid}` by `{receipt.reviewer}` as "
        f"`{receipt.reviewer_association}` at `{receipt.submitted_at}`)"
    )
def _pr_review_kind(text: str) -> str:
    if text.splitlines().count("## Review Kind") != 1:
        raise PlanContractError(
            "review-unit PR body must contain exactly one ## Review Kind section"
        )
    try:
        body = _required_section_body(text, "## Review Kind")
    except PlanContractError as exc:
        raise PlanContractError(
            "review-unit PR body must provide a completed ## Review Kind section"
        ) from exc
    without_comments = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    values = [
        line.strip().removeprefix("- ").strip()
        for line in without_comments.splitlines()
        if line.strip()
    ]
    if len(values) != 1:
        raise PlanContractError(
            "review-unit PR body ## Review Kind must contain exactly one value"
        )
    review_kind = _normalize_review_kind(values[0])
    if review_kind not in ALLOWED_REVIEW_KINDS:
        raise PlanContractError(
            f"review-unit PR body has unsupported review kind {values[0]!r}"
        )
    return review_kind


def _validate_pr_review_kind(text: Any, *, expected: str) -> None:
    if not isinstance(text, str):
        raise PlanContractError("review-unit PR validation requires the PR body")
    actual = _pr_review_kind(text)
    normalized_expected = _normalize_review_kind(expected)
    if actual != normalized_expected:
        raise PlanContractError(
            "review-unit PR body review kind does not match the canonical plan: "
            f"{actual!r} != {normalized_expected!r}"
        )


def _implementation_adjunct_field(section: str, label: str) -> str:
    match = re.search(
        rf"(?m)^-\s+{re.escape(label)}:\s*(.*?)\s*$",
        section,
    )
    if match is None or not match.group(1).strip():
        raise PlanContractError(
            f"implementation adjunct must provide {label!r}"
        )
    return match.group(1).strip()


def validate_implementation_adjunct_body(
    text: str,
    *,
    base_branch: str | None = None,
    head_branch: str | None = None,
    milestone_number: str | None = None,
    frontier_name: str | None = None,
) -> None:
    """Validate the durable human request and compatibility claim for an adjunct."""

    if not text.startswith("# HITL Implementation Adjunct"):
        raise PlanContractError(
            "implementation adjunct PR body must start with "
            "'# HITL Implementation Adjunct'"
        )
    sections: dict[str, str] = {}
    for heading in IMPLEMENTATION_ADJUNCT_REQUIRED_HEADINGS:
        try:
            sections[heading] = _required_section_body(text, heading)
        except PlanContractError as exc:
            raise PlanContractError(
                f"implementation adjunct PR body is missing or incomplete: {heading}"
            ) from exc

    parent = sections["## Parent Implementation"]
    listed_milestone = _implementation_adjunct_field(
        parent,
        "Milestone",
    ).strip("`")
    if (
        milestone_number is not None
        and listed_milestone not in {milestone_number, f"M{milestone_number}"}
    ):
        raise PlanContractError(
            "implementation adjunct Milestone must match the owning plan "
            f"M{milestone_number}"
        )
    listed_frontier = _implementation_adjunct_field(parent, "Current frontier")
    if frontier_name is not None and listed_frontier != frontier_name:
        raise PlanContractError(
            "implementation adjunct Current frontier must match the owning plan "
            f"{frontier_name!r}"
        )
    parent_pr = _implementation_adjunct_field(
        parent,
        "Parent implementation PR",
    )
    if re.search(r"#[1-9]\d*", parent_pr) is None:
        raise PlanContractError(
            "implementation adjunct Parent implementation PR must contain #<number>"
        )
    listed_base = _implementation_adjunct_field(
        parent,
        "Base implementation branch",
    ).strip("`")
    if base_branch is not None and listed_base != base_branch:
        raise PlanContractError(
            "implementation adjunct Base implementation branch must match its PR base "
            f"{base_branch!r}"
        )
    listed_head = _implementation_adjunct_field(
        parent,
        "Adjunct branch",
    ).strip("`")
    if head_branch is not None and listed_head != head_branch:
        raise PlanContractError(
            "implementation adjunct Adjunct branch must match its PR head "
            f"{head_branch!r}"
        )

    request = _implementation_adjunct_field(
        sections["## Operator Request"],
        "Request issue",
    )
    if (
        re.search(r"#[1-9]\d*", request) is None
        and re.search(
            r"https://github\.com/[^\s/]+/[^\s/]+/issues/[1-9]\d*",
            request,
        )
        is None
    ):
        raise PlanContractError(
            "implementation adjunct Request issue must contain a durable issue reference"
        )

    authorization = sections["## HITL Authorization"]
    _implementation_adjunct_field(authorization, "Human requester")
    _implementation_adjunct_field(authorization, "Discovery context")
    disposition = _implementation_adjunct_field(
        authorization,
        "Requested disposition",
    ).strip("`")
    if disposition != "implement-now":
        raise PlanContractError(
            "implementation adjunct Requested disposition must be `implement-now`"
        )

    question = sections["## Review Question"]
    _implementation_adjunct_field(question, "Acceptance owner")
    question_without_owner = re.sub(
        r"(?m)^-\s+Acceptance owner:\s*.*$",
        "",
        question,
    )
    question_without_comments = re.sub(
        r"<!--.*?-->",
        "",
        question_without_owner,
        flags=re.DOTALL,
    )
    if "?" not in question_without_comments:
        raise PlanContractError(
            "implementation adjunct must state one explicit review question"
        )

    compatibility = sections["## Compatibility"]
    for check in IMPLEMENTATION_ADJUNCT_COMPATIBILITY_CHECKS:
        if re.search(
            rf"(?m)^-\s+\[[xX]\]\s+{re.escape(check)}\s*$",
            compatibility,
        ) is None:
            raise PlanContractError(
                "implementation adjunct compatibility assertion is not checked: "
                + check
            )

    for heading in ("### In Scope", "### Out Of Scope"):
        _required_section_body(text, heading)

    evidence = sections["## Evidence Impact"]
    _implementation_adjunct_field(evidence, "Existing evidence affected")
    _implementation_adjunct_field(evidence, "Evidence to refresh")
    _implementation_adjunct_field(evidence, "Parent integration check")

    validation = sections["## Validation"]
    fenced = re.search(r"```(?:text|sh)?\s*\n(.*?)```", validation, re.DOTALL)
    if fenced is None or not fenced.group(1).strip():
        raise PlanContractError(
            "implementation adjunct Validation must contain exact commands or results"
        )
    validate_repair_cycle_governance_body(text)


def load_handoff_template(proposal_text: str) -> dict[str, Any]:
    """Load and validate the proposal's reviewed post-merge handoff template."""

    lines = proposal_text.splitlines()
    start, end = _section_bounds(lines, "## Expected Handoff")
    fence_indexes = [
        index for index in range(start, end) if lines[index].strip() == "```json"
    ]
    if len(fence_indexes) != 1:
        raise PlanContractError(
            "Expected Handoff must contain exactly one ```json code block"
        )
    fence_start = fence_indexes[0]
    fence_end: int | None = None
    for index in range(fence_start + 1, end):
        if lines[index].strip() == "```":
            fence_end = index
            break
    if fence_end is None:
        raise PlanContractError("Expected Handoff JSON code block is not closed")
    try:
        payload = json.loads("\n".join(lines[fence_start + 1 : fence_end]))
    except json.JSONDecodeError as exc:
        raise PlanContractError(f"Expected Handoff contains invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanContractError("Expected Handoff template must be a JSON object")
    materialize_handoff_receipt(
        payload,
        accepted_pr=1,
        accepted_merge_commit="a" * 40,
    )
    return payload


def _replace_handoff_tokens(
    value: Any,
    *,
    accepted_pr: int,
    accepted_merge_commit: str,
) -> Any:
    if isinstance(value, str):
        return value.replace("{pr}", str(accepted_pr)).replace(
            "{merge_commit}",
            accepted_merge_commit,
        )
    if isinstance(value, list):
        return [
            _replace_handoff_tokens(
                item,
                accepted_pr=accepted_pr,
                accepted_merge_commit=accepted_merge_commit,
            )
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _replace_handoff_tokens(
                item,
                accepted_pr=accepted_pr,
                accepted_merge_commit=accepted_merge_commit,
            )
            for key, item in value.items()
        }
    return value


def materialize_handoff_receipt(
    template: dict[str, Any],
    *,
    accepted_pr: int,
    accepted_merge_commit: str,
) -> dict[str, Any]:
    """Fill merge-time identity into a proposal-reviewed handoff template."""

    if template.get("schema") != HANDOFF_TEMPLATE_SCHEMA:
        raise PlanContractError(
            f"Expected Handoff schema must be {HANDOFF_TEMPLATE_SCHEMA}"
        )
    forbidden = {"accepted_pr", "accepted_merge_commit"} & set(template)
    if forbidden:
        raise PlanContractError(
            "Expected Handoff cannot predeclare merge-time fields: "
            + ", ".join(sorted(forbidden))
        )
    materialized = _replace_handoff_tokens(
        template,
        accepted_pr=accepted_pr,
        accepted_merge_commit=accepted_merge_commit,
    )
    materialized["schema"] = "milestone_handoff_v1"
    materialized["accepted_pr"] = accepted_pr
    materialized["accepted_merge_commit"] = accepted_merge_commit
    return _normalize_receipt(materialized)


def validate_handoff_template_against_plan(
    proposal_text: str,
    proposal_review_plan: str,
) -> None:
    """Prove the reviewed success template can advance the frozen plan."""

    state = validate_plan_text(proposal_review_plan)
    if _workflow_state(state.current) != "proposal_in_review":
        raise PlanContractError(
            "Expected Handoff validation requires proposal_in_review"
        )
    used_prs = {
        int(match.group(1))
        for row in state.ledger.rows
        if (match := re.fullmatch(r"#(\d+)", row[0])) is not None
    }
    proposal_pr = max(used_prs, default=0) + 1
    implementation_pr = proposal_pr + 1
    accepted = accept_proposal(
        proposal_review_plan,
        proposal_pr=proposal_pr,
        merge_commit="b" * 40,
        proposal_url="https://example.invalid/proposal",
        review_receipt=ContractReviewReceipt(
            head_oid="a" * 40,
            reviewer="contract-simulation",
            reviewer_association="OWNER",
            submitted_at="2000-01-01T00:00:00+00:00",
        ),
    )
    implementation_review = _replace_current_frontier_state(
        accepted,
        expected_state="ready_for_implementation",
        new_state="implementation_in_review",
        evidence="Implementation branch started.",
    )
    receipt = materialize_handoff_receipt(
        load_handoff_template(proposal_text),
        accepted_pr=implementation_pr,
        accepted_merge_commit="c" * 40,
    )
    apply_handoff(implementation_review, receipt)


def proposal_allowed_paths(plan: Path, state: PlanState, *, repo_root: Path = ROOT) -> set[str]:
    plan_relative = _validate_plan_location(plan, repo_root=repo_root.resolve()).as_posix()
    html_relative = str(Path(plan_relative).with_suffix(".html"))
    proposal_relative = _frontier_proposal_path(
        state.current,
        heading="Current Frontier",
    )
    return {plan_relative, html_relative, proposal_relative}


def _fetch_pr_review_metadata(
    pr_number: int,
    *,
    repo_root: Path = ROOT,
) -> ProposalReviewMetadata:
    query = """
query(
  $owner: String!,
  $name: String!,
  $number: Int!
) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      mergedAt
      reviews(first: 100) {
        totalCount
        nodes {
          author { login }
          authorAssociation
          authorCanPushToRepository
          body
          commit { oid }
          includesCreatedEdit
          state
          submittedAt
        }
      }
    }
  }
}
""".strip()
    try:
        repository = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        repository_payload = json.loads(repository.stdout)
        if not isinstance(repository_payload, dict):
            raise PlanContractError("GitHub CLI returned invalid repository metadata")
        name_with_owner = repository_payload.get("nameWithOwner")
        if not isinstance(name_with_owner, str) or "/" not in name_with_owner:
            raise PlanContractError("GitHub CLI returned invalid repository metadata")
        owner, name = name_with_owner.split("/", 1)
        result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
                "-F",
                f"number={pr_number}",
                "-f",
                f"query={query}",
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        response = json.loads(result.stdout)
    except FileNotFoundError as exc:
        raise PlanContractError(
            "GitHub CLI `gh` is required for proposal acceptance"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise PlanContractError(f"cannot fetch proposal reviews: {detail}") from exc
    except json.JSONDecodeError as exc:
        raise PlanContractError("GitHub CLI returned invalid review metadata") from exc
    try:
        pull_request = response["data"]["repository"]["pullRequest"]
        connection = pull_request["reviews"]
        reviews = connection["nodes"]
        total_count = connection["totalCount"]
    except (KeyError, TypeError) as exc:
        raise PlanContractError(
            "GitHub CLI returned incomplete review metadata"
        ) from exc
    merged_at = pull_request.get("mergedAt")
    if not isinstance(merged_at, str):
        raise PlanContractError("GitHub pull request review metadata is incomplete")
    if not isinstance(total_count, int) or not isinstance(reviews, list):
        raise PlanContractError("GitHub review connection is invalid")
    if total_count > 100 or len(reviews) != total_count:
        raise PlanContractError(
            "proposal acceptance refuses a review history larger than the "
            "100-review verification window"
        )
    if any(not isinstance(review, dict) for review in reviews):
        raise PlanContractError("GitHub review connection has invalid review nodes")
    return ProposalReviewMetadata(
        merged_at=merged_at,
        reviews=tuple(reviews),
    )


def _review_metadata_from_payload(
    payload: dict[str, Any],
    *,
    label: str,
) -> ProposalReviewMetadata:
    merged_at = payload.get("mergedAt")
    reviews = payload.get("reviews")
    if (
        not isinstance(merged_at, str)
        or not isinstance(reviews, list)
        or any(not isinstance(review, dict) for review in reviews)
    ):
        raise PlanContractError(f"{label} did not expose complete review metadata")
    return ProposalReviewMetadata(
        merged_at=merged_at,
        reviews=tuple(reviews),
    )


def validate_merged_proposal_metadata(
    payload: dict[str, Any],
    state: PlanState,
    *,
    proposal_pr: int,
    allowed_paths: set[str],
    review_metadata: ProposalReviewMetadata | None = None,
) -> tuple[str, str, ContractReviewReceipt]:
    if _workflow_state(state.current) != "proposal_in_review":
        raise PlanContractError(
            "proposal acceptance requires workflow state proposal_in_review"
        )
    if payload.get("state") != "MERGED":
        raise PlanContractError(f"proposal PR #{proposal_pr} is not merged")
    if payload.get("baseRefName") != state.milestone_branch:
        raise PlanContractError(
            f"proposal PR #{proposal_pr} did not target {state.milestone_branch}"
        )
    expected_head = _frontier_branch(
        state.current,
        heading="Current Frontier",
        field="proposal branch",
    )
    if payload.get("headRefName") != expected_head:
        raise PlanContractError(
            f"proposal PR #{proposal_pr} did not use {expected_head}"
        )
    if review_metadata is None:
        review_metadata = _review_metadata_from_payload(payload, label="proposal PR")
    review_receipt = _validate_exact_head_contract_review(
        payload,
        review_metadata=review_metadata,
        label=f"proposal PR #{proposal_pr}",
    )
    _validate_pr_review_kind(
        payload.get("body"),
        expected=state.current.fields["review kind"],
    )
    merge_commit = payload.get("mergeCommit")
    merge_oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    if not isinstance(merge_oid, str) or re.fullmatch(r"[0-9a-f]{40}", merge_oid) is None:
        raise PlanContractError(
            f"proposal PR #{proposal_pr} has no full merge commit"
        )
    files = payload.get("files")
    if not isinstance(files, list):
        raise PlanContractError(
            f"proposal PR #{proposal_pr} did not expose its changed files"
        )
    changed = {
        item.get("path")
        for item in files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    unexpected = changed - allowed_paths
    if unexpected:
        raise PlanContractError(
            "proposal PR contains implementation changes: "
            + ", ".join(sorted(unexpected))
        )
    proposal_path = _frontier_proposal_path(
        state.current,
        heading="Current Frontier",
    )
    if proposal_path not in changed:
        raise PlanContractError(
            f"proposal PR must create or update {proposal_path}"
        )
    return (
        merge_oid,
        str(payload.get("url") or f"PR #{proposal_pr}"),
        review_receipt,
    )


def accept_proposal(
    text: str,
    *,
    proposal_pr: int,
    merge_commit: str,
    proposal_url: str,
    review_receipt: ContractReviewReceipt,
) -> str:
    reviewed_suffix = _contract_review_receipt_suffix(review_receipt)
    return _replace_current_frontier_state(
        text,
        expected_state="proposal_in_review",
        new_state="ready_for_implementation",
        evidence=(
            f"Proposal PR #{proposal_pr} accepted at {merge_commit}"
            f"{reviewed_suffix}."
        ),
        accepted_proposal=(
            f"[#{proposal_pr}]({proposal_url}) at `{merge_commit}`"
            f"{reviewed_suffix}"
        ),
    )


def proposal_amendment_allowed_paths(
    plan: Path,
    state: PlanState,
    *,
    repo_root: Path = ROOT,
) -> set[str]:
    plan_relative = _validate_plan_location(
        plan,
        repo_root=repo_root.resolve(),
    ).as_posix()
    html_relative = str(Path(plan_relative).with_suffix(".html"))
    amendment_relative = _frontier_proposal_amendment_path(
        state.current,
        heading="Current Frontier",
    )
    return {plan_relative, html_relative, amendment_relative}


def validate_merged_proposal_amendment_metadata(
    payload: dict[str, Any],
    state: PlanState,
    *,
    amendment_pr: int,
    allowed_paths: set[str],
    review_metadata: ProposalReviewMetadata | None = None,
) -> tuple[str, str, ContractReviewReceipt]:
    if _workflow_state(state.current) != "proposal_amendment_in_review":
        raise PlanContractError(
            "proposal amendment acceptance requires workflow state "
            "proposal_amendment_in_review"
        )
    if payload.get("state") != "MERGED":
        raise PlanContractError(f"proposal amendment PR #{amendment_pr} is not merged")
    if payload.get("baseRefName") != state.milestone_branch:
        raise PlanContractError(
            f"proposal amendment PR #{amendment_pr} did not target "
            f"{state.milestone_branch}"
        )
    expected_head = _frontier_branch(
        state.current,
        heading="Current Frontier",
        field="proposal amendment branch",
    )
    if payload.get("headRefName") != expected_head:
        raise PlanContractError(
            f"proposal amendment PR #{amendment_pr} did not use {expected_head}"
        )
    if review_metadata is None:
        review_metadata = _review_metadata_from_payload(
            payload,
            label="proposal amendment PR",
        )
    review_receipt = _validate_exact_head_contract_review(
        payload,
        review_metadata=review_metadata,
        label=f"proposal amendment PR #{amendment_pr}",
    )
    _validate_pr_review_kind(
        payload.get("body"),
        expected=state.current.fields["review kind"],
    )
    merge_commit = payload.get("mergeCommit")
    merge_oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    if not isinstance(merge_oid, str) or re.fullmatch(r"[0-9a-f]{40}", merge_oid) is None:
        raise PlanContractError(
            f"proposal amendment PR #{amendment_pr} has no full merge commit"
        )
    files = payload.get("files")
    if not isinstance(files, list):
        raise PlanContractError(
            f"proposal amendment PR #{amendment_pr} did not expose its changed files"
        )
    changed = {
        item.get("path")
        for item in files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    unexpected = changed - allowed_paths
    if unexpected:
        raise PlanContractError(
            "proposal amendment PR contains non-contract changes: "
            + ", ".join(sorted(unexpected))
        )
    amendment_path = _frontier_proposal_amendment_path(
        state.current,
        heading="Current Frontier",
    )
    if amendment_path not in changed:
        raise PlanContractError(
            f"proposal amendment PR must create {amendment_path}"
        )
    return (
        merge_oid,
        str(payload.get("url") or f"PR #{amendment_pr}"),
        review_receipt,
    )


def accept_proposal_amendment(
    text: str,
    *,
    amendment_pr: int,
    merge_commit: str,
    amendment_url: str,
    review_receipt: ContractReviewReceipt,
) -> str:
    state = validate_plan_text(text)
    amendment_path = _frontier_proposal_amendment_path(
        state.current,
        heading="Current Frontier",
    )
    receipt = (
        f"[#{amendment_pr}]({amendment_url}) at `{merge_commit}` "
        f"(`{amendment_path}`)"
    )
    receipt += _contract_review_receipt_suffix(review_receipt)
    existing = state.current.fields.get("accepted proposal amendments")
    accepted = f"{existing}; {receipt}" if existing else receipt
    return _replace_current_frontier_state(
        text,
        expected_state="proposal_amendment_in_review",
        new_state="ready_for_implementation",
        evidence=(
            f"Proposal amendment PR #{amendment_pr} accepted at {merge_commit}"
            f"{_contract_review_receipt_suffix(review_receipt)}."
        ),
        field_updates={"accepted proposal amendments": accepted},
        field_removes=("amendment source state",),
    )


def abandon_proposal_amendment(
    plan: Path,
    state: PlanState,
    *,
    reason: str,
    repo_root: Path = ROOT,
) -> str:
    repo_root = repo_root.resolve()
    _validate_plan_location(plan, repo_root=repo_root)
    if state.status != "Active" or state.current.is_empty:
        raise PlanContractError(
            "proposal amendment abandon requires an active current frontier"
        )
    if _workflow_state(state.current) != "proposal_amendment_in_review":
        raise PlanContractError(
            "proposal amendment abandon requires proposal_amendment_in_review"
        )
    if not str(reason).strip() or str(reason).strip().lower() in {
        "later",
        "someday",
        "tbd",
        "none",
    }:
        raise PlanContractError("proposal amendment abandon requires a concrete reason")
    current_branch = _run_git(
        ["branch", "--show-current"],
        cwd=repo_root,
    ).stdout.strip()
    if current_branch != state.milestone_branch:
        raise PlanContractError(
            f"proposal amendment abandon must run on {state.milestone_branch!r}, "
            f"currently {current_branch!r}"
        )
    if _run_git(["status", "--porcelain"], cwd=repo_root).stdout.strip():
        raise PlanContractError("proposal amendment abandon requires a clean worktree")
    source = state.current.fields.get("amendment source state")
    if source not in {
        "ready_for_implementation",
        "implementation_in_review",
    }:
        raise PlanContractError(
            "proposal amendment abandon requires a recorded amendment source state"
        )
    field_updates: dict[str, str] = {}
    field_removes = (
        "proposal amendment branch",
        "proposal amendment path",
        "amendment source state",
        "paused implementation",
    )
    paused = state.current.fields.get("paused implementation")
    if source == "implementation_in_review":
        if not paused:
            raise PlanContractError(
                "implementation-sourced amendment abandon requires the paused "
                "implementation receipt"
            )
        parsed = parse_paused_implementation(paused)
        field_updates["pr"] = f"[#{parsed['pr']}]({parsed['url']})"
    original = plan.read_text(encoding="utf-8")
    updated = _replace_current_frontier_state(
        original,
        expected_state="proposal_amendment_in_review",
        new_state=source,
        evidence=f"Abandoned proposal amendment; restored {source}: {reason.strip()}",
        field_updates=field_updates,
        field_removes=field_removes,
    )
    plan.write_text(updated, encoding="utf-8")
    return updated


def _is_plan_revision_branch(milestone_number: str, branch: str) -> bool:
    return (
        re.fullmatch(
            rf"m{re.escape(milestone_number)}/plan-[a-z0-9][a-z0-9-]*",
            branch,
        )
        is not None
    )


def _criterion_rows_by_id(state: PlanState) -> dict[str, tuple[str, ...]]:
    return {row[0]: row for row in state.criteria.rows}


def _validate_plan_revision_transition(
    base: PlanState,
    head: PlanState,
    *,
    plan_path: str,
    changed_paths: set[str],
    head_branch: str,
) -> str:
    base_state = _workflow_state(base.current)
    head_state = _workflow_state(head.current)
    if base_state != "ready_for_proposal" or head_state != "ready_for_proposal":
        raise PlanContractError(
            "plan revision requires ready_for_proposal before and after review"
        )
    if not _is_plan_revision_branch(base.milestone_number, head_branch):
        raise PlanContractError(
            "plan revision branch must match "
            f"m{base.milestone_number}/plan-<slug>, not {head_branch}"
        )
    if (
        base.milestone_number != head.milestone_number
        or base.milestone_branch != head.milestone_branch
        or base.status != head.status
    ):
        raise PlanContractError(
            "plan revision cannot change milestone identity, branch, or status"
        )
    for state in (base, head):
        if "pr" in state.current.fields or "accepted proposal" in state.current.fields:
            raise PlanContractError(
                "plan revision is unavailable after proposal work has started"
            )

    plan_html = str(Path(plan_path).with_suffix(".html"))
    required_paths = {plan_path, plan_html}
    unexpected = changed_paths - required_paths
    if unexpected:
        raise PlanContractError(
            "plan revision contains non-plan changes: "
            + ", ".join(sorted(unexpected))
        )
    missing = required_paths - changed_paths
    if missing:
        raise PlanContractError(
            "plan revision must update canonical plan and rendered HTML: "
            + ", ".join(sorted(missing))
        )

    if base.ledger != head.ledger:
        raise PlanContractError(
            "plan revision cannot rewrite accepted review-unit evidence"
        )
    base_criteria = _criterion_rows_by_id(base)
    head_criteria = _criterion_rows_by_id(head)
    for criterion_id, row in head_criteria.items():
        if row[2] != "Met":
            continue
        if base_criteria.get(criterion_id) != row:
            raise PlanContractError(
                "plan revision cannot add or rewrite a Met exit criterion "
                f"({criterion_id})"
            )
    for criterion_id, row in base_criteria.items():
        if row[2] == "Met" and head_criteria.get(criterion_id) != row:
            raise PlanContractError(
                "plan revision cannot remove or rewrite a Met exit criterion "
                f"({criterion_id})"
            )

    base_history = base.workflow_history.rows
    head_history = head.workflow_history.rows
    if (
        len(head_history) != len(base_history) + 1
        or head_history[: len(base_history)] != base_history
    ):
        raise PlanContractError(
            "plan revision must append exactly one workflow-history entry"
        )
    last_frontier, last_state, last_evidence = head_history[-1]
    if (
        last_frontier != head.current.name
        or last_state != "ready_for_proposal"
        or not last_evidence.startswith("Plan revision:")
    ):
        raise PlanContractError(
            "plan revision history must name the current frontier, remain "
            "ready_for_proposal, and begin its evidence with 'Plan revision:'"
        )
    return "plan_revision"


def validate_review_unit_transition(
    base_text: str,
    head_text: str,
    *,
    plan_path: str,
    changed_paths: set[str],
    head_branch: str,
    proposal_text: str | None = None,
    proposal_amendment_text: str | None = None,
    pr_body: str | None = None,
) -> str:
    base = validate_plan_text(base_text)
    head = validate_plan_text(head_text)
    if base.current.is_empty or head.current.is_empty:
        raise PlanContractError("review-unit PR requires an active current frontier")
    if _is_plan_revision_branch(base.milestone_number, head_branch):
        return _validate_plan_revision_transition(
            base,
            head,
            plan_path=plan_path,
            changed_paths=changed_paths,
            head_branch=head_branch,
        )
    if base.current.name != head.current.name:
        raise PlanContractError("review-unit PR cannot replace the current frontier")
    if base.next_frontier != head.next_frontier:
        raise PlanContractError("review-unit PR cannot change the queued frontier")
    base_state = _workflow_state(base.current)
    head_state = _workflow_state(head.current)
    is_proposal_amendment = (
        base_state
        in {"ready_for_implementation", "implementation_in_review"}
        and head_state == "proposal_amendment_in_review"
    )
    opened_branch_field = {
        ("ready_for_proposal", "proposal_in_review"): "proposal branch",
        (
            "ready_for_implementation",
            "implementation_in_review",
        ): "implementation branch",
    }.get((base_state, head_state))
    mutable_fields = {"workflow state", "pr"}
    if opened_branch_field is not None:
        mutable_fields.add(opened_branch_field)
    if is_proposal_amendment:
        mutable_fields.update(
            {
                "proposal amendment branch",
                "proposal amendment path",
                "amendment source state",
                "paused implementation",
            }
        )
    for field in (
        set(base.current.fields) | set(head.current.fields)
    ) - mutable_fields:
        if base.current.fields.get(field) != head.current.fields.get(field):
            raise PlanContractError(
                f"review-unit PR changed frozen frontier field {field!r}"
            )
    if opened_branch_field is not None:
        base_branch = _frontier_branch(
            base.current,
            heading="Current Frontier",
            field=opened_branch_field,
        )
        head_branch_value = _frontier_branch(
            head.current,
            heading="Current Frontier",
            field=opened_branch_field,
        )
        if head_branch_value != base_branch:
            raise PlanContractError(
                f"review-unit PR changed frozen {opened_branch_field} identity"
            )
        if head.current.fields[opened_branch_field] != f"`{base_branch}`":
            raise PlanContractError(
                f"opened {opened_branch_field} must be the canonical branch name"
            )
    if base.criteria != head.criteria:
        raise PlanContractError(
            "review-unit PR cannot pre-claim exit-criterion changes"
        )
    if base.ledger != head.ledger:
        raise PlanContractError(
            "review-unit PR cannot pre-claim an accepted ledger entry"
        )
    if base.risks != head.risks:
        raise PlanContractError(
            "review-unit PR cannot pre-claim risk resolution"
        )
    base_history = base.workflow_history.rows
    head_history = head.workflow_history.rows
    if (
        len(head_history) != len(base_history) + 1
        or head_history[: len(base_history)] != base_history
    ):
        raise PlanContractError(
            "review-unit PR must append exactly one workflow-history transition"
        )

    proposal_path = _frontier_proposal_path(
        base.current,
        heading="Current Frontier",
    )
    if base_state == "ready_for_proposal":
        if head_state != "proposal_in_review":
            raise PlanContractError(
                "proposal PR must transition ready_for_proposal to proposal_in_review"
            )
        expected_branch = _frontier_branch(
            base.current,
            heading="Current Frontier",
            field="proposal branch",
        )
        if head_branch != expected_branch:
            raise PlanContractError(
                f"proposal PR must use {expected_branch}, not {head_branch}"
            )
        plan_html = str(Path(plan_path).with_suffix(".html"))
        allowed_paths = {plan_path, plan_html, proposal_path}
        unexpected = changed_paths - allowed_paths
        if unexpected:
            raise PlanContractError(
                "proposal PR contains implementation changes: "
                + ", ".join(sorted(unexpected))
            )
        if proposal_path not in changed_paths or proposal_text is None:
            raise PlanContractError(f"proposal PR must provide {proposal_path}")
        _validate_pr_review_kind(
            pr_body,
            expected=base.current.fields["review kind"],
        )
        validate_proposal_text(proposal_text)
        validate_handoff_template_against_plan(proposal_text, head_text)
        transition_kind = "proposal"
    elif is_proposal_amendment:
        amendment_branch = _frontier_branch(
            head.current,
            heading="Current Frontier",
            field="proposal amendment branch",
        )
        if head.current.fields["proposal amendment branch"] != f"`{amendment_branch}`":
            raise PlanContractError(
                "opened proposal amendment branch must be the canonical branch name"
            )
        if head_branch != amendment_branch:
            raise PlanContractError(
                f"proposal amendment PR must use {amendment_branch}, not {head_branch}"
            )
        amendment_path = _frontier_proposal_amendment_path(
            head.current,
            heading="Current Frontier",
        )
        if head.current.fields["proposal amendment path"] != f"`{amendment_path}`":
            raise PlanContractError(
                "proposal amendment path must be the canonical artifact path"
            )
        accepted_amendment_paths = {
            record[2]
            for record in _accepted_proposal_amendments(
                base.current,
                heading="Current Frontier",
            )
        }
        if amendment_path == proposal_path or amendment_path in accepted_amendment_paths:
            raise PlanContractError(
                "proposal amendment PR must create a new additive amendment artifact"
            )
        plan_html = str(Path(plan_path).with_suffix(".html"))
        allowed_paths = {plan_path, plan_html, amendment_path}
        unexpected = changed_paths - allowed_paths
        if unexpected:
            raise PlanContractError(
                "proposal amendment PR contains non-contract changes: "
                + ", ".join(sorted(unexpected))
            )
        if (
            amendment_path not in changed_paths
            or proposal_amendment_text is None
        ):
            raise PlanContractError(
                f"proposal amendment PR must provide {amendment_path}"
            )
        _validate_pr_review_kind(
            pr_body,
            expected=base.current.fields["review kind"],
        )
        validate_proposal_amendment_text(proposal_amendment_text)
        transition_kind = "proposal_amendment"
    elif base_state == "ready_for_implementation":
        if head_state != "implementation_in_review":
            raise PlanContractError(
                "ready_for_implementation must transition to either "
                "proposal_amendment_in_review or implementation_in_review"
            )
        expected_branch = _frontier_branch(
            base.current,
            heading="Current Frontier",
            field="implementation branch",
        )
        if head_branch != expected_branch:
            raise PlanContractError(
                f"implementation PR must use {expected_branch}, not {head_branch}"
            )
        if (
            base.current.fields.get("accepted proposal")
            != head.current.fields.get("accepted proposal")
        ):
            raise PlanContractError(
                "implementation PR cannot replace its accepted proposal"
            )
        protected_proposal_paths = {
            proposal_path,
            *(
                record[2]
                for record in _accepted_proposal_amendments(
                    base.current,
                    heading="Current Frontier",
                )
            ),
        }
        changed_proposal_paths = protected_proposal_paths & changed_paths
        if changed_proposal_paths:
            raise PlanContractError(
                "implementation PR cannot modify the accepted proposal or its "
                "amendments: "
                + ", ".join(sorted(changed_proposal_paths))
            )
        _validate_pr_review_kind(
            pr_body,
            expected=base.current.fields["review kind"],
        )
        transition_kind = "implementation"
    else:
        raise PlanContractError(
            f"base workflow state {base_state!r} does not accept a new review-unit PR"
        )

    last_frontier, last_state, _ = head_history[-1]
    if last_frontier != head.current.name or last_state != head_state:
        raise PlanContractError(
            "review-unit PR history does not record its workflow transition"
        )
    if pr_body is not None:
        validate_repair_cycle_governance_body(pr_body)
    return transition_kind


def _git_text_at(ref: str, path: str, *, repo_root: Path = ROOT) -> str:
    result = _run_git(["show", f"{ref}:{path}"], cwd=repo_root, check=False)
    if result.returncode != 0:
        raise PlanContractError(f"{path} is unavailable at {ref}")
    return result.stdout


def _plan_at_branch(
    ref: str,
    *,
    milestone_branch: str,
    repo_root: Path = ROOT,
) -> tuple[str, str]:
    listing = _run_git(
        ["ls-tree", "-r", "--name-only", ref, "--", "docs/milestones"],
        cwd=repo_root,
    ).stdout.splitlines()
    for path in listing:
        if not path.endswith("/plan.md"):
            continue
        text = _git_text_at(ref, path, repo_root=repo_root)
        try:
            state = validate_plan_text(text)
        except PlanContractError:
            continue
        if state.milestone_branch == milestone_branch:
            return path, text
    raise PlanContractError(
        f"no canonical plan at {ref} owns milestone branch {milestone_branch}"
    )


def _plan_at_implementation_branch(
    ref: str,
    *,
    implementation_branch: str,
    repo_root: Path = ROOT,
) -> tuple[str, str] | None:
    listing = _run_git(
        ["ls-tree", "-r", "--name-only", ref, "--", "docs/milestones"],
        cwd=repo_root,
    ).stdout.splitlines()
    matches: list[tuple[str, str]] = []
    for path in listing:
        if not path.endswith("/plan.md"):
            continue
        text = _git_text_at(ref, path, repo_root=repo_root)
        try:
            state = validate_plan_text(text)
        except PlanContractError:
            continue
        if state.current.is_empty:
            continue
        if _workflow_state(state.current) != "implementation_in_review":
            continue
        planned_branch = _frontier_branch(
            state.current,
            heading="Current Frontier",
            field="implementation branch",
        )
        if planned_branch == implementation_branch:
            matches.append((path, text))
    if len(matches) > 1:
        raise PlanContractError(
            "multiple canonical plans claim implementation branch "
            f"{implementation_branch!r}"
        )
    return matches[0] if matches else None


def _is_implementation_adjunct_branch(base_branch: str, head_branch: str) -> bool:
    return (
        re.fullmatch(
            rf"{re.escape(base_branch)}--adjunct-[a-z0-9][a-z0-9-]*",
            head_branch,
        )
        is not None
    )


def _validate_implementation_adjunct_git_diff(
    *,
    plan_path: str,
    base_text: str,
    base_ref: str,
    head_ref: str,
    base_sha: str,
    head_sha: str,
    pr_body: str | None,
    repo_root: Path,
) -> str:
    if "--adjunct-" in base_ref:
        raise PlanContractError(
            "implementation adjuncts cannot target another adjunct branch"
        )
    if not _is_implementation_adjunct_branch(base_ref, head_ref):
        raise PlanContractError(
            "implementation adjunct branch must match "
            f"{base_ref}--adjunct-<slug>, not {head_ref}"
        )
    ancestor = _run_git(
        ["merge-base", "--is-ancestor", base_sha, head_sha],
        cwd=repo_root,
        check=False,
    )
    if ancestor.returncode != 0:
        raise PlanContractError(
            "implementation adjunct must include the current parent implementation "
            "branch head"
        )

    head_text = _git_text_at(head_sha, plan_path, repo_root=repo_root)
    if head_text != base_text:
        raise PlanContractError(
            "implementation adjunct cannot change the canonical milestone plan"
        )
    changed_paths = set(
        _run_git(
            ["diff", "--name-only", base_sha, head_sha],
            cwd=repo_root,
        ).stdout.splitlines()
    )
    if not changed_paths:
        raise PlanContractError("implementation adjunct must contain a bounded diff")
    protected_contract_paths = {
        path
        for path in changed_paths
        if re.fullmatch(
            r"docs/milestones/[^/]+/(?:plan\.(?:md|html)|proposals/.+)",
            path,
        )
        is not None
    }
    if protected_contract_paths:
        raise PlanContractError(
            "implementation adjunct cannot modify milestone contract artifacts: "
            + ", ".join(sorted(protected_contract_paths))
        )
    if pr_body is None:
        raise PlanContractError(
            "implementation adjunct validation requires the pull-request body"
        )
    state = validate_plan_text(base_text)
    validate_implementation_adjunct_body(
        pr_body,
        base_branch=base_ref,
        head_branch=head_ref,
        milestone_number=state.milestone_number,
        frontier_name=state.current.name,
    )
    return "implementation_adjunct"


def validate_review_unit_git_diff(
    *,
    base_ref: str,
    head_ref: str,
    base_sha: str,
    head_sha: str,
    pr_body: str | None = None,
    repo_root: Path = ROOT,
) -> str | None:
    if not base_ref.startswith("milestone/"):
        if "--adjunct-" in base_ref:
            raise PlanContractError(
                "implementation adjuncts cannot be used as a PR base"
            )
        adjunct_plan = _plan_at_implementation_branch(
            base_sha,
            implementation_branch=base_ref,
            repo_root=repo_root,
        )
        if adjunct_plan is None:
            return None
        declares_adjunct = _is_implementation_adjunct_branch(
            base_ref,
            head_ref,
        ) or bool(
            pr_body
            and pr_body.startswith("# HITL Implementation Adjunct")
        )
        if not declares_adjunct:
            return None
        plan_path, base_text = adjunct_plan
        return _validate_implementation_adjunct_git_diff(
            plan_path=plan_path,
            base_text=base_text,
            base_ref=base_ref,
            head_ref=head_ref,
            base_sha=base_sha,
            head_sha=head_sha,
            pr_body=pr_body,
            repo_root=repo_root,
        )
    plan_path, base_text = _plan_at_branch(
        base_sha,
        milestone_branch=base_ref,
        repo_root=repo_root,
    )
    head_text = _git_text_at(head_sha, plan_path, repo_root=repo_root)
    changed_paths = set(
        _run_git(
            ["diff", "--name-only", base_sha, head_sha],
            cwd=repo_root,
        ).stdout.splitlines()
    )
    base = validate_plan_text(base_text)
    head = validate_plan_text(head_text)
    proposal_text: str | None = None
    proposal_amendment_text: str | None = None
    if (
        _workflow_state(base.current) == "ready_for_proposal"
        and not _is_plan_revision_branch(base.milestone_number, head_ref)
    ):
        proposal_path = _frontier_proposal_path(
            base.current,
            heading="Current Frontier",
        )
        proposal_text = _git_text_at(
            head_sha,
            proposal_path,
            repo_root=repo_root,
        )
    if (
        _workflow_state(base.current) == "ready_for_implementation"
        and _workflow_state(head.current) == "proposal_amendment_in_review"
    ):
        amendment_path = _frontier_proposal_amendment_path(
            head.current,
            heading="Current Frontier",
        )
        proposal_amendment_text = _git_text_at(
            head_sha,
            amendment_path,
            repo_root=repo_root,
        )
    return validate_review_unit_transition(
        base_text,
        head_text,
        plan_path=plan_path,
        changed_paths=changed_paths,
        head_branch=head_ref,
        proposal_text=proposal_text,
        proposal_amendment_text=proposal_amendment_text,
        pr_body=pr_body,
    )


def _workflow_status_payload(plan: Path, state: PlanState) -> dict[str, Any]:
    if state.current.is_empty:
        return {
            "milestone": state.milestone_number,
            "status": state.status,
            "frontier": None,
            "workflow_state": None,
            "next_action": state.current.fields.get("revisit when"),
            "plan": str(plan),
        }
    workflow_state = _workflow_state(state.current)
    next_actions = {
        "ready_for_proposal": (
            "Hand the frozen frontier contract to a proposal author. "
            "Do not start implementation."
        ),
        "proposal_in_review": (
            "Review and finalize the proposal. Implementation remains blocked."
        ),
        "proposal_amendment_in_review": (
            "Review the additive proposal amendment. Implementation remains blocked."
        ),
        "ready_for_implementation": (
            "Hand the accepted contract to the implementer, or start an additive "
            "proposal amendment when established evidence requires correction."
            if not state.current.fields.get("paused implementation")
            else (
                "Resume the paused implementation with start-implementation "
                "(reconcile the existing PR or open a replacement on the planned "
                "branch, as recorded), or abandon if the amendment was rejected."
            )
        ),
        "implementation_in_review": (
            "Review the implementation against the accepted proposal. "
            "An eligible human implement-now discovery may use a HITL adjunct. "
            "A recorded proposal-amendment escalation may start a contract-only "
            "amendment after the implementation PR is paused or closed."
        ),
    }
    return {
        "milestone": state.milestone_number,
        "status": state.status,
        "frontier": state.current.name,
        "workflow_state": workflow_state,
        "proposal_branch": _frontier_branch(
            state.current,
            heading="Current Frontier",
            field="proposal branch",
        ),
        "implementation_branch": _frontier_branch(
            state.current,
            heading="Current Frontier",
            field="implementation branch",
        ),
        "proposal_path": _frontier_proposal_path(
            state.current,
            heading="Current Frontier",
        ),
        "accepted_proposal": state.current.fields.get("accepted proposal"),
        "proposal_amendment_branch": state.current.fields.get(
            "proposal amendment branch"
        ),
        "proposal_amendment_path": state.current.fields.get(
            "proposal amendment path"
        ),
        "accepted_proposal_amendments": state.current.fields.get(
            "accepted proposal amendments"
        ),
        "next_action": next_actions[workflow_state],
        "plan": str(plan),
        "history": [
            {"frontier": row[0], "state": row[1], "evidence": row[2]}
            for row in state.workflow_history.rows
        ],
    }


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanContractError(f"cannot load receipt {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanContractError("handoff receipt must be a JSON object")
    return _normalize_receipt(payload)


def _discover_plans() -> tuple[Path, ...]:
    return tuple(sorted(MILESTONES.glob("*/plan.md")))


def _render_docs() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "docs" / "render_markdown.py")],
        cwd=ROOT,
        check=True,
    )


def _cmd_validate(paths: list[Path]) -> int:
    selected = paths or list(_discover_plans())
    if not selected:
        raise PlanContractError("no milestone plan.md files found")
    for path in selected:
        validate_plan_path(path)
        print(f"Valid milestone plan: {path.resolve().relative_to(ROOT)}")
    return 0


def _worktree_changed_paths(*, repo_root: Path) -> set[str]:
    tracked = _run_git(
        ["diff", "--name-only"],
        cwd=repo_root,
    ).stdout.splitlines()
    untracked = _run_git(
        ["ls-files", "--others", "--exclude-standard"],
        cwd=repo_root,
    ).stdout.splitlines()
    return {path for path in (*tracked, *untracked) if path}


def complete_implementation(
    plan: Path,
    accepted_pr: int,
    *,
    repo_root: Path = ROOT,
    pr_payload: dict[str, Any] | None = None,
    render_docs: Callable[[], None] | None = None,
    push: bool = True,
) -> PlanState:
    """Advance a merged implementation using its proposal-reviewed template."""

    repo_root = repo_root.resolve()
    plan = plan.resolve()
    initial = validate_plan_path(plan)
    _validate_plan_location(plan, repo_root=repo_root)
    branch = _run_git(["branch", "--show-current"], cwd=repo_root).stdout.strip()
    if branch != initial.milestone_branch:
        raise PlanContractError(
            "complete-implementation must run on "
            f"{initial.milestone_branch!r}, currently {branch!r}"
        )
    if _run_git(["status", "--porcelain"], cwd=repo_root).stdout.strip():
        raise PlanContractError("complete-implementation requires a clean worktree")

    _run_git(
        ["fetch", "origin", initial.milestone_branch],
        cwd=repo_root,
    )
    _run_git(
        ["merge", "--ff-only", f"origin/{initial.milestone_branch}"],
        cwd=repo_root,
    )

    original = plan.read_text(encoding="utf-8")
    state = validate_plan_text(original)
    if _workflow_state(state.current) != "implementation_in_review":
        raise PlanContractError(
            "complete-implementation requires workflow state "
            "implementation_in_review"
        )

    payload = (
        pr_payload
        if pr_payload is not None
        else _fetch_pr_metadata(accepted_pr, repo_root=repo_root)
    )
    merge_commit = payload.get("mergeCommit")
    merge_oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
    if not isinstance(merge_oid, str) or re.fullmatch(r"[0-9a-f]{40}", merge_oid) is None:
        raise PlanContractError(f"PR #{accepted_pr} has no full merge commit")

    proposal_path = repo_root / _frontier_proposal_path(
        state.current,
        heading="Current Frontier",
    )
    try:
        proposal_text = proposal_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanContractError(
            f"cannot read accepted proposal {proposal_path}: {exc}"
        ) from exc
    validate_proposal_text(proposal_text)
    receipt = materialize_handoff_receipt(
        load_handoff_template(proposal_text),
        accepted_pr=accepted_pr,
        accepted_merge_commit=merge_oid,
    )
    verify_handoff_git_state(plan, state, receipt, repo_root=repo_root)
    validate_merged_pr_metadata(payload, state, receipt)
    updated = apply_handoff(original, receipt)

    renderer = render_docs or _render_docs
    html_path = plan.with_suffix(".html")
    original_html = html_path.read_bytes() if html_path.exists() else None
    committed = False
    try:
        plan.write_text(updated, encoding="utf-8")
        renderer()
        completed = validate_plan_path(plan)
        plan_relative = plan.relative_to(repo_root).as_posix()
        html_relative = html_path.relative_to(repo_root).as_posix()
        changed = _worktree_changed_paths(repo_root=repo_root)
        expected = {plan_relative, html_relative}
        if changed != expected:
            raise PlanContractError(
                "complete-implementation produced unexpected paths: "
                + ", ".join(sorted(changed ^ expected))
            )
        diff_check = _run_git(["diff", "--check"], cwd=repo_root, check=False)
        if diff_check.returncode != 0:
            detail = diff_check.stdout.strip() or diff_check.stderr.strip()
            raise PlanContractError(f"handoff diff check failed: {detail}")
        _run_git(
            ["add", "--", plan_relative, html_relative],
            cwd=repo_root,
        )
        _run_git(
            ["commit", "-m", f"Record PR {accepted_pr} milestone handoff"],
            cwd=repo_root,
        )
        committed = True
        if push:
            _run_git(
                ["push", "origin", state.milestone_branch],
                cwd=repo_root,
            )
    except Exception:
        if committed:
            raise
        if _run_git(["diff", "--cached", "--quiet"], cwd=repo_root, check=False).returncode:
            _run_git(["reset"], cwd=repo_root)
        plan.write_text(original, encoding="utf-8")
        if original_html is None:
            html_path.unlink(missing_ok=True)
        else:
            html_path.write_bytes(original_html)
        raise
    return completed


def _cmd_handoff(plan: Path, receipt_path: Path) -> int:
    plan = plan.resolve()
    receipt = _load_receipt(receipt_path)
    original = plan.read_text(encoding="utf-8")
    state = validate_plan_text(original)
    verify_handoff_git_state(plan, state, receipt)
    verify_handoff_github_state(state, receipt)
    updated = apply_handoff(original, receipt)
    try:
        plan.write_text(updated, encoding="utf-8")
        _render_docs()
    except Exception:
        plan.write_text(original, encoding="utf-8")
        _render_docs()
        raise
    print(f"Applied PR #{receipt['accepted_pr']} handoff to {plan.relative_to(ROOT)}")
    print("Review the plan diff, run tests, then commit the plan and generated HTML together.")
    return 0


def _cmd_complete_implementation(plan: Path, accepted_pr: int) -> int:
    completed = complete_implementation(plan, accepted_pr)
    print(f"Completed implementation PR #{accepted_pr}.")
    print(f"Frontier: {completed.current.name or 'None'}")
    workflow_state = _workflow_state(completed.current)
    print(f"Workflow state: {workflow_state or 'none'}")
    if workflow_state == "ready_for_proposal":
        proposal_branch = _frontier_branch(
            completed.current,
            heading="Current Frontier",
            field="proposal branch",
        )
        print(
            "Next: python3 docs/milestones/workflow.py start-proposal "
            f"--plan {plan} --branch {proposal_branch}"
        )
    return 0


def _write_plan_and_render(plan: Path, original: str, updated: str) -> None:
    try:
        plan.write_text(updated, encoding="utf-8")
        _render_docs()
    except Exception:
        plan.write_text(original, encoding="utf-8")
        _render_docs()
        raise


def _cmd_start_proposal(plan: Path, branch: str) -> int:
    plan = plan.resolve()
    state = validate_plan_path(plan)
    start_proposal_branch(plan, state, branch)
    _render_docs()
    print(f"Proposal branch started: {branch}")
    print(f"Frontier: {state.current.name}")
    print(
        "Next: author only the proposal and planning transition; "
        "implementation changes are blocked."
    )
    return 0


def _cmd_accept_proposal(plan: Path, proposal_pr: int) -> int:
    plan = plan.resolve()
    original = plan.read_text(encoding="utf-8")
    state = validate_plan_text(original)
    repo_root = ROOT.resolve()
    _validate_plan_location(plan, repo_root=repo_root)
    branch = _run_git(["branch", "--show-current"], cwd=repo_root).stdout.strip()
    if branch != state.milestone_branch:
        raise PlanContractError(
            f"proposal acceptance must run on {state.milestone_branch!r}, "
            f"currently {branch!r}"
        )
    if _run_git(["status", "--porcelain"], cwd=repo_root).stdout.strip():
        raise PlanContractError("proposal acceptance requires a clean worktree")
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(proposal_pr),
                "--json",
                (
                    "state,mergeCommit,baseRefName,headRefName,headRefOid,files,url,body"
                ),
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PlanContractError(
            "GitHub CLI `gh` is required for proposal acceptance"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise PlanContractError(f"cannot verify proposal PR on GitHub: {detail}") from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PlanContractError("GitHub CLI returned invalid proposal metadata") from exc
    if not isinstance(payload, dict):
        raise PlanContractError("GitHub CLI returned invalid proposal metadata")
    review_metadata = _fetch_pr_review_metadata(
        proposal_pr,
        repo_root=repo_root,
    )
    merge_commit, proposal_url, review_receipt = validate_merged_proposal_metadata(
        payload,
        state,
        proposal_pr=proposal_pr,
        allowed_paths=proposal_allowed_paths(plan, state),
        review_metadata=review_metadata,
    )
    ancestor = _run_git(
        ["merge-base", "--is-ancestor", merge_commit, "HEAD"],
        cwd=repo_root,
        check=False,
    )
    if ancestor.returncode != 0:
        raise PlanContractError(
            f"proposal merge commit {merge_commit} is not an ancestor of HEAD"
        )
    proposal_path = repo_root / _frontier_proposal_path(
        state.current,
        heading="Current Frontier",
    )
    proposal_text = proposal_path.read_text(encoding="utf-8")
    validate_proposal_text(proposal_text)
    validate_handoff_template_against_plan(proposal_text, original)
    updated = accept_proposal(
        original,
        proposal_pr=proposal_pr,
        merge_commit=merge_commit,
        proposal_url=proposal_url,
        review_receipt=review_receipt,
    )
    _write_plan_and_render(plan, original, updated)
    print(f"Accepted proposal PR #{proposal_pr} for {state.current.name}.")
    print("Workflow state: ready_for_implementation")
    print(
        "Next: hand the accepted proposal to the implementer, then use "
        "start-implementation."
    )
    return 0


def _cmd_start_proposal_amendment(
    plan: Path,
    branch: str,
    amendment_path: str,
    *,
    implementation_pr: int | None = None,
    implementation_url: str | None = None,
    implementation_head: str | None = None,
    implementation_disposition: str | None = None,
    resume_policy: str | None = None,
    escalation_receipt: str | None = None,
) -> int:
    plan = plan.resolve()
    state = validate_plan_path(plan)
    start_proposal_amendment_branch(
        plan,
        state,
        branch,
        amendment_path,
        implementation_pr=implementation_pr,
        implementation_url=implementation_url,
        implementation_head=implementation_head,
        implementation_disposition=implementation_disposition,
        resume_policy=resume_policy,
        escalation_receipt=escalation_receipt,
    )
    _render_docs()
    print(f"Proposal amendment branch started: {branch}")
    print(f"Frontier: {state.current.name}")
    print(
        "Next: author only the additive amendment and planning transition; "
        "the accepted proposal remains frozen and any paused implementation "
        "must not merge until the amendment is accepted or abandoned."
    )
    return 0


def _cmd_abandon_proposal_amendment(plan: Path, reason: str) -> int:
    plan = plan.resolve()
    state = validate_plan_path(plan)
    abandon_proposal_amendment(plan, state, reason=reason)
    _render_docs()
    print(f"Abandoned proposal amendment for {state.current.name}.")
    print(f"Reason: {reason}")
    return 0


def _cmd_accept_proposal_amendment(plan: Path, amendment_pr: int) -> int:
    plan = plan.resolve()
    original = plan.read_text(encoding="utf-8")
    state = validate_plan_text(original)
    repo_root = ROOT.resolve()
    _validate_plan_location(plan, repo_root=repo_root)
    branch = _run_git(["branch", "--show-current"], cwd=repo_root).stdout.strip()
    if branch != state.milestone_branch:
        raise PlanContractError(
            "proposal amendment acceptance must run on "
            f"{state.milestone_branch!r}, currently {branch!r}"
        )
    if _run_git(["status", "--porcelain"], cwd=repo_root).stdout.strip():
        raise PlanContractError(
            "proposal amendment acceptance requires a clean worktree"
        )
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(amendment_pr),
                "--json",
                (
                    "state,mergeCommit,baseRefName,headRefName,headRefOid,files,url,body"
                ),
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise PlanContractError(
            "GitHub CLI `gh` is required for proposal amendment acceptance"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise PlanContractError(
            f"cannot verify proposal amendment PR on GitHub: {detail}"
        ) from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PlanContractError(
            "GitHub CLI returned invalid proposal amendment metadata"
        ) from exc
    if not isinstance(payload, dict):
        raise PlanContractError(
            "GitHub CLI returned invalid proposal amendment metadata"
        )
    review_metadata = _fetch_pr_review_metadata(
        amendment_pr,
        repo_root=repo_root,
    )
    merge_commit, amendment_url, review_receipt = (
        validate_merged_proposal_amendment_metadata(
            payload,
            state,
            amendment_pr=amendment_pr,
            allowed_paths=proposal_amendment_allowed_paths(plan, state),
            review_metadata=review_metadata,
        )
    )
    ancestor = _run_git(
        ["merge-base", "--is-ancestor", merge_commit, "HEAD"],
        cwd=repo_root,
        check=False,
    )
    if ancestor.returncode != 0:
        raise PlanContractError(
            f"proposal amendment merge commit {merge_commit} is not an ancestor of HEAD"
        )
    amendment_path = repo_root / _frontier_proposal_amendment_path(
        state.current,
        heading="Current Frontier",
    )
    validate_proposal_amendment_text(amendment_path.read_text(encoding="utf-8"))
    updated = accept_proposal_amendment(
        original,
        amendment_pr=amendment_pr,
        merge_commit=merge_commit,
        amendment_url=amendment_url,
        review_receipt=review_receipt,
    )
    _write_plan_and_render(plan, original, updated)
    print(f"Accepted proposal amendment PR #{amendment_pr} for {state.current.name}.")
    print("Workflow state: ready_for_implementation")
    print(
        "Next: hand the accepted proposal plus amendments to the implementer, "
        "then use start-implementation."
    )
    return 0


def _cmd_start_implementation(plan: Path, branch: str) -> int:
    plan = plan.resolve()
    state = validate_plan_path(plan)
    start_implementation_branch(plan, state, branch)
    _render_docs()
    print(f"Implementation branch started: {branch}")
    print(f"Frontier: {state.current.name}")
    print("Accepted proposal: " + state.current.fields["accepted proposal"])
    if state.current.fields.get("accepted proposal amendments"):
        print(
            "Accepted proposal amendments: "
            + state.current.fields["accepted proposal amendments"]
        )
    print(
        "Next: implement only the accepted proposal and amendments, then open "
        "the implementation PR."
    )
    return 0


def _cmd_status(plan: Path, *, as_json: bool) -> int:
    plan = plan.resolve()
    state = validate_plan_path(plan)
    payload = _workflow_status_payload(plan.relative_to(ROOT), state)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"Milestone {payload['milestone']}: {payload['status']}")
    print(f"Frontier: {payload['frontier'] or 'None'}")
    print(f"Workflow state: {payload['workflow_state'] or 'none'}")
    if payload.get("proposal_path"):
        print(f"Proposal: {payload['proposal_path']}")
    if payload.get("accepted_proposal"):
        print(f"Accepted proposal: {payload['accepted_proposal']}")
    if payload.get("accepted_proposal_amendments"):
        print(
            "Accepted proposal amendments: "
            + payload["accepted_proposal_amendments"]
        )
    print(f"Next: {payload['next_action']}")
    return 0


def _pull_request_body_from_event(event_path: Path | None) -> str | None:
    if event_path is None:
        return None
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanContractError(
            f"cannot load pull-request event {event_path}: {exc}"
        ) from exc
    pull_request = payload.get("pull_request") if isinstance(payload, dict) else None
    if not isinstance(pull_request, dict):
        raise PlanContractError("event payload does not contain pull_request metadata")
    body = pull_request.get("body")
    if body is None:
        return ""
    if not isinstance(body, str):
        raise PlanContractError("pull_request.body must be text")
    return body


def _pull_request_body_from_file(body_path: Path | None) -> str | None:
    if body_path is None:
        return None
    try:
        return body_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanContractError(
            f"cannot load pull-request body {body_path}: {exc}"
        ) from exc


def _cmd_validate_pr(
    *,
    base_ref: str,
    head_ref: str,
    base_sha: str,
    head_sha: str,
    event_path: Path | None,
    body_path: Path | None,
) -> int:
    pr_body = (
        _pull_request_body_from_event(event_path)
        if event_path is not None
        else _pull_request_body_from_file(body_path)
    )
    transition = validate_review_unit_git_diff(
        base_ref=base_ref,
        head_ref=head_ref,
        base_sha=base_sha,
        head_sha=head_sha,
        pr_body=pr_body,
    )
    if transition is None:
        print(f"PR targets {base_ref}; milestone review-unit gate not applicable.")
    else:
        print(f"Valid {transition} PR transition into {base_ref}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate milestone plans and enforce proposal-before-implementation "
            "frontier handoffs."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate",
        help="validate one or all canonical milestone plans",
    )
    validate_parser.add_argument("plans", nargs="*", type=Path)

    handoff_parser = subparsers.add_parser(
        "handoff",
        help="apply a post-merge frontier handoff from a JSON receipt",
    )
    handoff_parser.add_argument("--plan", required=True, type=Path)
    handoff_parser.add_argument("--receipt", required=True, type=Path)

    complete_parser = subparsers.add_parser(
        "complete-implementation",
        help="finish a merged implementation from its reviewed handoff template",
    )
    complete_parser.add_argument("--plan", required=True, type=Path)
    complete_parser.add_argument("--pr", required=True, type=int)

    status_parser = subparsers.add_parser(
        "status",
        help="show the current workflow state and next handoff",
    )
    status_parser.add_argument("--plan", required=True, type=Path)
    status_parser.add_argument("--json", action="store_true")

    proposal_start_parser = subparsers.add_parser(
        "start-proposal",
        help="create the proposal-only branch for a ready frontier",
    )
    proposal_start_parser.add_argument("--plan", required=True, type=Path)
    proposal_start_parser.add_argument("--branch", required=True)

    proposal_accept_parser = subparsers.add_parser(
        "accept-proposal",
        help="record a merged proposal PR and unblock implementation",
    )
    proposal_accept_parser.add_argument("--plan", required=True, type=Path)
    proposal_accept_parser.add_argument("--pr", required=True, type=int)

    amendment_start_parser = subparsers.add_parser(
        "start-proposal-amendment",
        help="create an additive amendment branch for an accepted proposal",
    )
    amendment_start_parser.add_argument("--plan", required=True, type=Path)
    amendment_start_parser.add_argument("--branch", required=True)
    amendment_start_parser.add_argument("--path", required=True)
    amendment_start_parser.add_argument("--implementation-pr", type=int)
    amendment_start_parser.add_argument("--implementation-url")
    amendment_start_parser.add_argument("--implementation-head")
    amendment_start_parser.add_argument(
        "--implementation-disposition",
        choices=("paused", "closed"),
    )
    amendment_start_parser.add_argument(
        "--resume-policy",
        choices=("reconcile", "replace"),
    )
    amendment_start_parser.add_argument("--escalation-receipt")

    amendment_abandon_parser = subparsers.add_parser(
        "abandon-proposal-amendment",
        help="reject or abandon an in-review amendment and restore the prior state",
    )
    amendment_abandon_parser.add_argument("--plan", required=True, type=Path)
    amendment_abandon_parser.add_argument("--reason", required=True)

    amendment_accept_parser = subparsers.add_parser(
        "accept-proposal-amendment",
        help="record a merged proposal amendment and restore implementation readiness",
    )
    amendment_accept_parser.add_argument("--plan", required=True, type=Path)
    amendment_accept_parser.add_argument("--pr", required=True, type=int)

    implementation_start_parser = subparsers.add_parser(
        "start-implementation",
        help="create the implementation branch after proposal acceptance",
    )
    implementation_start_parser.add_argument("--plan", required=True, type=Path)
    implementation_start_parser.add_argument("--branch", required=True)

    validate_pr_parser = subparsers.add_parser(
        "validate-pr",
        help=(
            "validate a proposal, amendment, implementation, or HITL adjunct PR"
        ),
    )
    validate_pr_parser.add_argument("--base-ref", required=True)
    validate_pr_parser.add_argument("--head-ref", required=True)
    validate_pr_parser.add_argument("--base-sha", required=True)
    validate_pr_parser.add_argument("--head-sha", required=True)
    body_source = validate_pr_parser.add_mutually_exclusive_group()
    body_source.add_argument(
        "--event-path",
        type=Path,
        help=(
            "GitHub pull_request event JSON used to validate PR-body receipts "
            "and adjunct metadata"
        ),
    )
    body_source.add_argument(
        "--pr-body-file",
        type=Path,
        help="local Markdown PR body used to validate review metadata",
    )

    subparsers.add_parser(
        "receipt-example",
        help="print the machine-readable handoff receipt shape",
    )

    args = parser.parse_args()
    try:
        if args.command == "validate":
            return _cmd_validate(args.plans)
        if args.command == "receipt-example":
            print(json.dumps(EXAMPLE_RECEIPT, indent=2, sort_keys=True))
            return 0
        if args.command == "status":
            return _cmd_status(args.plan, as_json=args.json)
        if args.command == "start-proposal":
            return _cmd_start_proposal(args.plan, args.branch)
        if args.command == "accept-proposal":
            return _cmd_accept_proposal(args.plan, args.pr)
        if args.command == "start-proposal-amendment":
            return _cmd_start_proposal_amendment(
                args.plan,
                args.branch,
                args.path,
                implementation_pr=args.implementation_pr,
                implementation_url=args.implementation_url,
                implementation_head=args.implementation_head,
                implementation_disposition=args.implementation_disposition,
                resume_policy=args.resume_policy,
                escalation_receipt=args.escalation_receipt,
            )
        if args.command == "abandon-proposal-amendment":
            return _cmd_abandon_proposal_amendment(args.plan, args.reason)
        if args.command == "accept-proposal-amendment":
            return _cmd_accept_proposal_amendment(args.plan, args.pr)
        if args.command == "start-implementation":
            return _cmd_start_implementation(args.plan, args.branch)
        if args.command == "complete-implementation":
            return _cmd_complete_implementation(args.plan, args.pr)
        if args.command == "validate-pr":
            return _cmd_validate_pr(
                base_ref=args.base_ref,
                head_ref=args.head_ref,
                base_sha=args.base_sha,
                head_sha=args.head_sha,
                event_path=args.event_path,
                body_path=args.pr_body_file,
            )
        return _cmd_handoff(args.plan, args.receipt)
    except (OSError, PlanContractError, subprocess.CalledProcessError) as exc:
        print(f"Milestone workflow error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
