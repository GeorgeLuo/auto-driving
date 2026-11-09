"""Repository checks as QCA evaluators: Ruff, basedpyright and retired vocabulary.

Each evaluator runs on a directory and the paths to inspect there, and returns
findings in the shape of ``qca.evaluation``.  The context form (a change,
files or text) is handled by the caller.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from qca.evaluation import Finding, finding, in_scope, location

METRIC = re.compile(r"\((\d+) > (\d+)\)")
RETIRED = "snap" + "shot"  # Spelled apart so this file does not report itself.
RETIRED_EXEMPT = ("docs/deprecated",)


def ruff(tree: Path, paths: list[str]) -> list[Finding]:
    completed = _run(tree, "ruff", "check", "--no-cache", "--force-exclude", "--output-format", "json", *paths)
    return ruff_findings(json.loads(completed.stdout), tree)


def ruff_findings(diagnostics: list[dict], tree: Path) -> list[Finding]:
    """Ruff diagnostics block; a metric diagnostic such as ``(13 > 10)`` carries its measure."""

    findings = []
    for item in diagnostics:
        metric = METRIC.search(item["message"])
        findings.append(finding(
            "ruff",
            item["code"] or "invalid-syntax",
            location(_relative(item["filename"], tree), item["location"]["row"], item["end_location"]["row"]),
            f"Does this indicate a problem: {item['message']}?",
            status="blocking",
            evidence={"message": item["message"]},
            measure={"value": int(metric[1]), "limit": int(metric[2])} if metric else None,
        ))
    return findings


def basedpyright(tree: Path, paths: list[str]) -> list[Finding]:
    """Type-check the configured project, reporting diagnostics within ``paths``."""

    completed = _run(tree, "basedpyright", "--pythonpath", sys.executable, "--outputjson")
    return basedpyright_findings(json.loads(completed.stdout)["generalDiagnostics"], tree, paths)


def basedpyright_findings(diagnostics: list[dict], tree: Path, paths: list[str]) -> list[Finding]:
    """Errors block; warnings and information are review questions."""

    findings = []
    for item in diagnostics:
        path = _relative(item["file"], tree)
        if not in_scope(path, paths):
            continue
        span = item["range"]
        findings.append(finding(
            "basedpyright",
            item.get("rule", "diagnostic"),
            location(path, span["start"]["line"] + 1, span["end"]["line"] + 1),
            f"Does this indicate a problem: {item['message']}?",
            status="blocking" if item["severity"] == "error" else "review",
            evidence={"message": item["message"], "severity": item["severity"]},
        ))
    return findings


def vocabulary(tree: Path, paths: list[str]) -> list[Finding]:
    """Retired vocabulary in any text file outside ``docs/deprecated``."""

    findings = []
    for path in paths:
        file = tree / path
        if in_scope(path, RETIRED_EXEMPT) or not file.is_file():
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # Binary files have no vocabulary.
        for number, line in enumerate(text.splitlines(), start=1):
            findings.extend(
                finding(
                    "vocabulary",
                    "retired-term",
                    location(path, number),
                    f"Should {match.group()!r} use the current vocabulary?",
                    status="blocking",
                    evidence={"term": match.group()},
                )
                for match in re.finditer(RETIRED, line, re.IGNORECASE)
            )
    return findings


def _run(tree: Path, module: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a tool that exits 1 when it reports diagnostics and otherwise fails above 1."""

    completed = subprocess.run(
        [sys.executable, "-m", module, *args], cwd=tree, capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        output = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(output or f"{module} exited {completed.returncode}")
    return completed


def _relative(filename: str, tree: Path) -> str:
    return Path(filename).resolve().relative_to(tree.resolve()).as_posix()
