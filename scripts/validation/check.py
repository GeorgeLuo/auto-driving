"""Check maintained Python code without requiring existing debt to be repaired."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from qca.evaluation import evaluate, validate_finding
from scripts.validation import findings as output_findings
from scripts.validation.text_evaluators import retired_vocabulary

ROOT = Path(__file__).resolve().parents[2]
PATHS = ("autonomy", "cli", "implementations", "deploy", "qca", "scripts/validation", "tests")

def changed_lines() -> dict[str, set[int] | None]:
    """Find added/edited lines in the current tree relative to the PR base."""
    base = os.environ.get("PR_BASE") or os.environ.get("QUALITY_BASE") or "origin/main"
    head = os.environ.get("PR_HEAD") or "HEAD"
    merge_base = subprocess.check_output(["git", "merge-base", base, head], cwd=ROOT, text=True).strip()
    names = subprocess.check_output(["git", "diff", "--name-only", "-z", "--no-renames", merge_base, "--"], cwd=ROOT)
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    changed = {path.decode(): None for path in untracked.split(b"\0") if path}
    for raw in names.split(b"\0"):
        if not raw:
            continue
        path = raw.decode()
        diff = subprocess.check_output(["git", "diff", "--no-ext-diff", "--unified=0", merge_base, "--", path],
                                       cwd=ROOT, text=True)
        lines = set()
        for match in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.MULTILINE):
            start, count = int(match[1]), int(match[2] or "1")
            lines.update(range(start, start + count))
        changed[path] = lines
    return changed


def is_changed(path: str, line: int, changed: dict[str, set[int] | None]) -> bool:
    return path in changed and (changed[path] is None or line in changed[path])


def ruff_findings(root: Path) -> list[dict]:
    completed = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "--output-format", "json", *PATHS],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise ValueError(completed.stderr or "Ruff failed without diagnostics")
    diagnostics = json.loads(completed.stdout)
    for diagnostic in diagnostics:
        if diagnostic["code"] is None:
            diagnostic["code"] = "invalid-syntax"
    return diagnostics


def check_ruff(changed: dict[str, set[int] | None], records: list, *, root: Path | None = None) -> int:
    root = root or ROOT
    items = []
    for item in ruff_findings(root):
        path = Path(item["filename"]).resolve().relative_to(root.resolve()).as_posix()
        if is_changed(path, item["location"]["row"], changed) or (item["code"] == "invalid-syntax" and path in changed):
            items.append(item)
            print(f"{path}:{item['location']['row']}:{item['location']['column']}: {item['code']} {item['message']}")
    records.extend(output_findings.ruff_records(items, root))
    print(f"Ruff: {len(items)} diagnostics on changed lines.", flush=True)
    return int(bool(items))


def check_imports(records: list | None = None, *, root: Path | None = None) -> int:
    # Isolate import discovery from the calling process's already-imported modules.
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "imports.json"
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
        result = subprocess.run([sys.executable, "-m", "scripts.validation.import_check", str(output)],
                                cwd=root or ROOT, env=env, check=False)
        if not output.is_file():
            raise ValueError("Import contract inspection failed without a report")
        if records is not None:
            records.extend(json.loads(output.read_text()))
        return result.returncode


def check_types(changed: dict[str, set[int] | None], records: list, *, root: Path | None = None) -> int:
    root = root or ROOT
    completed = subprocess.run(
        [sys.executable, "-m", "basedpyright", "--pythonpath", sys.executable, "--outputjson"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise ValueError(completed.stderr or completed.stdout or "Type check failed")
    payload = json.loads(completed.stdout)
    items = [item for item in payload["generalDiagnostics"]
             if is_changed(Path(item["file"]).resolve().relative_to(root.resolve()).as_posix(),
                           item["range"]["start"]["line"] + 1, changed)]
    records.extend(output_findings.type_records(items, root))
    for item in items:
        print(f"{item['file']}:{item['range']['start']['line'] + 1}: {item['message']}")
    errors = sum(item["severity"] == "error" for item in items)
    print(f"Type check: {errors} errors on changed lines.")
    return int(bool(errors))


def check_vocabulary(records: list, *, root: Path | None = None) -> int:
    word = "snap" + "shot"
    result = subprocess.run(["git", "grep", "-n", "-i", word, "--", ":!docs/deprecated", ":!.github/workflows/tests.yml"],
                            cwd=root or ROOT, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise ValueError(result.stderr or "Vocabulary check failed")
    for match in result.stdout.splitlines():
        path, line, content = match.split(":", 2)
        for record in retired_vocabulary(content):
            record["location"].update({"path": path, "line": int(line)})
            records.append(record)
        print(match)
    return int(bool(result.stdout))


def inspect_changes(output: Path, records: list, *, root: Path | None = None,
                    base: str | None = None, head: str | None = None) -> list[str]:
    root = root or ROOT
    if not base or not head:
        return []
    try:
        merge_base = subprocess.check_output(["git", "merge-base", base, head], cwd=root, text=True).strip()
        inspected = subprocess.run(
            [sys.executable, "-m", "scripts.validation.qca_summary", "--base", merge_base,
             "--head", head, "--output", str(output)], cwd=root, check=False,
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


@dataclass
class QualityContext:
    changed: dict[str, set[int] | None]
    output: Path | None
    root: Path = field(default_factory=lambda: ROOT)
    base: str | None = field(default_factory=lambda: os.environ.get("PR_BASE"))
    head: str | None = field(default_factory=lambda: os.environ.get("PR_HEAD"))
    inspection_errors: list[str] = field(default_factory=list)


def evaluate_ruff(context: QualityContext) -> list[dict]:
    records: list[dict] = []
    check_ruff(context.changed, records, root=context.root)
    return records


def evaluate_imports(context: QualityContext) -> list[dict]:
    records: list[dict] = []
    if check_imports(records, root=context.root) and not any(item["status"] == "blocking" for item in records):
        raise ValueError("Import contract check failed without findings")
    return records


def evaluate_types(context: QualityContext) -> list[dict]:
    records: list[dict] = []
    check_types(context.changed, records, root=context.root)
    return records


def evaluate_vocabulary(context: QualityContext) -> list[dict]:
    records: list[dict] = []
    check_vocabulary(records, root=context.root)
    return records


def evaluate_inspection(context: QualityContext) -> list[dict]:
    records: list[dict] = []
    if context.output is not None:
        context.inspection_errors.extend(inspect_changes(context.output, records, root=context.root,
                                                          base=context.base, head=context.head))
    try:
        for record in records:
            validate_finding(record)
    except ValueError as exc:
        message = f"Informational inspection produced an invalid finding: {exc}"
        print(message, file=sys.stderr)
        context.inspection_errors.append(message)
        return []
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ruff-only", action="store_true", help="Run the fast lint checks only.")
    parser.add_argument("--output", type=Path, help="Write quality findings and PR inspection evidence here.")
    args = parser.parse_args(argv)
    records: list = []
    errors: list[str] = []
    context = QualityContext({}, args.output)
    try:
        context.changed = changed_lines()
        evaluators = [evaluate_ruff]
        if not args.ruff_only:
            evaluators.extend([evaluate_imports, evaluate_types, evaluate_vocabulary, evaluate_inspection])
        evaluate(((evaluator, context) for evaluator in evaluators), findings=records)
        return int(any(item["status"] == "blocking" for item in records))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        errors.append(str(exc))
        print(f"Quality check failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
            (args.output / "findings.json").write_text(json.dumps({
                "schema": "quality_findings_v1", "revision": revision.stdout.strip(),
                "findings": records, "errors": errors, "inspection_errors": context.inspection_errors,
            }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
