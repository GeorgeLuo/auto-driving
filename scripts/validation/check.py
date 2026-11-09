"""Check maintained Python code without requiring existing debt to be repaired."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from qca.evaluation import evaluate
from scripts.validation import findings as output_findings
from scripts.validation.text_evaluators import retired_vocabulary

ROOT = Path(__file__).resolve().parents[2]
RUFF_BASELINE = ROOT / ".ruff-baseline.json"
PATHS = ("autonomy", "cli", "implementations", "deploy", "qca", "scripts/validation", "tests")
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
    diagnostics = json.loads(completed.stdout)
    for diagnostic in diagnostics:
        if diagnostic["code"] is None:
            diagnostic["code"] = "invalid-syntax"
    return findings(diagnostics, ROOT)


def write_entries(entries: list[BaselineEntry]) -> None:
    RUFF_BASELINE.write_text(
        json.dumps({"version": 1, "entries": entries}, indent=2) + "\n", encoding="utf-8",
    )


def check_ruff(*, baseline_action: Literal["check", "write", "prune"] = "check",
               records: list | None = None) -> int:
    items = ruff_findings()
    if baseline_action != "check":
        if any(item.diagnostic["code"] == "invalid-syntax" for item in items):
            raise ValueError("Repair syntax errors before recording a baseline")
    if baseline_action == "write":
        write_entries(baseline_entries(items))
        print(f"Recorded {len(items)} Ruff diagnostics in {RUFF_BASELINE.name}.")
        return 0
    baseline = json.loads(RUFF_BASELINE.read_text(encoding="utf-8"))
    if baseline["version"] != 1:
        raise ValueError("Unsupported Ruff baseline version")
    entries: list[BaselineEntry] = baseline["entries"]
    added, retained = compare_baseline(items, entries)
    if records is not None:
        records.extend(output_findings.ruff_records(items, added))
    stale = retained != entries
    if baseline_action == "prune":
        write_entries(retained)
        stale = False
        print("Pruned resolved Ruff debt and lowered improved metric limits; no new debt accepted.")
    elif stale:
        print("Ruff baseline has resolved debt or improved limits. Run "
              "python3 -m scripts.validation.check --prune-ruff-baseline and commit the reductions.")
    for item in added:
        location = item.diagnostic["location"]
        print(f"{item.key[0]}:{location['row']}:{location['column']}: {item.key[1]} {item.diagnostic['message']}")
    print(f"Ruff: {len(added)} new or worsened diagnostics; {len(items) - len(added)} baselined.", flush=True)
    return int(bool(added) or stale)


def check_imports(records: list | None = None) -> int:
    # Isolate import discovery from the calling process's already-imported modules.
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "imports.json"
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
        result = subprocess.run([sys.executable, "-m", "scripts.validation.import_check", str(output)],
                                cwd=ROOT, env=env, check=False)
        if not output.is_file():
            raise ValueError("Import contract inspection failed without a report")
        if records is not None:
            records.extend(json.loads(output.read_text()))
        return result.returncode


def check_types(records: list) -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "basedpyright", "--pythonpath", sys.executable, "--outputjson"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise ValueError(completed.stderr or completed.stdout or "Type check failed")
    payload = json.loads(completed.stdout)
    records.extend(output_findings.type_records(payload["generalDiagnostics"], ROOT))
    for item in payload["generalDiagnostics"]:
        print(f"{item['file']}:{item['range']['start']['line'] + 1}: {item['message']}")
    print(f"Type check: {payload['summary']['errorCount']} errors.")
    return completed.returncode


def check_vocabulary(records: list) -> int:
    word = "snap" + "shot"
    result = subprocess.run(["git", "grep", "-n", "-i", word, "--", ":!docs/deprecated", ":!.github/workflows/tests.yml"],
                            cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise ValueError(result.stderr or "Vocabulary check failed")
    for match in result.stdout.splitlines():
        path, line, content = match.split(":", 2)
        for record in evaluate(content, [retired_vocabulary]):
            record["location"].update({"path": path, "line": int(line)})
            records.append(record)
        print(match)
    return int(bool(result.stdout))


def inspect_changes(output: Path, records: list) -> list[str]:
    base, head = os.environ.get("PR_BASE"), os.environ.get("PR_HEAD")
    if not base or not head:
        return []
    try:
        merge_base = subprocess.check_output(["git", "merge-base", base, head], cwd=ROOT, text=True).strip()
        inspected = subprocess.run(
            [sys.executable, "-m", "scripts.validation.qca_summary", "--base", merge_base,
             "--head", head, "--output", str(output)], cwd=ROOT, check=False,
        )
        if inspected.returncode:
            raise ValueError("QCA inspection failed; see the quality job log.")
        report = json.loads((output / "report.json").read_text())
        inspection = json.loads((output / "inspection.json").read_text())
        records.extend(output_findings.qca_records(report, inspection))
        if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(summary_path, "a", encoding="utf-8") as summary:
                summary.write((output / "summary.md").read_text())
        return []
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        message = f"Informational inspection failed: {exc}"
        print(message, file=sys.stderr)
        return [message]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ruff-only", action="store_true", help="Run the fast lint checks only.")
    baseline = parser.add_mutually_exclusive_group()
    baseline.add_argument("--write-ruff-baseline", dest="baseline_action", action="store_const", const="write",
                          help="Explicitly accept current Ruff debt; review the resulting diff.")
    baseline.add_argument("--prune-ruff-baseline", dest="baseline_action", action="store_const", const="prune",
                          help="Remove resolved debt and lower limits without accepting new findings.")
    parser.add_argument("--output", type=Path, help="Write quality findings and PR inspection evidence here.")
    parser.set_defaults(baseline_action="check")
    args = parser.parse_args(argv)
    records: list = []
    errors = []
    try:
        lint_status = check_ruff(baseline_action=args.baseline_action, records=records)
        if args.ruff_only or args.baseline_action != "check":
            return lint_status
        import_status = check_imports(records)
        type_status = check_types(records)
        vocabulary_status = check_vocabulary(records)
        if args.output:
            errors.extend(inspect_changes(args.output, records))
        return max(lint_status, import_status, type_status, vocabulary_status)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Quality check failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
            (args.output / "findings.json").write_text(json.dumps({
                "schema": "quality_findings_v1", "revision": revision.stdout.strip(),
                "findings": records, "inspection_errors": errors,
            }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
