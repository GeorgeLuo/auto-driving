#!/usr/bin/env python3
"""Print a concise post-capture report for an M006 evidence packet."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from verify_packet import PAGE, RECORD, PacketError, check_html, load


LABELS = {
    "passed": "PASS",
    "failed": "FAIL",
    "blocked": "BLOCKED",
    "review": "REVIEW",
}


def _detail(item: dict[str, Any]) -> str:
    for field in ("evidence", "path", "reason", "observation"):
        value = item.get(field)
        if value not in (None, "", []):
            return str(value)
    return ""


def _rows(payload: dict[str, Any]) -> Iterable[tuple[str, str, str]]:
    yield "PASS", "packet", f"status={payload['status']} shape and paths verified"
    for section in ("case_outcomes", "environment_packages", "interval_coverage", "reviews"):
        for name, item in payload[section].items():
            yield LABELS[item["status"]], f"{section}.{name}", _detail(item)
    for environment, package in payload["environment_packages"].items():
        for index, artifact in enumerate(package["artifacts"]):
            path = artifact if isinstance(artifact, str) else artifact["path"]
            yield "PASS", f"environment_packages.{environment}.artifact[{index}]", path


def _print_row(label: str, name: str, detail: str) -> None:
    suffix = f"  {detail}" if detail else ""
    print(f"{label:<7} {name}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--html", type=Path)
    args = parser.parse_args()

    try:
        payload, _ = load(args.record)
        if payload["status"] == "incomplete":
            raise PacketError("post-capture report requires status captured or accepted")
        page = args.html or args.record.with_name(PAGE.name)
        check_html(args.record, page)
    except (OSError, ValueError, PacketError) as exc:
        _print_row("FAIL", "packet", str(exc))
        print("SUMMARY PASS=0 FAIL=1 BLOCKED=0 REVIEW=0")
        return 1

    rows = list(_rows(payload))
    rows.append(("PASS", "result.html", f"derived from {args.record.name}"))
    counts = Counter(label for label, _, _ in rows)
    for row in rows:
        _print_row(*row)
    print(
        "SUMMARY "
        + " ".join(
            f"{label}={counts[label]}" for label in ("PASS", "FAIL", "BLOCKED", "REVIEW")
        )
    )
    return 1 if counts["FAIL"] or counts["BLOCKED"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
