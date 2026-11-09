"""Publish a bounded QCA review summary and retain the complete evidence."""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from qca import AnalyzerConfig, Report, analyze_diff, report_to_dict
from qca.analyzer import DiffMetrics
from qca.render import render_html
from scripts.validation.inspection import PATHS, Inspection, inspect_revisions


LIMIT = 20


def text(value: str) -> str:
    return html.escape(value).replace("`", "&#96;").replace("\n", " ")


def section(title: str, rows: list[str]) -> list[str]:
    lines = [f"### {title} ({len(rows)})", ""]
    lines.extend(f"- {row}" for row in rows[:LIMIT])
    if not rows:
        lines.append("None.")
    if len(rows) > LIMIT:
        lines.append(f"\nShowing {LIMIT}; the complete evidence is in the QCA artifact.")
    return [*lines, ""]


def changed_location(diff: DiffMetrics, path: str, line: int) -> bool:
    return any(
        change["path"] == path
        and change["after"] is not None
        and change["after"]["start_line"] <= line <= change["after"]["end_line"]
        for change in diff.changed_callables
    )


def larger_functions(diff: DiffMetrics) -> list[str]:
    rows = []
    thresholds = {"cyclomatic_complexity": 10, "logical_loc": 50, "max_nesting": 5}
    for change in diff.changed_callables:
        after, before = change["after"], change["before"]
        if after is None:
            continue
        increases = []
        for metric, threshold in thresholds.items():
            old = before[metric] if before else 0
            new = after[metric]
            if new > max(old, threshold):
                increases.append(f"{metric}: {old} → {new}")
        if increases:
            location = f"{change['path']}:{after['start_line']}"
            rows.append(f"`{text(location)}` `{text(change['qualified_name'])}` — {', '.join(increases)}")
    return rows


def render_summary(report: Report) -> str:
    if report.diff is None:
        raise ValueError("A PR summary requires a QCA diff report")
    lines = [
        "## QCA inspection", "",
        f"Base `{report.identity.base_sha}` → head `{report.identity.head_sha}`.", "",
        "Informational, not a merge gate. Pattern and clone candidates involve changed functions; "
        "they may predate this change. Counts are not quality scores. "
        "QCA's complexity measurements differ from Ruff's.", "",
    ]
    lines += section("New or growing functions above review thresholds", larger_functions(report.diff))
    patterns = [
        f"`{text(finding['path'])}:{finding['line']}` {text(finding['message'])}"
        for finding in report.factors["patterns"]["findings"]
        if finding["kind"] in {"opaque_conditional", "redundant_any_guard", "swallowed_exception"}
        and changed_location(report.diff, finding["path"], finding["line"])
    ]
    lines += section("Conditional and error-handling candidates", patterns)
    clones = [
        f"`{text(finding['path'])}:{finding['line']}` {text(finding['message'])}"
        for finding in report.factors["redundancy"]["findings"]
        if any(
            changed_location(report.diff, occurrence["path"], occurrence["line"])
            for occurrence in finding.get("occurrences", [finding])
        )
    ]
    lines += section("Reuse candidates", clones)
    edges = [
        f"`{text(edge.replace('->', ' → '))}`" for edge in report.diff.new_import_edges
        if edge.partition("->")[2].split(".")[0] not in sys.stdlib_module_names
    ]
    lines += section("Added non-standard-library import edges", edges)
    return "\n".join(lines)


def render_inspection(inspection: Inspection) -> str:
    lines = ["## Production usage and related implementations", "",
             "Inspection candidates, not proven defects. Newly unused symbols can be in unchanged files. "
             "Existing candidates are included when their file changed. Test-only references do not "
             "establish production use; callbacks, public interfaces and dynamic lookup need review.", ""]
    unused = [
        f"`{text(item.path)}:{item.lines[0]}` `{text(item.symbol)}` — {item.reason}; "
        f"{len(item.test_references)} test reference locations (name-based)."
        for item in inspection.unused
    ]
    lines += section("Unused production code candidates", unused)
    comparisons = [
        f"`{text(item.changed.path)}:{item.changed.line}` `{text(item.changed.name)}` ↔ "
        f"`{text(item.related.path)}:{item.related.line}` `{text(item.related.name)}` "
        f"({item.relationship}); shared calls: {', '.join(f'`{text(call)}`' for call in item.shared_calls)}."
        for item in inspection.comparisons
    ]
    lines += section("Related functions to compare", comparisons)
    lines += ["Ranked by shared call spellings within a module, sibling directory or import neighbor; "
              "this does not establish equivalent behavior. Up to three comparisons per changed function. "
              "`inspection.json` retains source snippets, test references, rankings and limitations.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="PR merge-base commit.")
    parser.add_argument("--head", required=True, help="PR head commit.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = analyze_diff(args.base, args.head, path=list(PATHS), config=AnalyzerConfig(languages=("python",)))
    args.output.mkdir(parents=True, exist_ok=True)
    payload = report_to_dict(report)
    (args.output / "report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (args.output / "report.html").write_text(render_html(payload), encoding="utf-8")
    inspection = inspect_revisions(report.identity.base_sha, report.identity.head_sha, args.output, PATHS)
    summary = render_summary(report) + "\n" + render_inspection(inspection)
    (args.output / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
