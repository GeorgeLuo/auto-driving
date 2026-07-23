from __future__ import annotations

import json
import os
import queue
import shlex
import signal
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision import (
    DecisionFrameContext,
    DecisionStages,
    load_memory_stage_if_present,
)
from autonomy.perception import PERCEPTION_TEXT_SCHEMA, build_perception_request
from autonomy.runtime import AutonomyManager
from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim import ChaseSimCar
from implementations.vehicle.chase_sim.frame_identity import (
    format_chase_frame_id,
    simulator_epoch_from_snapshot,
    simulator_frame_index_from_snapshot,
)
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError

from .bundles import controller_bundle_paths
from .decision import load_decision_activation
from .paths import display_path, safe_path_part
from .perception import (
    _close_mapper,
    _load_mapper,
)
from .perception_view import PerceptionViewServer, get_perception_view_status
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles_snapshot


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))
AUTOMA_EXECUTABLE = ROOT / "cli" / "automa"
MAX_STATUS_REASON_CHARS = 240


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


@dataclass(frozen=True)
class _PendingAutomationFrame:
    context: DecisionFrameContext
    front_path: Path
    # Evaluator-only shadow reference; never fed into the decision cycle.
    shadow_reference: dict[str, Any] | None = None


def run_vehicle_automation(
    *,
    vehicle_id: str,
    timeout_s: float = 3.0,
    interval_s: float = 0.25,
    frames: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    payload = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=False,
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
    try:
        mapper = _load_mapper(mapper_spec, mapper_config, bundle_root=bundle_root)
    except Exception as exc:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Could not load perception for {vehicle_id}.",
                    f"Mapper: {mapper_spec}",
                    f"Reason: {type(exc).__name__}: {exc}",
                ]
            ),
        )

    def perceive_stage(context: DecisionFrameContext):
        if context.sensor_snapshot is None:
            return None
        output_dir_text = context.metadata.get("perception_output_dir")
        output_dir = (
            Path(output_dir_text)
            if record and isinstance(output_dir_text, str)
            else None
        )
        return mapper.perceive(
            build_perception_request(
                context.sensor_snapshot,
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
    memory_activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    memory_stage = None
    if memory_activation_path.exists():
        try:
            memory_stage = load_memory_stage_if_present(memory_activation_path)
        except (FileNotFoundError, ValueError, TypeError, ImportError, AttributeError) as exc:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Could not load memory activation for {vehicle_id}.",
                        f"Activation: {display_path(memory_activation_path)}",
                        f"Reason: {type(exc).__name__}: {exc}",
                    ]
                ),
            )
    cycle_host = AutonomyCycleHost(
        manager=engine_manager,
        stages=DecisionStages(
            perceive=perceive_stage,
            remember=memory_stage,
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
    else:
        for transient_dir in (frames_dir, perception_dir):
            if transient_dir.exists():
                shutil.rmtree(transient_dir)

    view_server: PerceptionViewServer | None = None
    try:
        view_server = PerceptionViewServer(
            vehicle_id=vehicle_id,
            automation_dir=automation_dir,
        ).start()
        published_view = view_server.describe()
    except (OSError, RuntimeError, ValueError) as exc:
        published_view = {
            "status": "error",
            "available": False,
            "url": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }

    max_frames = max(0, int(frames))
    state = {
        "schema": "automa_automation_run_state_v0",
        "vehicle_id": vehicle_id,
        "run_id": run_id,
        "status": "running",
        "pid": os.getpid(),
        "started_at_ms": _timestamp_ms(),
        "updated_at_ms": _timestamp_ms(),
        "frames_captured": 0,
        "frames_processed": 0,
        "frames_dropped": 0,
        "max_frames": None if max_frames == 0 else max_frames,
        "interval_s": max(0.0, float(interval_s)),
        "pipeline": "latest_frame_async_perception",
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
        "memory": (
            {
                "activation": display_path(memory_activation_path),
                "implementation_id": memory_stage.activation.implementation_id,
                "implementation_spec": memory_stage.activation.implementation_spec,
                "status": memory_stage.status(),
            }
            if memory_stage is not None
            else {
                "activation": display_path(memory_activation_path),
                "status": "absent",
            }
        ),
        "run_dir": display_path(run_dir) if run_dir is not None else None,
        "latest": {
            "front_camera": display_path(latest_front_camera_path) if not record else None,
            "perception_json": display_path(latest_json_path),
            "perception_text": display_path(latest_text_path),
        },
        "published_view": published_view,
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
    if published_view.get("available"):
        _emit(output, f"Perception view: {published_view.get('url')}")
    else:
        _emit(output, f"Perception view: unavailable ({published_view.get('reason', 'startup failed')})")
    if max_frames == 0:
        _emit(output, "Frames: until Ctrl-C")
    else:
        _emit(output, f"Frames: {max_frames}")

    pending_frames: queue.Queue[_PendingAutomationFrame | object] = queue.Queue(maxsize=1)
    worker_sentinel = object()
    worker_failed = threading.Event()
    worker_errors: list[BaseException] = []
    state_lock = threading.Lock()

    def update_view_state(payload: dict[str, Any]) -> None:
        with state_lock:
            state["published_view"] = payload

    memory_reset_lock = threading.Lock()

    def apply_memory_reset_if_requested() -> None:
        request_path = automation_dir / "memory_reset.request.json"
        result_path = automation_dir / "memory_reset.result.json"
        if not request_path.exists():
            return
        if not memory_reset_lock.acquire(blocking=False):
            return
        try:
            _apply_memory_reset_locked(request_path=request_path, result_path=result_path)
        finally:
            memory_reset_lock.release()

    def _apply_memory_reset_locked(*, request_path: Path, result_path: Path) -> None:
        if not request_path.exists():
            return
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _write_json(
                result_path,
                {
                    "schema": "automa_memory_reset_result_v0",
                    "ok": False,
                    "status": "error",
                    "error": f"invalid reset request: {exc}",
                    "completed_at_ms": _timestamp_ms(),
                },
            )
            try:
                request_path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        if not isinstance(request, dict):
            request = {}
        token = request.get("token")
        if memory_stage is None:
            result = {
                "schema": "automa_memory_reset_result_v0",
                "ok": False,
                "status": "absent",
                "token": token,
                "error": "no memory stage is activated in the automation worker",
                "completed_at_ms": _timestamp_ms(),
            }
        else:
            try:
                snapshot = cycle_host.reset_memory()
                result = {
                    "schema": "automa_memory_reset_result_v0",
                    "ok": True,
                    "status": "reset",
                    "token": token,
                    "snapshot": snapshot.to_dict() if snapshot is not None and hasattr(snapshot, "to_dict") else None,
                    "memory": memory_stage.status(),
                    "completed_at_ms": _timestamp_ms(),
                }
            except Exception as exc:  # noqa: BLE001 - worker control boundary
                result = {
                    "schema": "automa_memory_reset_result_v0",
                    "ok": False,
                    "status": "error",
                    "token": token,
                    "error": f"{type(exc).__name__}: {exc}",
                    "completed_at_ms": _timestamp_ms(),
                }
        _write_json(result_path, result)
        with state_lock:
            if memory_stage is not None:
                state["memory"] = {
                    "activation": display_path(memory_activation_path),
                    "implementation_id": memory_stage.activation.implementation_id,
                    "implementation_spec": memory_stage.activation.implementation_spec,
                    "status": memory_stage.status(),
                }
            state["updated_at_ms"] = _timestamp_ms()
            _write_json(state_path, state)
        try:
            request_path.unlink(missing_ok=True)
        except OSError:
            pass

    def process_frame(pending: _PendingAutomationFrame) -> None:
        apply_memory_reset_if_requested()
        context = pending.context
        snapshot = context.sensor_snapshot
        if snapshot is None:
            raise ValueError(f"{context.frame_id} has no sensor snapshot")
        cycle_started_at_ms = _timestamp_ms()
        perception_started_at_ms = _timestamp_ms()
        cycle_result = cycle_host.run(context)
        perception = cycle_result.perception
        perception_completed_at_ms = _timestamp_ms()
        if perception is None:
            latest_perception_text = "\n".join(
                [
                    f"schema={PERCEPTION_TEXT_SCHEMA}",
                    "plugin=decision-cycle",
                    "signal id=perception_ready value=false confidence=1.000 reason=no_perception",
                ]
            )
            perception_dict: dict[str, Any] | None = None
        else:
            latest_perception_text = perception.text
            perception_dict = perception.to_dict()

        control_record = {
            **cycle_result.control.to_dict(),
            # The current Chase worker never applies decision output to the car.
            # In observe-only mode it also performs no control handoff or stop command.
            "applied": False,
        }
        simulator_frame_index = simulator_frame_index_from_snapshot(snapshot)
        simulation_epoch = simulator_epoch_from_snapshot(snapshot)
        if simulator_frame_index is None or simulation_epoch is None:
            raise ValueError(
                "Chase decision frame is missing atomic simulation-run identity"
            )
        frame_record = {
            "frame_id": context.frame_id,
            "frame_index": context.frame_index,
            "simulator_frame_index": simulator_frame_index,
            "simulation_epoch": simulation_epoch,
            "captured_at_ms": snapshot.completed_at_ms,
            "cycle_started_at_ms": cycle_started_at_ms,
            "cycle_completed_at_ms": perception_completed_at_ms,
            "cycle_duration_ms": perception_completed_at_ms - cycle_started_at_ms,
            "perception_started_at_ms": perception_started_at_ms,
            "perception_completed_at_ms": perception_completed_at_ms,
            "perception_duration_ms": perception_completed_at_ms - perception_started_at_ms,
            "capture_to_perception_ms": perception_completed_at_ms - snapshot.completed_at_ms,
            "sensor_snapshot": snapshot.to_dict(),
            "perception": perception_dict,
            "observation": cycle_result.observation.to_dict()
            if cycle_result.observation is not None
            else None,
            "memory": cycle_result.memory.to_dict()
            if cycle_result.memory is not None
            else None,
            "control": control_record,
            "engine": cycle_host.manager.status(),
            "decision_cycle": cycle_result.to_dict(),
            "action_policy": state["action_policy"],
            "control_source": state["control_source"],
            "control_application": state["control_application"],
        }
        # Shadow reference is evaluator-only: sibling of candidate results, not an input.
        if isinstance(pending.shadow_reference, dict):
            frame_record["shadow_reference"] = pending.shadow_reference
            frame_record["shadow_alignment"] = {
                "aligned": pending.shadow_reference.get("simulator_frame_index")
                == simulator_frame_index
                and pending.shadow_reference.get("simulation_epoch") == simulation_epoch,
                "candidate_frame_index": simulator_frame_index,
                "shadow_frame_index": pending.shadow_reference.get("simulator_frame_index"),
                "candidate_simulation_epoch": simulation_epoch,
                "shadow_simulation_epoch": pending.shadow_reference.get("simulation_epoch"),
            }
        if view_server is not None:
            try:
                view_server.publish_perception(frame_record=frame_record)
                update_view_state(view_server.health_payload())
            except (OSError, TypeError, ValueError) as exc:
                update_view_state(
                    {
                        **view_server.describe(),
                        "last_error": f"{type(exc).__name__}: {exc}",
                    }
                )

        frame_json_path = None
        frame_text_path = None
        if record:
            frame_json_path = perception_dir / context.frame_id / "perception.json"
            frame_text_path = perception_dir / context.frame_id / "perception.txt"
            _write_json(frame_json_path, frame_record)
            frame_text_path.write_text(latest_perception_text + "\n", encoding="utf-8")
        _write_json(latest_json_path, frame_record)
        latest_text_path.write_text(latest_perception_text + "\n", encoding="utf-8")

        with state_lock:
            state["frames_processed"] = int(state["frames_processed"]) + 1
            state["last_frame"] = {
                "frame_id": context.frame_id,
                "frame_index": context.frame_index,
                "simulator_frame_index": simulator_frame_index,
                "simulation_epoch": simulation_epoch,
                "captured_at_ms": snapshot.completed_at_ms,
                "perception_completed_at_ms": perception_completed_at_ms,
                "perception_duration_ms": perception_completed_at_ms - perception_started_at_ms,
                "capture_to_perception_ms": perception_completed_at_ms - snapshot.completed_at_ms,
                "cycle_duration_ms": perception_completed_at_ms - cycle_started_at_ms,
                "perception_json": display_path(frame_json_path)
                if frame_json_path is not None
                else display_path(latest_json_path),
                "perception_text": display_path(frame_text_path)
                if frame_text_path is not None
                else display_path(latest_text_path),
                "things": len(perception.things) if perception is not None else 0,
                "signals": len(perception.signals) if perception is not None else 0,
                "control": control_record,
                "engine": cycle_host.manager.status().get("engine"),
                "shadow_aligned": bool(
                    isinstance(pending.shadow_reference, dict)
                    and pending.shadow_reference.get("simulator_frame_index")
                    == simulator_frame_index
                    and pending.shadow_reference.get("simulation_epoch") == simulation_epoch
                ),
            }
            state["engine"] = cycle_host.manager.status()
            if memory_stage is not None:
                state["memory"] = {
                    "activation": display_path(memory_activation_path),
                    "implementation_id": memory_stage.activation.implementation_id,
                    "implementation_spec": memory_stage.activation.implementation_spec,
                    "status": memory_stage.status(),
                }
            state["updated_at_ms"] = _timestamp_ms()
            _write_json(state_path, state)

        processed_count = int(state["frames_processed"])
        if verbose or processed_count == 1 or processed_count % 10 == 0:
            _emit(
                output,
                f"{context.frame_id}: signals={len(perception.signals) if perception is not None else 0} "
                f"things={len(perception.things) if perception is not None else 0} "
                f"action={cycle_result.control.reason}",
            )

    def perception_worker() -> None:
        while True:
            item = pending_frames.get()
            try:
                if item is worker_sentinel:
                    return
                if not isinstance(item, _PendingAutomationFrame):
                    raise TypeError("perception queue received an invalid frame")
                if not worker_failed.is_set():
                    process_frame(item)
            except BaseException as exc:
                if not worker_failed.is_set():
                    worker_errors.append(exc)
                    worker_failed.set()
            finally:
                if isinstance(item, _PendingAutomationFrame) and not record:
                    item.front_path.unlink(missing_ok=True)
                pending_frames.task_done()

    worker_thread = threading.Thread(
        target=perception_worker,
        name=f"automa-perception-worker-{vehicle_id}",
        daemon=True,
    )

    def enqueue_latest(pending: _PendingAutomationFrame) -> None:
        while True:
            try:
                pending_frames.put_nowait(pending)
                return
            except queue.Full:
                try:
                    dropped = pending_frames.get_nowait()
                except queue.Empty:
                    continue
                if isinstance(dropped, _PendingAutomationFrame) and not record:
                    dropped.front_path.unlink(missing_ok=True)
                pending_frames.task_done()
                with state_lock:
                    state["frames_dropped"] = int(state["frames_dropped"]) + 1

    def stop_perception_worker(*, process_latest: bool) -> None:
        if not process_latest:
            try:
                dropped = pending_frames.get_nowait()
            except queue.Empty:
                dropped = None
            if isinstance(dropped, _PendingAutomationFrame):
                if not record:
                    dropped.front_path.unlink(missing_ok=True)
                with state_lock:
                    state["frames_dropped"] = int(state["frames_dropped"]) + 1
                pending_frames.task_done()
        pending_frames.put(worker_sentinel)
        worker_thread.join()

    try:
        if take_control:
            _emit(output, "Taking simulator control...")
            car.prepare_for_external_control()
            car.stop()

        worker_thread.start()
        capture_sequence = 0
        next_capture_at = time.monotonic()
        capture_interval_s = max(0.0, float(interval_s))
        while max_frames == 0 or capture_sequence < max_frames:
            if worker_failed.is_set():
                raise worker_errors[0]
            apply_memory_reset_if_requested()
            if capture_sequence > 0 and capture_interval_s > 0:
                next_capture_at += capture_interval_s
                delay_s = max(0.0, next_capture_at - time.monotonic())
                if worker_failed.wait(delay_s):
                    raise worker_errors[0]
                apply_memory_reset_if_requested()

            captured_started_at_ms = _timestamp_ms()
            # Provisional id for the sensor request path; rewritten from simulator
            # frame identity once the capture returns.
            provisional_id = f"capture_{capture_sequence:06d}"
            perception_output_dir = None
            snapshot = car.read_sensors(
                SensorReadRequest(
                    output_dir=frames_dir,
                    read_id=provisional_id,
                    requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
                    image_extension="png",
                    front_camera_endpoint="atomic-evaluation-capture",
                )
            )
            simulator_frame_index = simulator_frame_index_from_snapshot(snapshot)
            if simulator_frame_index is None and hasattr(car, "last_simulator_frame_index"):
                simulator_frame_index = getattr(car, "last_simulator_frame_index", None)
            if simulator_frame_index is not None:
                frame_index = int(simulator_frame_index)
                frame_id = format_chase_frame_id(frame_index)
            else:
                # Fail closed for live Chase: local counters cannot align shadow refs.
                raise ValueError(
                    "Chase sensor capture missing simulator frameIndex; "
                    "cannot assign camera-derived frame identity for shadow alignment"
                )
            simulation_epoch = simulator_epoch_from_snapshot(snapshot)
            if simulation_epoch is None:
                raise ValueError(
                    "Chase sensor capture missing simulationEpoch; "
                    "cannot establish atomic run identity for shadow alignment"
                )
            # Align SensorSnapshot.read_id with simulator identity (capture used a provisional id).
            if snapshot.read_id != frame_id:
                snapshot = replace(snapshot, read_id=frame_id)
            if record:
                perception_output_dir = perception_dir / frame_id
            shadow_reference = None
            if hasattr(car, "last_capture_shadow_reference"):
                shadow_reference = getattr(car, "last_capture_shadow_reference", None)

            front_reading = snapshot.readings.get(FRONT_CAMERA_SENSOR_ID)
            front_path = (
                Path(front_reading.path)
                if front_reading is not None and isinstance(front_reading.path, str)
                else None
            )
            if front_path is None:
                raise ValueError("front camera reading has no published path")
            # Rename capture file to the simulator-anchored frame id when needed.
            desired_name = f"{frame_id}_{FRONT_CAMERA_SENSOR_ID}{front_path.suffix}"
            if front_path.name != desired_name:
                target_path = front_path.with_name(desired_name)
                try:
                    if front_path.exists() and not target_path.exists():
                        front_path.rename(target_path)
                        front_path = target_path
                        if front_reading is not None:
                            updated = replace(front_reading, path=str(front_path))
                            snapshot = replace(
                                snapshot,
                                readings={**snapshot.readings, FRONT_CAMERA_SENSOR_ID: updated},
                            )
                except OSError:
                    pass
            if not record:
                _copy_file_atomic(front_path, latest_front_camera_path)

            capture_record = {
                "frame_id": frame_id,
                "frame_index": frame_index,
                "simulator_frame_index": frame_index,
                "simulation_epoch": simulation_epoch,
                "capture_sequence": capture_sequence,
                "captured_at_ms": snapshot.completed_at_ms,
                "capture_started_at_ms": captured_started_at_ms,
                "capture_duration_ms": snapshot.completed_at_ms - captured_started_at_ms,
                "sensor_snapshot": snapshot.to_dict(),
            }
            # Evaluator-only: never placed on DecisionFrameContext / observation.
            if isinstance(shadow_reference, dict):
                capture_record["shadow_reference"] = shadow_reference
            if view_server is not None:
                try:
                    view_server.publish_frame(frame_path=front_path, frame_record=capture_record)
                    update_view_state(view_server.health_payload())
                except (OSError, TypeError, ValueError) as exc:
                    update_view_state(
                        {
                            **view_server.describe(),
                            "last_error": f"{type(exc).__name__}: {exc}",
                        }
                    )

            context = DecisionFrameContext(
                frame_id=frame_id,
                frame_index=frame_index,
                timestamp_ms=captured_started_at_ms,
                sensor_snapshot=snapshot,
                mode="autonomy" if take_control else "observe_only",
                metadata={
                    "vehicle_id": vehicle_id,
                    "run_id": run_id,
                    "activation": str(manifest_path),
                    "recording": bool(record),
                    "simulator_frame_index": frame_index,
                    "simulation_epoch": simulation_epoch,
                    "capture_sequence": capture_sequence,
                    "perception_output_dir": (
                        str(perception_output_dir) if perception_output_dir is not None else None
                    ),
                    "control_application": "stop_only_safety_gate" if take_control else "not_applied",
                },
            )
            enqueue_latest(
                _PendingAutomationFrame(
                    context=context,
                    front_path=front_path,
                    shadow_reference=shadow_reference if isinstance(shadow_reference, dict) else None,
                )
            )

            with state_lock:
                state["frames_captured"] = capture_sequence + 1
                state["last_capture"] = {
                    "frame_id": frame_id,
                    "frame_index": frame_index,
                    "simulator_frame_index": frame_index,
                    "simulation_epoch": simulation_epoch,
                    "capture_sequence": capture_sequence,
                    "captured_at_ms": snapshot.completed_at_ms,
                    "capture_duration_ms": snapshot.completed_at_ms - captured_started_at_ms,
                    "front_camera": display_path(latest_front_camera_path if not record else front_path),
                    "shadow_aligned": isinstance(shadow_reference, dict)
                    and shadow_reference.get("simulator_frame_index") == frame_index
                    and shadow_reference.get("simulation_epoch") == simulation_epoch,
                }
                state["updated_at_ms"] = _timestamp_ms()
                _write_json(state_path, state)

            capture_sequence += 1

        stop_perception_worker(process_latest=True)
        if worker_failed.is_set():
            raise worker_errors[0]

    except KeyboardInterrupt:
        if worker_thread.is_alive():
            stop_perception_worker(process_latest=False)
        _close_mapper(mapper)
        state["status"] = "stopped"
        state["stop_reason"] = "keyboard_interrupt"
        state["completed_at_ms"] = _timestamp_ms()
        state["updated_at_ms"] = state["completed_at_ms"]
        state["published_view"] = _stop_perception_view(view_server)
        _write_json(state_path, state)
        return CommandResult(130, f"Automation stopped: {vehicle_id}\nState: {display_path(state_path)}")
    except MetricsUiWebSocketError as exc:
        if worker_thread.is_alive():
            stop_perception_worker(process_latest=False)
        _close_mapper(mapper)
        state["status"] = "error"
        state["error"] = str(exc)
        state["completed_at_ms"] = _timestamp_ms()
        state["updated_at_ms"] = state["completed_at_ms"]
        state["published_view"] = _stop_perception_view(view_server)
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
    except Exception as exc:
        if worker_thread.is_alive():
            stop_perception_worker(process_latest=False)
        _close_mapper(mapper)
        state["status"] = "error"
        state["error"] = f"{type(exc).__name__}: {exc}"
        state["completed_at_ms"] = _timestamp_ms()
        state["updated_at_ms"] = state["completed_at_ms"]
        state["published_view"] = _stop_perception_view(view_server)
        _write_json(state_path, state)
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Automation failed for {vehicle_id}.",
                    f"Reason: {type(exc).__name__}: {exc}",
                    f"State: {display_path(state_path)}",
                ]
            ),
        )

    _close_mapper(mapper)
    state["status"] = "completed"
    state["completed_at_ms"] = _timestamp_ms()
    state["updated_at_ms"] = state["completed_at_ms"]
    state["published_view"] = _stop_perception_view(view_server)
    _write_json(state_path, state)
    return CommandResult(
        0,
        "\n".join(
            [
                f"Automation completed: {vehicle_id}",
                f"Frames captured: {state['frames_captured']}",
                f"Frames processed: {state['frames_processed']}",
                f"Frames skipped by perception: {state['frames_dropped']}",
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
    interval_s: float = 0.25,
    frames: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
    startup_wait_s: float = 20.0,
) -> CommandResult:
    automation_dir = _automation_dir(vehicle_id)
    automation_dir.mkdir(parents=True, exist_ok=True)
    process_path = automation_dir / "process.json"
    log_path = automation_dir / "automation.log"

    existing = _read_json(process_path)
    existing_pid = existing.get("pid") if isinstance(existing, dict) else None
    if isinstance(existing_pid, int) and _pid_alive(existing_pid):
        existing_state = _read_json(automation_dir / "state.json")
        existing_status = (
            existing_state.get("status") if isinstance(existing_state, dict) else "unknown"
        )
        if existing_status in {"launching", "starting"}:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Automation is still starting for {vehicle_id}.",
                        f"PID: {existing_pid}",
                        f"State: {display_path(automation_dir / 'state.json')}",
                    ]
                ),
            )
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

    started_at_ms = _timestamp_ms()
    _initialize_automation_startup(
        automation_dir=automation_dir,
        vehicle_id=vehicle_id,
        started_at_ms=started_at_ms,
        interval_s=interval_s,
        frames=frames,
        take_control=take_control,
        record=record,
    )

    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    stdout_target: Any
    log_handle = None
    if log_to_disk:
        log_handle = log_path.open("a", encoding="utf-8")
        stdout_target = log_handle
    else:
        stdout_target = subprocess.DEVNULL
    try:
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
        except OSError as exc:
            _mark_automation_startup_error(
                automation_dir=automation_dir,
                error=f"Could not launch automation worker: {type(exc).__name__}: {exc}",
            )
            return CommandResult(
                2,
                f"Could not launch automation for {vehicle_id}: {type(exc).__name__}: {exc}",
            )
    finally:
        if log_handle is not None:
            log_handle.close()

    process_record = {
        "schema": "automa_automation_process_v0",
        "vehicle_id": vehicle_id,
        "pid": process.pid,
        "started_at_ms": started_at_ms,
        "command": command,
        "log_to_disk": bool(log_to_disk),
        "log_path": display_path(log_path) if log_to_disk else None,
        "state_path": display_path(automation_dir / "state.json"),
        "latest_perception_text": display_path(automation_dir / "latest_perception.txt"),
        "stream_command": f"./cli/automa vehicles stream perception --id {vehicle_id}",
    }
    _write_json(process_path, process_record)
    startup = _wait_for_automation_startup(
        process=process,
        automation_dir=automation_dir,
        timeout_s=startup_wait_s,
    )
    process_record["startup"] = {
        key: value for key, value in startup.items() if key != "state"
    }
    _write_json(process_path, process_record)

    startup_status = startup["status"]
    if startup_status == "ready":
        lines = [
            f"Automation ready for {vehicle_id}.",
            f"PID: {process.pid}",
            f"First frame: {startup.get('frame_id', 'captured')}",
            f"Perception view: {startup.get('view_url')}",
        ]
        exit_code = 0
    elif startup_status == "completed":
        lines = [
            f"Automation completed for {vehicle_id} during startup verification.",
            f"PID: {process.pid}",
        ]
        exit_code = 0
    else:
        lines = [
            f"Automation did not become ready for {vehicle_id}.",
            f"PID: {process.pid}",
            f"Reason: {startup.get('reason', 'unknown startup failure')}",
        ]
        exit_code = 2
    lines.extend(
        [
            f"State: {display_path(automation_dir / 'state.json')}",
            _log_status_line(process_record, log_path),
            f"Stream: ./cli/automa vehicles stream perception --id {vehicle_id}",
            f"View: ./cli/automa vehicles info perception --id {vehicle_id}",
        ]
    )
    return CommandResult(exit_code, "\n".join(lines))


def record_vehicle_automation_terminal_result(
    *,
    vehicle_id: str,
    result: CommandResult,
) -> None:
    """Persist failures that occur before the worker creates its normal run state."""

    if result.exit_code == 0:
        return
    automation_dir = _automation_dir(vehicle_id)
    state_path = automation_dir / "state.json"
    state = _read_json(state_path)
    if not isinstance(state, dict) or state.get("status") not in {"launching", "starting"}:
        return
    completed_at_ms = _timestamp_ms()
    _write_json(
        state_path,
        {
            **state,
            "status": "error",
            "pid": os.getpid(),
            "error": result.message or f"automation worker exited with code {result.exit_code}",
            "exit_code": result.exit_code,
            "completed_at_ms": completed_at_ms,
            "updated_at_ms": completed_at_ms,
            "published_view": {
                "status": "error",
                "available": False,
                "url": None,
                "reason": "automation worker failed before the perception view started",
            },
        },
    )


def _initialize_automation_startup(
    *,
    automation_dir: Path,
    vehicle_id: str,
    started_at_ms: int,
    interval_s: float,
    frames: int,
    take_control: bool,
    record: bool,
) -> None:
    view_record_path = automation_dir / "perception_view.json"
    view_record_path.unlink(missing_ok=True)
    starting_text = "\n".join(
        [
            f"schema={PERCEPTION_TEXT_SCHEMA}",
            "plugin=automation-worker",
            f"status=starting vehicle_id={vehicle_id}",
            "signal id=perception_ready value=false confidence=1.000 reason=worker_starting",
        ]
    )
    (automation_dir / "latest_perception.txt").write_text(
        starting_text + "\n",
        encoding="utf-8",
    )
    _write_json(
        automation_dir / "latest_perception.json",
        {
            "schema": "automa_latest_perception_placeholder_v0",
            "vehicle_id": vehicle_id,
            "status": "starting",
            "started_at_ms": started_at_ms,
            "text": starting_text,
            "perception": {"confidence": 0.0, "things": []},
        },
    )
    _write_json(
        automation_dir / "state.json",
        {
            "schema": "automa_automation_run_state_v0",
            "vehicle_id": vehicle_id,
            "run_id": "starting",
            "status": "starting",
            "pid": None,
            "started_at_ms": started_at_ms,
            "updated_at_ms": started_at_ms,
            "frames_captured": 0,
            "frames_processed": 0,
            "frames_dropped": 0,
            "max_frames": None if max(0, int(frames)) == 0 else max(0, int(frames)),
            "interval_s": max(0.0, float(interval_s)),
            "pipeline": "latest_frame_async_perception",
            "control_source": "external_ws" if take_control else "simulator",
            "action_policy": "engine_idle" if take_control else "observe_only",
            "control_application": "stop_only_safety_gate" if take_control else "not_applied",
            "recording": bool(record),
            "latest": {
                "front_camera": display_path(
                    automation_dir / "latest" / "frames" / f"latest_{FRONT_CAMERA_SENSOR_ID}.png"
                )
                if not record
                else None,
                "perception_json": display_path(automation_dir / "latest_perception.json"),
                "perception_text": display_path(automation_dir / "latest_perception.txt"),
            },
            "published_view": {
                "status": "starting",
                "available": False,
                "url": None,
                "reason": "automation worker is starting",
            },
        },
    )


def _wait_for_automation_startup(
    *,
    process: subprocess.Popen[Any],
    automation_dir: Path,
    timeout_s: float,
) -> dict[str, Any]:
    state_path = automation_dir / "state.json"
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while True:
        state = _read_json(state_path)
        state = state if isinstance(state, dict) else {}
        status = state.get("status")
        published_view = (
            state.get("published_view")
            if isinstance(state.get("published_view"), dict)
            else {}
        )
        frames_captured = state.get("frames_captured")

        if (
            status == "running"
            and isinstance(frames_captured, int)
            and frames_captured > 0
            and published_view.get("available")
            and published_view.get("url")
        ):
            last_capture = (
                state.get("last_capture")
                if isinstance(state.get("last_capture"), dict)
                else {}
            )
            return {
                "status": "ready",
                "frame_id": last_capture.get("frame_id"),
                "view_url": published_view.get("url"),
                "state": state,
            }
        if status == "completed":
            return {"status": "completed", "state": state}
        if status in {"error", "stopped"}:
            return {
                "status": "failed",
                "reason": state.get("error") or state.get("stop_reason") or f"worker status is {status}",
                "state": state,
            }
        if (
            status == "running"
            and isinstance(frames_captured, int)
            and frames_captured > 0
            and published_view.get("status") == "error"
        ):
            return {
                "status": "failed",
                "reason": published_view.get("reason") or published_view.get("last_error") or "perception view failed",
                "state": state,
            }

        exit_code = process.poll()
        if exit_code is not None:
            state = _read_json(state_path)
            state = state if isinstance(state, dict) else {}
            reason = state.get("error")
            if not isinstance(reason, str) or not reason:
                reason = f"automation worker exited with code {exit_code} before publishing a frame"
                _mark_automation_startup_error(
                    automation_dir=automation_dir,
                    error=reason,
                    exit_code=exit_code,
                )
                state = _read_json(state_path)
            return {"status": "failed", "reason": reason, "state": state}

        if time.monotonic() >= deadline:
            return {
                "status": "failed",
                "reason": (
                    f"worker is still running but did not publish a camera frame and view "
                    f"within {max(0.1, float(timeout_s)):.1f}s"
                ),
                "state": state,
            }
        time.sleep(0.05)


def _mark_automation_startup_error(
    *,
    automation_dir: Path,
    error: str,
    exit_code: int | None = None,
) -> None:
    state_path = automation_dir / "state.json"
    state = _read_json(state_path)
    state = state if isinstance(state, dict) else {}
    completed_at_ms = _timestamp_ms()
    _write_json(
        state_path,
        {
            **state,
            "status": "error",
            "error": error,
            "exit_code": exit_code,
            "completed_at_ms": completed_at_ms,
            "updated_at_ms": completed_at_ms,
            "published_view": {
                "status": "error",
                "available": False,
                "url": None,
                "reason": error,
            },
        },
    )


def get_vehicle_automation_status(
    *,
    vehicle_id: str | None = None,
    json_output: bool = False,
) -> CommandResult:
    vehicles = _collect_automation_status(vehicle_id=vehicle_id)
    payload = {
        "schema": "automa_automation_status_v0",
        "generated_at_ms": _timestamp_ms(),
        "runtime_root": display_path(RUNTIME_ROOT),
        "requested_vehicle_id": vehicle_id,
        "vehicles": vehicles,
    }
    exit_code = 0
    if vehicle_id is not None and not vehicles:
        exit_code = 2
        payload["outcome"] = {
            "status": "not_found",
            "message": f"No deployed automation runtime found for {vehicle_id!r}.",
            "expected_bundle": display_path(
                RUNTIME_ROOT / safe_path_part(vehicle_id) / "bundle"
            ),
            "recovery": (
                f"./cli/automa vehicles update perception --id {vehicle_id}"
            ),
        }
    elif not vehicles:
        payload["outcome"] = {
            "status": "empty",
            "message": "No deployed automation runtimes found.",
            "expected_bundle": None,
            "recovery": (
                "./cli/automa vehicles update perception --id <vehicle_id>"
            ),
        }
    elif any(_automation_status_needs_attention(vehicle) for vehicle in vehicles):
        payload["outcome"] = {
            "status": "degraded",
            "message": "One or more automation runtimes require attention.",
            "expected_bundle": None,
            "recovery": None,
        }
    else:
        payload["outcome"] = {
            "status": "ok",
            "message": f"Found {len(vehicles)} locally deployed automation runtime(s).",
            "expected_bundle": None,
            "recovery": None,
        }
    if json_output:
        return CommandResult(exit_code, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(exit_code, _format_automation_status(payload))


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

    _terminate_pid(pid, signal.SIGINT, process_group=False)
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
    interval_s: float = 0.25,
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
        candidate = RUNTIME_ROOT / safe_path_part(vehicle_id)
        candidates = [candidate] if candidate.is_dir() else []
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
        published_view = get_perception_view_status(automation_dir)

        perception_manifest = _read_json(perception_manifest_path)
        decision_manifest = _read_json(decision_manifest_path)
        process = _read_json(process_path)
        state = _read_json(state_path)
        process = process if isinstance(process, dict) else {}
        state = state if isinstance(state, dict) else {}

        pid = process.get("pid") if isinstance(process.get("pid"), int) else state.get("pid")
        pid_alive = _pid_alive(pid) if isinstance(pid, int) else False
        worker_status = _worker_status(
            pid=pid,
            pid_alive=pid_alive,
            run_status=state.get("status"),
        )
        worker_reason = _worker_reason(
            worker_status=worker_status,
            pid=pid,
            state=state,
        )
        worker_recovery = (
            f"./cli/automa vehicles automation restart --id {vehicle_name}"
            if worker_status in {"error", "stale"}
            else None
        )
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
                    "status": worker_status,
                    "reason": worker_reason,
                    "recovery": worker_recovery,
                    "log_to_disk": bool(process.get("log_to_disk")),
                    "log_path": process.get("log_path") if isinstance(process.get("log_path"), str) else None,
                    "command": process.get("command") if isinstance(process.get("command"), list) else None,
                    "process_record": display_path(process_path),
                },
                "state": {
                    "status": state.get("status", "none"),
                    "run_id": state.get("run_id"),
                    "pipeline": state.get("pipeline"),
                    "frames_captured": state.get("frames_captured", 0),
                    "frames_processed": state.get("frames_processed", 0),
                    "frames_dropped": state.get("frames_dropped", 0),
                    "max_frames": state.get("max_frames"),
                    "interval_s": state.get("interval_s"),
                    "recording": state.get("recording"),
                    "control_source": state.get("control_source"),
                    "action_policy": state.get("action_policy"),
                    "error": state.get("error") if isinstance(state.get("error"), str) else None,
                    "exit_code": _int_or_none(state.get("exit_code")),
                    "stop_reason": state.get("stop_reason")
                    if isinstance(state.get("stop_reason"), str)
                    else None,
                    "updated_at_ms": state.get("updated_at_ms"),
                    "last_capture": state.get("last_capture")
                    if isinstance(state.get("last_capture"), dict)
                    else {},
                    "last_frame": last_frame,
                    "latest_perception_text": display_path(latest_perception_path),
                    "state_record": display_path(state_path),
                    "latest_perception_age_ms": None if completed_at_ms is None else max(0, generated_at_ms - completed_at_ms),
                },
                "published_view": published_view,
            }
        )
    return statuses


def _automation_status_needs_attention(vehicle: dict[str, Any]) -> bool:
    if not vehicle.get("deployed"):
        return True
    process = vehicle.get("process")
    return isinstance(process, dict) and process.get("status") in {"error", "stale"}


def _format_automation_status(payload: dict[str, Any]) -> str:
    vehicles = payload.get("vehicles") if isinstance(payload.get("vehicles"), list) else []
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    lines = [
        "automa automation status",
        "",
        f"runtime: {payload.get('runtime_root', 'unknown')}",
        f"deployed automations: {sum(1 for item in vehicles if isinstance(item, dict) and item.get('deployed'))}",
    ]
    if not vehicles:
        lines.extend(["", str(outcome.get("message") or "No deployed automation runtimes found.")])
        expected_bundle = outcome.get("expected_bundle")
        if isinstance(expected_bundle, str) and expected_bundle:
            lines.append(f"Expected bundle: {expected_bundle}")
        recovery = outcome.get("recovery")
        if isinstance(recovery, str) and recovery:
            lines.append(f"Next: {recovery}")
        return "\n".join(lines)

    for item in vehicles:
        if not isinstance(item, dict):
            continue
        perception = item.get("perception") if isinstance(item.get("perception"), dict) else {}
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        process = item.get("process") if isinstance(item.get("process"), dict) else {}
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        published_view = item.get("published_view") if isinstance(item.get("published_view"), dict) else {}
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
                f"  view: {_published_view_label(published_view)}",
                f"  state: {state.get('state_record', 'unknown')}",
                f"  log: {_status_log_label(process)}",
            ]
        )
        reason = process.get("reason")
        if isinstance(reason, str) and reason:
            lines.append(f"  problem: {reason}")
        recovery = process.get("recovery")
        if isinstance(recovery, str) and recovery:
            lines.append(f"  next: {recovery}")
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
    return f"{process.get('status', 'unknown')}  pid={pid_text} ({process.get('pid_state', 'unknown')})"


def _worker_status(*, pid: Any, pid_alive: bool, run_status: Any) -> str:
    if run_status == "error":
        return "error"
    if pid_alive:
        return "starting" if run_status in {"launching", "starting"} else "running"
    if run_status in {"launching", "starting", "running"}:
        return "stale"
    if run_status in {"completed", "stopped"}:
        return str(run_status)
    if isinstance(pid, int):
        return "not_running"
    return "not_started"


def _worker_reason(
    *,
    worker_status: str,
    pid: Any,
    state: dict[str, Any],
) -> str | None:
    if worker_status == "stale":
        pid_text = str(pid) if isinstance(pid, int) else "none"
        return f"recorded worker PID {pid_text} is not running"
    if worker_status == "error":
        return _status_reason(
            state.get("error"),
            fallback="automation worker reported an error",
        )
    return None


def _status_reason(value: Any, *, fallback: str) -> str:
    lines = (
        [line.strip() for line in value.splitlines() if line.strip()]
        if isinstance(value, str)
        else []
    )
    reason = lines[0] if lines else fallback
    if len(reason) <= MAX_STATUS_REASON_CHARS:
        return reason
    return f"{reason[:MAX_STATUS_REASON_CHARS]}..."


def _run_label(state: dict[str, Any]) -> str:
    max_frames = state.get("max_frames")
    max_text = "unbounded" if max_frames is None else str(max_frames)
    parts = [
        f"id={state.get('run_id', 'none')}",
        f"captured={state.get('frames_captured', 0)}/{max_text}",
        f"processed={state.get('frames_processed', 0)}",
        f"skipped={state.get('frames_dropped', 0)}",
        f"capture_interval_s={state.get('interval_s', 'unknown')}",
        f"recording={state.get('recording', 'unknown')}",
        f"control={state.get('control_source', 'unknown')}",
        f"action={state.get('action_policy', 'unknown')}",
    ]
    return "  ".join(parts)


def _latest_status_label(state: dict[str, Any], last_frame: dict[str, Any]) -> str:
    if not last_frame:
        return f"none  perception={state.get('latest_perception_text', 'unknown')}"
    parts = [
        f"frame={last_frame.get('frame_id', 'none')}",
        f"signals={last_frame.get('signals', 'unknown')}",
        f"things={last_frame.get('things', 'unknown')}",
        f"perception_ms={last_frame.get('perception_duration_ms', 'unknown')}",
        f"cycle_ms={last_frame.get('cycle_duration_ms', 'unknown')}",
        f"age_ms={state.get('latest_perception_age_ms', 'unknown')}",
    ]
    return "  ".join(parts)


def _published_view_label(published_view: dict[str, Any]) -> str:
    if published_view.get("available") and published_view.get("url"):
        return str(published_view["url"])
    return f"unavailable ({published_view.get('reason', 'automation view is not running')})"


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


def _copy_file_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _stop_perception_view(view_server: PerceptionViewServer | None) -> dict[str, Any]:
    if view_server is None:
        return {
            "status": "unavailable",
            "available": False,
            "url": None,
            "reason": "perception view did not start",
        }
    try:
        view_server.stop()
    except OSError as exc:
        return {
            **view_server.describe(status="error"),
            "reason": f"{type(exc).__name__}: {exc}",
        }
    record = _read_json(view_server.record_path)
    return record if isinstance(record, dict) else view_server.describe(status="stopped")


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None




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
    """Return whether *pid* looks like this vehicle's automation worker.

    When the process command cannot be read, returns True (stop path stays
    permissive). Callers that must fail closed should read the command first
    and use :func:`_automation_command_matches_vehicle` directly.
    """

    command = _process_command(pid)
    if command is None:
        return True
    return _automation_command_matches_vehicle(command, vehicle_id)


def _automation_command_matches_vehicle(command: str, vehicle_id: str) -> bool:
    """Pure check: command is an automation run for exactly *vehicle_id*.

    Requires the contiguous launcher subcommand ``vehicles automation run`` and
    an exact ``--id <vehicle_id>`` argument pair (token equality, not substring).
    """

    if not vehicle_id or not str(vehicle_id).strip():
        return False
    vehicle_key = str(vehicle_id).strip()
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    # Contiguous launcher subcommand as emitted by start_automation.
    matched_run = False
    for index in range(len(tokens) - 2):
        if tokens[index : index + 3] == ["vehicles", "automation", "run"]:
            matched_run = True
            break
    if not matched_run:
        return False
    for index, token in enumerate(tokens):
        if token == "--id":
            if index + 1 < len(tokens) and tokens[index + 1] == vehicle_key:
                return True
        elif token.startswith("--id="):
            if token[len("--id=") :] == vehicle_key:
                return True
    return False


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


def _terminate_pid(
    pid: int,
    sig: signal.Signals,
    *,
    process_group: bool = True,
) -> None:
    try:
        if process_group:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            return


def _emit(output: TextIO | None, message: str) -> None:
    if output is None:
        return
    print(message, file=output, flush=True)
