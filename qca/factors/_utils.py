"""Shared helpers for deterministic static factor measurements."""

from __future__ import annotations

from typing import Any


def _norm(path: str) -> str:
    value = str(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value or "."


def _sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        findings,
        key=lambda item: (
            str(item.get("path", "")),
            int(item.get("line", 0)),
            str(item.get("kind", "")),
            str(item.get("message", "")),
        ),
    )
