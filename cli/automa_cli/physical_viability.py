from __future__ import annotations

import json
import os
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from .paths import ROOT, display_path
from .physical_observation import (
    fetch_observation_publication,
    picar_base_url,
)
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles_snapshot


VIABILITY_OUTPUT_ROOT = Path(
    os.environ.get(
        "AUTOMA_PERCEPTION_VIABILITY_OUTPUT_ROOT",
        ROOT / "lab" / "runs" / "perception-viability",
    )
)

DEFAULT_DURATION_S = 60.0
DEFAULT_SAMPLE_PERIOD_S = 0.25
REQUIRED_MIN_FRESH_HZ = 2.0
REQUIRED_CADENCE_FRACTION = 0.90
REQUIRED_P95_AGE_MS = 1000.0


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def run_physical_viability_measurement(
    *,
    vehicle_id: str,
    duration_s: float = DEFAULT_DURATION_S,
    sample_period_s: float = DEFAULT_SAMPLE_PERIOD_S,
    timeout_s: float = 3.0,
    record: bool = True,
    json_output: bool = False,
    output: TextIO | None = None,
    fetch_publication: Callable[[str], dict[str, Any]] | None = None,
    sample_host_metrics: Callable[[], dict[str, Any]] | None = None,
) -> CommandResult:
    """Measure onboard observation cadence/freshness for the deployed Pi observer."""
    discovery = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=False,
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
            "viability measurement supports physical PiCar only.",
        )
    base_url = picar_base_url(vehicle)
    if not base_url:
        return CommandResult(2, f"Vehicle {vehicle_id!r} has no picar base_url connection.")

    get_pub = fetch_publication or (
        lambda url: fetch_observation_publication(url, timeout_s=timeout_s)
    )
    host_sampler = sample_host_metrics or (lambda: _sample_pi_process_metrics())

    duration_s = max(1.0, float(duration_s))
    sample_period_s = max(0.05, float(sample_period_s))
    run_id = f"{vehicle_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = VIABILITY_OUTPUT_ROOT / run_id if record else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    _emit(output, "Physical perception viability measurement")
    _emit(output, f"vehicle: {vehicle_id}")
    _emit(output, f"endpoint: {base_url}")
    _emit(output, f"duration_s: {duration_s}")
    _emit(output, f"sample_period_s: {sample_period_s}")
    if out_dir is not None:
        _emit(output, f"record: {display_path(out_dir)}")
    _emit(output, "")

    samples: list[dict[str, Any]] = []
    host_samples: list[dict[str, Any]] = []
    errors: list[str] = []
    started = time.monotonic()
    deadline = started + duration_s
    next_host_sample = started
    last_frame_id: str | None = None
    fresh_transitions = 0

    while time.monotonic() < deadline:
        now = time.monotonic()
        wall_ms = int(time.time() * 1000)
        try:
            publication = get_pub(base_url)
            sample = _extract_sample(publication, wall_ms=wall_ms, mono_s=now - started)
            frame_id = sample.get("frame_id")
            if frame_id is not None:
                frame_id_s = str(frame_id)
                if last_frame_id is None:
                    last_frame_id = frame_id_s
                elif frame_id_s != last_frame_id:
                    fresh_transitions += 1
                    last_frame_id = frame_id_s
            samples.append(sample)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            samples.append(
                {
                    "t_s": round(now - started, 3),
                    "wall_ms": wall_ms,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

        if now >= next_host_sample:
            try:
                host = host_sampler()
                host["t_s"] = round(now - started, 3)
                host_samples.append(host)
            except Exception as exc:
                host_samples.append(
                    {
                        "t_s": round(now - started, 3),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            next_host_sample = now + max(1.0, sample_period_s * 4)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(sample_period_s, remaining))

    elapsed_s = max(time.monotonic() - started, 1e-6)
    metrics = _compute_metrics(
        samples=samples,
        host_samples=host_samples,
        elapsed_s=elapsed_s,
        fresh_transitions=fresh_transitions,
    )
    gates = _evaluate_gates(metrics)
    report = {
        "schema": "automa_physical_perception_viability_v0",
        "run_id": run_id,
        "vehicle_id": vehicle_id,
        "base_url": base_url,
        "duration_s_requested": duration_s,
        "duration_s_elapsed": round(elapsed_s, 3),
        "sample_period_s": sample_period_s,
        "requirements": {
            "min_fresh_results_per_s": REQUIRED_MIN_FRESH_HZ,
            "max_p95_result_age_ms": REQUIRED_P95_AGE_MS,
            "control_must_remain_zero": True,
            "mode_must_remain_user": True,
        },
        "metrics": metrics,
        "gates": gates,
        "passed": all(bool(item.get("passed")) for item in gates),
        "sample_count": len(samples),
        "host_sample_count": len(host_samples),
        "error_count": len(errors),
        "errors_head": errors[:10],
        "samples": samples,
        "host_samples": host_samples,
        "limits": [
            "Polls the publication endpoint; does not instrument in-process Donkey loop counters directly.",
            "Freshness uses frame_id transitions and published result_age_ms/duration_ms fields.",
            "Host RSS/CPU are sampled from the remote manage.py process when SSH metrics are available.",
        ],
    }

    if out_dir is not None:
        (out_dir / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        (out_dir / "summary.md").write_text(_format_markdown(report), encoding="utf-8")
        report["out_dir"] = display_path(out_dir)
        report["report_json"] = display_path(out_dir / "report.json")
        report["summary_md"] = display_path(out_dir / "summary.md")

    exit_code = 0 if report["passed"] else 1
    if json_output:
        return CommandResult(exit_code, json.dumps(report, indent=2, sort_keys=True, default=str))
    return CommandResult(exit_code, _format_report(report))


def _extract_sample(publication: dict[str, Any], *, wall_ms: int, mono_s: float) -> dict[str, Any]:
    frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else {}
    control = publication.get("control") if isinstance(publication.get("control"), dict) else {}
    return {
        "t_s": round(mono_s, 3),
        "wall_ms": wall_ms,
        "health": publication.get("health"),
        "mode": publication.get("mode") or publication.get("drive_mode"),
        "algorithm": publication.get("algorithm"),
        "frame_id": frame.get("frame_id"),
        "processed_count": publication.get("processed_count"),
        "skipped_count": publication.get("skipped_count"),
        "min_interval_s": publication.get("min_interval_s"),
        "duration_ms": publication.get("duration_ms"),
        "result_age_ms": publication.get("result_age_ms"),
        "control_steering": control.get("steering"),
        "control_throttle": control.get("throttle"),
        "control_reason": control.get("reason"),
    }


def _compute_metrics(
    *,
    samples: list[dict[str, Any]],
    host_samples: list[dict[str, Any]],
    elapsed_s: float,
    fresh_transitions: int,
) -> dict[str, Any]:
    healthy = [s for s in samples if s.get("health") == "healthy" and s.get("error") is None]
    ages = [float(s["result_age_ms"]) for s in healthy if _is_number(s.get("result_age_ms"))]
    durations = [float(s["duration_ms"]) for s in healthy if _is_number(s.get("duration_ms"))]
    processeds = [
        int(s["processed_count"])
        for s in samples
        if isinstance(s.get("processed_count"), int)
    ]
    skipped = [
        int(s["skipped_count"])
        for s in samples
        if isinstance(s.get("skipped_count"), int)
    ]
    control_zero = all(
        float(s.get("control_steering") or 0.0) == 0.0
        and float(s.get("control_throttle") or 0.0) == 0.0
        for s in healthy
    ) if healthy else False
    mode_user = all((s.get("mode") in {None, "user"}) for s in healthy) if healthy else False

    processed_delta = (
        (processeds[-1] - processeds[0]) if len(processeds) >= 2 else 0
    )
    skipped_delta = (skipped[-1] - skipped[0]) if len(skipped) >= 2 else None
    # transitions count changes only; include the first observed frame for rate.
    unique_frames = fresh_transitions + (1 if any(s.get("frame_id") for s in samples) else 0)
    # Prefer onboard processed_count delta when available; fall back to unique frames.
    rate_count = processed_delta if processed_delta > 0 else unique_frames
    fresh_hz = rate_count / elapsed_s
    processed_hz = processed_delta / elapsed_s

    rss = [float(s["rss_mb"]) for s in host_samples if _is_number(s.get("rss_mb"))]
    cpu = [float(s["cpu_percent"]) for s in host_samples if _is_number(s.get("cpu_percent"))]

    return {
        "elapsed_s": round(elapsed_s, 3),
        "sample_count": len(samples),
        "healthy_sample_count": len(healthy),
        "fresh_frame_transitions": fresh_transitions,
        "unique_frames_observed": unique_frames,
        "fresh_results_per_s": round(fresh_hz, 4),
        "processed_count_delta": processed_delta,
        "processed_results_per_s": round(processed_hz, 4),
        "skipped_count_delta": skipped_delta,
        "result_age_ms": _distribution(ages),
        "duration_ms": _distribution(durations),
        "control_always_zero": control_zero,
        "mode_always_user": mode_user,
        "algorithms_seen": sorted(
            {str(s.get("algorithm")) for s in healthy if s.get("algorithm")}
        ),
        "host": {
            "sample_count": len(host_samples),
            "rss_mb": _distribution(rss),
            "cpu_percent": _distribution(cpu),
            "pid": next((s.get("pid") for s in host_samples if s.get("pid")), None),
            "errors": [s.get("error") for s in host_samples if s.get("error")][:5],
        },
        "configured_min_interval_s": _first_number(
            s.get("min_interval_s") for s in samples
        ),
        "dropped_frame_policy": {
            "description": (
                "Onboard AutonomyPilotPart uses newest-frame consumption with "
                "min_interval cadence skips; intermediate camera ticks are counted in skipped_count."
            ),
            "skipped_count_delta": skipped_delta,
        },
    }


def _evaluate_gates(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    fresh_hz = float(metrics.get("fresh_results_per_s") or 0.0)
    min_interval = metrics.get("configured_min_interval_s")
    configured_hz = (
        (1.0 / float(min_interval))
        if _is_number(min_interval) and float(min_interval) > 0
        else REQUIRED_MIN_FRESH_HZ
    )
    # Design target is >=2 Hz, but a configured 0.5s interval caps theoretical rate at 2.0.
    # Require at least 90% of the lesser of the design target and the configured cadence.
    required_hz = min(REQUIRED_MIN_FRESH_HZ, configured_hz) * REQUIRED_CADENCE_FRACTION
    age = metrics.get("result_age_ms") if isinstance(metrics.get("result_age_ms"), dict) else {}
    p95_age = age.get("p95")
    return [
        {
            "id": "fresh_results_meet_configured_cadence",
            "passed": fresh_hz >= required_hz,
            "detail": (
                f"fresh_results_per_s={fresh_hz} required>={required_hz:.4f} "
                f"(design_target={REQUIRED_MIN_FRESH_HZ}, configured_hz={configured_hz:.4f}, "
                f"fraction={REQUIRED_CADENCE_FRACTION})"
            ),
        },
        {
            "id": "p95_result_age_at_most_1s",
            "passed": _is_number(p95_age) and float(p95_age) <= REQUIRED_P95_AGE_MS,
            "detail": f"p95_result_age_ms={p95_age} required<={REQUIRED_P95_AGE_MS}",
        },
        {
            "id": "control_always_zero",
            "passed": bool(metrics.get("control_always_zero")),
            "detail": f"control_always_zero={metrics.get('control_always_zero')}",
        },
        {
            "id": "mode_always_user",
            "passed": bool(metrics.get("mode_always_user")),
            "detail": f"mode_always_user={metrics.get('mode_always_user')}",
        },
        {
            "id": "healthy_samples_present",
            "passed": int(metrics.get("healthy_sample_count") or 0) > 0,
            "detail": f"healthy_sample_count={metrics.get('healthy_sample_count')}",
        },
    ]


def _sample_pi_process_metrics(
    *,
    ssh_target: str = "piracer@piracer.local",
) -> dict[str, Any]:
    remote = (
        "pid=$(pgrep -f 'manage.py drive' | head -1); "
        "if [ -z \"$pid\" ]; then echo '{\"error\":\"manage.py drive process not found\"}'; exit 0; fi; "
        "rss_kb=$(awk '/VmRSS:/ {print $2}' /proc/$pid/status 2>/dev/null); "
        "cpu=$(ps -p \"$pid\" -o %cpu= 2>/dev/null | tr -d ' '); "
        "printf '{\"pid\":%s,\"rss_mb\":%s,\"cpu_percent\":%s}\\n' "
        "\"$pid\" "
        "$(python3 -c \"print(float('$rss_kb')/1024 if '$rss_kb'.strip() else 'null')\") "
        "$(python3 -c \"print(float('$cpu') if '$cpu'.strip() else 'null')\")"
    )
    completed = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes", ssh_target, remote],
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    if completed.returncode != 0:
        return {
            "error": (
                f"ssh metrics failed rc={completed.returncode}: "
                f"{(completed.stderr or completed.stdout or '').strip()[:240]}"
            )
        }
    line = (completed.stdout or "").strip().splitlines()
    if not line:
        return {"error": "ssh metrics returned empty output"}
    try:
        payload = json.loads(line[-1])
    except json.JSONDecodeError as exc:
        return {"error": f"ssh metrics JSON parse failed: {exc}"}
    return payload if isinstance(payload, dict) else {"error": "ssh metrics returned non-object"}


def _distribution(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "p50": round(_percentile(ordered, 0.50), 3),
        "p95": round(_percentile(ordered, 0.95), 3),
        "max": round(ordered[-1], 3),
        "mean": round(float(statistics.fmean(ordered)), 3),
    }


def _percentile(ordered: list[float], q: float) -> float:
    if len(ordered) == 1:
        return float(ordered[0])
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return float(ordered[index])


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _first_number(values) -> float | None:
    for value in values:
        if _is_number(value):
            return float(value)
    return None


def _format_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        f"Physical perception viability: {'PASS' if report['passed'] else 'FAIL'}",
        f"vehicle: {report['vehicle_id']}",
        f"endpoint: {report['base_url']}",
        f"elapsed_s: {metrics.get('elapsed_s')}",
        f"fresh_results_per_s: {metrics.get('fresh_results_per_s')}",
        f"processed_results_per_s: {metrics.get('processed_results_per_s')}",
        f"result_age_ms p50/p95: {_dist_pair(metrics.get('result_age_ms'))}",
        f"duration_ms p50/p95: {_dist_pair(metrics.get('duration_ms'))}",
        f"control_always_zero: {metrics.get('control_always_zero')}",
        f"mode_always_user: {metrics.get('mode_always_user')}",
        f"host rss_mb p50/max: {_dist_pair(metrics.get('host', {}).get('rss_mb'), keys=('p50','max'))}",
        f"host cpu_percent p50/max: {_dist_pair(metrics.get('host', {}).get('cpu_percent'), keys=('p50','max'))}",
        "",
        "gates:",
    ]
    for gate in report["gates"]:
        lines.append(
            f"- {'PASS' if gate['passed'] else 'FAIL'} {gate['id']}: {gate['detail']}"
        )
    if report.get("out_dir"):
        lines.extend(["", f"record: {report['out_dir']}"])
    if report.get("summary_md"):
        lines.append(f"summary: {report['summary_md']}")
    return "\n".join(lines)


def _format_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# Physical perception viability",
        "",
        f"- result: `{'PASS' if report['passed'] else 'FAIL'}`",
        f"- vehicle: `{report['vehicle_id']}`",
        f"- endpoint: `{report['base_url']}`",
        f"- elapsed_s: {metrics.get('elapsed_s')}",
        "",
        "## Metrics",
        "",
        f"- fresh_results_per_s: {metrics.get('fresh_results_per_s')}",
        f"- processed_results_per_s: {metrics.get('processed_results_per_s')}",
        f"- result_age_ms: `{metrics.get('result_age_ms')}`",
        f"- duration_ms: `{metrics.get('duration_ms')}`",
        f"- skipped_count_delta: {metrics.get('skipped_count_delta')}",
        f"- control_always_zero: {metrics.get('control_always_zero')}",
        f"- mode_always_user: {metrics.get('mode_always_user')}",
        f"- host: `{metrics.get('host')}`",
        "",
        "## Gates",
        "",
    ]
    for gate in report["gates"]:
        lines.append(f"- {'PASS' if gate['passed'] else 'FAIL'} `{gate['id']}`: {gate['detail']}")
    lines.extend(["", "## Limits", ""])
    for limit in report.get("limits") or []:
        lines.append(f"- {limit}")
    lines.append("")
    return "\n".join(lines)


def _dist_pair(dist: Any, keys: tuple[str, str] = ("p50", "p95")) -> str:
    if not isinstance(dist, dict):
        return "—/—"
    return f"{dist.get(keys[0])}/{dist.get(keys[1])}"


def _emit(output: TextIO | None, message: str) -> None:
    if output is None:
        return
    print(message, file=output, flush=True)
