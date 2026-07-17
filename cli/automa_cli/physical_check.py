from __future__ import annotations

import html
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from .paths import ROOT, display_path
from .physical_observation import (
    fetch_observation_frame,
    fetch_observation_publication,
    picar_base_url,
)
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles_snapshot


CHECK_OUTPUT_ROOT = Path(
    os.environ.get(
        "AUTOMA_PERCEPTION_CHECK_OUTPUT_ROOT",
        ROOT / "lab" / "runs" / "perception-check",
    )
)

# Default path excludes unavailable: forcing a camera fault is optional and risky.
DEFAULT_PLACEMENTS: tuple[str, ...] = (
    "clear",
    "left",
    "center",
    "right",
    "removed",
)
OPTIONAL_PLACEMENTS: tuple[str, ...] = ("unavailable",)
PLACEMENTS: tuple[str, ...] = DEFAULT_PLACEMENTS + OPTIONAL_PLACEMENTS

PLACEMENT_PROMPTS: dict[str, str] = {
    "clear": "Clear floor: remove objects from the camera view. Leave open floor ahead.",
    "left": "Object left: place a contrasting floor-standing object on the LEFT side of the view.",
    "center": "Object center: place the object in the CENTER of the view.",
    "right": "Object right: place the object on the RIGHT side of the view.",
    "removed": "Object removed: remove the object so the floor is clear again.",
    "unavailable": (
        "Camera unavailable (optional): cover the lens only if you accept that risk. "
        "This step is not in the default sequence."
    ),
}


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def run_physical_perception_check(
    *,
    vehicle_id: str,
    timeout_s: float = 3.0,
    fresh_timeout_s: float = 12.0,
    record: bool = False,
    auto: bool = False,
    steps: tuple[str, ...] | None = None,
    from_run: Path | None = None,
    json_output: bool = False,
    output: TextIO | None = None,
    input_fn: Callable[[str], str] | None = None,
    fetch_publication: Callable[[str], dict[str, Any]] | None = None,
    fetch_frame: Callable[[str], tuple[bytes, dict[str, str]]] | None = None,
) -> CommandResult:
    """Guided stationary physical check against onboard latest observation."""
    if from_run is not None:
        return rescore_physical_perception_check_run(
            from_run,
            steps=steps,
            json_output=json_output,
            output=output,
        )
    selected_steps = _normalize_steps(steps)
    discovery = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=True,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
    if error:
        return CommandResult(
            2,
            "\n\n".join(
                [
                    error,
                    "Discovery snapshot:",
                    format_active_vehicles_snapshot(discovery, include_inactive=True),
                ]
            ),
        )
    if vehicle is None:
        return CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")
    if vehicle.get("provider") != "picar":
        return CommandResult(
            2,
            f"Vehicle {vehicle_id!r} is provider {vehicle.get('provider')!r}; "
            "perception check is only supported for physical PiCar targets.",
        )
    base_url = picar_base_url(vehicle)
    if not base_url:
        return CommandResult(2, f"Vehicle {vehicle_id!r} has no picar base_url connection.")

    run_id = f"{vehicle_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = CHECK_OUTPUT_ROOT / run_id if record else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    get_publication = fetch_publication or (
        lambda url: fetch_observation_publication(url, timeout_s=timeout_s)
    )
    get_frame = fetch_frame or (
        lambda url: fetch_observation_frame(url, timeout_s=timeout_s)
    )
    prompt = input_fn or input

    _emit(output, "Physical perception check")
    _emit(output, f"vehicle: {vehicle_id}")
    _emit(output, f"endpoint: {base_url}")
    _emit(output, "movement: never commanded (manual placement only)")
    if out_dir is not None:
        _emit(output, f"record: {display_path(out_dir)}")
    else:
        _emit(output, "record: disabled (pass --record to keep frames and review)")
    _emit(output, "")

    step_results: list[dict[str, Any]] = []
    previous_publication: dict[str, Any] | None = None
    last_frame_id: str | None = None
    object_boundary_counts: list[int] = []

    for index, placement in enumerate(selected_steps, start=1):
        _emit(output, f"[{index}/{len(selected_steps)}] {placement}")
        _emit(output, PLACEMENT_PROMPTS[placement])
        if not auto:
            try:
                prompt("Press Enter when the placement is ready (Ctrl-C to abort)... ")
            except KeyboardInterrupt:
                return CommandResult(130, "Physical perception check aborted.")
        else:
            _emit(output, "(auto mode: capturing without prompt)")

        try:
            publication = _wait_for_fresh_publication(
                base_url=base_url,
                get_publication=get_publication,
                previous_frame_id=last_frame_id,
                timeout_s=fresh_timeout_s,
            )
        except ConnectionError as exc:
            return CommandResult(2, f"Could not fetch onboard observation: {exc}")
        except TimeoutError as exc:
            return CommandResult(2, str(exc))

        frame_meta = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
        frame_id = str(frame_meta.get("frame_id") or "")
        last_frame_id = frame_id or last_frame_id

        frame_bytes: bytes | None = None
        frame_headers: dict[str, str] = {}
        frame_error: str | None = None
        if frame_meta.get("has_image"):
            try:
                frame_bytes, frame_headers = get_frame(base_url)
            except ConnectionError as exc:
                frame_error = str(exc)

        baseline = max(object_boundary_counts) if object_boundary_counts else None
        score = score_placement(
            placement=placement,
            publication=publication,
            previous_publication=previous_publication,
            object_boundary_baseline=baseline,
        )
        step = {
            "placement": placement,
            "prompt": PLACEMENT_PROMPTS[placement],
            "captured_at_ms": _timestamp_ms(),
            "publication": _bounded_publication(publication),
            "frame_id": frame_id or None,
            "frame_headers": frame_headers,
            "frame_error": frame_error,
            "score": score,
        }

        if out_dir is not None:
            step_dir = out_dir / f"{index:02d}-{placement}"
            step_dir.mkdir(parents=True, exist_ok=True)
            publication_path = step_dir / "publication.json"
            publication_path.write_text(
                json.dumps(publication, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
            frame_path = None
            if frame_bytes:
                frame_path = step_dir / "frame.jpg"
                frame_path.write_bytes(frame_bytes)
            step["artifacts"] = {
                "publication": display_path(publication_path),
                "frame": display_path(frame_path) if frame_path is not None else None,
            }
            score_path = step_dir / "score.json"
            score_path.write_text(
                json.dumps(score, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            step["artifacts"]["score"] = display_path(score_path)

        step_results.append(step)
        previous_publication = publication
        if placement in {"left", "center", "right"}:
            object_boundary_counts.append(int(score.get("boundary_count") or 0))
        _emit(
            output,
            (
                f"  result: {'PASS' if score['passed'] else 'FAIL'}  "
                f"health={score['health']}  frame={frame_id or 'none'}  "
                f"control_zero={score['control_zero']}  checks={score['summary']}"
            ),
        )
        _emit(output, "")

    passed = all(bool(step["score"]["passed"]) for step in step_results)
    report = {
        "schema": "automa_physical_perception_check_v0",
        "run_id": run_id,
        "vehicle_id": vehicle_id,
        "base_url": base_url,
        "recorded": out_dir is not None,
        "out_dir": display_path(out_dir) if out_dir is not None else None,
        "steps": selected_steps,
        "passed": passed,
        "step_results": step_results,
        "safety": {
            "movement_commands_sent": False,
            "mode_change_commands_sent": False,
            "expected_drive_mode": "user",
        },
    }

    if out_dir is not None:
        report_path = out_dir / "report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        review_path = out_dir / "review.html"
        review_path.write_text(_render_review_html(report), encoding="utf-8")
        report["review_html"] = display_path(review_path)
        report["report_json"] = display_path(report_path)

    exit_code = 0 if passed else 1
    if json_output:
        return CommandResult(exit_code, json.dumps(report, indent=2, sort_keys=True, default=str))
    return CommandResult(exit_code, _format_report(report))


def rescore_physical_perception_check_run(
    run_dir: Path,
    *,
    steps: tuple[str, ...] | None = None,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Re-score an existing recorded check from saved publications/frames."""
    root = Path(run_dir)
    if not root.is_dir():
        return CommandResult(2, f"Check run directory not found: {display_path(root)}")

    report_path = root / "report.json"
    prior_report: dict[str, Any] = {}
    if report_path.exists():
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prior_report = loaded
        except json.JSONDecodeError as exc:
            return CommandResult(2, f"Could not parse {display_path(report_path)}: {exc}")

    step_dirs = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and path.name[:2].isdigit() and "-" in path.name
    )
    if not step_dirs:
        return CommandResult(2, f"No step directories found under {display_path(root)}")

    allow = set(_normalize_steps(steps)) if steps is not None else None
    step_results: list[dict[str, Any]] = []
    previous_publication: dict[str, Any] | None = None
    object_boundary_counts: list[int] = []

    for step_dir in step_dirs:
        _, _, placement = step_dir.name.partition("-")
        placement = placement.strip().lower()
        if placement not in PLACEMENTS:
            continue
        if allow is not None and placement not in allow:
            continue
        publication_path = step_dir / "publication.json"
        if not publication_path.exists():
            return CommandResult(2, f"Missing publication for step {step_dir.name}")
        try:
            publication = json.loads(publication_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return CommandResult(2, f"Could not parse {display_path(publication_path)}: {exc}")
        if not isinstance(publication, dict):
            return CommandResult(2, f"Publication is not an object: {display_path(publication_path)}")

        baseline = max(object_boundary_counts) if object_boundary_counts else None
        score = score_placement(
            placement=placement,
            publication=publication,
            previous_publication=previous_publication,
            object_boundary_baseline=baseline,
        )
        frame_meta = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
        frame_path = step_dir / "frame.jpg"
        captured_at_ms = None
        for prior in prior_report.get("step_results") or []:
            if isinstance(prior, dict) and prior.get("placement") == placement:
                captured_at_ms = prior.get("captured_at_ms")
                break
        step = {
            "placement": placement,
            "prompt": PLACEMENT_PROMPTS.get(placement, ""),
            "captured_at_ms": captured_at_ms,
            "publication": _bounded_publication(publication),
            "frame_id": frame_meta.get("frame_id"),
            "frame_headers": {},
            "frame_error": None if frame_path.exists() else "frame.jpg missing in recorded run",
            "score": score,
            "artifacts": {
                "publication": display_path(publication_path),
                "frame": display_path(frame_path) if frame_path.exists() else None,
                "score": display_path(step_dir / "score.json"),
            },
        }

        (step_dir / "score.json").write_text(
            json.dumps(score, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        step_results.append(step)
        previous_publication = publication
        if placement in {"left", "center", "right"}:
            object_boundary_counts.append(int(score.get("boundary_count") or 0))
        _emit(
            output,
            (
                f"{placement}: {'PASS' if score['passed'] else 'FAIL'}  "
                f"zones={score.get('zones')}  failed={score.get('failed_checks') or []}"
            ),
        )

    if not step_results:
        return CommandResult(2, f"No matching steps to rescore under {display_path(root)}")

    passed = all(bool(step["score"]["passed"]) for step in step_results)
    report = {
        "schema": "automa_physical_perception_check_v0",
        "run_id": prior_report.get("run_id") or root.name,
        "vehicle_id": prior_report.get("vehicle_id") or "unknown",
        "base_url": prior_report.get("base_url"),
        "recorded": True,
        "rescored": True,
        "out_dir": display_path(root),
        "steps": [step["placement"] for step in step_results],
        "passed": passed,
        "step_results": step_results,
        "safety": prior_report.get("safety")
        or {
            "movement_commands_sent": False,
            "mode_change_commands_sent": False,
            "expected_drive_mode": "user",
        },
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    review_path = root / "review.html"
    review_path.write_text(_render_review_html(report), encoding="utf-8")
    report["review_html"] = display_path(review_path)
    report["report_json"] = display_path(report_path)

    exit_code = 0 if passed else 1
    if json_output:
        return CommandResult(exit_code, json.dumps(report, indent=2, sort_keys=True, default=str))
    return CommandResult(exit_code, _format_report(report))


def score_placement(
    *,
    placement: str,
    publication: dict[str, Any],
    previous_publication: dict[str, Any] | None = None,
    object_boundary_baseline: int | None = None,
) -> dict[str, Any]:
    """Score one placement using generic floor-boundary evidence only."""
    health = str(publication.get("health") or "unknown")
    control = publication.get("control") if isinstance(publication.get("control"), dict) else {}
    steering = _as_float(control.get("steering"))
    throttle = _as_float(control.get("throttle"))
    control_zero = steering == 0.0 and throttle == 0.0
    mode = publication.get("mode") or publication.get("drive_mode")
    perception = publication.get("perception") if isinstance(publication.get("perception"), dict) else {}
    signals = perception.get("signals") if isinstance(perception.get("signals"), list) else []
    things = perception.get("things") if isinstance(perception.get("things"), list) else []
    floor_visible = _signal_bool(signals, "floor_visible")
    boundaries = [
        thing
        for thing in things
        if isinstance(thing, dict) and str(thing.get("kind") or "") == "floor_boundary"
    ]
    zones = [_boundary_zone(thing) for thing in boundaries]
    checks: list[dict[str, Any]] = []

    checks.append(
        {
            "id": "control_zero",
            "passed": control_zero,
            "detail": f"steering={steering} throttle={throttle}",
        }
    )
    checks.append(
        {
            "id": "manual_mode",
            "passed": mode in {None, "user"},
            "detail": f"mode={mode}",
        }
    )

    if placement == "unavailable":
        checks.append(
            {
                "id": "unavailable_or_stale",
                "passed": health in {"unavailable", "stale", "error", "absent", "warming"},
                "detail": f"health={health}",
            }
        )
        checks.append(
            {
                "id": "no_fresh_boundaries",
                "passed": health != "healthy" or len(boundaries) == 0,
                "detail": f"boundary_count={len(boundaries)}",
            }
        )
    else:
        checks.append(
            {
                "id": "healthy_publication",
                "passed": health in {"healthy", "stale"},
                "detail": f"health={health}",
            }
        )
        checks.append(
            {
                "id": "has_frame_identity",
                "passed": isinstance((publication.get("frame") or {}).get("frame_id"), str)
                and bool((publication.get("frame") or {}).get("frame_id")),
                "detail": f"frame_id={(publication.get('frame') or {}).get('frame_id')}",
            }
        )

        if placement == "clear":
            checks.append(
                {
                    "id": "floor_visible",
                    "passed": floor_visible is True,
                    "detail": f"floor_visible={floor_visible}",
                }
            )
            strong_center = [zone for zone in zones if zone == "center"]
            checks.append(
                {
                    "id": "no_strong_central_boundary",
                    "passed": len(strong_center) == 0,
                    "detail": f"center_boundaries={len(strong_center)} all_zones={zones}",
                }
            )
        elif placement in {"left", "center", "right"}:
            target = placement
            if target == "center":
                target_hit = (
                    "center" in zones
                    or ("left" in zones and "right" in zones)
                )
                detail = f"zones={zones} (center, or left+right span)"
            else:
                target_hit = target in zones
                detail = f"zones={zones}"
            checks.append(
                {
                    "id": f"boundary_{target}",
                    "passed": target_hit,
                    "detail": detail,
                }
            )
            checks.append(
                {
                    "id": "has_boundary",
                    "passed": len(boundaries) > 0,
                    "detail": f"boundary_count={len(boundaries)}",
                }
            )
        elif placement == "removed":
            previous_boundaries = 0
            if previous_publication is not None:
                previous_boundaries = _boundary_count(previous_publication)
            baseline = (
                int(object_boundary_baseline)
                if object_boundary_baseline is not None
                else previous_boundaries
            )
            cleared = len(boundaries) == 0 or (
                baseline > 0 and len(boundaries) < baseline
            )
            checks.append(
                {
                    "id": "boundary_cleared_or_reduced",
                    "passed": cleared,
                    "detail": (
                        f"boundaries_now={len(boundaries)} "
                        f"baseline_object_boundaries={baseline} "
                        f"previous_step_boundaries={previous_boundaries}"
                    ),
                }
            )
            checks.append(
                {
                    "id": "floor_visible_after_removal",
                    "passed": floor_visible is not False,
                    "detail": f"floor_visible={floor_visible}",
                }
            )

    passed = all(bool(item["passed"]) for item in checks)
    failed = [item["id"] for item in checks if not item["passed"]]
    return {
        "passed": passed,
        "health": health,
        "control_zero": control_zero,
        "mode": mode,
        "floor_visible": floor_visible,
        "boundary_count": len(boundaries),
        "zones": zones,
        "checks": checks,
        "failed_checks": failed,
        "summary": "ok" if passed else ",".join(failed) or "failed",
    }


def _wait_for_fresh_publication(
    *,
    base_url: str,
    get_publication: Callable[[str], dict[str, Any]],
    previous_frame_id: str | None,
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.5, float(timeout_s))
    last_error: str | None = None
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            payload = get_publication(base_url)
        except ConnectionError as exc:
            last_error = str(exc)
            time.sleep(0.25)
            continue
        last_payload = payload
        frame = payload.get("frame") if isinstance(payload.get("frame"), dict) else {}
        frame_id = frame.get("frame_id")
        health = payload.get("health")
        if previous_frame_id is None:
            if frame_id or health in {"unavailable", "stale", "error", "absent", "warming"}:
                return payload
        else:
            if frame_id and frame_id != previous_frame_id:
                return payload
            if health in {"unavailable", "stale", "error", "absent"} and previous_frame_id:
                # Unavailable may not advance frame ids; accept health transition.
                return payload
        time.sleep(0.25)
    if last_payload is not None:
        return last_payload
    raise TimeoutError(
        f"Timed out after {timeout_s}s waiting for a fresh onboard observation"
        + (f": {last_error}" if last_error else "")
    )


def _normalize_steps(steps: tuple[str, ...] | None) -> tuple[str, ...]:
    if not steps:
        return DEFAULT_PLACEMENTS
    normalized: list[str] = []
    for step in steps:
        name = str(step).strip().lower()
        if name not in PLACEMENTS:
            raise ValueError(
                f"unknown placement step {step!r}; expected one of {', '.join(PLACEMENTS)}"
            )
        if name not in normalized:
            normalized.append(name)
    if not normalized:
        raise ValueError("at least one placement step is required")
    return tuple(normalized)


def _boundary_count(publication: dict[str, Any]) -> int:
    perception = publication.get("perception") if isinstance(publication.get("perception"), dict) else {}
    things = perception.get("things") if isinstance(perception.get("things"), list) else []
    return sum(
        1
        for thing in things
        if isinstance(thing, dict) and str(thing.get("kind") or "") == "floor_boundary"
    )


def _boundary_zone(thing: dict[str, Any]) -> str:
    """Resolve horizontal zone from thing.zone, location.zone, or bbox center."""
    location = thing.get("location") if isinstance(thing.get("location"), dict) else {}
    raw = thing.get("zone") or location.get("zone") or ""
    bucket = _zone_bucket(str(raw))
    if bucket in {"left", "center", "right"}:
        return bucket

    box = location.get("bbox_xyxy_norm")
    if isinstance(box, (list, tuple)) and len(box) >= 4:
        try:
            center_x = (float(box[0]) + float(box[2])) / 2.0
        except (TypeError, ValueError):
            return bucket or "unknown"
        if center_x < 1.0 / 3.0:
            return "left"
        if center_x > 2.0 / 3.0:
            return "right"
        return "center"
    return bucket or "unknown"


def _bounded_publication(publication: dict[str, Any]) -> dict[str, Any]:
    """Keep report JSON useful without embedding full nested noise twice."""
    perception = publication.get("perception") if isinstance(publication.get("perception"), dict) else None
    compact_perception = None
    if perception is not None:
        compact_perception = {
            "status": perception.get("status"),
            "schema": perception.get("schema"),
            "signals": perception.get("signals"),
            "things": perception.get("things"),
        }
    return {
        "health": publication.get("health"),
        "ok": publication.get("ok"),
        "algorithm": publication.get("algorithm"),
        "mode": publication.get("mode") or publication.get("drive_mode"),
        "result_age_ms": publication.get("result_age_ms"),
        "duration_ms": publication.get("duration_ms"),
        "processed_count": publication.get("processed_count"),
        "skipped_count": publication.get("skipped_count"),
        "control": publication.get("control"),
        "frame": publication.get("frame"),
        "perception": compact_perception,
    }


def _signal_bool(signals: list[Any], signal_id: str) -> bool | None:
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        if str(signal.get("signal_id") or signal.get("id") or "") != signal_id:
            continue
        value = signal.get("value")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
    return None


def _zone_bucket(zone: str) -> str:
    text = zone.lower()
    if "left" in text:
        return "left"
    if "right" in text:
        return "right"
    if "center" in text or "centre" in text or text in {"mid", "middle"}:
        return "center"
    return text or "unknown"


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _format_report(report: dict[str, Any]) -> str:
    lines = [
        f"Physical perception check: {'PASS' if report['passed'] else 'FAIL'}",
        f"vehicle: {report['vehicle_id']}",
        f"endpoint: {report['base_url']}",
        f"steps: {', '.join(report['steps'])}",
    ]
    if report.get("out_dir"):
        lines.append(f"record: {report['out_dir']}")
    if report.get("review_html"):
        lines.append(f"review: {report['review_html']}")
    lines.append("")
    for step in report["step_results"]:
        score = step["score"]
        lines.append(
            f"- {step['placement']}: {'PASS' if score['passed'] else 'FAIL'} "
            f"(health={score['health']}, frame={step.get('frame_id') or 'none'}, "
            f"zones={score.get('zones')}, failed={score.get('failed_checks') or []})"
        )
    lines.extend(
        [
            "",
            "safety: no movement or mode-change commands were sent",
        ]
    )
    return "\n".join(lines)


def _render_review_html(report: dict[str, Any]) -> str:
    rows: list[str] = []
    for index, step in enumerate(report["step_results"], start=1):
        score = step["score"]
        artifacts = step.get("artifacts") if isinstance(step.get("artifacts"), dict) else {}
        frame_rel = None
        if artifacts.get("frame"):
            # Store relative path for portable review.
            frame_rel = f"{index:02d}-{step['placement']}/frame.jpg"
        checks = "".join(
            f"<li class='{'ok' if check['passed'] else 'bad'}'>"
            f"{html.escape(check['id'])}: {html.escape(str(check['detail']))}</li>"
            for check in score.get("checks") or []
        )
        image = (
            f"<img src='{html.escape(frame_rel)}' alt='{html.escape(step['placement'])} frame'/>"
            if frame_rel
            else "<p class='muted'>No frame captured</p>"
        )
        rows.append(
            f"""
<section class="step {'pass' if score['passed'] else 'fail'}">
  <h2>{index}. {html.escape(step['placement'])} — {'PASS' if score['passed'] else 'FAIL'}</h2>
  <p>{html.escape(step.get('prompt') or '')}</p>
  <div class="grid">
    <div>{image}</div>
    <div>
      <p><strong>frame:</strong> {html.escape(str(step.get('frame_id') or 'none'))}</p>
      <p><strong>health:</strong> {html.escape(str(score.get('health')))}</p>
      <p><strong>zones:</strong> {html.escape(str(score.get('zones')))}</p>
      <p><strong>control zero:</strong> {html.escape(str(score.get('control_zero')))}</p>
      <ul>{checks}</ul>
    </div>
  </div>
</section>
"""
        )
    body = "\n".join(rows)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Physical perception check {html.escape(report['run_id'])}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #122; background: #f7f8fa; }}
    h1,h2 {{ margin: 0 0 8px; }}
    .meta {{ margin-bottom: 20px; color: #345; }}
    .step {{ background: #fff; border: 1px solid #d7dde7; border-radius: 10px; padding: 16px; margin: 0 0 16px; }}
    .step.pass {{ border-left: 5px solid #1a7f37; }}
    .step.fail {{ border-left: 5px solid #b42318; }}
    .grid {{ display: grid; grid-template-columns: minmax(220px, 360px) 1fr; gap: 16px; }}
    img {{ max-width: 100%; border-radius: 8px; border: 1px solid #ccd; background: #111; }}
    ul {{ margin: 8px 0; padding-left: 18px; }}
    li.ok {{ color: #1a7f37; }}
    li.bad {{ color: #b42318; }}
    .muted {{ color: #667; }}
    @media (max-width: 800px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <h1>Physical perception check: {'PASS' if report['passed'] else 'FAIL'}</h1>
  <div class="meta">
    <div>run: {html.escape(report['run_id'])}</div>
    <div>vehicle: {html.escape(report['vehicle_id'])}</div>
    <div>endpoint: {html.escape(report['base_url'])}</div>
    <div>safety: no movement commands were sent</div>
  </div>
  {body}
</body>
</html>
"""


def _emit(output: TextIO | None, message: str) -> None:
    if output is None:
        return
    print(message, file=output, flush=True)


def _timestamp_ms() -> int:
    return int(time.time() * 1000)
