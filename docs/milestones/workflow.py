#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
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
    "implementation_in_review",
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
            field = _normalize_field(field_match.group(1))
            value = field_match.group(2).strip()
            if field == "workflow state":
                value = value.strip("`")
            fields[field] = value

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
    raw_value = frontier.fields["proposal path"]
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
            f"{heading} proposal path must be "
            "docs/milestones/<number>-<slug>/proposals/<name>.md"
        )
    return path


def _accepted_proposal(frontier: Frontier, *, heading: str) -> tuple[int, str] | None:
    raw_value = frontier.fields.get("accepted proposal")
    if not raw_value:
        return None
    pr_match = re.search(r"#(\d+)", raw_value)
    sha_match = re.search(r"`([0-9a-f]{7,40})`", raw_value)
    if pr_match is None or sha_match is None:
        raise PlanContractError(
            f"{heading} accepted proposal must identify a PR and merge commit"
        )
    return int(pr_match.group(1)), sha_match.group(1)


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
        if workflow_state in {"ready_for_proposal", "proposal_in_review"}:
            if accepted_proposal is not None:
                raise PlanContractError(
                    f"{workflow_state} cannot already identify an accepted proposal"
                )
        elif accepted_proposal is None:
            raise PlanContractError(
                f"{workflow_state} requires an accepted proposal PR and merge commit"
            )
        if workflow_state.startswith("ready_for_") and current.fields.get("pr"):
            raise PlanContractError(
                f"{workflow_state} cannot identify an active review PR"
            )
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
    expected_transition = {
        "ready_for_proposal": "proposal_in_review",
        "proposal_in_review": "ready_for_implementation",
        "ready_for_implementation": "implementation_in_review",
        "implementation_in_review": "accepted",
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
                    and expected_transition.get(prior_state) != history_state
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
                "state,mergeCommit,baseRefName,headRefName",
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
    if opened_branch_field is not None:
        opened_branch = _frontier_branch(
            state.current,
            heading="Current Frontier",
            field=opened_branch_field,
        )
        fields[opened_branch_field] = f"`{opened_branch}`"
    if accepted_proposal is not None:
        fields["accepted proposal"] = accepted_proposal
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
    return _start_frontier_branch(
        plan,
        state,
        requested_branch,
        branch_field="implementation branch",
        expected_state="ready_for_implementation",
        new_state="implementation_in_review",
        repo_root=repo_root,
    )


def validate_proposal_text(text: str) -> None:
    if not text.startswith("# Proposal:"):
        raise PlanContractError("proposal must start with '# Proposal:'")
    for heading in PROPOSAL_REQUIRED_HEADINGS:
        if heading not in text:
            raise PlanContractError(f"proposal is missing {heading}")
    load_handoff_template(text)


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


def validate_merged_proposal_metadata(
    payload: dict[str, Any],
    state: PlanState,
    *,
    proposal_pr: int,
    allowed_paths: set[str],
) -> tuple[str, str]:
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
    return merge_oid, str(payload.get("url") or f"PR #{proposal_pr}")


def accept_proposal(
    text: str,
    *,
    proposal_pr: int,
    merge_commit: str,
    proposal_url: str,
) -> str:
    return _replace_current_frontier_state(
        text,
        expected_state="proposal_in_review",
        new_state="ready_for_implementation",
        evidence=f"Proposal PR #{proposal_pr} accepted at {merge_commit}.",
        accepted_proposal=(
            f"[#{proposal_pr}]({proposal_url}) at `{merge_commit}`"
        ),
    )


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
        validate_proposal_text(proposal_text)
        validate_handoff_template_against_plan(proposal_text, head_text)
        transition_kind = "proposal"
    elif base_state == "ready_for_implementation":
        if head_state != "implementation_in_review":
            raise PlanContractError(
                "implementation PR must transition ready_for_implementation "
                "to implementation_in_review"
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
        if proposal_path in changed_paths:
            raise PlanContractError(
                "implementation PR cannot modify the accepted proposal"
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


def validate_review_unit_git_diff(
    *,
    base_ref: str,
    head_ref: str,
    base_sha: str,
    head_sha: str,
    repo_root: Path = ROOT,
) -> str | None:
    if not base_ref.startswith("milestone/"):
        return None
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
    proposal_text: str | None = None
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
    return validate_review_unit_transition(
        base_text,
        head_text,
        plan_path=plan_path,
        changed_paths=changed_paths,
        head_branch=head_ref,
        proposal_text=proposal_text,
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
        "ready_for_implementation": (
            "Hand the accepted proposal to the implementer."
        ),
        "implementation_in_review": (
            "Review the implementation against the accepted proposal."
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
                "state,mergeCommit,baseRefName,headRefName,files,url",
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
    merge_commit, proposal_url = validate_merged_proposal_metadata(
        payload,
        state,
        proposal_pr=proposal_pr,
        allowed_paths=proposal_allowed_paths(plan, state),
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
    )
    _write_plan_and_render(plan, original, updated)
    print(f"Accepted proposal PR #{proposal_pr} for {state.current.name}.")
    print("Workflow state: ready_for_implementation")
    print(
        "Next: hand the accepted proposal to the implementer, then use "
        "start-implementation."
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
    print("Next: implement only the accepted proposal, then open the implementation PR.")
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
    print(f"Next: {payload['next_action']}")
    return 0


def _cmd_validate_pr(
    *,
    base_ref: str,
    head_ref: str,
    base_sha: str,
    head_sha: str,
) -> int:
    transition = validate_review_unit_git_diff(
        base_ref=base_ref,
        head_ref=head_ref,
        base_sha=base_sha,
        head_sha=head_sha,
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

    implementation_start_parser = subparsers.add_parser(
        "start-implementation",
        help="create the implementation branch after proposal acceptance",
    )
    implementation_start_parser.add_argument("--plan", required=True, type=Path)
    implementation_start_parser.add_argument("--branch", required=True)

    validate_pr_parser = subparsers.add_parser(
        "validate-pr",
        help="validate a proposal or implementation PR transition",
    )
    validate_pr_parser.add_argument("--base-ref", required=True)
    validate_pr_parser.add_argument("--head-ref", required=True)
    validate_pr_parser.add_argument("--base-sha", required=True)
    validate_pr_parser.add_argument("--head-sha", required=True)

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
            )
        return _cmd_handoff(args.plan, args.receipt)
    except (OSError, PlanContractError, subprocess.CalledProcessError) as exc:
        print(f"Milestone workflow error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
