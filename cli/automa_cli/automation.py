from __future__ import annotations

import copy
import json
import os
import queue
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision_cycle.activation import read_step_activation
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.perception.interface import PERCEPTION_TEXT_SCHEMA
from autonomy.decision_cycle.steps import decision_steps
from autonomy.runtime.cycle_host import LIVE_SELECTION_STEPS, AutonomyCycleHost
from autonomy.runtime.session import RunConfiguration
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.runtime.chase_sim import create_host
from implementations.vehicle.chase_sim import (
    ChaseCaptureValidationError,
    ChasePassiveCaptureError,
)
from implementations.vehicle.chase_sim.frame_identity import (
    format_chase_frame_id,
    simulator_epoch_from_sensor_frame,
    simulator_frame_index_from_sensor_frame,
)
from implementations.vehicle.chase_sim.metrics_ws import (
    MetricsUiWebSocketError,
    compare_chase_session_fingerprints,
)

from .vehicle_access import create_vehicle_access
from .staged_bundle import write_json_atomically
from .bundles import controller_bundle_paths
from .chase_observation import (
    _pid_alive,
    _pid_matches_automation,
    _process_command,
    chase_automation_dir,
)
from .decision import (
    invalidate_latest_decision_frame,
    publish_decision_frame,
)
from .paths import display_path, safe_path_part
from .picar_observation import fetch_autonomy_status, picar_view_status, picar_base_url
from .step_activations import (
    apply_staged,
    bundle_activation_path,
    bundle_activation_problems,
    decision_generation_id,
    decision_identity,
    format_activation_problems,
    read_bundle_activation,
    staging_vehicle,
)
from .step_hosting import load_staged_runner, plugin_report
from .runtime_view import RuntimeViewServer
from .perception_view import (
    get_perception_view_status,
    perception_view_ready,
)
from .vehicles import (
    DEFAULT_CHASE_READINESS_TIMEOUT_S,
    discover_active_vehicles,
    find_vehicle_by_id,
    format_active_vehicles,
    is_chase_vehicle_id,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))
AUTOMA_EXECUTABLE = ROOT / "cli" / "automa"
MAX_STATUS_REASON_CHARS = 240
MAX_DECISION_FILE_BYTES = 8 * 1024 * 1024
# Observe-only continuous runs allow playback/input to evolve; identity and
# control authority must stay fixed until stop.
PASSIVE_RUN_STABLE_FIELDS = (
    "game_id",
    "scenario_id",
    "simulation_epoch",
    "control_source",
)
PASSIVE_RUN_DYNAMIC_FIELDS = (
    "playback",
    "control_input",
)


def _step_status(cycle_host: AutonomyCycleHost) -> dict[str, Any]:
    """Each step's plugin IDs and last error, without the per-cycle records."""

    status = cycle_host.status()
    steps = status.get("steps") if isinstance(status.get("steps"), dict) else {}
    return {
        step: (
            None
            if not isinstance(item, dict)
            else {
                "plugin_ids": item.get("plugin_ids"),
                "last_error": item.get("last_error"),
            }
        )
        for step, item in steps.items()
    } | {"cycle_count": status.get("cycle_count"), "error_count": status.get("error_count")}


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


@dataclass(frozen=True)
class _PendingAutomationFrame:
    context: DecisionFrameContext
    front_path: Path
    # Evaluator-only chaser reference; never fed into the decision cycle.
    chaser_reference: dict[str, Any] | None = None


def _staged_onboard_vehicle(vehicle_id: str) -> dict[str, Any] | None:
    if is_chase_vehicle_id(vehicle_id):
        return None
    vehicle, _unknown = staging_vehicle(vehicle_id, runtime_root=RUNTIME_ROOT, timeout_s=1.0)
    provider = vehicle.get("provider") if isinstance(vehicle, dict) else None
    return vehicle if isinstance(provider, str) and provider != "chase-sim" else None


def _onboard_runtime_status(
    vehicle_id: str,
    vehicle: dict[str, Any],
    *,
    timeout_s: float,
) -> dict[str, Any]:
    """An onboard host's ``/autonomy/status`` in the worker status rows.

    The Chase worker reports these rows from its local state files; a PiCar
    reports the same cycle counters from its observation status provider.
    The local view is the one a terminal ``vehicles stream`` hosts.
    """

    base_url = picar_base_url(vehicle)
    endpoint = f"{base_url}/autonomy/status" if base_url else None
    try:
        if not base_url:
            raise ConnectionError(f"Vehicle {vehicle_id!r} has no PiCar base URL.")
        status = fetch_autonomy_status(base_url, timeout_s=max(0.1, timeout_s))
        error = None
    except ConnectionError as exc:
        status, error = {}, str(exc)
    autonomy = status.get("autonomy") if isinstance(status.get("autonomy"), dict) else {}
    components = autonomy.get("components") if isinstance(autonomy.get("components"), dict) else {}
    observation = (
        components.get("observation") if isinstance(components.get("observation"), dict) else {}
    )
    latest = observation.get("latest") if isinstance(observation.get("latest"), dict) else {}
    completed_at_ms = _int_or_none(latest.get("completed_at_ms"))
    if error is None and not autonomy:
        error = "Donkey runtime is up but reports no onboard host"
    session = autonomy.get("session") or {}
    worker_status = str(session.get("status") or "stopped") if error is None else "error"
    view = picar_view_status(vehicle_id, timeout_s=min(0.25, max(0.0, timeout_s)))
    if not view.get("available"):
        view = {
            **view,
            "reason": f"start `./cli/automa vehicles stream perception --id {vehicle_id}` to host it",
        }
    return {
        "process": {
            "pid": None,
            "running": worker_status == "running",
            "pid_state": f"onboard {base_url or 'no endpoint'}",
            "status": worker_status,
            "generation_matches": True,
            "reason": error,
            "recovery": (
                apply_staged(vehicle_id, vehicle["provider"], "perception")["restart_command"]
                if error is not None
                else None
            ),
            "log_to_disk": False,
            "log_path": None,
            "command": None,
            "process_record": endpoint,
        },
        "state": {
            "status": worker_status,
            "run_id": None,
            "frames_captured": observation.get("camera_frame_count", 0),
            "processed_count": observation.get("processed_count", 0),
            "skipped_count": observation.get("skipped_count", 0),
            "max_frames": None,
            "interval_s": observation.get("min_interval_s"),
            "recording": False,
            "control_source": "onboard",
            "action_policy": (autonomy.get("execution") or {}).get("mode"),
            "execution": autonomy.get("execution"),
            "session": autonomy.get("session"),
            "error": error,
            "last_frame": (
                {
                    "frame_id": latest.get("frame_id"),
                    "perception_completed_at_ms": completed_at_ms,
                    "cycle_duration_ms": latest.get("duration_ms"),
                }
                if latest
                else {}
            ),
            "latest_perception_text": endpoint,
            "state_record": endpoint,
            "latest_perception_age_ms": (
                None if completed_at_ms is None else max(0, _timestamp_ms() - completed_at_ms)
            ),
        },
        "published_view": view,
    }


def run_vehicle_automation(
    *,
    vehicle_id: str,
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
    interval_s: float = 0.25,
    frames: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    try:
        configuration = RunConfiguration(
            mode="autonomy" if take_control else "observe_only", interval_s=interval_s, frames=frames
        )
    except (TypeError, ValueError) as exc:
        return CommandResult(2, str(exc))
    onboard = _staged_onboard_vehicle(vehicle_id)
    if onboard is not None:
        from .onboard_automation import monitor_onboard_runtime
        code, message = monitor_onboard_runtime(
            vehicle_id=vehicle_id, base_url=picar_base_url(onboard),
            automation_dir=chase_automation_dir(vehicle_id), configuration=configuration,
            timeout_s=timeout_s, record=record, verbose=verbose, output=output,
        )
        return CommandResult(code, message)
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
                    "Discovery:",
                    format_active_vehicles(payload, include_inactive=True),
                ]
            ),
        )
    if vehicle is None:
        return CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")
    if vehicle.get("provider") != "chase-sim":
        return CommandResult(2, f"Unsupported runtime provider: {vehicle.get('provider')}")
    if not take_control:
        vehicle_status = (
            vehicle.get("status")
            if isinstance(vehicle.get("status"), dict)
            else {}
        )
        passive = (
            vehicle_status.get("passive_capture")
            if isinstance(vehicle_status.get("passive_capture"), dict)
            else {}
        )
        if passive.get("status") != "available":
            code = str(passive.get("code") or "simulator_capability_missing")
            preservation = (
                passive.get("session_preservation")
                if isinstance(passive.get("session_preservation"), dict)
                else {}
            )
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Observation-only automation is not ready for {vehicle_id}.",
                        f"Reason: {code}",
                        "Layer: passive_capture",
                        (
                            "Missing preservation fields: "
                            + ", ".join(
                                str(item)
                                for item in preservation.get("unknown_fields", [])
                            )
                            if preservation.get("unknown_fields")
                            else "Session preservation could not be proven."
                        ),
                        "No scenario, playback, control-source, or input mutation was attempted.",
                        (
                            "Minimum Metrics UI contract: expose the required session "
                            "fingerprint fields or a fail-closed preserveSession receipt."
                        ),
                    ]
                ),
            )

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    if not manifest_path.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No active perception preset found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(manifest_path)}",
                    f"Run: ./cli/automa vehicles update perception --id {vehicle_id}",
                ]
            ),
        )
    problems = bundle_activation_problems(bundle, vehicle_id)
    if problems:
        return CommandResult(2, format_activation_problems(problems))
    try:
        perception_activation = read_step_activation(manifest_path, "perception")
        memory_activation = read_bundle_activation(bundle, "memory")
        proposal_activation = read_bundle_activation(bundle, "proposal")
        identity = decision_identity(bundle)
        activations = {
            step: read_bundle_activation(bundle, step)
            for step in ("observation", "plan", "action")
        }
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return CommandResult(2, f"Could not read staged step activations for {vehicle_id}: {exc}")
    perception_plugins = ", ".join(perception_activation.plugins) or "(none)"
    perception_preset = perception_activation.metadata.get("preset") or perception_plugins
    # A decision frame is published only when proposals are staged.
    decision_published = identity["steps"]["proposal"] is not None

    car = create_vehicle_access(vehicle, timeout_s=timeout_s).car
    try:
        perception_step = load_staged_runner(perception_activation)
    except Exception as exc:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Could not load perception for {vehicle_id}.",
                    f"Plugins: {perception_plugins}",
                    f"Reason: {type(exc).__name__}: {exc}",
                ]
            ),
        )

    memory_activation_path = bundle_activation_path(bundle, "memory")
    memory_step = None
    if memory_activation is not None:
        try:
            memory_step = load_staged_runner(memory_activation)
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
    # Proposals load from the bundle, as memory does; without a staged
    # activation the vehicle proposes nothing and holds.
    proposal_activation_path = bundle_activation_path(bundle, "proposal")
    proposal_step = None
    if proposal_activation is not None:
        try:
            proposal_step = load_staged_runner(proposal_activation)
        except (FileNotFoundError, ValueError, TypeError, ImportError, AttributeError) as exc:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Could not load proposal activation for {vehicle_id}.",
                        f"Activation: {display_path(proposal_activation_path)}",
                        f"Reason: {type(exc).__name__}: {exc}",
                    ]
                ),
            )
    try:
        steps = decision_steps(
            {step: activation for step, activation in activations.items() if activation}
        )
    except Exception as exc:
        return CommandResult(2, f"Could not load decision steps for {vehicle_id}: {type(exc).__name__}: {exc}")
    cycle_host = create_host(
        car, steps=replace(
            steps, perception=perception_step, memory=memory_step, proposal=proposal_step
        ),
    )
    # Restaged selections apply between frames, as on the onboard host.
    for step, activation in (
        ("perception", perception_activation),
        ("memory", memory_activation),
        ("proposal", proposal_activation),
    ):
        if step in LIVE_SELECTION_STEPS and activation is not None:
            cycle_host.watch_selection(step, bundle_activation_path(bundle, step), activation)

    automation_dir = Path(bundle["runtime_dir"]) / "automation"
    run_id = _now_id("automation")
    run_dir = automation_dir / "runs" / run_id if record else None
    frames_dir = run_dir / "frames" if run_dir is not None else automation_dir / "latest" / "frames"
    perception_dir = run_dir / "perception" if run_dir is not None else automation_dir / "latest" / "perception"
    state_path = automation_dir / "state.json"
    latest_json_path = automation_dir / "latest_perception.json"
    latest_text_path = automation_dir / "latest_perception.txt"
    latest_front_camera_path = frames_dir / f"latest_{FRONT_CAMERA_SENSOR_ID}.png"
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    # Stale latest_decision.json from prior workers must not remain stream-valid.
    invalidate_latest_decision_frame(vehicle_runtime_dir)
    if run_dir is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        for transient_dir in (frames_dir, perception_dir):
            if transient_dir.exists():
                shutil.rmtree(transient_dir)

    view_server: RuntimeViewServer | None = None
    try:
        view_server = RuntimeViewServer(
            vehicle_id=vehicle_id,
            automation_dir=automation_dir,
            run_id=run_id,
            worker_pid=os.getpid(),
            decision_activation=identity if decision_published else None,
            decision_activation_path=Path(bundle["runtime_dir"]),
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
        "processed_count": 0,
        "skipped_count": 0,
        "max_frames": None if max_frames == 0 else max_frames,
        "interval_s": max(0.0, float(interval_s)),
        "pipeline": "latest_frame_async_perception",
        "control_source": "external_ws" if take_control else "preserved_current",
        "action_policy": "autonomy" if take_control else "observe_only",
        "control_application": "shared_execution" if take_control else "not_applied",
        "execution": cycle_host.execution.status(),
        "steps": _step_status(cycle_host),
        "recording": bool(record),
        "perception": {
            "activation": display_path(manifest_path),
            "preset": perception_activation.metadata.get("preset"),
            "plugins": list(perception_activation.plugins),
            "plugin_report": plugin_report(perception_step),
        },
        "decision": {
            "generation_id": identity["generation_id"],
            "steps": identity["steps"],
            "published": decision_published,
            "latest_frame_publish_skips": 0,
            "latest_frame_publish_skip_reason": None,
        },
        "memory": (
            {
                "activation": display_path(memory_activation_path),
                "status": memory_step.status(),
            }
            if memory_step is not None
            else {
                "activation": display_path(memory_activation_path),
                "status": "absent",
            }
        ),
        "proposal": (
            {
                "activation": display_path(proposal_activation_path),
                "status": proposal_step.status(),
            }
            if proposal_step is not None
            else {
                "activation": display_path(proposal_activation_path),
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
        "readiness": {
            "schema": "automa_cli_readiness_v1",
            "status": "blocked",
            "ready_for": "inspect perception",
            "checked_at_ms": _timestamp_ms(),
            "gates": {
                "sensor_capture": {"status": "incomplete"},
                "perception": {"status": "incomplete"},
                "perception_view": {"status": "incomplete"},
            },
            "blocking_layer": "sensor_capture",
        },
    }
    _write_json(state_path, state)

    _emit(output, f"Automation running: {vehicle_id}")
    _emit(output, f"Perception: {perception_preset}")
    if run_dir is not None:
        _emit(output, f"Recording: {display_path(run_dir)}")
    else:
        _emit(output, "Recording: off; latest frame and perception are overwritten each iteration")
    _emit(
        output,
        f"Control source: {'external WS' if take_control else 'preserved current simulator source'}",
    )
    _emit(output, f"Action policy: {state['action_policy']}")
    _emit(output, f"Decision generation: {identity['generation_id']}")
    if published_view.get("available"):
        _emit(output, f"Runtime view: {published_view.get('url')}")
    else:
        _emit(output, f"Runtime view: unavailable ({published_view.get('reason', 'startup failed')})")
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
    passive_session_initial: dict[str, Any] | None = None

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
        if memory_step is None:
            result = {
                "schema": "automa_memory_reset_result_v0",
                "ok": False,
                "status": "absent",
                "token": token,
                "error": "no memory step is activated in the automation worker",
                "completed_at_ms": _timestamp_ms(),
            }
        else:
            try:
                report = cycle_host.reset_memory()
                result = {
                    "schema": "automa_memory_reset_result_v0",
                    "ok": True,
                    "status": "reset",
                    "token": token,
                    "report": report,
                    "memory": memory_step.status(),
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
            if memory_step is not None:
                state["memory"] = {
                    "activation": display_path(memory_activation_path),
                    "status": memory_step.status(),
                }
            state["updated_at_ms"] = _timestamp_ms()
            _write_json(state_path, state)
        try:
            request_path.unlink(missing_ok=True)
        except OSError:
            pass

    def adopt_staged_decision() -> None:
        """Publish under the staged decision generation once this worker runs it.

        The proposal runner applies a changed plugin list during the cycle.
        Adopt only after its applied IDs match the staged generation: a load
        or reset failure can leave the requested selection unapplied.
        Restaged proposal configs, plan, or action wait for a restart.
        """

        nonlocal identity
        try:
            staged = decision_identity(bundle)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        applied_ids = getattr(proposal_step, "plugin_ids", None)
        if staged == identity or applied_ids is None:
            return
        running = {
            **identity["steps"],
            "proposal": {**identity["steps"]["proposal"], "plugins": list(applied_ids)},
        }
        if decision_generation_id(running) != staged["generation_id"]:
            return
        identity = staged
        with state_lock:
            state["decision"].update(
                generation_id=staged["generation_id"], steps=staged["steps"]
            )
        if view_server is not None:
            view_server.decision.adopt(staged)
        _emit(output, f"Decision generation: {staged['generation_id']} (restaged)")

    def process_frame(pending: _PendingAutomationFrame) -> None:
        apply_memory_reset_if_requested()
        context = pending.context
        sensor_frame = context.sensor_frame
        if sensor_frame is None:
            raise ValueError(f"{context.frame_id} has no sensor frame")
        cycle_started_at_ms = _timestamp_ms()
        perception_started_at_ms = _timestamp_ms()
        cycle_host.sync_selection()
        cycle_result = cycle_host.run(context)
        if proposal_step is not None:
            adopt_staged_decision()
        # Publish the accepted decision frame first. The server-owned decision
        # transaction is joined only after the full frame record exists below.
        published = False
        try:
            last_cycle = cycle_result.action
            published = decision_published and publish_decision_frame(
                cycle_result=cycle_result,
                context_frame_id=context.frame_id,
                vehicle_id=vehicle_id,
                vehicle_runtime_dir=vehicle_runtime_dir,
                run_id=str(state.get("run_id") or run_id),
                worker_pid=int(state.get("pid") or os.getpid()),
                activation=identity,
            )
            if not published and decision_published:
                # Count for workers that staged proposals at start (including
                # after a restage this worker cannot run until it restarts).
                reason = (
                    "gate_rejected"
                    if last_cycle is None
                    else "gate_rejected_frame_or_activation_mismatch"
                )
                _record_decision_publish_skip(
                    state, state_path, state_lock, reason=reason
                )
                if verbose:
                    _emit(
                        output,
                        f"{context.frame_id}: decision latest-frame publish "
                        f"skipped ({reason})",
                    )
        except Exception as exc:  # noqa: BLE001 - non-fatal publish skip
            reason = f"{type(exc).__name__}: {exc}"
            # Counter persistence must never re-raise into the cycle path.
            _record_decision_publish_skip(state, state_path, state_lock, reason=reason)
            if verbose:
                try:
                    _emit(
                        output,
                        f"{context.frame_id}: decision latest-frame publish skip: {reason}",
                    )
                except Exception:  # noqa: BLE001 - logging is best-effort
                    pass
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
        perception_plugin_report = plugin_report(perception_step)
        memory_plugin_report = plugin_report(memory_step)
        proposal_plugin_report = plugin_report(proposal_step)

        control_record = cycle_result.control_record()
        simulator_frame_index = simulator_frame_index_from_sensor_frame(sensor_frame)
        simulation_epoch = simulator_epoch_from_sensor_frame(sensor_frame)
        if simulator_frame_index is None or simulation_epoch is None:
            raise ValueError(
                "Chase decision frame is missing atomic simulation-run identity"
            )
        frame_record = {
            "frame_id": context.frame_id,
            "frame_index": context.frame_index,
            "simulator_frame_index": simulator_frame_index,
            "simulation_epoch": simulation_epoch,
            # Immutable automation generation: pairs frame publications with probe.
            "run_id": state.get("run_id"),
            "worker_pid": state.get("pid"),
            "captured_at_ms": sensor_frame.completed_at_ms,
            "cycle_started_at_ms": cycle_started_at_ms,
            "cycle_completed_at_ms": perception_completed_at_ms,
            "cycle_duration_ms": perception_completed_at_ms - cycle_started_at_ms,
            "perception_started_at_ms": perception_started_at_ms,
            "perception_completed_at_ms": perception_completed_at_ms,
            "perception_duration_ms": perception_completed_at_ms - perception_started_at_ms,
            "capture_to_perception_ms": perception_completed_at_ms - sensor_frame.completed_at_ms,
            "sensor_frame": sensor_frame.to_dict(),
            "perception": perception_dict,
            "perception_plugin_report": perception_plugin_report,
            "memory_plugin_report": memory_plugin_report,
            "proposal_plugin_report": proposal_plugin_report,
            "observation": cycle_result.observation.to_dict()
            if cycle_result.observation is not None
            else None,
            # The memory step's report of each plugin's state.
            "memory": copy.deepcopy(cycle_result.memory),
            "control": control_record,
            "steps": _step_status(cycle_host),
            "decision_cycle": cycle_result.to_dict(),
            "action_policy": state["action_policy"],
            "control_source": state["control_source"],
            "control_application": state["control_application"],
        }
        # The chaser reference is evaluator-only: sibling of candidate results, not an input.
        if isinstance(pending.chaser_reference, dict):
            frame_record["chaser_reference"] = pending.chaser_reference
            frame_record["reference_alignment"] = {
                "aligned": pending.chaser_reference.get("simulator_frame_index")
                == simulator_frame_index
                and pending.chaser_reference.get("simulation_epoch") == simulation_epoch,
                "candidate_frame_index": simulator_frame_index,
                "reference_frame_index": pending.chaser_reference.get("simulator_frame_index"),
                "candidate_simulation_epoch": simulation_epoch,
                "reference_simulation_epoch": pending.chaser_reference.get("simulation_epoch"),
            }
        if view_server is not None:
            try:
                if published:
                    latest_decision = _read_latest_decision_frame_for_view(
                        automation_dir / "latest_decision.json",
                        frame_id=context.frame_id,
                        run_id=str(state.get("run_id") or run_id),
                        worker_pid=int(state.get("pid") or os.getpid()),
                        generation_id=identity["generation_id"],
                    )
                    if latest_decision is None:
                        view_server.decision.invalidate_latest()
                        decision_view_skip = True
                    else:
                        decision_view_skip = not view_server.decision.publish(
                            stream_frame=latest_decision,
                            frame_record=frame_record,
                            image=view_server.perception.frame(context.frame_id),
                        )
                    if decision_view_skip:
                        _record_decision_publish_skip(
                            state,
                            state_path,
                            state_lock,
                            reason="decision_view_exact_transaction_unavailable",
                        )
                view_server.perception.publish_perception(frame_record=frame_record)
                update_view_state(view_server.health_payload())
            except Exception as exc:  # noqa: BLE001 - view publication is observational
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
            state["processed_count"] = int(state["processed_count"]) + 1
            state["last_frame"] = {
                "frame_id": context.frame_id,
                "frame_index": context.frame_index,
                "simulator_frame_index": simulator_frame_index,
                "simulation_epoch": simulation_epoch,
                "captured_at_ms": sensor_frame.completed_at_ms,
                "perception_completed_at_ms": perception_completed_at_ms,
                "perception_duration_ms": perception_completed_at_ms - perception_started_at_ms,
                "capture_to_perception_ms": perception_completed_at_ms - sensor_frame.completed_at_ms,
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
                "generation_id": identity["generation_id"],
                "reference_aligned": bool(
                    isinstance(pending.chaser_reference, dict)
                    and pending.chaser_reference.get("simulator_frame_index")
                    == simulator_frame_index
                    and pending.chaser_reference.get("simulation_epoch") == simulation_epoch
                ),
            }
            state["steps"] = _step_status(cycle_host)
            view_health = (
                state.get("published_view")
                if isinstance(state.get("published_view"), dict)
                else {}
            )
            view_ready = perception_view_ready(view_health)
            state["readiness"] = {
                "schema": "automa_cli_readiness_v1",
                "status": "ready" if view_ready else "blocked",
                "ready_for": "inspect perception",
                "checked_at_ms": _timestamp_ms(),
                "gates": {
                    "sensor_capture": {"status": "ready"},
                    "perception": {"status": "ready"},
                    "perception_view": {
                        "status": "ready" if view_ready else "blocked"
                    },
                },
                "blocking_layer": None if view_ready else "perception_view",
            }
            if memory_step is not None:
                state["memory"] = {
                    "activation": display_path(memory_activation_path),
                    "status": memory_step.status(),
                }
            if proposal_step is not None:
                state["proposal"] = {
                    "activation": display_path(proposal_activation_path),
                    "status": proposal_step.status(),
                }
            perception_state = state.get("perception")
            if isinstance(perception_state, dict):
                perception_state["plugin_report"] = copy.deepcopy(perception_plugin_report)
            state["session"] = cycle_host.session_status()
            state["execution"] = cycle_host.execution.status()
            state["updated_at_ms"] = _timestamp_ms()
            _write_json(state_path, state)

        processed_count = int(state["processed_count"])
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
                cycle_host.close()
                if not worker_failed.is_set():
                    worker_errors.append(exc)
                    worker_failed.set()
            finally:
                if isinstance(item, _PendingAutomationFrame) and view_server is not None:
                    view_server.perception.release_frame(item.context.frame_id)
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
                if isinstance(dropped, _PendingAutomationFrame) and view_server is not None:
                    view_server.perception.release_frame(dropped.context.frame_id)
                pending_frames.task_done()
                with state_lock:
                    state["skipped_count"] = int(state["skipped_count"]) + 1

    def stop_perception_worker(*, process_latest: bool) -> None:
        if not process_latest:
            cycle_host.close()
        if not process_latest:
            try:
                dropped = pending_frames.get_nowait()
            except queue.Empty:
                dropped = None
            if isinstance(dropped, _PendingAutomationFrame):
                if view_server is not None:
                    view_server.perception.release_frame(dropped.context.frame_id)
                if not record:
                    dropped.front_path.unlink(missing_ok=True)
                with state_lock:
                    state["skipped_count"] = int(state["skipped_count"]) + 1
                pending_frames.task_done()
        try:
            pending_frames.put(worker_sentinel, timeout=1.0)
        except queue.Full:
            cycle_host.close()
            raise TimeoutError("decision queue did not accept shutdown")
        worker_thread.join(timeout=5.0)
        if worker_thread.is_alive():
            cycle_host.close()
            raise TimeoutError("decision worker did not stop within five seconds")

    try:
        cycle_host.start(RunConfiguration(
            mode="autonomy" if take_control else "observe_only",
            interval_s=interval_s, frames=frames,
        ))
        worker_thread.start()
        capture_sequence = 0
        next_capture_at = time.monotonic()
        capture_interval_s = max(0.0, float(interval_s))
        while cycle_host.run_state == "running":
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
            sensor_frame = car.read_sensors(
                SensorReadRequest(
                    output_dir=frames_dir,
                    read_id=provisional_id,
                    requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
                    image_extension="png",
                    front_camera_endpoint="atomic-evaluation-capture",
                )
            )
            if not take_control:
                passive_capture = getattr(car, "last_passive_capture", None)
                environment = (
                    passive_capture.get("environment")
                    if isinstance(passive_capture, dict)
                    and isinstance(passive_capture.get("environment"), dict)
                    else {}
                )
                preserved_source = environment.get("control_source")
                if isinstance(preserved_source, str) and preserved_source:
                    state["control_source"] = preserved_source
                state["passive_capture"] = passive_capture
                preservation = (
                    passive_capture.get("session_preservation")
                    if isinstance(passive_capture, dict)
                    and isinstance(
                        passive_capture.get("session_preservation"),
                        dict,
                    )
                    else {}
                )
                before_fingerprint = (
                    preservation.get("before")
                    if isinstance(preservation.get("before"), dict)
                    else None
                )
                after_fingerprint = (
                    preservation.get("after")
                    if isinstance(preservation.get("after"), dict)
                    else None
                )
                if passive_session_initial is None and before_fingerprint is not None:
                    passive_session_initial = before_fingerprint
                if (
                    passive_session_initial is not None
                    and after_fingerprint is not None
                ):
                    session_receipt = compare_chase_session_fingerprints(
                        passive_session_initial,
                        after_fingerprint,
                        field_names=PASSIVE_RUN_STABLE_FIELDS,
                    )
                    session_receipt["dynamic_fields_allowed"] = list(
                        PASSIVE_RUN_DYNAMIC_FIELDS
                    )
                    state["passive_session"] = session_receipt
                    if not session_receipt.get("preserved"):
                        raise ChasePassiveCaptureError(
                            code=(
                                "simulator_state_changed"
                                if session_receipt.get("changed_fields")
                                else "simulator_capability_missing"
                            ),
                            message=(
                                "The simulator identity or control authority "
                                "changed during observation-only automation."
                            ),
                            details={
                                "session_preservation": session_receipt,
                                "mutation_attempted": False,
                            },
                        )
            simulator_frame_index = simulator_frame_index_from_sensor_frame(sensor_frame)
            if simulator_frame_index is None and hasattr(car, "last_simulator_frame_index"):
                simulator_frame_index = getattr(car, "last_simulator_frame_index", None)
            if simulator_frame_index is not None:
                frame_index = int(simulator_frame_index)
                frame_id = format_chase_frame_id(frame_index)
            else:
                # Fail closed for live Chase: local counters cannot align chaser references.
                raise ValueError(
                    "Chase sensor capture missing simulator frameIndex; "
                    "cannot assign camera-derived frame identity for reference alignment"
                )
            simulation_epoch = simulator_epoch_from_sensor_frame(sensor_frame)
            if simulation_epoch is None:
                raise ValueError(
                    "Chase sensor capture missing simulationEpoch; "
                    "cannot establish atomic run identity for reference alignment"
                )
            # Align SensorFrame.read_id with simulator identity (capture used a provisional id).
            if sensor_frame.read_id != frame_id:
                sensor_frame = replace(sensor_frame, read_id=frame_id)
            if record:
                perception_output_dir = perception_dir / frame_id
            chaser_reference = None
            if hasattr(car, "last_capture_chaser_reference"):
                chaser_reference = getattr(car, "last_capture_chaser_reference", None)

            front_reading = sensor_frame.readings.get(FRONT_CAMERA_SENSOR_ID)
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
                            sensor_frame = replace(
                                sensor_frame,
                                readings={**sensor_frame.readings, FRONT_CAMERA_SENSOR_ID: updated},
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
                "captured_at_ms": sensor_frame.completed_at_ms,
                "capture_started_at_ms": captured_started_at_ms,
                "capture_duration_ms": sensor_frame.completed_at_ms - captured_started_at_ms,
                "sensor_frame": sensor_frame.to_dict(),
            }
            # Evaluator-only: never placed on DecisionFrameContext / observation.
            if isinstance(chaser_reference, dict):
                capture_record["chaser_reference"] = chaser_reference
            if view_server is not None:
                try:
                    view_server.perception.publish_frame(frame_path=front_path, frame_record=capture_record)
                    update_view_state(view_server.health_payload())
                except Exception as exc:  # noqa: BLE001 - view publication is observational
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
                sensor_frame=sensor_frame,
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
                    "control_application": "shared_execution" if take_control else "not_applied",
                },
            )
            if view_server is not None:
                view_server.perception.retain_frame(frame_id)
            enqueue_latest(
                _PendingAutomationFrame(
                    context=context,
                    front_path=front_path,
                    chaser_reference=chaser_reference if isinstance(chaser_reference, dict) else None,
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
                    "captured_at_ms": sensor_frame.completed_at_ms,
                    "capture_duration_ms": sensor_frame.completed_at_ms - captured_started_at_ms,
                    "front_camera": display_path(latest_front_camera_path if not record else front_path),
                    "reference_aligned": isinstance(chaser_reference, dict)
                    and chaser_reference.get("simulator_frame_index") == frame_index
                    and chaser_reference.get("simulation_epoch") == simulation_epoch,
                }
                state["updated_at_ms"] = _timestamp_ms()
                _write_json(state_path, state)

            capture_sequence += 1

        stop_perception_worker(process_latest=True)
        if worker_failed.is_set():
            raise worker_errors[0]

    except KeyboardInterrupt:
        cycle_host.close()
        if worker_thread.is_alive():
            stop_perception_worker(process_latest=False)
        state["status"] = "stopped"
        state["stop_reason"] = "keyboard_interrupt"
        state["completed_at_ms"] = _timestamp_ms()
        state["updated_at_ms"] = state["completed_at_ms"]
        state["published_view"] = _stop_perception_view(view_server)
        state["readiness"] = _automation_stopped_readiness()
        _write_json(state_path, state)
        return CommandResult(
            130,
            "\n".join(
                [
                    f"Automation stopped: {vehicle_id}",
                    f"State: {display_path(state_path)}",
                    "Ready for: inspect stopped deployment",
                ]
            ),
        )
    except (MetricsUiWebSocketError, ChaseCaptureValidationError, ChasePassiveCaptureError) as exc:
        cycle_host.close()
        if worker_thread.is_alive():
            stop_perception_worker(process_latest=False)
        state["status"] = "error"
        state["error"] = str(exc)
        state["error_code"] = getattr(exc, "code", "simulator_transport_error")
        state["error_details"] = (
            exc.to_dict() if hasattr(exc, "to_dict") and callable(exc.to_dict) else None
        )
        state["readiness"] = {
            "schema": "automa_cli_readiness_v1",
            "status": "blocked",
            "ready_for": "inspect perception",
            "checked_at_ms": _timestamp_ms(),
            "gates": {},
            "blocking_layer": (
                "capture"
                if isinstance(exc, ChaseCaptureValidationError)
                else "passive_capture"
            ),
        }
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
                    "Not ready for: inspect perception",
                ]
            ),
        )
    except Exception as exc:
        cycle_host.close()
        if worker_thread.is_alive():
            stop_perception_worker(process_latest=False)
        state["status"] = "error"
        state["error"] = f"{type(exc).__name__}: {exc}"
        state["readiness"] = {
            "schema": "automa_cli_readiness_v1",
            "status": "blocked",
            "ready_for": "inspect perception",
            "checked_at_ms": _timestamp_ms(),
            "gates": {},
            "blocking_layer": "automation_worker",
        }
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
                    "Not ready for: inspect perception",
                ]
            ),
        )

    cycle_host.close()
    state["status"] = "completed"
    state["completed_at_ms"] = _timestamp_ms()
    state["updated_at_ms"] = state["completed_at_ms"]
    state["published_view"] = _stop_perception_view(view_server)
    state["readiness"] = _automation_stopped_readiness()
    _write_json(state_path, state)
    return CommandResult(
        0,
        "\n".join(
            [
                f"Automation completed: {vehicle_id}",
                f"Frames captured: {state['frames_captured']}",
                f"Frames processed: {state['processed_count']}",
                f"Frames skipped by perception: {state['skipped_count']}",
                f"Control source: {state['control_source']}",
                f"Action policy: {state['action_policy']}",
                f"Recording: {'on' if record else 'off'}",
                f"State: {display_path(state_path)}",
                f"Latest perception: {display_path(latest_text_path)}",
                "Ready for: inspect stopped deployment",
            ]
        ),
    )


def start_vehicle_automation_background(
    *,
    vehicle_id: str,
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
    interval_s: float = 0.25,
    frames: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
    open_view: bool = False,
    startup_wait_s: float = 20.0,
) -> CommandResult:
    try:
        RunConfiguration(mode="autonomy" if take_control else "observe_only", interval_s=interval_s, frames=frames)
    except (TypeError, ValueError) as exc:
        return CommandResult(2, str(exc))
    automation_dir = chase_automation_dir(vehicle_id)
    automation_dir.mkdir(parents=True, exist_ok=True)
    process_path = automation_dir / "process.json"
    log_path = automation_dir / "automation.log"

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    problems = bundle_activation_problems(bundle, vehicle_id)
    if problems:
        return CommandResult(2, format_activation_problems(problems))

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
                        "Not ready for: inspect perception",
                    ]
                ),
            )
        existing_run_id = (
            existing_state.get("run_id")
            if isinstance(existing_state, dict)
            and isinstance(existing_state.get("run_id"), str)
            else None
        )
        existing_state_pid = (
            existing_state.get("pid")
            if isinstance(existing_state, dict)
            and isinstance(existing_state.get("pid"), int)
            else None
        )
        if existing_state_pid is not None and existing_state_pid != existing_pid:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Automation records disagree for {vehicle_id}.",
                        f"Process PID: {existing_pid}",
                        f"State PID: {existing_state_pid}",
                        "Reason: worker generation is stale; no duplicate worker was started.",
                        "Not ready for: inspect perception",
                    ]
                ),
            )
        expected_authority = {
            "action_policy": "autonomy" if take_control else "observe_only",
            "control_application": (
                "shared_execution" if take_control else "not_applied"
            ),
        }
        actual_authority = {
            key: existing_state.get(key) if isinstance(existing_state, dict) else None
            for key in expected_authority
        }
        if actual_authority != expected_authority:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Automation is already running for {vehicle_id}, but its authority does not match this command.",
                        f"PID: {existing_pid}",
                        (
                            "Expected authority: "
                            f"action={expected_authority['action_policy']}, "
                            f"control={expected_authority['control_application']}"
                        ),
                        (
                            "Current authority: "
                            f"action={actual_authority['action_policy'] or 'unknown'}, "
                            f"control={actual_authority['control_application'] or 'unknown'}"
                        ),
                        (
                            "Recovery: ./cli/automa vehicles automation stop "
                            f"--id {vehicle_id}"
                        ),
                        "Not ready for: inspect perception",
                    ]
                ),
            )
        view = get_perception_view_status(
            automation_dir,
            expected_run_id=existing_run_id,
            expected_worker_pid=existing_pid,
        )
        if not _view_ready_for_inspection(view):
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Automation is running for {vehicle_id}, but its perception view is not ready.",
                        f"PID: {existing_pid}",
                        f"Reason: {view.get('reason') or 'no correlated publication'}",
                        f"State: {display_path(automation_dir / 'state.json')}",
                        "Not ready for: inspect perception",
                    ]
                ),
            )
        browser_line = _open_view_message(str(view["url"])) if open_view else None
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Automation already running for {vehicle_id}.",
                    f"PID: {existing_pid}",
                    f"Runtime view: {view['url']}",
                    *([browser_line] if browser_line else []),
                    "Ready for: inspect perception and stop automation",
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
                "\n".join(
                    [
                        f"Could not launch automation for {vehicle_id}: {type(exc).__name__}: {exc}",
                        "Not ready for: inspect perception",
                    ]
                ),
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
        phases = startup.get("phases") if isinstance(startup.get("phases"), dict) else {}
        capture_phase = (
            phases.get("capture") if isinstance(phases.get("capture"), dict) else {}
        )
        perception_phase = (
            phases.get("perception")
            if isinstance(phases.get("perception"), dict)
            else {}
        )
        lines = [
            f"Automation ready for {vehicle_id}.",
            f"PID: {process.pid}",
            f"First frame: {startup.get('frame_id', 'captured')}",
            (
                "Startup phases: "
                f"capture={capture_phase.get('duration_ms', 'unknown')}ms, "
                f"perception={perception_phase.get('duration_ms', 'unknown')}ms, "
                "view=current-generation correlated"
            ),
            f"Runtime view: {startup.get('view_url')}",
            "Ready for: inspect perception and stop automation",
        ]
        if open_view:
            lines.append(_open_view_message(str(startup.get("view_url"))))
        exit_code = 0
    elif startup_status == "completed":
        lines = [
            f"Automation completed for {vehicle_id} during startup verification.",
            f"PID: {process.pid}",
            "Ready for: inspect stopped deployment",
        ]
        exit_code = 0
    else:
        lines = [
            f"Automation did not become ready for {vehicle_id}.",
            f"PID: {process.pid}",
            f"Reason: {startup.get('reason', 'unknown startup failure')}",
            "Not ready for: inspect perception",
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
    automation_dir = chase_automation_dir(vehicle_id)
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
            "readiness": {
                "schema": "automa_cli_readiness_v1",
                "status": "blocked",
                "ready_for": "inspect perception",
                "checked_at_ms": completed_at_ms,
                "gates": {},
                "blocking_layer": "automation_worker",
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
            "processed_count": 0,
            "skipped_count": 0,
            "max_frames": None if max(0, int(frames)) == 0 else max(0, int(frames)),
            "interval_s": max(0.0, float(interval_s)),
            "pipeline": "latest_frame_async_perception",
            "control_source": "external_ws" if take_control else "preserved_current",
            "action_policy": "autonomy" if take_control else "observe_only",
            "control_application": "shared_execution" if take_control else "not_applied",
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
            "readiness": {
                "schema": "automa_cli_readiness_v1",
                "status": "blocked",
                "ready_for": "inspect perception",
                "checked_at_ms": started_at_ms,
                "gates": {
                    "sensor_capture": {"status": "incomplete"},
                    "perception": {"status": "incomplete"},
                    "perception_view": {"status": "incomplete"},
                },
                "blocking_layer": "sensor_capture",
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
        processed_count = state.get("processed_count")
        last_capture = (
            state.get("last_capture")
            if isinstance(state.get("last_capture"), dict)
            else {}
        )
        last_frame = (
            state.get("last_frame")
            if isinstance(state.get("last_frame"), dict)
            else {}
        )

        if (
            status == "running"
            and isinstance(frames_captured, int)
            and frames_captured > 0
            and isinstance(processed_count, int)
            and processed_count > 0
            and last_capture.get("frame_id") == last_frame.get("frame_id")
        ):
            expected_pid = state.get("pid") if isinstance(state.get("pid"), int) else None
            expected_run_id = (
                state.get("run_id") if isinstance(state.get("run_id"), str) else None
            )
            remaining = deadline - time.monotonic()
            if remaining < 0.05:
                continue
            view = get_perception_view_status(
                automation_dir,
                timeout_s=min(0.25, remaining),
                expected_run_id=expected_run_id,
                expected_worker_pid=expected_pid,
            )
            if (
                _view_ready_for_inspection(view)
                and view.get("latest_frame_id") == last_frame.get("frame_id")
                and view.get("latest_perception_frame_id") == last_frame.get("frame_id")
            ):
                return {
                    "status": "ready",
                    "frame_id": last_frame.get("frame_id"),
                    "view_url": view.get("url"),
                    "phases": {
                        "capture": {
                            "status": "complete",
                            "duration_ms": last_capture.get("capture_duration_ms"),
                        },
                        "perception": {
                            "status": "complete",
                            "duration_ms": last_frame.get("perception_duration_ms"),
                        },
                        "view": {
                            "status": "complete",
                            "duration_ms": 0,
                            "run_id": expected_run_id,
                            "worker_pid": expected_pid,
                        },
                    },
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
                    "worker is still running but did not publish one correlated "
                    "camera/perception frame and current-generation view "
                    f"within {max(0.1, float(timeout_s)):.1f}s"
                ),
                "phases": {
                    "capture": {
                        "status": "complete"
                        if isinstance(frames_captured, int) and frames_captured > 0
                        else "incomplete",
                    },
                    "perception": {
                        "status": "complete"
                        if isinstance(processed_count, int) and processed_count > 0
                        else "incomplete",
                    },
                    "view": {
                        "status": "complete"
                        if _view_ready_for_inspection(published_view)
                        else "incomplete",
                    },
                },
                "state": state,
            }
        time.sleep(0.05)


def _view_ready_for_inspection(view: dict[str, Any]) -> bool:
    return perception_view_ready(view)


def _open_view_message(url: str) -> str:
    """Attempt the explicit browser launch without weakening worker readiness."""

    try:
        opened = webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error) as exc:
        opened = False
        detail = f"{type(exc).__name__}: {exc}"
    else:
        detail = "browser launcher returned false"
    if opened:
        return f"Browser opened: {url}"
    return (
        f"Warning: could not open the browser ({detail}). "
        f"Open manually: {url}"
    )


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
            "readiness": {
                "schema": "automa_cli_readiness_v1",
                "status": "blocked",
                "ready_for": "inspect perception",
                "checked_at_ms": completed_at_ms,
                "gates": {},
                "blocking_layer": "automation_worker",
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
    onboard = _staged_onboard_vehicle(vehicle_id)
    if onboard is not None:
        from implementations.runtime.donkeycar.client import OnboardRuntimeClient
        try:
            OnboardRuntimeClient(picar_base_url(onboard), timeout_s=max(1.0, wait_s)).stop()
        except (RuntimeError, OSError, ValueError) as exc:
            return CommandResult(2, f"Could not stop {vehicle_id}: {exc}")
    automation_dir = chase_automation_dir(vehicle_id)
    process_path = automation_dir / "process.json"
    state_path = automation_dir / "state.json"
    process = _read_json(process_path)
    pid = process.get("pid") if isinstance(process, dict) else None

    if not isinstance(pid, int):
        _mark_state_stopped(state_path, stopped_by="stop_command_no_pid")
        return CommandResult(
            0,
            "\n".join(
                [
                    f"No automation PID is recorded for {vehicle_id}.",
                    f"State: {display_path(state_path)}",
                    "Ready for: inspect stopped deployment",
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
                    "Ready for: inspect stopped deployment",
                ]
            ),
        )
    if not _pid_matches_automation(pid, vehicle_id):
        _mark_process_stopped(process_path, process, stopped_by="stop_command_stale_pid")
        _mark_state_stopped(state_path, stopped_by="stop_command_stale_pid")
        return CommandResult(
            0,
            "\n".join(
                [
                    f"Recorded automation PID for {vehicle_id} is alive but does not match this automation command.",
                    f"PID: {pid}",
                    "The PID record was marked stale; no process was terminated.",
                    f"Process: {_process_command(pid) or 'unknown'}",
                    "Ready for: inspect stopped deployment",
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
                        "Ready for: inspect stopped deployment",
                    ]
                ),
            )
        time.sleep(0.1)

    if onboard is None and (_read_json(state_path) or {}).get("action_policy") == "autonomy":
        # A killed process cannot run its watchdog or finally blocks. Stop the
        # transport before terminating a worker whose plugin did not return.
        discovery = discover_active_vehicles(timeout_s=max(1.0, wait_s), include_inactive=True)
        vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
        if vehicle is None or error:
            return CommandResult(2, f"Could not resolve {vehicle_id} for forced stop: {error}")
        try:
            create_vehicle_access(vehicle, timeout_s=max(1.0, wait_s)).car.stop()
        except Exception as exc:
            return CommandResult(2, f"Could not deliver stop to {vehicle_id}: {exc}")
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
                        "Ready for: inspect stopped deployment",
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
                "Not ready for: inspect stopped deployment",
            ]
        ),
    )


def restart_vehicle_automation(
    *,
    vehicle_id: str,
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
    interval_s: float = 0.25,
    frames: int = 0,
    take_control: bool = True,
    record: bool = False,
    verbose: bool = False,
    log_to_disk: bool = False,
    open_view: bool = False,
    wait_s: float = 3.0,
) -> CommandResult:
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    problems = bundle_activation_problems(bundle, vehicle_id)
    if problems:
        return CommandResult(2, format_activation_problems(problems))
    stop_result = stop_vehicle_automation(vehicle_id=vehicle_id, wait_s=wait_s)
    if stop_result.exit_code != 0:
        return stop_result

    onboard = _staged_onboard_vehicle(vehicle_id)
    if onboard is not None:
        from implementations.runtime.donkeycar.client import OnboardRuntimeClient
        try:
            OnboardRuntimeClient(picar_base_url(onboard), timeout_s=timeout_s).restart(
                timeout_s=max(30.0, timeout_s)
            )
        except (RuntimeError, OSError, ValueError) as exc:
            return CommandResult(2, f"Could not restart {vehicle_id}: {exc}")

    start_result = start_vehicle_automation_background(
        vehicle_id=vehicle_id,
        timeout_s=timeout_s,
        interval_s=interval_s,
        frames=frames,
        take_control=take_control,
        record=record,
        verbose=verbose,
        log_to_disk=log_to_disk,
        open_view=open_view,
    )
    message = "\n\n".join(part for part in (stop_result.message, start_result.message) if part)
    return CommandResult(start_result.exit_code, message)


def _collect_automation_status(
    *,
    vehicle_id: str | None,
    view_timeout_s: float | None = None,
) -> list[dict[str, Any]]:
    if vehicle_id is not None:
        candidate = RUNTIME_ROOT / safe_path_part(vehicle_id)
        candidates = [candidate] if candidate.is_dir() else []
    elif RUNTIME_ROOT.exists():
        candidates = sorted(path for path in RUNTIME_ROOT.iterdir() if path.is_dir())
    else:
        candidates = []

    # One wall-clock budget for every runtime's view probes and warm-up sleeps.
    # Aggregate status without --id must not restart the full timeout per card.
    if view_timeout_s is None:
        view_budget_s = 1.0
    else:
        view_budget_s = max(0.0, float(view_timeout_s))
    view_deadline = time.monotonic() + view_budget_s

    def _remaining_view_budget() -> float:
        return max(0.0, view_deadline - time.monotonic())

    def _exhausted_view_status() -> dict[str, Any]:
        return {
            "schema": "automa_perception_view_v1",
            "available": False,
            "status": "unavailable",
            "url": None,
            "reason": (
                "command deadline was exhausted before current-generation "
                "view health could be checked"
            ),
        }

    statuses = []
    for vehicle_runtime_dir in candidates:
        vehicle_name = vehicle_runtime_dir.name
        onboard = _staged_onboard_vehicle(vehicle_name)
        bundle = controller_bundle_paths(vehicle_runtime_dir)
        bundle_root = Path(bundle["root_dir"])
        automation_dir = Path(bundle["runtime_dir"]) / "automation"
        perception_manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
        process_path = automation_dir / "process.json"
        state_path = automation_dir / "state.json"
        latest_perception_path = automation_dir / "latest_perception.txt"

        perception_manifest = _read_json(perception_manifest_path)
        process = _read_json(process_path)
        state = _read_json(state_path)
        process = process if isinstance(process, dict) else {}
        state = state if isinstance(state, dict) else {}

        pid = process.get("pid") if isinstance(process.get("pid"), int) else state.get("pid")
        pid_alive = _pid_alive(pid) if isinstance(pid, int) else False
        state_pid = state.get("pid") if isinstance(state.get("pid"), int) else None
        process_pid = process.get("pid") if isinstance(process.get("pid"), int) else None
        generation_matches = (
            state_pid is None
            or process_pid is None
            or state_pid == process_pid
        )
        run_id = state.get("run_id") if isinstance(state.get("run_id"), str) else None
        remaining_view_budget = _remaining_view_budget()
        if remaining_view_budget <= 0:
            published_view = _exhausted_view_status()
        else:
            first_probe_timeout_s = min(0.25, remaining_view_budget)
            published_view = get_perception_view_status(
                automation_dir,
                timeout_s=first_probe_timeout_s,
                expected_run_id=run_id,
                expected_worker_pid=pid if isinstance(pid, int) else None,
            )
        if not generation_matches:
            worker_status = "stale"
            worker_reason = (
                "process/state worker generation mismatch: "
                f"process pid={process_pid}, state pid={state_pid}"
            )
        else:
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
            # Live workers publish camera then perception asynchronously. Status
            # must not fail the frontier gate by sampling the gap between those
            # publishes; poll briefly for a correlated current view — always
            # capped by the remaining command budget.
            if (
                worker_status == "running"
                and pid_alive
                and not published_view.get("available")
                and published_view.get("status") in {None, "warming", "unavailable"}
                and _remaining_view_budget() > 0
            ):
                while _remaining_view_budget() > 0:
                    sleep_s = min(0.1, _remaining_view_budget())
                    if sleep_s <= 0:
                        break
                    time.sleep(sleep_s)
                    probe_timeout_s = min(0.25, _remaining_view_budget())
                    if probe_timeout_s <= 0:
                        break
                    published_view = get_perception_view_status(
                        automation_dir,
                        timeout_s=probe_timeout_s,
                        expected_run_id=run_id,
                        expected_worker_pid=pid if isinstance(pid, int) else None,
                    )
                    if published_view.get("available"):
                        break
        activation_problems = bundle_activation_problems(bundle, vehicle_name)
        worker_recovery = activation_problems[0]["command"] if activation_problems else (
            f"./cli/automa vehicles automation restart --id {vehicle_name}"
            if worker_status in {"error", "stale"}
            else None
        )
        last_frame = state.get("last_frame") if isinstance(state.get("last_frame"), dict) else {}
        completed_at_ms = _int_or_none(last_frame.get("perception_completed_at_ms"))
        generated_at_ms = _timestamp_ms()

        perception = {}
        if isinstance(perception_manifest, dict):
            metadata = perception_manifest.get("metadata")
            perception = {
                "preset": metadata.get("preset") if isinstance(metadata, dict) else None,
                "plugins": perception_manifest.get("plugins")
                if isinstance(perception_manifest.get("plugins"), list)
                else [],
            }

        # Unstaged decision steps run their built-ins, so a readable identity is deployed.
        try:
            identity = decision_identity(bundle)
        except (OSError, TypeError, ValueError):
            identity = None
        decision: dict[str, Any] = {"deployed": identity is not None}
        if identity is not None:
            decision["generation_id"] = identity["generation_id"]
            decision["plugins"] = {
                step: (payload or {}).get("plugins") or []
                for step, payload in identity["steps"].items()
            }

        statuses.append(
            {
                "vehicle_id": vehicle_name,
                "activation_problems": activation_problems,
                "deployed": bundle_root.exists()
                and perception_manifest_path.exists()
                and decision["deployed"]
                and not activation_problems,
                "bundle_root": display_path(bundle_root),
                "automation_runtime_exists": automation_dir.exists(),
                "automation_dir": display_path(automation_dir),
                "perception": {
                    "deployed": perception_manifest_path.exists(),
                    "activation": display_path(perception_manifest_path),
                    **perception,
                },
                "decision": {
                    "activation": display_path(Path(bundle["runtime_dir"])),
                    **decision,
                },
                "process": {
                    "pid": pid,
                    "running": pid_alive,
                    "pid_state": "alive" if pid_alive else ("not_running" if isinstance(pid, int) else "none"),
                    "status": worker_status,
                    "generation_matches": generation_matches,
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
                    "processed_count": state.get("processed_count", 0),
                    "skipped_count": state.get("skipped_count", 0),
                    "max_frames": state.get("max_frames"),
                    "interval_s": state.get("interval_s"),
                    "recording": state.get("recording"),
                    "pid": state.get("pid"),
                    "control_source": state.get("control_source"),
                    "action_policy": state.get("action_policy"),
                    "execution": state.get("execution"),
                    "session": state.get("session"),
                    "control_application": state.get("control_application"),
                    "passive_capture": state.get("passive_capture")
                    if isinstance(state.get("passive_capture"), dict)
                    else None,
                    "passive_session": state.get("passive_session")
                    if isinstance(state.get("passive_session"), dict)
                    else None,
                    "readiness": state.get("readiness")
                    if isinstance(state.get("readiness"), dict)
                    else None,
                    "error": state.get("error") if isinstance(state.get("error"), str) else None,
                    "error_code": state.get("error_code")
                    if isinstance(state.get("error_code"), str)
                    else None,
                    "error_details": state.get("error_details")
                    if isinstance(state.get("error_details"), dict)
                    else None,
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
        if onboard is not None and not pid_alive:
            statuses[-1].update(
                _onboard_runtime_status(
                    vehicle_name, onboard, timeout_s=_remaining_view_budget() or 0.5
                )
            )
            if activation_problems:
                statuses[-1]["process"]["recovery"] = activation_problems[0]["command"]
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
        problems = item.get("activation_problems")
        if isinstance(problems, list) and problems:
            lines.extend(f"  {line}" for line in format_activation_problems(problems).splitlines())
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
    preset = perception.get("preset") or "unknown"
    plugins = ", ".join(perception.get("plugins") or []) or "no plugins"
    return f"{preset} ({plugins})"


def _decision_label(decision: dict[str, Any]) -> str:
    if not decision.get("deployed"):
        return f"not deployed; expected {decision.get('activation', 'unknown')}"
    plugins = decision.get("plugins") if isinstance(decision.get("plugins"), dict) else {}
    described = " ".join(
        f"{step}={','.join(ids) or '-'}" for step, ids in plugins.items()
    )
    return f"{described or 'unknown'} ({decision.get('generation_id') or 'no generation'})"


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
        f"processed={state.get('processed_count', 0)}",
        f"skipped={state.get('skipped_count', 0)}",
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
    """Generate a unique automation/run identifier.

    Wall-clock seconds alone collide when two workers start in the same second.
    Append a UUID fragment so the generation token is unique even under a frozen
    or equal timestamp.
    """

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:12]}"


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
    write_json_atomically(path, payload)


def _record_decision_publish_skip(
    state: dict[str, Any],
    state_path: Path,
    state_lock: threading.Lock,
    *,
    reason: str,
) -> None:
    """Count a non-fatal decision latest-frame publish skip (never raises).

    State persistence is best-effort. In-memory counters are updated first so a
    later successful state write still reflects the skip even if an intermediate
    disk write fails. Disk failures are swallowed so publish observability cannot
    fail the automation cycle path.
    """

    try:
        with state_lock:
            decision = state.get("decision")
            if not isinstance(decision, dict):
                decision = {}
                state["decision"] = decision
            decision["latest_frame_publish_skips"] = int(
                decision.get("latest_frame_publish_skips") or 0
            ) + 1
            decision["latest_frame_publish_skip_reason"] = reason[:MAX_STATUS_REASON_CHARS]
            state["updated_at_ms"] = _timestamp_ms()
            try:
                _write_json(state_path, state)
            except Exception:  # noqa: BLE001 - disk observability is best-effort
                pass
    except Exception:  # noqa: BLE001 - counter path must never raise
        pass


def _read_latest_decision_frame_for_view(
    path: Path,
    *,
    frame_id: str,
    run_id: str,
    worker_pid: int,
    generation_id: Any,
) -> dict[str, Any] | None:
    """Read only the bounded frame just accepted by this worker generation."""

    try:
        payload = Path(path).read_bytes()
        if len(payload) > MAX_DECISION_FILE_BYTES:
            return None
        frame = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(frame, dict):
        return None
    if (
        frame.get("frame_id") != frame_id
        or frame.get("run_id") != run_id
        or frame.get("worker_pid") != worker_pid
        or frame.get("generation_id") != generation_id
    ):
        return None
    return frame


def _stop_perception_view(view_server: RuntimeViewServer | None) -> dict[str, Any]:
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
        "readiness": _automation_stopped_readiness(),
    }
    _write_json(path, updated)


def _automation_stopped_readiness() -> dict[str, Any]:
    return {
        "schema": "automa_cli_readiness_v1",
        "status": "ready",
        "ready_for": "inspect stopped deployment",
        "checked_at_ms": _timestamp_ms(),
        "gates": {
            "automation_worker": {"status": "stopped"},
            "perception_view": {"status": "not_current"},
        },
        "blocking_layer": None,
    }


def _log_status_line(process: Any, log_path: Path) -> str:
    record = process if isinstance(process, dict) else {}
    configured_path = record.get("log_path")
    log_to_disk = bool(record.get("log_to_disk")) or isinstance(configured_path, str)
    if not log_to_disk:
        return "Log: disabled; pass --log to persist worker output"
    if isinstance(configured_path, str) and configured_path:
        return f"Log: {configured_path}"
    return f"Log: {display_path(log_path)}"


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
