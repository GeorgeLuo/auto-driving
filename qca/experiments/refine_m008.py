"""Measure M008 and test factor-guided candidates in disposable historical clones.

Usage: python3 -m qca.experiments.refine_m008 --output-dir <directory>
Requires the repository test dependencies. Product edits occur only in the
temporary clones; committed candidate patches are the reproducible inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from qca import AnalyzerConfig, analyze_diff, render_markdown, report_to_dict
from qca.analyzer import _read_git_diff
from qca.factors.verification import attach_verification
from qca.render import render_html


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
M008 = "3fce449d1eb64d408458231163c3f8b9b5c23af3"
CANDIDATES = (
    ("combine_actions", "small", "Merge identical idle and terminal action branches; decision burden should fall by one, with action order and fresh lists preserved."),
    ("path_containment", "medium", "Delegate two path-containment checks to Path.is_relative_to; decision burden should fall by two with identical lexical path semantics."),
    ("skip_validation", "negative-control", "Removing frame-sequence validation lowers decision burden but must be rejected when duplicate/non-increasing inputs cease to fail."),
)


def _json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    # Fixed metadata makes each candidate commit reconstructable from its
    # original parent and patch without publishing product changes.
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "QCA experiment", "GIT_AUTHOR_EMAIL": "qca@example.invalid",
        "GIT_COMMITTER_NAME": "QCA experiment", "GIT_COMMITTER_EMAIL": "qca@example.invalid",
        "GIT_AUTHOR_DATE": "2026-09-04T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-09-04T00:00:00+00:00",
    }
    return subprocess.check_output(["git", *args], cwd=root, env=environment, text=True).strip()


def _execute(checkout: Path, script: str, destination: Path) -> tuple[int, dict]:
    command = [sys.executable, str(HERE / script)]
    try:
        result = subprocess.run(
            command, cwd=checkout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"experiment command timed out: {script}") from exc
    destination.with_suffix(".log").write_text(result.stderr, encoding="utf-8")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        detail = (result.stderr or "")[-1500:] + "\n--- stdout ---\n" + (result.stdout or "")[-500:]
        raise RuntimeError(f"{script} did not produce execution evidence: {detail}") from exc
    _json(destination, payload)
    return result.returncode, payload


def _compact(report: dict) -> dict:
    diff = report["diff"]
    return {
        "identity": report["identity"],
        "files": len(diff["changed_files"]),
        "added_lines": diff["added_lines"], "deleted_lines": diff["deleted_lines"],
        "core_churn": diff["included_churn"],
        "decision_burden_delta": diff["decision_burden_delta"],
        "source_classes": diff["changed_by_class"],
        "factors": {
            name: {
                "status": value["status"], "metrics": value["metrics"],
                "delta": value.get("delta", {}), "findings": value["findings"],
                "limitations": value["limitations"],
            } for name, value in report["factors"].items()
        },
    }


def _changed_coverage(checkout: Path, candidate: str, baseline: dict, after: dict) -> dict:
    result = {}
    files = _read_git_diff(repo_root=checkout, base_sha=M008, head_sha=candidate, scope_rel=".")
    for side, tests, attribute in (("base", baseline, "changed_old_lines"), ("head", after, "changed_new_lines")):
        executable = covered = 0
        missing = []
        for item in files:
            measured = tests["lines"].get(item.path)
            if measured is None:
                continue
            changed = set(getattr(item, attribute)) & set(measured["statements"])
            reached = changed & set(measured["executed"])
            executable += len(changed)
            covered += len(reached)
            missing.extend(f"{item.path}:{line}" for line in sorted(changed - reached))
        result[side] = {"changed_executable_lines": executable, "covered": covered, "missing": missing}
    result["limitation"] = "Line reachability does not establish assertion strength or branch coverage; subprocess execution is excluded."
    return result


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "qca/backtests/m008-refined.json").read_text())
    config = AnalyzerConfig(**{key: tuple(value) for key, value in manifest["config"].items()})
    states = []
    for state in manifest["states"]:
        print(f"Measuring {state['id']}", flush=True)
        report = analyze_diff(state["base"], state["head"], path=ROOT, config=config)
        payload = report_to_dict(report)
        states.append({**state, **_compact(payload)})
        if state["id"] == "milestone-total":
            _json(output / "m008-report.json", payload)
            (output / "m008-report.md").write_text(render_markdown(report), encoding="utf-8")
            (output / "m008-report.html").write_text(render_html(payload, title="M008 · factor analysis"), encoding="utf-8")
    trials = []
    with tempfile.TemporaryDirectory(prefix="qca-m008-") as temporary:
        root = Path(temporary)
        baseline = root / "baseline"
        _git(ROOT, "clone", "--shared", "--quiet", "--no-checkout", str(ROOT), str(baseline))
        _git(baseline, "checkout", "--quiet", "--detach", M008)
        print("Running baseline consumer checks and replay probe", flush=True)
        baseline_rc, baseline_tests = _execute(baseline, "run_tests.py", output / "baseline-tests.json")
        probe_rc, baseline_probe = _execute(baseline, "workbench_probe.py", output / "baseline-probe.json")
        if baseline_rc or probe_rc or baseline_tests["tests_run"] == 0:
            raise RuntimeError("baseline validation failed; candidate comparisons would be inconclusive")
        for name, scale, hypothesis in CANDIDATES:
            print(f"Testing {name}", flush=True)
            checkout = root / name
            _git(ROOT, "clone", "--shared", "--quiet", "--no-checkout", str(ROOT), str(checkout))
            _git(checkout, "checkout", "--quiet", "--detach", M008)
            patch = HERE / "candidates" / f"{name}.patch"
            _git(checkout, "apply", "--check", "--unidiff-zero", str(patch))
            _git(checkout, "apply", "--unidiff-zero", str(patch))
            _git(checkout, "add", "cli/automa_cli")
            _git(checkout, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", f"QCA M008 experiment: {name}")
            candidate = _git(checkout, "rev-parse", "HEAD")
            report = analyze_diff(M008, candidate, path=checkout, config=config)
            test_rc, tests = _execute(checkout, "run_tests.py", output / f"{name}-tests.json")
            probe_rc, probe = _execute(checkout, "workbench_probe.py", output / f"{name}-probe.json")
            baseline_digest = hashlib.sha256(
                json.dumps(baseline_probe, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            candidate_digest = hashlib.sha256(
                json.dumps(probe, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            equal_trace = baseline_probe == probe
            changed_coverage = _changed_coverage(checkout, candidate, baseline_tests, tests)
            behavior_passed = test_rc == probe_rc == 0 and equal_trace and tests["tests_run"] == baseline_tests["tests_run"]
            verification = {
                "schema": "qca/verification/v1", "base_sha": M008, "head_sha": candidate,
                "provenance": {
                    "runner": "python3 -m qca.experiments.refine_m008",
                    "candidate_patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
                    "scope": "deterministic synthetic replay, consumer tests, and in-process line coverage",
                },
                "factors": {
                    "test_effectiveness": {
                        "status": "passed" if test_rc == 0 else "failed",
                        "commands": ["python3 qca/experiments/run_tests.py"],
                        "results": [{"returncode": test_rc, "tests_run": tests["tests_run"], "failures": tests["failures"], "errors": tests["errors"]}],
                        "changed_line_coverage": changed_coverage,
                    },
                    "end_to_end": {
                        "status": "passed" if probe_rc == 0 and equal_trace else "failed",
                        "commands": ["python3 qca/experiments/workbench_probe.py"],
                        "results": [{
                            "returncode": probe_rc,
                            "baseline_trace_equal": equal_trace,
                            "baseline_digest": baseline_digest,
                            "candidate_digest": candidate_digest,
                        }],
                        "expected": {"trace_digest": baseline_digest},
                        "actual": {"trace_digest": candidate_digest},
                    },
                    "ui_behavior": {"status": "not_measured", "reason": "No browser interaction was executed; API replay does not establish visual behavior."},
                    "lifecycle": {"status": "not_measured", "reason": "Consult replay-probe observations; no complete filesystem/process/network side-effect audit was executed."},
                },
            }
            report.factors = attach_verification(report.factors, verification, M008, candidate)
            payload = report_to_dict(report)
            _json(output / f"{name}-verification.json", verification)
            # The complete milestone report has full snapshots. Candidate
            # results retain concrete changed targets/deltas without repeating
            # the unchanged repository inventories for every trial.
            trial = {
                "id": name, "scale": scale, "hypothesis": hypothesis,
                "patch": patch.relative_to(ROOT).as_posix(), "candidate_sha": candidate,
                "candidate_tree": _git(checkout, "rev-parse", "HEAD^{tree}"),
                "tests_run": tests["tests_run"], "failures": tests["failures"], "errors": tests["errors"],
                "trace_equal": equal_trace, "probe_returncode": probe_rc,
                "changed_line_coverage": changed_coverage,
                "decision": "supported_by_checks" if behavior_passed else "rejected_by_checks",
                "measurements": _compact(payload),
                "changed_callables": payload["diff"]["changed_callables"],
                "contract_changes": payload["factors"]["contracts"].get("surface_changes", {}),
                "verification": verification,
            }
            trials.append(trial)
    result = {
        "schema": "qca/refinement-experiment/v1", "analyzer_version": states[0]["identity"]["analyzer_version"],
        "hypothesis": manifest["experiment"]["hypothesis"], "configuration": config.canonical(),
        "baseline_sha": M008, "states": states, "trials": trials,
        "negative_control": {
            "attempted": 1,
            "detected": sum(item["id"] == "skip_validation" and item["failures"] > 0 for item in trials),
            "interpretation": "One deliberate mutation; not a general mutation score.",
        },
        "limitations": [
            "Synthetic replay and existing tests cover a bounded sample, not universal behavior equivalence.",
            "Visual Chrome interactions and full side-effect monitoring were not executed.",
            "Static measurements repeat; runtime logs, source fixture directories, and elapsed durations can vary.",
            "Candidate commits are reconstructed locally from the pinned parent, patches, and fixed commit metadata.",
        ],
    }
    _json(output / "experiment.json", result)
    (output / "experiment.md").write_text(_render_experiment(result), encoding="utf-8")
    return result


def _render_experiment(result: dict) -> str:
    lines = [
        "# M008 refined QCA experiment",
        "",
        f"- schema: `{result['schema']}`",
        f"- analyzer: `{result['analyzer_version']}`",
        f"- baseline: `{result['baseline_sha']}`",
        f"- negative control detected: `{result['negative_control']['detected']}`",
        "",
        result["hypothesis"],
        "",
        "## Historical states",
        "",
        "| State | Files | Core churn | Decision burden Δ |",
        "| --- | ---: | ---: | ---: |",
    ]
    for state in result["states"]:
        lines.append(
            f"| `{state['id']}` | {state['files']} | {state['core_churn']} | "
            f"{state['decision_burden_delta']:+d} |"
        )
    lines.extend(["", "## Trials", ""])
    for trial in result["trials"]:
        burden = trial["measurements"]["decision_burden_delta"]
        lines.extend(
            [
                f"### `{trial['id']}` ({trial['scale']})",
                "",
                trial["hypothesis"],
                "",
                f"- decision: `{trial['decision']}`",
                f"- tests: {trial['tests_run']} run, {trial['failures']} failed, {trial['errors']} errors",
                f"- replay trace equal: `{trial['trace_equal']}`",
                f"- decision-burden Δ: {burden:+d}",
                f"- candidate: `{trial['candidate_sha']}`",
                "",
            ]
        )
    lines.extend(["## Limitations", ""])
    lines.extend(f"- {item}" for item in result["limitations"])
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.output_dir.resolve())
