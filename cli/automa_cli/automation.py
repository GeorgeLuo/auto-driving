from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision import DecisionFrameContext, DecisionStages
from autonomy.perception import PerceptionRequest
from autonomy.runtime import AutonomyManager
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim import ChaseSimCar
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError

from .bundles import controller_bundle_paths
from .decision import load_decision_activation
from .paths import display_path, safe_path_part
from .perception import (
    _load_mapper,
)
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles_snapshot


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))
AUTOMA_EXECUTABLE = ROOT / "cli" / "automa"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def run_vehicle_automation(
    *,
    vehicle_id: str,
    timeout_s: float = 3.0,
    interval_s: float = 1.0,
    frames: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    payload = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=True,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(payload, vehicle_id)
    if error:
        return CommandResult(
            2,
            "\n\n".join(
                [
                    error,
                    "Discovery snapshot:",
                    format_active_vehicles_snapshot(payload, include_inactive=True),
                ]
            ),
        )
    if vehicle is None:
        return CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")
    if vehicle.get("provider") != "chase-sim":
        return CommandResult(
            2,
            f"Vehicle {vehicle_id!r} is provider {vehicle.get('provider')!r}; automation run currently supports chase-sim.",
        )

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    if not manifest_path.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No active perception algorithm found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(manifest_path)}",
                    f"Run: ./cli/automa vehicles update perception --id {vehicle_id}",
                ]
            ),
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mapper_spec = _manifest_get_str(manifest, "perception", "mapper_spec")
    if mapper_spec is None:
        return CommandResult(2, f"Activation {display_path(manifest_path)} does not define perception.mapper_spec.")
    mapper_config = _manifest_get_dict(manifest, "perception", "mapper_config")
    bundle_root = Path(_manifest_get_str(manifest, "controller_bundle", "root_dir") or bundle["root_dir"])
    try:
        decision_activation = load_decision_activation(bundle)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return CommandResult(2, str(exc))
    decision_config = decision_activation["decision"]

    connection = vehicle.get("connection") if isinstance(vehicle.get("connection"), dict) else {}
    ws_url = connection.get("ws_url") if isinstance(connection.get("ws_url"), str) else None
    car = ChaseSimCar(ws_url=ws_url, timeout_s=timeout_s, vehicle_id=vehicle_id)
    mapper = _load_mapper(mapper_spec, mapper_config, bundle_root=bundle_root)

    def perceive_stage(context: DecisionFrameContext):
        if context.sensor_snapshot is None:
            return None
        output_dir_text = context.metadata.get("perception_output_dir")
        output_dir = Path(output_dir_text) if isinstance(output_dir_text, str) else None
        return mapper.perceive(
            PerceptionRequest(
                snapshot=context.sensor_snapshot,
                output_dir=output_dir,
                metadata={
                    "vehicle_id": vehicle_id,
                    "run_id": run_id,
                    "frame_index": context.frame_index,
                    "activation": str(manifest_path),
                    "recording": bool(record),
                },
            )
        )

    engine_manager = AutonomyManager(
        default_engine_spec=decision_config["engine_spec"],
        default_engine_config=dict(decision_config["engine_config"]),
    )
    cycle_host = AutonomyCycleHost(
        manager=engine_manager,
        stages=DecisionStages(
            perceive=perceive_stage,
        ),
    )

    automation_dir = Path(bundle["runtime_dir"]) / "automation"
    run_id = _now_id("automation")
    run_dir = automation_dir / "runs" / run_id if record else None
    frames_dir = run_dir / "frames" if run_dir is not None else automation_dir / "latest" / "frames"
    perception_dir = run_dir / "perception" if run_dir is not None else automation_dir / "latest" / "perception"
    state_path = automation_dir / "state.json"
    latest_json_path = automation_dir / "latest_perception.json"
    latest_text_path = automation_dir / "latest_perception.txt"
    latest_front_camera_path = frames_dir / f"latest_{FRONT_CAMERA_SENSOR_ID}.png"
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)

    max_frames = max(0, int(frames))
    state = {
        "schema": "automa_automation_run_state_v0",
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "status": "running",
        "pid": os.getpid(),
        "started_at_ms": _timestamp_ms(),
        "updated_at_ms": _timestamp_ms(),
        "frames_processed": 0,
        "max_frames": None if max_frames == 0 else max_frames,
        "interval_s": max(0.0, float(interval_s)),
        "control_source": "external_ws" if take_control else "simulator",
        "action_policy": "engine_idle" if take_control else "observe_only",
        "control_application": "stop_only_safety_gate" if take_control else "not_applied",
        "engine": cycle_host.manager.status(),
        "recording": bool(record),
        "perception": {
            "activation": display_path(manifest_path),
            "mapper_spec": mapper_spec,
            "mapper_config": mapper_config,
        },
        "decision": {
            "activation": display_path(Path(bundle["decision_runtime_dir"]) / "active.json"),
            "engine_id": decision_config.get("engine_id"),
            "engine_spec": decision_config["engine_spec"],
            "engine_config": decision_config["engine_config"],
        },
        "run_dir": display_path(run_dir) if run_dir is not None else None,
        "latest": {
            "front_camera": display_path(latest_front_camera_path) if not record else None,
            "perception_json": display_path(latest_json_path),
            "perception_text": display_path(latest_text_path),
        },
    }
    _write_json(state_path, state)

    _emit(output, f"Automation running: {vehicle_id}")
    _emit(output, f"Perception: {manifest.get('perception', {}).get('algorithm', mapper_spec)}")
    if run_dir is not None:
        _emit(output, f"Recording: {display_path(run_dir)}")
    else:
        _emit(output, "Recording: off; latest frame and perception are overwritten each iteration")
    _emit(output, f"Control source: {'external WS' if take_control else 'simulator'}")
    _emit(output, f"Action policy: {state['action_policy']}")
    _emit(output, f"Engine: {cycle_host.manager.status().get('engine')}")
    if max_frames == 0:
        _emit(output, "Frames: until Ctrl-C")
    else:
        _emit(output, f"Frames: {max_frames}")

    try:
        if take_control:
            _emit(output, "Taking simulator control...")
            car.prepare_for_external_control()
            car.stop()

        frame_index = 0
        while max_frames == 0 or frame_index < max_frames:
            cycle_started_at_ms = _timestamp_ms()
            if take_control:
                car.stop()
            frame_id = f"frame_{frame_index:06d}"
            read_id = frame_id if record else "latest"
            frame_output_dir = frames_dir
            perception_output_dir = perception_dir / frame_id if record else perception_dir
            if not record:
                _clear_dir(perception_output_dir)
            snapshot = car.read_sensors(
                SensorReadRequest(
                    output_dir=frame_output_dir,
                    read_id=read_id,
                    requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
                    image_extension="png",
                    front_camera_endpoint="play-front-view-snapshot",
                )
            )
            cycle_context = DecisionFrameContext(
                frame_id=frame_id,
                frame_index=frame_index,
                timestamp_ms=cycle_started_at_ms,
                sensor_snapshot=snapshot,
                mode="autonomy" if take_control else "observe_only",
                metadata={
                    "vehicle_id": vehicle_id,
                    "run_id": run_id,
                    "activation": str(manifest_path),
                    "recording": bool(record),
                    "perception_output_dir": str(perception_output_dir),
                    "control_application": "stop_only_safety_gate" if take_control else "not_applied",
                },
            )
            perception_started_at_ms = _timestamp_ms()
            cycle_result = cycle_host.run(cycle_context)
            perception = cycle_result.perception
            perception_completed_at_ms = _timestamp_ms()
            if perception is None:
                latest_perception_text = "\n".join(
                    [
                        "schema=perception_text_v0",
                        "plugin=decision-cycle",
                        "signal id=perception_ready value=false confidence=1.000 reason=no_perception",
                    ]
                )
                perception_dict: dict[str, Any] | None = None
            else:
                latest_perception_text = perception.text
                perception_dict = perception.to_dict()

            frame_record = {
                "frame_id": frame_id,
                "frame_index": frame_index,
                "captured_at_ms": snapshot.completed_at_ms,
                "cycle_started_at_ms": cycle_started_at_ms,
                "cycle_completed_at_ms": perception_completed_at_ms,
                "cycle_duration_ms": perception_completed_at_ms - cycle_started_at_ms,
                "perception_started_at_ms": perception_started_at_ms,
                "perception_completed_at_ms": perception_completed_at_ms,
                "perception_duration_ms": perception_completed_at_ms - perception_started_at_ms,
                "sensor_snapshot": snapshot.to_dict(),
                "perception": perception_dict,
                "observation": cycle_result.observation.to_dict()
                if cycle_result.observation is not None
                else None,
                "control": cycle_result.control.to_dict(),
                "engine": cycle_host.manager.status(),
                "decision_cycle": cycle_result.to_dict(),
                "action_policy": state["action_policy"],
                "control_source": state["control_source"],
                "control_application": state["control_application"],
            }
            frame_json_path = None
            frame_text_path = None
            if record:
                frame_json_path = perception_dir / frame_id / "perception.json"
                frame_text_path = perception_dir / frame_id / "perception.txt"
                _write_json(frame_json_path, frame_record)
                frame_text_path.write_text(latest_perception_text + "\n", encoding="utf-8")
            _write_json(latest_json_path, frame_record)
            latest_text_path.write_text(latest_perception_text + "\n", encoding="utf-8")

            state["frames_processed"] = frame_index + 1
            state["last_frame"] = {
                "frame_id": frame_id,
                "captured_at_ms": snapshot.completed_at_ms,
                "perception_completed_at_ms": perception_completed_at_ms,
                "perception_duration_ms": perception_completed_at_ms - perception_started_at_ms,
                "cycle_duration_ms": perception_completed_at_ms - cycle_started_at_ms,
                "perception_json": display_path(frame_json_path)
                if frame_json_path is not None
                else display_path(latest_json_path),
                "perception_text": display_path(frame_text_path)
                if frame_text_path is not None
                else display_path(latest_text_path),
                "things": len(perception.things) if perception is not None else 0,
                "confidence": perception.confidence if perception is not None else 0.0,
                "control": cycle_result.control.to_dict(),
                "engine": cycle_host.manager.status().get("engine"),
            }
            state["engine"] = cycle_host.manager.status()
            state["updated_at_ms"] = _timestamp_ms()
            _write_json(state_path, state)

            if verbose or frame_index == 0 or (frame_index + 1) % 10 == 0:
                _emit(
                    output,
                    f"{frame_id}: things={len(perception.things) if perception is not None else 0} "
                    f"confidence={perception.confidence if perception is not None else 0.0:.3f} "
                    f"action={cycle_result.control.reason}",
                )

            frame_index += 1
            if max_frames == 0 or frame_index < max_frames:
                sleep_s = max(0.0, float(interval_s))
                if sleep_s > 0:
                    time.sleep(sleep_s)

    except KeyboardInterrupt:
        state["status"] = "stopped"
        state["stop_reason"] = "keyboard_interrupt"
        state["completed_at_ms"] = _timestamp_ms()
        state["updated_at_ms"] = state["completed_at_ms"]
        _write_json(state_path, state)
        return CommandResult(130, f"Automation stopped: {vehicle_id}\nState: {display_path(state_path)}")
    except MetricsUiWebSocketError as exc:
        state["status"] = "error"
        state["error"] = str(exc)
        state["completed_at_ms"] = _timestamp_ms()
        state["updated_at_ms"] = state["completed_at_ms"]
        _write_json(state_path, state)
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Automation failed for {vehicle_id}.",
                    f"Reason: {exc}",
                    f"State: {display_path(state_path)}",
                ]
            ),
        )

    state["status"] = "completed"
    state["completed_at_ms"] = _timestamp_ms()
    state["updated_at_ms"] = state["completed_at_ms"]
    _write_json(state_path, state)
    return CommandResult(
        0,
        "\n".join(
            [
                f"Automation completed: {vehicle_id}",
                f"Frames processed: {state['frames_processed']}",
                f"Control source: {state['control_source']}",
                f"Action policy: {state['action_policy']}",
                f"Recording: {'on' if record else 'off'}",
                f"State: {display_path(state_path)}",
                f"Latest perception: {display_path(latest_text_path)}",
            ]
        ),
    )


def start_vehicle_automation_background(
    *,
    vehicle_id: str,
    timeout_s: float = 3.0,
    interval_s: float = 1.0,
    frames: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
) -> CommandResult:
    automation_dir = _automation_dir(vehicle_id)
    automation_dir.mkdir(parents=True, exist_ok=True)
    process_path = automation_dir / "process.json"
    log_path = automation_dir / "automation.log"

    existing = _read_json(process_path)
    existing_pid = existing.get("pid") if isinstance(existing, dict) else None
    if isinstance(existing_pid, int) and _pid_alive(existing_pid):
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Automation already running for {vehicle_id}.",
                    f"PID: {existing_pid}",
                    f"State: {display_path(automation_dir / 'state.json')}",
                    _log_status_line(existing, log_path),
                    f"Stream: ./cli/automa vehicles stream perception --id {vehicle_id}",
                ]
            ),
        )

    command = [
        sys.executable,
        str(AUTOMA_EXECUTABLE),
        "vehicles",
        "automation",
        "run",
        "--id",
        vehicle_id,
        "--timeout-s",
        str(timeout_s),
        "--interval-s",
        str(interval_s),
        "--frames",
        str(max(0, int(frames))),
        "--foreground",
    ]
    if not take_control:
        command.append("--observe-only")
    if record:
        command.append("--record")
    if verbose:
        command.append("--verbose")

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    stdout_target: Any
    log_handle = None
    if log_to_disk:
        log_handle = log_path.open("a", encoding="utf-8")
        stdout_target = log_handle
    else:
        stdout_target = subprocess.DEVNULL
    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=stdout_target,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            text=True,
        )
    finally:
        if log_handle is not None:
            log_handle.close()

    process_record = {
        "schema": "automa_automation_process_v0",
        "vehicle_id": vehicle_id,
        "pid": process.pid,
        "started_at_ms": _timestamp_ms(),
        "command": command,
        "log_to_disk": bool(log_to_disk),
        "log_path": display_path(log_path) if log_to_disk else None,
        "state_path": display_path(automation_dir / "state.json"),
        "latest_perception_text": display_path(automation_dir / "latest_perception.txt"),
        "stream_command": f"./cli/automa vehicles stream perception --id {vehicle_id}",
    }
    _write_json(process_path, process_record)
    _write_json(
        automation_dir / "state.json",
        {
            "schema": "automa_automation_run_state_v0",
            "vehicle_id": vehicle_id,
            "run_id": "starting",
            "status": "starting",
            "pid": process.pid,
            "started_at_ms": process_record["started_at_ms"],
            "updated_at_ms": _timestamp_ms(),
            "frames_processed": 0,
            "max_frames": None if max(0, int(frames)) == 0 else max(0, int(frames)),
            "interval_s": max(0.0, float(interval_s)),
            "control_source": "external_ws" if take_control else "simulator",
            "action_policy": "engine_idle" if take_control else "observe_only",
            "control_application": "stop_only_safety_gate" if take_control else "not_applied",
            "recording": bool(record),
            "latest": {
                "front_camera": display_path(automation_dir / "latest" / "frames" / f"latest_{FRONT_CAMERA_SENSOR_ID}.png")
                if not record
                else None,
                "perception_json": display_path(automation_dir / "latest_perception.json"),
                "perception_text": display_path(automation_dir / "latest_perception.txt"),
            },
        },
    )
    starting_text = "\n".join(
        [
            "schema=perception_text_v0",
            "plugin=automation-worker",
            f"status=starting vehicle_id={vehicle_id}",
            "signal id=perception_ready value=false confidence=1.000 reason=worker_starting",
        ]
    )
    (automation_dir / "latest_perception.txt").write_text(starting_text + "\n", encoding="utf-8")
    _write_json(
        automation_dir / "latest_perception.json",
        {
            "schema": "automa_latest_perception_placeholder_v0",
            "vehicle_id": vehicle_id,
            "status": "starting",
            "started_at_ms": process_record["started_at_ms"],
            "text": starting_text,
            "perception": {
                "confidence": 0.0,
                "things": [],
            },
        },
    )

    return CommandResult(
        0,
        "\n".join(
            [
                f"Automation started in background for {vehicle_id}.",
                f"PID: {process.pid}",
                f"State: {display_path(automation_dir / 'state.json')}",
                _log_status_line(process_record, log_path),
                f"Stream: ./cli/automa vehicles stream perception --id {vehicle_id}",
            ]
        ),
    )


def get_vehicle_automation_status(
    *,
    vehicle_id: str | None = None,
    json_output: bool = False,
) -> CommandResult:
    payload = {
        "schema": "automa_automation_status_v0",
        "generated_at_ms": _timestamp_ms(),
        "runtime_root": display_path(RUNTIME_ROOT),
        "vehicles": _collect_automation_status(vehicle_id=vehicle_id),
    }
    if vehicle_id is not None and not payload["vehicles"]:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No deployed automation runtime found for {vehicle_id!r}.",
                    f"Expected: {display_path(RUNTIME_ROOT / safe_path_part(vehicle_id) / 'bundle')}",
                    "Deploy perception first: ./cli/automa vehicles update perception --id <vehicle_id>",
                ]
            ),
        )
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_automation_status(payload))


def stop_vehicle_automation(
    *,
    vehicle_id: str,
    wait_s: float = 3.0,
) -> CommandResult:
    automation_dir = _automation_dir(vehicle_id)
    process_path = automation_dir / "process.json"
    state_path = automation_dir / "state.json"
    process = _read_json(process_path)
    pid = process.get("pid") if isinstance(process, dict) else None

    if not isinstance(pid, int):
        return CommandResult(
            0,
            "\n".join(
                [
                    f"No automation PID is recorded for {vehicle_id}.",
                    f"State: {display_path(state_path)}",
                ]
            ),
        )

    if not _pid_alive(pid):
        _mark_process_stopped(process_path, process, stopped_by="stop_command_already_dead")
        _mark_state_stopped(state_path, stopped_by="stop_command_already_dead")
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Automation is not running for {vehicle_id}.",
                    f"Recorded PID: {pid}",
                    f"State: {display_path(state_path)}",
                ]
            ),
        )
    if not _pid_matches_automation(pid, vehicle_id):
        _mark_process_stopped(process_path, process, stopped_by="stop_command_stale_pid")
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Recorded automation PID for {vehicle_id} is alive but does not match this automation command.",
                    f"PID: {pid}",
                    "The PID record was marked stale; no process was terminated.",
                    f"Process: {_process_command(pid) or 'unknown'}",
                ]
            ),
        )

    _terminate_pid(pid, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, float(wait_s))
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            _mark_process_stopped(process_path, process, stopped_by="stop_command")
            _mark_state_stopped(state_path, stopped_by="stop_command")
            return CommandResult(
                0,
                "\n".join(
                    [
                        f"Automation stopped for {vehicle_id}.",
                        f"PID: {pid}",
                        f"State: {display_path(state_path)}",
                    ]
                ),
            )
        time.sleep(0.1)

    _terminate_pid(pid, signal.SIGKILL)
    forced_deadline = time.monotonic() + 1.0
    while time.monotonic() < forced_deadline:
        if not _pid_alive(pid):
            _mark_process_stopped(process_path, process, stopped_by="stop_command_forced")
            _mark_state_stopped(state_path, stopped_by="stop_command_forced")
            return CommandResult(
                0,
                "\n".join(
                    [
                        f"Automation force-stopped for {vehicle_id}.",
                        f"PID: {pid}",
                        f"State: {display_path(state_path)}",
                    ]
                ),
            )
        time.sleep(0.05)

    return CommandResult(
        2,
        "\n".join(
            [
                f"Automation did not stop for {vehicle_id}.",
                f"PID: {pid}",
                f"State: {display_path(state_path)}",
            ]
        ),
    )


def restart_vehicle_automation(
    *,
    vehicle_id: str,
    timeout_s: float = 3.0,
    interval_s: float = 1.0,
    frames: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
    wait_s: float = 3.0,
) -> CommandResult:
    stop_result = stop_vehicle_automation(vehicle_id=vehicle_id, wait_s=wait_s)
    if stop_result.exit_code != 0:
        return stop_result

    start_result = start_vehicle_automation_background(
        vehicle_id=vehicle_id,
        timeout_s=timeout_s,
        interval_s=interval_s,
        frames=frames,
        take_control=take_control,
        record=record,
        verbose=verbose,
        log_to_disk=log_to_disk,
    )
    message = "\n\n".join(part for part in (stop_result.message, start_result.message) if part)
    return CommandResult(start_result.exit_code, message)


def _collect_automation_status(*, vehicle_id: str | None) -> list[dict[str, Any]]:
    if vehicle_id is not None:
        candidates = [RUNTIME_ROOT / safe_path_part(vehicle_id)]
    elif RUNTIME_ROOT.exists():
        candidates = sorted(path for path in RUNTIME_ROOT.iterdir() if path.is_dir())
    else:
        candidates = []

    statuses = []
    for vehicle_runtime_dir in candidates:
        vehicle_name = vehicle_runtime_dir.name
        bundle = controller_bundle_paths(vehicle_runtime_dir)
        bundle_root = Path(bundle["root_dir"])
        automation_dir = Path(bundle["runtime_dir"]) / "automation"
        perception_manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
        decision_manifest_path = Path(bundle["decision_runtime_dir"]) / "active.json"
        process_path = automation_dir / "process.json"
        state_path = automation_dir / "state.json"
        latest_perception_path = automation_dir / "latest_perception.txt"

        perception_manifest = _read_json(perception_manifest_path)
        decision_manifest = _read_json(decision_manifest_path)
        process = _read_json(process_path)
        state = _read_json(state_path)
        process = process if isinstance(process, dict) else {}
        state = state if isinstance(state, dict) else {}

        pid = process.get("pid") if isinstance(process.get("pid"), int) else state.get("pid")
        pid_alive = _pid_alive(pid) if isinstance(pid, int) else False
        last_frame = state.get("last_frame") if isinstance(state.get("last_frame"), dict) else {}
        completed_at_ms = _int_or_none(last_frame.get("perception_completed_at_ms"))
        generated_at_ms = _timestamp_ms()

        perception = {}
        if isinstance(perception_manifest, dict):
            perception_data = perception_manifest.get("perception")
            if isinstance(perception_data, dict):
                perception = {
                    "algorithm": perception_data.get("algorithm"),
                    "mapper_spec": perception_data.get("mapper_spec"),
                    "mapper_config": perception_data.get("mapper_config") if isinstance(perception_data.get("mapper_config"), dict) else {},
                }

        decision = {}
        if isinstance(decision_manifest, dict):
            decision_data = decision_manifest.get("decision")
            if isinstance(decision_data, dict):
                decision = {
                    "engine_id": decision_data.get("engine_id"),
                    "engine_spec": decision_data.get("engine_spec"),
                    "engine_config": decision_data.get("engine_config")
                    if isinstance(decision_data.get("engine_config"), dict)
                    else {},
                }

        statuses.append(
            {
                "vehicle_id": vehicle_name,
                "deployed": bundle_root.exists()
                and perception_manifest_path.exists()
                and decision_manifest_path.exists(),
                "bundle_root": display_path(bundle_root),
                "automation_runtime_exists": automation_dir.exists(),
                "automation_dir": display_path(automation_dir),
                "perception": {
                    "deployed": perception_manifest_path.exists(),
                    "activation": display_path(perception_manifest_path),
                    **perception,
                },
                "decision": {
                    "deployed": decision_manifest_path.exists(),
                    "activation": display_path(decision_manifest_path),
                    **decision,
                },
                "process": {
                    "pid": pid,
                    "running": pid_alive,
                    "pid_state": "alive" if pid_alive else ("not_running" if isinstance(pid, int) else "none"),
                    "log_to_disk": bool(process.get("log_to_disk")),
                    "log_path": process.get("log_path") if isinstance(process.get("log_path"), str) else None,
                    "command": process.get("command") if isinstance(process.get("command"), list) else None,
                    "process_record": display_path(process_path),
                },
                "state": {
                    "status": state.get("status", "none"),
                    "run_id": state.get("run_id"),
                    "frames_processed": state.get("frames_processed", 0),
                    "max_frames": state.get("max_frames"),
                    "interval_s": state.get("interval_s"),
                    "recording": state.get("recording"),
                    "control_source": state.get("control_source"),
                    "action_policy": state.get("action_policy"),
                    "updated_at_ms": state.get("updated_at_ms"),
                    "last_frame": last_frame,
                    "latest_perception_text": display_path(latest_perception_path),
                    "state_record": display_path(state_path),
                    "latest_perception_age_ms": None if completed_at_ms is None else max(0, generated_at_ms - completed_at_ms),
                },
            }
        )
    return statuses


def _format_automation_status(payload: dict[str, Any]) -> str:
    vehicles = payload.get("vehicles") if isinstance(payload.get("vehicles"), list) else []
    lines = [
        "automa automation status",
        "",
        f"runtime: {payload.get('runtime_root', 'unknown')}",
        f"deployed automations: {sum(1 for item in vehicles if isinstance(item, dict) and item.get('deployed'))}",
    ]
    if not vehicles:
        lines.extend(
            [
                "",
                "No deployed automation runtimes found.",
                "Deploy perception first: ./cli/automa vehicles update perception --id <vehicle_id>",
            ]
        )
        return "\n".join(lines)

    for item in vehicles:
        if not isinstance(item, dict):
            continue
        perception = item.get("perception") if isinstance(item.get("perception"), dict) else {}
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        process = item.get("process") if isinstance(item.get("process"), dict) else {}
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        last_frame = state.get("last_frame") if isinstance(state.get("last_frame"), dict) else {}
        lines.extend(
            [
                "",
                str(item.get("vehicle_id", "unknown")),
                f"  deployment: {'deployed' if item.get('deployed') else 'not deployed'}",
                f"  perception: {_perception_label(perception)}",
                f"  decision: {_decision_label(decision)}",
                f"  worker: {_worker_label(process, state)}",
                f"  run: {_run_label(state)}",
                f"  latest: {_latest_status_label(state, last_frame)}",
                f"  state: {state.get('state_record', 'unknown')}",
                f"  log: {_status_log_label(process)}",
            ]
        )
    return "\n".join(lines)


def _perception_label(perception: dict[str, Any]) -> str:
    if not perception.get("deployed"):
        return f"not deployed; expected {perception.get('activation', 'unknown')}"
    algorithm = perception.get("algorithm") or "unknown"
    mapper = perception.get("mapper_spec") or "unknown mapper"
    return f"{algorithm} ({mapper})"


def _decision_label(decision: dict[str, Any]) -> str:
    if not decision.get("deployed"):
        return f"not deployed; expected {decision.get('activation', 'unknown')}"
    engine_id = decision.get("engine_id") or "unknown"
    engine_spec = decision.get("engine_spec") or "unknown engine"
    return f"{engine_id} ({engine_spec})"


def _worker_label(process: dict[str, Any], state: dict[str, Any]) -> str:
    pid = process.get("pid")
    if pid is None:
        pid = state.get("pid")
    pid_text = str(pid) if isinstance(pid, int) else "none"
    return f"{state.get('status', 'none')}  pid={pid_text} ({process.get('pid_state', 'unknown')})"


def _run_label(state: dict[str, Any]) -> str:
    max_frames = state.get("max_frames")
    max_text = "unbounded" if max_frames is None else str(max_frames)
    parts = [
        f"id={state.get('run_id', 'none')}",
        f"frames={state.get('frames_processed', 0)}/{max_text}",
        f"interval_s={state.get('interval_s', 'unknown')}",
        f"recording={state.get('recording', 'unknown')}",
        f"control={state.get('control_source', 'unknown')}",
        f"action={state.get('action_policy', 'unknown')}",
    ]
    return "  ".join(parts)


def _latest_status_label(state: dict[str, Any], last_frame: dict[str, Any]) -> str:
    if not last_frame:
        return f"none  perception={state.get('latest_perception_text', 'unknown')}"
    confidence = last_frame.get("confidence", "unknown")
    parts = [
        f"frame={last_frame.get('frame_id', 'none')}",
        f"things={last_frame.get('things', 'unknown')}",
        f"confidence={confidence}",
        f"perception_ms={last_frame.get('perception_duration_ms', 'unknown')}",
        f"cycle_ms={last_frame.get('cycle_duration_ms', 'unknown')}",
        f"age_ms={state.get('latest_perception_age_ms', 'unknown')}",
    ]
    return "  ".join(parts)


def _status_log_label(process: dict[str, Any]) -> str:
    if not process.get("log_to_disk"):
        return "disabled"
    return process.get("log_path") or "enabled"


def _manifest_get_str(manifest: dict[str, Any], section: str, key: str) -> str | None:
    value = manifest.get(section)
    if not isinstance(value, dict):
        return None
    found = value.get(key)
    return found if isinstance(found, str) else None


def _manifest_get_dict(manifest: dict[str, Any], section: str, key: str) -> dict[str, Any]:
    value = manifest.get(section)
    if not isinstance(value, dict):
        return {}
    found = value.get(key)
    return dict(found) if isinstance(found, dict) else {}


def _now_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _timestamp_ms() -> int:
    return int(time.time() * 1000)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _mark_process_stopped(path: Path, process: Any, *, stopped_by: str) -> None:
    record = process if isinstance(process, dict) else {}
    updated = {
        **record,
        "status": "stopped",
        "stopped_by": stopped_by,
        "stopped_at_ms": _timestamp_ms(),
    }
    _write_json(path, updated)


def _mark_state_stopped(path: Path, *, stopped_by: str) -> None:
    state = _read_json(path)
    if not isinstance(state, dict):
        return
    if state.get("status") not in ("starting", "running", "error"):
        return
    updated = {
        **state,
        "status": "stopped",
        "stop_reason": stopped_by,
        "completed_at_ms": _timestamp_ms(),
        "updated_at_ms": _timestamp_ms(),
    }
    _write_json(path, updated)


def _log_status_line(process: Any, log_path: Path) -> str:
    record = process if isinstance(process, dict) else {}
    configured_path = record.get("log_path")
    log_to_disk = bool(record.get("log_to_disk")) or isinstance(configured_path, str)
    if not log_to_disk:
        return "Log: disabled; pass --log to persist worker output"
    if isinstance(configured_path, str) and configured_path:
        return f"Log: {configured_path}"
    return f"Log: {display_path(log_path)}"


def _automation_dir(vehicle_id: str) -> Path:
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    return Path(bundle["runtime_dir"]) / "automation"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_matches_automation(pid: int, vehicle_id: str) -> bool:
    command = _process_command(pid)
    if command is None:
        return True
    required_parts = ("automa", "vehicles", "automation", "run", vehicle_id)
    return all(part in command for part in required_parts)


def _process_command(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    command = result.stdout.strip()
    return command or None


def _terminate_pid(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            return


def _emit(output: TextIO | None, message: str) -> None:
    if output is None:
        return
    print(message, file=output, flush=True)
