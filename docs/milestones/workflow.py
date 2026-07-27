#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
MILESTONES = ROOT / "docs" / "milestones"
ALLOWED_MILESTONE_STATUSES = {"Active", "Blocked", "pre-plan", "closed"}
ALLOWED_CRITERION_STATUSES = {"Unmet", "Partial", "Met", "Blocked"}
FRONTIER_FIELDS = (
    "branch",
    "review kind",
    "review question",
    "acceptance owner",
    "exit criteria affected",
    "prerequisite",
)
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
    risks: MarkdownTable


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


def _require_frontier_fields(frontier: Frontier, *, heading: str) -> None:
    if frontier.is_empty:
        for field in ("reason", "revisit when"):
            if not frontier.fields.get(field):
                raise PlanContractError(f"{heading} empty state is missing {field!r}")
        return
    for field in FRONTIER_FIELDS:
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


def _frontier_branch(frontier: Frontier, *, heading: str) -> str:
    raw_value = frontier.fields["branch"]
    quoted = re.search(r"`([^`]+)`", raw_value)
    branch = quoted.group(1) if quoted else raw_value.split(maxsplit=1)[0]
    if re.fullmatch(r"[A-Za-z0-9._/-]+", branch) is None:
        raise PlanContractError(f"{heading} has an invalid planned branch")
    return branch


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
    _require_frontier_fields(current, heading="Current Frontier")
    _require_frontier_fields(next_frontier, heading="Next-Frontier Candidate")
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
        current_branch = _frontier_branch(current, heading="Current Frontier")
        transition_exception = current.fields.get("transition exception")
        if (
            not current_branch.startswith(expected_review_prefix)
            and not (
                baseline_value
                and cutover_value
                and transition_exception
            )
        ):
            raise PlanContractError(
                f"Current Frontier branch must start with {expected_review_prefix!r}"
            )
        if not current_branch.startswith(expected_review_prefix):
            current_pr_match = re.search(r"#(\d+)", current.fields.get("pr", ""))
            if (
                current_pr_match is None
                or int(current_pr_match.group(1)) not in grandfathered_prs
            ):
                raise PlanContractError(
                    "Current Frontier transition exception must identify a "
                    "Grandfathered PR"
                )
    if not next_frontier.is_empty:
        next_branch = _frontier_branch(
            next_frontier,
            heading="Next-Frontier Candidate",
        )
        if not next_branch.startswith(expected_review_prefix):
            raise PlanContractError(
                "Next-Frontier Candidate branch must start with "
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
        risks=risks,
    )


def validate_plan_path(path: Path) -> PlanState:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanContractError(f"cannot read {path}: {exc}") from exc
    return validate_plan_text(text)


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
        ("branch", "Branch"),
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


def _current_pr_number(current: Frontier) -> int:
    value = current.fields.get("pr", "")
    match = re.search(r"#(\d+)", value)
    if match is None:
        raise PlanContractError("Current Frontier must identify a PR number")
    return int(match.group(1))


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
    if _current_pr_number(state.current) != accepted_pr:
        raise PlanContractError(
            f"receipt PR #{accepted_pr} does not match the current frontier"
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

    outcome = receipt["outcome"]
    if outcome == "advance":
        if state.next_frontier.is_empty:
            raise PlanContractError(
                "advance requires a reviewed next-frontier candidate to promote"
            )
        new_current = state.next_frontier
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
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(receipt["accepted_pr"]),
                "--json",
                "state,mergeCommit,baseRefName",
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
        raise PlanContractError(f"cannot verify accepted PR on GitHub: {detail}") from exc
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PlanContractError("GitHub CLI returned invalid PR metadata") from exc
    if not isinstance(payload, dict):
        raise PlanContractError("GitHub CLI returned invalid PR metadata")
    validate_merged_pr_metadata(payload, state, receipt)


def start_current_frontier_branch(
    plan: Path,
    state: PlanState,
    requested_branch: str,
    *,
    repo_root: Path = ROOT,
) -> None:
    repo_root = repo_root.resolve()
    _validate_plan_location(plan, repo_root=repo_root)
    if state.status != "Active" or state.current.is_empty:
        raise PlanContractError("branch start requires an active current frontier")
    if state.current.fields.get("pr"):
        raise PlanContractError(
            "current frontier already has a PR; complete its handoff before starting"
        )
    planned_value = state.current.fields.get("branch", "")
    planned_match = re.search(r"`?(m\d{3}/[A-Za-z0-9._/-]+)`?", planned_value)
    if planned_match is None:
        raise PlanContractError("current frontier must contain a planned review-unit branch")
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


def _cmd_start(plan: Path, branch: str) -> int:
    plan = plan.resolve()
    state = validate_plan_path(plan)
    start_current_frontier_branch(plan, state, branch)
    print(f"Started {branch} for current frontier {state.current.name}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate milestone plans and apply ordered frontier handoffs.",
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

    start_parser = subparsers.add_parser(
        "start",
        help="create the current frontier branch after a committed handoff",
    )
    start_parser.add_argument("--plan", required=True, type=Path)
    start_parser.add_argument("--branch", required=True)

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
        if args.command == "start":
            return _cmd_start(args.plan, args.branch)
        return _cmd_handoff(args.plan, args.receipt)
    except (OSError, PlanContractError, subprocess.CalledProcessError) as exc:
        print(f"Milestone workflow error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
