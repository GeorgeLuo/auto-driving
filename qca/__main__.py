"""Command-line entrypoint for ``python3 -m qca``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyzer import AnalysisError, AnalyzerConfig, analyze_diff, analyze_tree, render_markdown, report_to_dict
from .backtest import render_backtest_markdown, run_manifest


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
    parser.add_argument("--json", dest="json_path", type=Path, help="Write machine-readable JSON to this path.")
    parser.add_argument("--markdown", dest="markdown_path", type=Path, help="Write the Markdown summary to this path.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m qca",
        description="Standalone quantitative change analysis (observations only).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Measure a tree or revision snapshot.")
    analyze.add_argument("path", nargs="?", default=".")
    analyze.add_argument("--ref", help="Analyze this Git revision instead of the working tree.")
    _common_options(analyze)

    diff = subparsers.add_parser("diff", help="Measure a Git base→head change.")
    diff.add_argument("--base", required=True)
    diff.add_argument("--head", required=True)
    diff.add_argument("--path", default=".")
    _common_options(diff)

    backtest = subparsers.add_parser("backtest", help="Run a declared historical backtest manifest.")
    backtest.add_argument("--manifest", required=True, type=Path)
    backtest.add_argument("--json", dest="json_path", type=Path)
    backtest.add_argument("--markdown", dest="markdown_path", type=Path)
    return parser


def _config(args: argparse.Namespace) -> AnalyzerConfig:
    return AnalyzerConfig(
        include_roots=tuple(args.include_roots or ["."]),
        excluded_globs=tuple(args.excluded_globs),
    )


def _write(path: Path | None, content: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze":
            report = analyze_tree(args.path, ref=args.ref, config=_config(args))
            payload = report_to_dict(report)
            markdown = render_markdown(report)
            _write(args.json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            _write(args.markdown_path, markdown)
            print(markdown, end="")
            return 0
        if args.command == "diff":
            report = analyze_diff(args.base, args.head, path=args.path, config=_config(args))
            payload = report_to_dict(report)
            markdown = render_markdown(report)
            _write(args.json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            _write(args.markdown_path, markdown)
            print(markdown, end="")
            return 0
        payload = run_manifest(args.manifest)
        markdown = render_backtest_markdown(payload)
        _write(args.json_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        _write(args.markdown_path, markdown)
        print(markdown, end="")
        return 0
    except AnalysisError as exc:
        print(f"qca: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
