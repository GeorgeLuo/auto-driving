from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from implementations.operations.capture_pulse_sequence import (
    CapturePulseStep,
    run_capture_pulse_sequence,
    safe_label_suffix,
)
from implementations.operations.artifact_writers import write_contact_sheet, write_diff_artifact, write_json
from implementations.perception.observation import compare_frame_pair
from autonomy.vehicle import CarInterface

from .scoring import check_startup_action_result
from .types import StartupActionCheckPlan


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def run_startup_action_check(
    *,
    car: CarInterface,
    plan: StartupActionCheckPlan,
    out_dir: Path,
    image_extension: str = "jpg",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute a startup plan on any `CarInterface` implementation."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    diffs_dir = out_dir / "diffs"
    plan_path = out_dir / "plan.json"
    write_json(plan_path, plan.to_dict())

    started_ms = timestamp_ms()
    sequence_results = run_capture_pulse_sequence(
        car=car,
        steps=[
            CapturePulseStep(label=instruction.label, pulse=instruction.pulse)
            for instruction in plan.instructions
        ],
        frames_dir=frames_dir,
        frame_endpoint=plan.frame_endpoint,
        image_extension=image_extension,
        dry_run=dry_run,
    )

    results: list[dict[str, Any]] = []
    noise_floor: dict[str, float] | None = None
    for instruction, sequence_result in zip(plan.instructions, sequence_results):
        index = int(sequence_result["index"])
        label = safe_label_suffix(instruction.label)
        before_path = Path(sequence_result["before_path"])
        after_path = Path(sequence_result["after_path"])
        diff_path = diffs_dir / f"{index:02d}_{label}_diff.jpg"
        comparison = compare_frame_pair(
            before_path,
            after_path,
            pixel_threshold=plan.comparison_pixel_threshold,
        )
        diff_artifact = write_diff_artifact(before_path, after_path, diff_path)
        passed, failure_reasons = check_startup_action_result(
            instruction=instruction,
            plan=plan,
            comparison=comparison,
            noise_floor=noise_floor,
        )
        if not instruction.expect_change:
            noise_floor = {
                "mean_abs_diff_norm": float(comparison.get("mean_abs_diff_norm") or 0.0),
                "changed_pixel_ratio": float(comparison.get("changed_pixel_ratio") or 0.0),
            }

        result = {
            "index": index,
            "instruction": instruction.to_dict(),
            "dry_run": dry_run,
            "before_capture": sequence_result["before_capture"],
            "after_capture": sequence_result["after_capture"],
            "before_observation": sequence_result["before_observation"],
            "after_observation": sequence_result["after_observation"],
            "comparison": comparison,
            "diff_path": diff_artifact,
            "command": sequence_result["command"],
            "passed": passed,
            "failure_reasons": failure_reasons,
        }
        results.append(result)

    passed_count = sum(1 for result in results if result["passed"])
    report = {
        "run_id": out_dir.name,
        "run_type": "startup_action_check",
        "created_at_ms": started_ms,
        "completed_at_ms": timestamp_ms(),
        "vehicle": car.capabilities.to_dict(),
        "plan_path": str(plan_path),
        "plan": plan.to_dict(),
        "dry_run": dry_run,
        "checks_total": len(results),
        "checks_passed": passed_count,
        "passed": passed_count == len(results),
        "results": results,
    }
    write_json(out_dir / "report.json", report)
    _write_summary(out_dir / "summary.md", report)
    write_contact_sheet(out_dir / "contact_sheet.jpg", results)
    return report


def _write_summary(path: Path, report: dict[str, Any]) -> None:
    lines = [
        f"# Startup Action Check: {report['run_id']}",
        "",
        f"- Vehicle: `{report['vehicle'].get('vehicle_kind')}` / `{report['vehicle'].get('vehicle_id')}`",
        f"- Overall passed: `{report['passed']}`",
        f"- Checks passed: `{report['checks_passed']}/{report['checks_total']}`",
        "",
        "| Check | Expected | Passed | Mean diff | Changed pixels | Notes |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for result in report["results"]:
        comparison = result["comparison"]
        expected = "change" if result["instruction"]["expect_change"] else "still"
        notes = "; ".join(result.get("failure_reasons") or result["instruction"].get("notes") or [])
        lines.append(
            "| {label} | {expected} | {passed} | {mean:.5f} | {changed:.5f} | {notes} |".format(
                label=result["instruction"]["label"],
                expected=expected,
                passed=result["passed"],
                mean=float(comparison.get("mean_abs_diff_norm") or 0.0),
                changed=float(comparison.get("changed_pixel_ratio") or 0.0),
                notes=notes.replace("|", "\\|"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
