"""Command-line entrypoint for ``python3 -m qca``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyzer import AnalysisError, AnalyzerConfig, analyze_diff, analyze_tree, render_markdown, report_to_dict
from .backtest import render_backtest_markdown, run_manifest
from .factors.verification import attach_verification
from .render import render_html


def _common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--exclude",
        dest="excluded_globs",
        action="append",
        default=[],
        help="Glob to exclude from core metrics; may be repeated.",
    )
    parser.add_argument(
        "--include-root",
        dest="include_roots",
        action="append",
        default=None,
        help="Root to include; may be repeated (default: .).",
    )
    parser.add_argument(
        "--python",
        action="store_true",
        help="Measure only Python files (.py, .pyi).",
    )
    parser.add_argument("--json", dest="json_path", type=Path, help="Write machine-readable JSON to this path.")
    parser.add_argument("--markdown", dest="markdown_path", type=Path, help="Write the Markdown summary to this path.")
    parser.add_argument("--html", dest="html_path", type=Path, help="Write a standalone HTML report.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m qca",
        description="Standalone quantitative change analysis (observations only).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Measure files, directories, or a Git revision.")
    analyze.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="Files and/or directories of text to measure (default: .). Git is not required.",
    )
    analyze.add_argument("--ref", help="Analyze this Git revision instead of the working tree.")
    _common_options(analyze)

    diff = subparsers.add_parser("diff", help="Measure a Git base→head change.")
    diff.add_argument("--base", required=True)
    diff.add_argument("--head", required=True)
    diff.add_argument("--path", default=".")
    _common_options(diff)
    diff.add_argument("--evidence", type=Path, help="Attach a qca/verification/v1 record for these exact revisions.")

    backtest = subparsers.add_parser("backtest", help="Run a declared historical backtest manifest.")
    backtest.add_argument("--manifest", required=True, type=Path)
    backtest.add_argument("--json", dest="json_path", type=Path)
    backtest.add_argument("--markdown", dest="markdown_path", type=Path)
    backtest.add_argument("--html", dest="html_path", type=Path)
    render = subparsers.add_parser("render", help="Render an existing JSON record as standalone HTML.")
    render.add_argument("report", type=Path)
    render.add_argument("--html", dest="html_path", required=True, type=Path)
    return parser


def _config(args: argparse.Namespace) -> AnalyzerConfig:
    return AnalyzerConfig(
        include_roots=tuple(args.include_roots or ["."]),
        excluded_globs=tuple(args.excluded_globs),
        languages=("python",) if getattr(args, "python", False) else (),
    )


def _write(path: Path | None, content: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "render":
            _write(args.html_path, render_html(json.loads(args.report.read_text(encoding="utf-8"))))
            return 0
        if args.command == "analyze":
            report = analyze_tree(args.paths, ref=args.ref, config=_config(args))
            payload = report_to_dict(report)
            markdown = render_markdown(report)
            _write(args.json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            _write(args.markdown_path, markdown)
            _write(args.html_path, render_html(payload))
            print(markdown, end="")
            return 0
        if args.command == "diff":
            report = analyze_diff(args.base, args.head, path=args.path, config=_config(args))
            if args.evidence:
                report.factors = attach_verification(
                    report.factors,
                    json.loads(args.evidence.read_text(encoding="utf-8")),
                    report.identity.base_sha,
                    report.identity.head_sha,
                )
            payload = report_to_dict(report)
            markdown = render_markdown(report)
            _write(args.json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            _write(args.markdown_path, markdown)
            _write(args.html_path, render_html(payload))
            print(markdown, end="")
            return 0
        payload = run_manifest(args.manifest)
        markdown = render_backtest_markdown(payload)
        _write(args.json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        _write(args.markdown_path, markdown)
        _write(args.html_path, render_html(payload))
        print(markdown, end="")
        return 0
    except (AnalysisError, ValueError, OSError) as exc:
        print(f"qca: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
