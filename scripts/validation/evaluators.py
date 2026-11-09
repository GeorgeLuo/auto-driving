"""Repository checks as QCA evaluators: Ruff, basedpyright, import contracts and retired vocabulary.

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

CONTRACTS = Path(__file__).with_name("import_contracts.py")
METRIC = re.compile(r"\((\d+) > (\d+)\)")
RETIRED = "snap" + "shot"  # Spelled apart so this file does not report itself.
RETIRED_EXEMPT = ("docs/deprecated",)


def ruff(tree: Path, paths: list[str]) -> list[Finding]:
    completed = _run(tree, "-m", "ruff", "check", "--no-cache", "--force-exclude", "--output-format", "json", *paths)
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


def basedpyright(tree: Path, _paths: list[str]) -> list[Finding]:
    """Type-check the project its configuration includes."""

    completed = _run(tree, "-m", "basedpyright", "--pythonpath", sys.executable, "--outputjson")
    return basedpyright_findings(json.loads(completed.stdout)["generalDiagnostics"], tree)


def basedpyright_findings(diagnostics: list[dict], tree: Path) -> list[Finding]:
    """Errors block; warnings and information are review questions."""

    findings = []
    for item in diagnostics:
        span = item["range"]
        findings.append(finding(
            "basedpyright",
            item.get("rule", "diagnostic"),
            location(_relative(item["file"], tree), span["start"]["line"] + 1, span["end"]["line"] + 1),
            f"Does this indicate a problem: {item['message']}?",
            status="blocking" if item["severity"] == "error" else "review",
            evidence={"message": item["message"], "severity": item["severity"]},
        ))
    return findings


def import_contracts(tree: Path, _paths: list[str]) -> list[Finding]:
    """Check the import contracts in the project's configuration."""

    # -P keeps this checkout's scripts off the path; the contracts judge the tree's own packages.
    completed = _run(tree, "-P", str(CONTRACTS), reports=False)
    return import_contract_findings(json.loads(completed.stdout), tree)


def import_contract_findings(broken: list[dict], tree: Path) -> list[Finding]:
    """A broken contract is located where its import chain starts; later links are related."""

    findings = []
    for item in broken:
        places = [location(_module_path(edge["importer"], tree), edge["line"]) for edge in item["chain"]]
        findings.append(finding(
            "import-linter",
            item["contract"],
            places[0],
            f"Should this import exist? It breaks the contract: {item['name']}.",
            status="blocking",
            evidence={
                "contract": item["name"],
                "chain": [f"{edge['importer']} -> {edge['imported']}" for edge in item["chain"]],
                **({"guidance": item["guidance"]} if item.get("guidance") else {}),
            },
            related=places[1:],
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


def _run(tree: Path, *args: str, reports: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Python in ``tree``; a tool that ``reports`` diagnostics exits 1 for them, so only higher codes fail."""

    completed = subprocess.run([sys.executable, *args], cwd=tree, capture_output=True, text=True, check=False)
    if completed.returncode > (1 if reports else 0):
        output = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(output or f"{' '.join(args[:2])} exited {completed.returncode}")
    return completed


def _module_path(module: str, tree: Path) -> str:
    path = Path(*module.split("."))
    return (path.with_suffix(".py") if (tree / path.with_suffix(".py")).is_file() else path / "__init__.py").as_posix()


def _relative(filename: str, tree: Path) -> str:
    return Path(filename).resolve().relative_to(tree.resolve()).as_posix()
