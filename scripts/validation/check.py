"""Check maintained Python code without requiring existing debt to be repaired."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


ROOT = Path(__file__).resolve().parents[2]
RUFF_BASELINE = ROOT / ".ruff-baseline.json"
PATHS = ("autonomy", "cli", "implementations", "qca", "scripts/validation", "tests")
METRIC = re.compile(r"\((\d+) > (\d+)\)")
METRIC_RULES = {"C901", "PLR0912", "PLR0914", "PLR0915", "PLR0916", "PLR0917", "PLR1702"}


class Location(TypedDict):
    row: int
    column: int


class RuffDiagnostic(TypedDict):
    filename: str
    code: str
    message: str
    location: Location


class BaselineEntry(TypedDict):
    key: list[str]
    limits: list[int]


@dataclass(frozen=True)
class Finding:
    key: tuple[str, ...]
    magnitude: int
    diagnostic: RuffDiagnostic


def scopes(source: str) -> list[tuple[int, int, str]]:
    """Locate diagnostics by their owning symbol, independent of line shifts."""
    found: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, parent: str) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            parent = f"{parent}.{node.name}" if parent else node.name
            found.append((node.lineno, node.end_lineno or node.lineno, parent))
        for child in ast.iter_child_nodes(node):
            visit(child, parent)

    try:
        visit(ast.parse(source), "")
    except SyntaxError:
        pass  # Ruff's syntax diagnostic still fails the check.
    return found


def findings(diagnostics: list[RuffDiagnostic], root: Path) -> list[Finding]:
    sources = {item["filename"]: Path(item["filename"]).read_text(encoding="utf-8") for item in diagnostics}
    owners = {path: scopes(source) for path, source in sources.items()}
    result = []
    for item in diagnostics:
        path, line = item["filename"], item["location"]["row"]
        containing = [name for start, end, name in owners[path] if start <= line <= end]
        owner = containing[-1] if containing else "<module>"
        message = item["message"]
        metric = METRIC.search(message) if item["code"] in METRIC_RULES else None
        magnitude = int(metric[1]) if metric else 0
        # A smaller still-over-limit function is an improvement, not a new error.
        message = METRIC.sub(r"(count > \2)", message) if metric else message
        source = "" if metric else "".join(sources[path].splitlines()[line - 1:line]).strip()
        key = (Path(path).resolve().relative_to(root.resolve()).as_posix(), item["code"], owner, message, source)
        result.append(Finding(key, magnitude, item))
    return result


def baseline_entries(items: list[Finding]) -> list[BaselineEntry]:
    grouped: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for item in items:
        grouped[item.key].append(item.magnitude)
    return [{"key": list(key), "limits": sorted(values)} for key, values in sorted(grouped.items())]


def compare_baseline(items: list[Finding], entries: list[BaselineEntry]) -> tuple[list[Finding], list[BaselineEntry]]:
    """Return new findings and only the debt still present, at its current size."""
    remaining = {tuple(entry["key"]): sorted(entry["limits"]) for entry in entries}
    added = []
    retained = []
    for item in sorted(items, key=lambda item: item.magnitude):
        limits = remaining.get(item.key, [])
        index = next((i for i, limit in enumerate(limits) if limit >= item.magnitude), None)
        if index is None:
            added.append(item)
        else:
            limits.pop(index)
            retained.append(item)
    return added, baseline_entries(retained)


def ruff_findings() -> list[Finding]:
    completed = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format", "json", *PATHS],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise ValueError(completed.stderr or "Ruff failed without diagnostics")
    diagnostics: list[RuffDiagnostic] = json.loads(completed.stdout)
    return findings(diagnostics, ROOT)


def write_entries(entries: list[BaselineEntry]) -> None:
    RUFF_BASELINE.write_text(
        json.dumps({"version": 1, "entries": entries}, indent=2) + "\n", encoding="utf-8",
    )


def check_ruff(*, write_baseline: bool = False, prune_baseline: bool = False) -> int:
    items = ruff_findings()
    if write_baseline or prune_baseline:
        if any(item.diagnostic["code"] == "invalid-syntax" for item in items):
            raise ValueError("Repair syntax errors before recording a baseline")
    if write_baseline:
        write_entries(baseline_entries(items))
        print(f"Recorded {len(items)} Ruff diagnostics in {RUFF_BASELINE.name}.")
        return 0
    baseline = json.loads(RUFF_BASELINE.read_text(encoding="utf-8"))
    if baseline["version"] != 1:
        raise ValueError("Unsupported Ruff baseline version")
    entries: list[BaselineEntry] = baseline["entries"]
    added, retained = compare_baseline(items, entries)
    stale = retained != entries
    if prune_baseline:
        write_entries(retained)
        print("Pruned resolved Ruff debt and lowered improved metric limits; no new debt accepted.")
    elif stale:
        print("Ruff baseline has resolved debt or improved limits. Run "
              "python3 -m scripts.validation.check --prune-ruff-baseline and commit the reductions.")
    for item in added:
        location = item.diagnostic["location"]
        print(f"{item.key[0]}:{location['row']}:{location['column']}: {item.key[1]} {item.diagnostic['message']}")
    print(f"Ruff: {len(added)} new or worsened diagnostics; {len(items) - len(added)} baselined.", flush=True)
    return int(bool(added) or (stale and not prune_baseline))


def check_imports() -> int:
    # Use this interpreter's installed CLI entrypoint, independent of shell PATH.
    return subprocess.run(
        [sys.executable, "-c", "from importlinter.cli import lint_imports_command\nlint_imports_command()",
         "--config", "pyproject.toml", "--no-cache", "--no-logo"],
        cwd=ROOT, check=False,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ruff-only", action="store_true", help="Run the fast lint checks only.")
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument("--write-ruff-baseline", action="store_true", help="Explicitly accept current Ruff debt; review the resulting diff.")
    baseline.add_argument("--prune-ruff-baseline", action="store_true", help="Remove resolved debt and lower limits without accepting new findings.")
    args = parser.parse_args(argv)
    try:
        lint_status = check_ruff(write_baseline=args.write_ruff_baseline, prune_baseline=args.prune_ruff_baseline)
        if args.ruff_only or args.write_ruff_baseline or args.prune_ruff_baseline:
            return lint_status
        import_status = check_imports()
        type_status = subprocess.run(
            [sys.executable, "-m", "basedpyright", "--pythonpath", sys.executable],
            cwd=ROOT, check=False,
        ).returncode
        return max(lint_status, import_status, type_status)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Quality check failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
