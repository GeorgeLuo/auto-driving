from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autonomy.runtime.execution import ControlTarget
from implementations.operations import (
    build_basic_startup_action_check_plan,
    run_startup_action_check,
)
from .paths import ROOT, display_path
from .vehicle_access import create_vehicle_access
from .vehicles import (
    discover_active_vehicles,
    find_vehicle_by_id,
    format_active_vehicles,
)


OPERATION_OUTPUT_ROOT = Path(
    os.environ.get("AUTOMA_OPERATION_OUTPUT_ROOT", ROOT / "lab" / "runs" / "startup-check")
)


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def run_vehicle_startup_check(
    *,
    vehicle_id: str,
    timeout_s: float = 8.0,
    throttle: float = 0.22,
    duration_s: float = 0.3,
    settle_s: float = 0.35,
    dry_run: bool = False,
    json_output: bool = False,
) -> CommandResult:
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
                    "Discovery:",
                    format_active_vehicles(discovery, include_inactive=True),
                ]
            ),
        )
    if vehicle is None:
        return CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")

    preparation: dict[str, Any] | None = None
    try:
        access = create_vehicle_access(vehicle, timeout_s=timeout_s)
    except ValueError as exc:
        return CommandResult(2, str(exc))
    car = access.car
    image_extension = access.image_extension
    run_id = f"{vehicle_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir = OPERATION_OUTPUT_ROOT / run_id
    try:
        plan = build_basic_startup_action_check_plan(
            throttle=throttle,
            duration_s=duration_s,
            settle_s=settle_s,
        )
    except (TypeError, ValueError) as exc:
        return CommandResult(2, f"Invalid startup action check plan: {exc}")

    # Dry runs stay passive: capture frames, but do not acquire control.
    # A live check acquires through the vehicle's control target, then releases.
    acquired = False
    if not dry_run:
        try:
            preparation = _acquire_control(access.control, vehicle_id)
            acquired = True
        except Exception as exc:
            return CommandResult(2, f"Could not prepare vehicle {vehicle_id!r}: {exc}")

    release_error: Exception | None = None
    try:
        report = run_startup_action_check(
            car=car,
            plan=plan,
            out_dir=out_dir,
            image_extension=image_extension,
            dry_run=dry_run,
        )
    except BaseException as exc:
        if acquired:
            try:
                car.stop()
            except Exception:
                pass
        if isinstance(exc, Exception):
            return CommandResult(2, f"Startup action check failed before completion: {exc}")
        raise
    finally:
        if acquired:
            try:
                _release_control(access.control)
            except Exception as exc:
                release_error = exc
    if release_error is not None:
        return CommandResult(2, f"Could not release vehicle {vehicle_id!r}: {release_error}")

    payload = _compact_startup_report(report, out_dir, preparation)
    exit_code = 0 if report["passed"] else 1
    if json_output:
        return CommandResult(exit_code, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(exit_code, _format_startup_report(payload))


def _acquire_control(control: ControlTarget | None, vehicle_id: str) -> dict[str, Any] | None:
    if control is None:
        raise RuntimeError("no control target")
    receipt = control.acquire()
    if receipt is None:
        return None
    if not isinstance(receipt, dict):
        raise RuntimeError(
            f"control target for {vehicle_id!r} returned {type(receipt).__name__}, expected a receipt"
        )
    return receipt


def _release_control(control: ControlTarget | None) -> None:
    if control is None:
        return
    control.release()


def _compact_startup_report(
    report: dict[str, Any],
    out_dir: Path,
    preparation: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema": "automa_startup_action_check_result_v0",
        "run_id": report["run_id"],
        "out_dir": str(out_dir),
        "vehicle": report["vehicle"],
        "preparation": preparation,
        "dry_run": report["dry_run"],
        "checks_total": report["checks_total"],
        "checks_passed": report["checks_passed"],
        "passed": report["passed"],
        "artifacts": {
            "report": str(out_dir / "report.json"),
            "summary": str(out_dir / "summary.md"),
            "contact_sheet": str(out_dir / "contact_sheet.jpg"),
        },
        "results": [
            {
                "label": result["instruction"]["label"],
                "expected": "change" if result["instruction"]["expect_change"] else "still",
                "passed": result["passed"],
                "mean_abs_diff_norm": result["comparison"]["mean_abs_diff_norm"],
                "changed_pixel_ratio": result["comparison"]["changed_pixel_ratio"],
                "failure_reasons": result["failure_reasons"],
            }
            for result in report["results"]
        ],
    }


def _format_startup_report(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Startup action check: {payload['run_id']}",
            f"Result: {'passed' if payload['passed'] else 'failed'} "
            f"({payload['checks_passed']}/{payload['checks_total']} checks)",
            f"Artifacts: {display_path(Path(payload['out_dir']))}",
            "",
            *[
                f"- {result['label']}: {'pass' if result['passed'] else 'fail'} "
                f"(mean diff={result['mean_abs_diff_norm']:.5f}, "
                f"changed={result['changed_pixel_ratio']:.5f})"
                for result in payload["results"]
            ],
        ]
    )
