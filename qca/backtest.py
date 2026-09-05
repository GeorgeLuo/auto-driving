"""Small historical backtest harness for the #180 experiment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .analyzer import (
    AnalysisError,
    AnalyzerConfig,
    analyze_diff,
    format_share_permille,
    production_test_split_summary,
    report_to_dict,
)


def run_manifest(manifest_path: str | Path) -> dict[str, Any]:
    """Analyze each declared base→head state without changing Git state."""

    path = Path(manifest_path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"cannot read backtest manifest {path}: {exc}") from exc
    if manifest.get("schema") != "qca/m008-backtest/v1":
        raise AnalysisError("backtest manifest must use qca/m008-backtest/v1")
    states = manifest.get("states")
    if not isinstance(states, list) or not states:
        raise AnalysisError("backtest manifest must contain a non-empty states list")
    config_data = manifest.get("config") or {}
    config = AnalyzerConfig(
        include_roots=tuple(config_data.get("include_roots", ["."])),
        excluded_globs=tuple(config_data.get("excluded_globs", [])),
    )
    results = []
    analyzer_version = None
    versions = set()
    for state in states:
        if not isinstance(state, dict) or not state.get("id"):
            raise AnalysisError("each backtest state needs an id")
        report = analyze_diff(
            str(state["base"]),
            str(state["head"]),
            path=manifest.get("path", "."),
            config=config,
        )
        payload = report_to_dict(report)
        analyzer_version = payload["identity"]["analyzer_version"]
        versions.add(analyzer_version)
        results.append(
            {
                "id": str(state["id"]),
                "scale": str(state.get("scale", "unspecified")),
                "description": str(state.get("description", "")),
                "actual_outcome": str(state.get("actual_outcome", "")),
                "base": payload["identity"]["base_sha"],
                "head": payload["identity"]["head_sha"],
                "diff": payload["diff"],
                "observations": payload["observations"],
                "operator_questions": _operator_questions(payload["diff"]),
                "identity": payload["identity"],
                "configuration": payload["configuration"],
                "factors": payload["factors"],
            }
        )
    return {
        "schema": "qca/m008-backtest-report/v1",
        "analyzer_version": analyzer_version,
        "manifest": str(path),
        "experiment": manifest.get("experiment", {}),
        "execution": {
            "state_count": len(results),
            "all_revisions_resolved": all(
                len(state["base"]) == 40 and len(state["head"]) == 40 for state in results
            ),
            "single_analyzer_version": len(versions) == 1,
            "comparison_boundary": "actual_outcome is historical context and is not supplied to the analyzer",
        },
        "states": results,
    }


def _operator_questions(diff: dict[str, Any]) -> list[str]:
    """Build evidence-linked questions without interpreting intent or quality."""

    questions: list[str] = []
    classes = {
        name: values
        for name, values in diff["changed_by_class"].items()
        if values["files"]
    }
    if classes.get("production", {}).get("files"):
        questions.append(
            "Which changed production callable(s) account for the implementation change, and are they the intended owners?"
        )
    if classes.get("tests", {}).get("files"):
        questions.append(
            "What consumer-visible behavior or boundary does the changed test surface verify?"
        )
    split = diff.get("production_test_split") or {}
    python = split.get("python") or {}
    if python.get("production", {}).get("files") and python.get("tests", {}).get("files"):
        questions.append(
            "Does the production vs tests Python net split "
            f"({python['production']['net_lines']} / {python['tests']['net_lines']}, "
            f"{format_share_permille(python['production_net_share_permille'])} / "
            f"{format_share_permille(python['tests_net_share_permille'])}) "
            "match the intended change?"
        )
    if classes.get("tooling/scripts", {}).get("files"):
        questions.append("Which tooling or workflow behavior does this changed script surface support?")
    if classes.get("docs/configuration", {}).get("files"):
        questions.append("Which contract, operator context, or evidence claim changed in the documentation surface?")
    if classes.get("experimental/lab", {}).get("files"):
        questions.append("Is the experimental/lab surface intentionally part of this transition?")
    if classes.get("generated/runtime", {}).get("files"):
        questions.append("Which generated or evidence artifacts are expected outputs of this transition?")
    if diff["decision_burden_delta"]:
        questions.append(
            "What requirement or control-flow change explains the decision-burden delta "
            f"({diff['decision_burden_delta']:+d})?"
        )
    if any(target["kind"] == "dependency" for target in diff["review_targets"]):
        questions.append("Are the new dependency edges intentional and within the expected ownership direction?")
    if diff["public_symbols_added"] or diff["public_symbols_removed"]:
        questions.append("Which public-surface changes are required by the intended consumer contract?")
    nonzero_classes = [
        name for name, values in diff["changed_by_class"].items() if values["files"]
    ]
    if len(nonzero_classes) > 1:
        questions.append(
            "Does the spread across source classes match the intended change, or is any work incidental?"
        )
    if not questions:
        questions.append("What changed outside the measured callable/dependency/public-surface signals?")
    return questions


def render_backtest_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# QCA M008 Backtest",
        "",
        f"- schema: `{payload['schema']}`",
        f"- analyzer: `{payload['analyzer_version']}`",
        f"- states: {len(payload['states'])}",
        f"- revisions resolved: `{payload['execution']['all_revisions_resolved']}`",
        f"- one analyzer version: `{payload['execution']['single_analyzer_version']}`",
        "",
        "## Hypothesis",
        "",
        payload.get("experiment", {}).get("hypothesis", "No hypothesis supplied."),
        "",
        "## Provisional signal assessment",
        "",
        "These are experiment findings, not workflow policy or quality grades.",
        "",
        "| Signal | Status | Evidence |",
        "| --- | --- | --- |",
    ]
    for assessment in payload.get("experiment", {}).get("provisional_signal_assessment", []):
        lines.append(
            f"| `{assessment['signal']}` | `{assessment['status']}` | {assessment['evidence']} |"
        )
    lines.extend(
        [
            "",
            "## Gradient readings",
            "",
            "| State | Base | Head | Files | Core files | Added | Core churn | Decision burden Δ |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for state in payload["states"]:
        diff = state["diff"]
        lines.append(
            f"| `{state['id']}` ({state['scale']}) | `{state['base'][:12]}` | `{state['head'][:12]}` | "
            f"{len(diff['changed_files'])} | {len(diff['included_changed_files'])} | {diff['added_lines']} | "
            f"{diff['included_churn']} | "
            f"{diff['decision_burden_delta']:+d} |"
        )
    lines.extend(["", "## Operator questions", ""])
    for state in payload["states"]:
        lines.append(f"### `{state['id']}` ({state['scale']})")
        lines.append("")
        lines.append(state["description"] or "No description supplied.")
        lines.append("")
        diff = state["diff"]
        if state["actual_outcome"]:
            lines.append(f"Historical outcome (revealed after the reading): {state['actual_outcome']}")
            lines.append("")
        class_summary = [
            f"{name} ({values['files']} files, +{values['added_lines']}/-{values['deleted_lines']})"
            for name, values in diff["changed_by_class"].items()
            if values["files"]
        ]
        if class_summary:
            lines.append("Changed source classes: " + "; ".join(class_summary))
            lines.append(
                f"Core measured change: {len(diff['included_changed_files'])} files, "
                f"+{diff['included_added_lines']}/-{diff['included_deleted_lines']} "
                f"({diff['included_churn']} churn)."
            )
            split = diff.get("production_test_split")
            if split:
                lines.append(production_test_split_summary(split))
            lines.append("")
        for question in state["operator_questions"]:
            lines.append(f"- {question}")
        lines.append(
            f"- Agent targets: {len(state['diff']['review_targets'])} deterministic inspection target(s)."
        )
        lines.append("")
    lines.extend(
        [
            "The readings are observations; no quality grade or gate is emitted.",
            "",
        ]
    )
    return "\n".join(lines)
