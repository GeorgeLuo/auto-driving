from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.perception import build_perception_request
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.perception.catalog import (
    DEFAULT_PERCEPTION_ALGORITHM,
    PERCEPTION_ALGORITHMS,
    PERCEPTION_MAPPER_SPEC,
)

from .lab_plugins import LabPerceptionMapper
from .paths import ROOT, display_path
from .perception import _close_mapper, _load_mapper
from .physical_check import score_placement


QUALIFY_OUTPUT_ROOT = Path(
    os.environ.get(
        "AUTOMA_PERCEPTION_QUALIFY_OUTPUT_ROOT",
        ROOT / "lab" / "runs" / "perception-qualify",
    )
)

DEFAULT_CONTROL_ALGORITHM = DEFAULT_PERCEPTION_ALGORITHM
DEFAULT_CANDIDATE_ID = "floor_continuity"
DEFAULT_PLACEMENTS = ("clear", "left", "center", "right", "removed")


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def run_physical_strategy_qualification(
    *,
    check_run: Path,
    control_algorithm: str = DEFAULT_CONTROL_ALGORITHM,
    candidate_id: str = DEFAULT_CANDIDATE_ID,
    steps: tuple[str, ...] | None = None,
    extra_frames: tuple[tuple[str, Path], ...] = (),
    record: bool = True,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Compare packaged control vs one lab candidate on labeled physical-check frames.

    This is an offline common-frame qualification. It does not measure onboard Pi
    latency or promote a candidate into the stable deploy path by itself.
    """
    run_dir = Path(check_run).expanduser().resolve()
    if not run_dir.is_dir():
        return CommandResult(2, f"Physical-check run not found: {display_path(run_dir)}")

    selected = tuple(steps) if steps else DEFAULT_PLACEMENTS
    frames = _load_labeled_frames(run_dir, selected)
    for placement, path in extra_frames:
        if not path.is_file():
            return CommandResult(2, f"Extra frame missing for {placement}: {path}")
        frames.append(
            {
                "placement": placement,
                "frame_path": path.resolve(),
                "source": "extra",
                "label": f"extra-{placement}-{path.stem}",
            }
        )
    if not frames:
        return CommandResult(2, f"No labeled frames found under {display_path(run_dir)}")

    if control_algorithm not in PERCEPTION_ALGORITHMS:
        return CommandResult(2, f"Unknown control algorithm {control_algorithm!r}")

    out_dir = None
    if record:
        out_dir = QUALIFY_OUTPUT_ROOT / f"{run_dir.name}-{time.strftime('%Y%m%d-%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)

    _emit(output, "Physical strategy qualification (offline common-frame)")
    _emit(output, f"source check run: {display_path(run_dir)}")
    _emit(output, f"control: {control_algorithm} (packaged floor-plane path)")
    _emit(output, f"candidate: {candidate_id} (lab)")
    _emit(output, f"frames: {len(frames)}")
    if out_dir is not None:
        _emit(output, f"record: {display_path(out_dir)}")
    _emit(output, "")

    try:
        control_results = _run_strategy_on_frames(
            strategy_id=control_algorithm,
            kind="control",
            frames=frames,
            output=output,
        )
        candidate_results = _run_strategy_on_frames(
            strategy_id=candidate_id,
            kind="candidate",
            frames=frames,
            output=output,
        )
    except Exception as exc:
        return CommandResult(2, f"Qualification apply failed: {type(exc).__name__}: {exc}")

    control_metrics = _strategy_metrics(control_results)
    candidate_metrics = _strategy_metrics(candidate_results)
    comparison = _compare_metrics(control_metrics, candidate_metrics)
    decision = _promotion_decision(comparison, control_metrics, candidate_metrics)

    report = {
        "schema": "automa_physical_strategy_qualification_v0",
        "source_check_run": display_path(run_dir),
        "control": {
            "id": control_algorithm,
            "kind": "packaged",
            "role": "floor-plane-v0 control via lightweight_observer",
            "metrics": control_metrics,
            "frames": control_results,
        },
        "candidate": {
            "id": candidate_id,
            "kind": "lab",
            "role": "floor-continuity-v1",
            "metrics": candidate_metrics,
            "frames": candidate_results,
        },
        "comparison": comparison,
        "decision": decision,
        "limits": [
            "Offline desktop apply only; not an onboard Raspberry Pi latency/RSS measurement.",
            "Placement labels come from the human-guided physical-check folder names.",
            "Scores use generic floor_boundary zone/presence checks, not semantic object identity.",
            "Promotion to stable deploy still requires explicit package activation and Pi viability evidence.",
        ],
        "recorded": out_dir is not None,
        "out_dir": display_path(out_dir) if out_dir is not None else None,
    }

    if out_dir is not None:
        (out_dir / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        (out_dir / "summary.md").write_text(_format_markdown(report), encoding="utf-8")
        report["summary_md"] = display_path(out_dir / "summary.md")
        report["report_json"] = display_path(out_dir / "report.json")
        # tracked milestone evidence copy is written by the caller/tests when desired

    exit_code = 0 if decision.get("status") in {"reject_keep_control", "promote_candidate"} else 1
    # reject and promote are both valid completed outcomes
    if decision.get("status") in {"reject_keep_control", "promote_candidate"}:
        exit_code = 0
    if json_output:
        return CommandResult(exit_code, json.dumps(report, indent=2, sort_keys=True, default=str))
    return CommandResult(exit_code, _format_report(report))


def _load_labeled_frames(run_dir: Path, selected: tuple[str, ...]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for step_dir in sorted(path for path in run_dir.iterdir() if path.is_dir()):
        if not step_dir.name[:2].isdigit() or "-" not in step_dir.name:
            continue
        placement = step_dir.name.split("-", 1)[1].strip().lower()
        if placement not in selected:
            continue
        frame_path = step_dir / "frame.jpg"
        if not frame_path.is_file():
            continue
        frames.append(
            {
                "placement": placement,
                "frame_path": frame_path.resolve(),
                "source": "physical_check",
                "label": step_dir.name,
            }
        )
    return frames


def _run_strategy_on_frames(
    *,
    strategy_id: str,
    kind: str,
    frames: list[dict[str, Any]],
    output: TextIO | None,
) -> list[dict[str, Any]]:
    _emit(output, f"Applying {kind}={strategy_id}...")
    results: list[dict[str, Any]] = []
    object_boundary_counts: list[int] = []
    previous_payload: dict[str, Any] | None = None

    if kind == "control":
        algorithm_config = PERCEPTION_ALGORITHMS[strategy_id]
        mapper = _load_mapper(
            str(algorithm_config["mapper_spec"]),
            dict(algorithm_config["mapper_config"]),
        )
        mapper_context = None
    else:
        mapper = LabPerceptionMapper(strategy_id)
        mapper_context = mapper

    try:
        active = mapper
        if mapper_context is not None:
            mapper_context.__enter__()
        active.reset()
        for index, frame in enumerate(frames):
            image_path = Path(frame["frame_path"])
            placement = str(frame["placement"])
            frame_id = f"{strategy_id}-{frame['label']}"
            captured_at_ms = int(image_path.stat().st_mtime * 1000)
            snapshot = SensorSnapshot(
                read_id=frame_id,
                readings={
                    FRONT_CAMERA_SENSOR_ID: SensorReading(
                        sensor_id=FRONT_CAMERA_SENSOR_ID,
                        sensor_kind="camera",
                        captured_at_ms=captured_at_ms,
                        path=str(image_path),
                        metadata={"source": "physical_qualify", "placement": placement},
                    )
                },
                started_at_ms=captured_at_ms,
                completed_at_ms=captured_at_ms,
                metadata={"source": "physical_qualify"},
            )
            started = time.perf_counter()
            perception = active.perceive(build_perception_request(snapshot))
            duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
            perception_dict = perception.to_dict()
            payload = _perception_to_score_payload(
                perception_dict=perception_dict,
                frame_id=frame_id,
                duration_ms=duration_ms,
            )
            baseline = max(object_boundary_counts) if object_boundary_counts else None
            score = score_placement(
                placement=placement,
                publication=payload,
                previous_publication=previous_payload,
                object_boundary_baseline=baseline,
            )
            item = {
                "placement": placement,
                "label": frame["label"],
                "source": frame["source"],
                "frame_path": display_path(image_path),
                "duration_ms": duration_ms,
                "perception_status": perception.status,
                "boundary_count": score.get("boundary_count"),
                "zones": score.get("zones"),
                "score": score,
                "signals": [
                    {
                        "signal_id": item.get("signal_id") or item.get("id"),
                        "value": item.get("value"),
                        "confidence": item.get("confidence"),
                    }
                    for item in (perception_dict.get("signals") or [])
                    if isinstance(item, dict)
                ],
            }
            results.append(item)
            previous_payload = payload
            if placement in {"left", "center", "right"}:
                object_boundary_counts.append(int(score.get("boundary_count") or 0))
            _emit(
                output,
                (
                    f"  {placement}: {'PASS' if score['passed'] else 'FAIL'} "
                    f"zones={score.get('zones')} boundaries={score.get('boundary_count')} "
                    f"{duration_ms}ms"
                ),
            )
    finally:
        if mapper_context is not None:
            mapper_context.__exit__(None, None, None)
        else:
            _close_mapper(mapper)
    return results


def _perception_to_score_payload(
    *,
    perception_dict: dict[str, Any],
    frame_id: str,
    duration_ms: float,
) -> dict[str, Any]:
    status = str(perception_dict.get("status") or "error")
    return {
        "health": "healthy" if status == "ok" else "error",
        "ok": status == "ok",
        "mode": "user",
        "duration_ms": duration_ms,
        "control": {
            "steering": 0.0,
            "throttle": 0.0,
            "confidence": 1.0,
            "reason": "offline-qualify-apply",
            "metadata": {},
        },
        "frame": {
            "frame_id": frame_id,
            "has_image": True,
        },
        "perception": perception_dict,
    }


def _strategy_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_placement: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_placement.setdefault(str(item["placement"]), []).append(item)

    def pass_rate(placement: str) -> float | None:
        rows = by_placement.get(placement) or []
        if not rows:
            return None
        return sum(1 for row in rows if row["score"]["passed"]) / len(rows)

    object_rows = [
        row
        for placement in ("left", "center", "right")
        for row in (by_placement.get(placement) or [])
    ]
    clear_rows = by_placement.get("clear") or []
    removed_rows = by_placement.get("removed") or []

    clear_fp = [
        int(row["score"].get("boundary_count") or 0)
        for row in clear_rows
    ]
    durations = [float(row["duration_ms"]) for row in results]

    directional_hits = 0
    directional_total = 0
    for placement in ("left", "center", "right"):
        for row in by_placement.get(placement) or []:
            directional_total += 1
            checks = {
                item["id"]: item["passed"]
                for item in row["score"].get("checks") or []
                if isinstance(item, dict)
            }
            if checks.get(f"boundary_{placement}"):
                directional_hits += 1

    return {
        "frame_count": len(results),
        "placement_pass_rate": {
            placement: pass_rate(placement) for placement in sorted(by_placement)
        },
        "overall_pass_rate": (
            sum(1 for row in results if row["score"]["passed"]) / len(results)
            if results
            else 0.0
        ),
        "clear_false_positive_boundaries_mean": (
            sum(clear_fp) / len(clear_fp) if clear_fp else None
        ),
        "directional_zone_hit_rate": (
            directional_hits / directional_total if directional_total else None
        ),
        "removal_pass_rate": pass_rate("removed"),
        "mean_boundary_count": (
            sum(int(row["score"].get("boundary_count") or 0) for row in results) / len(results)
            if results
            else 0.0
        ),
        "mean_object_boundary_count": (
            sum(int(row["score"].get("boundary_count") or 0) for row in object_rows) / len(object_rows)
            if object_rows
            else None
        ),
        "median_duration_ms": _median(durations),
        "p95_duration_ms": _percentile(durations, 0.95),
    }


def _compare_metrics(control: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    measures: list[dict[str, Any]] = []

    def add(
        name: str,
        *,
        higher_is_better: bool,
        control_value: Any,
        candidate_value: Any,
        material_min_delta: float,
    ) -> None:
        if control_value is None or candidate_value is None:
            measures.append(
                {
                    "name": name,
                    "control": control_value,
                    "candidate": candidate_value,
                    "delta": None,
                    "improved": False,
                    "regressed": False,
                    "material": False,
                    "higher_is_better": higher_is_better,
                }
            )
            return
        delta = float(candidate_value) - float(control_value)
        improved = delta > material_min_delta if higher_is_better else delta < -material_min_delta
        regressed = delta < -material_min_delta if higher_is_better else delta > material_min_delta
        measures.append(
            {
                "name": name,
                "control": control_value,
                "candidate": candidate_value,
                "delta": round(delta, 6),
                "improved": improved,
                "regressed": regressed,
                "material": improved,
                "higher_is_better": higher_is_better,
            }
        )

    add(
        "overall_pass_rate",
        higher_is_better=True,
        control_value=control.get("overall_pass_rate"),
        candidate_value=candidate.get("overall_pass_rate"),
        material_min_delta=0.05,
    )
    add(
        "directional_zone_hit_rate",
        higher_is_better=True,
        control_value=control.get("directional_zone_hit_rate"),
        candidate_value=candidate.get("directional_zone_hit_rate"),
        material_min_delta=0.05,
    )
    add(
        "clear_false_positive_boundaries_mean",
        higher_is_better=False,
        control_value=control.get("clear_false_positive_boundaries_mean"),
        candidate_value=candidate.get("clear_false_positive_boundaries_mean"),
        material_min_delta=0.25,
    )
    add(
        "removal_pass_rate",
        higher_is_better=True,
        control_value=control.get("removal_pass_rate"),
        candidate_value=candidate.get("removal_pass_rate"),
        material_min_delta=0.05,
    )
    add(
        "mean_boundary_count",
        higher_is_better=False,
        control_value=control.get("mean_boundary_count"),
        candidate_value=candidate.get("mean_boundary_count"),
        material_min_delta=0.5,
    )
    # latency is informational only for offline desktop apply
    add(
        "median_duration_ms_desktop",
        higher_is_better=False,
        control_value=control.get("median_duration_ms"),
        candidate_value=candidate.get("median_duration_ms"),
        material_min_delta=5.0,
    )
    material_improvements = [m["name"] for m in measures if m.get("material")]
    material_regressions = [
        m["name"]
        for m in measures
        if m.get("regressed") and m["name"] != "median_duration_ms_desktop"
    ]
    return {
        "measures": measures,
        "material_improvements": material_improvements,
        "material_regressions": material_regressions,
        "material_improvement_count": len(material_improvements),
    }


def _promotion_decision(
    comparison: dict[str, Any],
    control: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    improvements = list(comparison.get("material_improvements") or [])
    regressions = list(comparison.get("material_regressions") or [])
    # Desktop latency is not a promotion gate for onboard deploy.
    behavioral_improvements = [
        name for name in improvements if name != "median_duration_ms_desktop"
    ]
    if len(behavioral_improvements) >= 2 and not regressions:
        status = "promote_candidate"
        rationale = (
            "Candidate improved at least two material behavioral measures without "
            "material behavioral regressions on this labeled set."
        )
    else:
        status = "reject_keep_control"
        if len(behavioral_improvements) < 2:
            rationale = (
                "Candidate did not improve at least two material behavioral measures "
                "on the labeled physical-check frames; keep packaged floor-plane control."
            )
        else:
            rationale = (
                "Candidate improved behavioral measures but also regressed others; "
                "keep packaged floor-plane control."
            )
    return {
        "status": status,
        "promote": status == "promote_candidate",
        "rationale": rationale,
        "behavioral_improvements": behavioral_improvements,
        "behavioral_regressions": regressions,
        "control_remains_operational_fallback": True,
        "onboard_pi_viability_measured": False,
        "control_overall_pass_rate": control.get("overall_pass_rate"),
        "candidate_overall_pass_rate": candidate.get("overall_pass_rate"),
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return float(ordered[index])


def _format_report(report: dict[str, Any]) -> str:
    decision = report["decision"]
    control = report["control"]["metrics"]
    candidate = report["candidate"]["metrics"]
    lines = [
        f"Physical strategy qualification: {decision['status']}",
        f"source: {report['source_check_run']}",
        f"control: {report['control']['id']} pass_rate={control.get('overall_pass_rate')}",
        f"candidate: {report['candidate']['id']} pass_rate={candidate.get('overall_pass_rate')}",
        f"material improvements: {decision.get('behavioral_improvements') or []}",
        f"material regressions: {decision.get('behavioral_regressions') or []}",
        f"decision: {decision['rationale']}",
    ]
    if report.get("out_dir"):
        lines.append(f"record: {report['out_dir']}")
    if report.get("summary_md"):
        lines.append(f"summary: {report['summary_md']}")
    lines.append("")
    lines.append("measure comparison:")
    for measure in report["comparison"]["measures"]:
        lines.append(
            f"- {measure['name']}: control={measure['control']} candidate={measure['candidate']} "
            f"delta={measure['delta']} improved={measure['improved']} regressed={measure['regressed']}"
        )
    lines.extend(
        [
            "",
            "limits: offline desktop apply only; packaged floor-plane remains operational fallback "
            "unless an explicit promotion decision is accepted with Pi viability evidence.",
        ]
    )
    return "\n".join(lines)


def _format_markdown(report: dict[str, Any]) -> str:
    decision = report["decision"]
    lines = [
        f"# Physical strategy qualification",
        "",
        f"- status: `{decision['status']}`",
        f"- source: `{report['source_check_run']}`",
        f"- control: `{report['control']['id']}`",
        f"- candidate: `{report['candidate']['id']}`",
        "",
        "## Decision",
        "",
        decision["rationale"],
        "",
        f"- behavioral improvements: {decision.get('behavioral_improvements') or []}",
        f"- behavioral regressions: {decision.get('behavioral_regressions') or []}",
        f"- onboard Pi viability measured: {decision.get('onboard_pi_viability_measured')}",
        "",
        "## Metrics",
        "",
        "| measure | control | candidate | delta | improved | regressed |",
        "|---|---:|---:|---:|---|---|",
    ]
    for measure in report["comparison"]["measures"]:
        lines.append(
            f"| {measure['name']} | {measure['control']} | {measure['candidate']} | "
            f"{measure['delta']} | {measure['improved']} | {measure['regressed']} |"
        )
    lines.extend(["", "## Limits", ""])
    for limit in report.get("limits") or []:
        lines.append(f"- {limit}")
    lines.append("")
    return "\n".join(lines)


def _emit(output: TextIO | None, message: str) -> None:
    if output is None:
        return
    print(message, file=output, flush=True)
