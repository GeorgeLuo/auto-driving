"""Check Python quality for a change, whole files, or text, and report one findings list.

  python3 -m scripts.validation.check                           working tree against origin/main
  python3 -m scripts.validation.check --base main --head HEAD   a pull request
  python3 -m scripts.validation.check --base C~1 --head C       one commit
  python3 -m scripts.validation.check qca cli/automa_cli        whole files
  python3 -m scripts.validation.check --text cli/x.py < x.py    text evaluated as cli/x.py

A change reports findings throughout changed files and findings introduced
elsewhere. Exit status: 0 clean, 1 blocking findings, 2 a required check could
not run.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from contextlib import AbstractContextManager
from pathlib import Path

from qca import AnalysisError
from qca.evaluation import (
    Context,
    ContextRecord,
    Document,
    EvaluationRecord,
    Evaluator,
    Finding,
    change_context,
    evaluate,
    files_context,
    text_context,
)
from qca.evaluators import structure
from scripts.validation import evaluators, inspection

ROOT = Path(__file__).resolve().parents[2]
PATHS = ("autonomy", "cli", "implementations", "deploy", "qca", "scripts/validation", "tests")
# Both sides of a change are judged by the head's tool configuration.
SHARED = ("pyproject.toml", ".basedpyright")
DEFAULT_BASE = "origin/main"
LIMIT = 50


def build_evaluators(*, ruff_only: bool = False) -> list[Evaluator]:
    ruff = Evaluator("ruff", evaluators.ruff, roots=PATHS)
    if ruff_only:
        return [ruff]
    tools = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
    # QCA and the inspections leave out what Ruff's configuration excludes; type and import checks have their own.
    excluded = tuple(path.removeprefix("./") for path in tools["ruff"]["extend-exclude"])
    projects = frozenset({"files", "change"})  # Text has no project around it to read.
    return [
        ruff,
        Evaluator("basedpyright", evaluators.basedpyright, roots=tuple(tools["basedpyright"]["include"]),
                  forms=projects, reach="project"),
        Evaluator("import-linter", evaluators.import_contracts, roots=tuple(tools["importlinter"]["root_packages"]),
                  forms=projects, reach="project"),
        Evaluator("vocabulary", evaluators.vocabulary),
        Evaluator("qca", structure, roots=PATHS, excluded=excluded, required=False),
        Evaluator("vulture", inspection.unused, roots=PATHS, excluded=excluded, forms=projects, reach="project",
                  required=False),
        Evaluator("related-functions", inspection.related, roots=PATHS, excluded=excluded, forms=projects,
                  reach="project", required=False),
    ]


def open_context(args: argparse.Namespace) -> AbstractContextManager[Context]:
    if args.text:
        return text_context(args.text, sys.stdin.read(), root=ROOT, shared=SHARED)
    if args.paths:
        return files_context(ROOT, [Path(path).resolve().relative_to(ROOT.resolve()).as_posix() for path in args.paths])
    return change_context(ROOT, args.base or DEFAULT_BASE, args.head, shared=SHARED)


def exit_status(document: Document) -> int:
    if any(record["status"] == "failed" and record["required"] for record in document["evaluations"]):
        return 2
    return int(any(item["status"] == "blocking" for item in document["findings"]))


def render_summary(document: Document) -> str:
    lines = ["## Python quality", "", _describe(document["context"]), "", "| Check | Result |", "| --- | --- |"]
    lines += [f"| {record['evaluator']} | {_outcome(record)} |" for record in document["evaluations"]]
    for status, title in (("blocking", "Blocking"), ("review", "Review questions")):
        rows = [_row(item) for item in document["findings"] if item["status"] == status]
        lines += ["", f"### {title} ({len(rows)})", "", *(rows[:LIMIT] or ["None."])]
        if len(rows) > LIMIT:
            lines += ["", f"Showing {LIMIT}; findings.json has all {len(rows)}."]
    return "\n".join(lines)


def _describe(context: ContextRecord) -> str:
    if context["form"] == "text":
        return f"Text evaluated as `{context['paths'][0]}`."
    if context["form"] == "files":
        return f"{len(context['paths'])} whole files."
    head = f"`{context['head'][:12]}`" if context["head"] else "the working tree"
    return (f"Change from `{str(context['base'])[:12]}` to {head}, {len(context['paths'])} changed files. "
            "Findings cover changed files and new findings elsewhere.")


def _outcome(record: EvaluationRecord) -> str:
    if record["status"] == "completed":
        return f"{record.get('findings', 0)} findings"
    detail = _plain(record.get("error", record.get("reason", ""))).replace("|", "\\|")
    return f"{record['status']}{'' if record['required'] else ' (informational)'}: {detail}"


def _row(item: Finding) -> str:
    place = item["location"]
    return f"- `{place['path']}:{place['line']}` {item['tool']} `{item['rule']}`: {_plain(item['question'])}"


def _plain(text: str) -> str:
    return " ".join(text.split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="Evaluate these whole files or directories instead of a change.")
    parser.add_argument("--base", help=f"Base of the change; its merge base with the head is used (default: {DEFAULT_BASE}).")
    parser.add_argument("--head", help="Head revision of the change (default: the working tree).")
    parser.add_argument("--text", metavar="NAME", help="Evaluate standard input as the file NAME.")
    parser.add_argument("--ruff-only", action="store_true", help="Run only Ruff, for a fast editing loop.")
    parser.add_argument("--output", type=Path, help="Write findings.json and summary.md to this directory.")
    args = parser.parse_args(argv)
    if sum(map(bool, (args.paths, args.text, args.base or args.head))) > 1:
        parser.error("evaluate one context: paths, --text, or a change (--base/--head)")
    try:
        with open_context(args) as context:
            document = evaluate(context, build_evaluators(ruff_only=args.ruff_only))
    except (AnalysisError, OSError, ValueError) as exc:
        print(f"Quality check could not start: {exc}", file=sys.stderr)
        return 2
    summary = render_summary(document)
    print(summary)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "findings.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        (args.output / "summary.md").write_text(summary + "\n", encoding="utf-8")
    return exit_status(document)


if __name__ == "__main__":
    raise SystemExit(main())
