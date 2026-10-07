"""Local publication monitor for an onboard shared runtime.

Execution belongs to the host behind OnboardRuntimeClient. This module only
persists and displays its publications, in the run record and terminal lines
a locally hosted run writes (``run_record``).
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, TextIO

from autonomy.runtime.session import RunConfiguration
from autonomy.vehicle.vehicle import FRONT_CAMERA_SENSOR_ID
from implementations.runtime.donkeycar.client import OnboardRuntimeClient
from .paths import display_path
from .perception_view import perception_view_ready
from .picar_observation import (
    fetch_autonomy_status, fetch_observation_publication, fetch_observation_frame,
    frame_id_from_headers, publication_to_frame_record, perception_text_from_publication,
)
from .run_record import (
    control_source, finish_run, frame_line, frame_readiness, new_run_state,
    record_host_status, reports_frame, run_result, startup_lines, timestamp_ms,
)
from .runtime_view import RuntimeViewServer
from .decision_live import PicarDecisionViewAdapter
from .staged_bundle import write_json_atomically


def _observation_counts(host: dict[str, Any]) -> tuple[int, int]:
    """The onboard host's cumulative camera and skipped frame counters."""

    components = host.get("components") if isinstance(host.get("components"), dict) else {}
    observation = components.get("observation") if isinstance(components.get("observation"), dict) else {}
    return int(observation.get("camera_frame_count") or 0), int(observation.get("skipped_count") or 0)


def _host_status(base_url: str, timeout_s: float) -> dict[str, Any]:
    autonomy = fetch_autonomy_status(base_url, timeout_s=timeout_s).get("autonomy")
    if not isinstance(autonomy, dict):
        raise ConnectionError("Donkey runtime is up but reports no onboard host")
    return autonomy


def monitor_onboard_runtime(*, vehicle_id: str, base_url: str, automation_dir: Path,
                            perception: dict[str, Any], decision: dict[str, Any],
                            step_activations: dict[str, Path],
                            configuration: RunConfiguration, timeout_s: float,
                            record: bool, verbose: bool, output: TextIO | None) -> tuple[int, str]:
    client = OnboardRuntimeClient(base_url, timeout_s=timeout_s)
    automation_dir.mkdir(parents=True, exist_ok=True)
    state_path = automation_dir / "state.json"
    latest_json_path = automation_dir / "latest_perception.json"
    latest_text_path = automation_dir / "latest_perception.txt"
    server = None
    state = new_run_state(
        vehicle_id=vehicle_id, run_id=None, status="starting", pid=os.getpid(),
        configuration=configuration, record=record,
        control_source=control_source(configuration, onboard=True),
        automation_dir=automation_dir,
        front_camera_path=automation_dir / "latest" / "frames" / f"latest_{FRONT_CAMERA_SENSOR_ID}.jpg",
        run_dir=None, published_view={"status": "starting", "available": False, "url": None},
    )
    state.update(perception=perception, decision=decision)
    try:
        camera_base, skipped_base = _observation_counts(_host_status(base_url, timeout_s))
        response = client.start(configuration)
        run_id = str(response["host_run_id"])
        run_dir = automation_dir / "runs" / run_id if record else None
        frames_dir = (run_dir or automation_dir / "latest") / "frames"
        perception_dir = (run_dir or automation_dir / "latest") / "perception"
        frames_dir.mkdir(parents=True, exist_ok=True)
        server = RuntimeViewServer(
            vehicle_id=vehicle_id, automation_dir=automation_dir,
            run_id=run_id, worker_pid=os.getpid(),
        ).start()
        decision_view = PicarDecisionViewAdapter(
            vehicle_id=vehicle_id, base_url=base_url, view_server=server, timeout_s=timeout_s,
            action_policy=configuration.mode,
        )
        state.update(
            run_id=run_id, status="running", published_view=server.describe(),
            run_dir=display_path(run_dir) if run_dir is not None else None,
        )
        write_json_atomically(state_path, state)
        for line in startup_lines(state):
            if output is not None:
                print(line, file=output, flush=True)
        last_frame_id = None
        frames_seen = 0
        while True:
            runtime = client.status()
            if runtime["host_run_id"] != run_id:
                raise RuntimeError("onboard host restarted during this run")
            session = runtime["session"]
            host = _host_status(base_url, timeout_s)
            camera_count, skipped_count = _observation_counts(host)
            state.update(
                session=session, execution=session["execution"],
                processed_count=session["processed_frames"],
                frames_captured=max(0, camera_count - camera_base),
                skipped_count=max(0, skipped_count - skipped_base),
            )
            record_host_status(state, host, activations=step_activations)
            # Onboard capture has its own cadence; the monitor never executes
            # a decision or chooses a vehicle command.
            publication = fetch_observation_publication(base_url, timeout_s=timeout_s)
            frame = publication_to_frame_record(publication)
            frame_id = frame.get("frame_id")
            if frame_id and frame_id != last_frame_id and session["processed_frames"]:
                jpeg, headers = fetch_observation_frame(base_url, timeout_s=timeout_s)
                if frame_id_from_headers(headers) == frame_id:
                    frame.update(
                        run_id=run_id, steps=state["steps"],
                        action_policy=state["action_policy"],
                        control_source=state["control_source"],
                        control_application=state["control_application"],
                    )
                    try:
                        published = decision_view.refresh()
                        reason = "decision_view_exact_transaction_unavailable"
                    except Exception as exc:  # noqa: BLE001 - publication is observational
                        # Publication availability never grants or revokes authority.
                        server.decision.invalidate_latest()
                        published, reason = False, f"{type(exc).__name__}: {exc}"
                    if not published and state["decision"]["published"]:
                        state["decision"]["latest_frame_publish_skips"] += 1
                        state["decision"]["latest_frame_publish_skip_reason"] = reason
                    # The decision view also publishes its frame; this run's own
                    # perception frame publishes last, as the latest one.
                    frame_path = frames_dir / (
                        f"{frame_id}_{FRONT_CAMERA_SENSOR_ID}.jpg" if record
                        else f"latest_{FRONT_CAMERA_SENSOR_ID}.jpg"
                    )
                    frame_path.write_bytes(jpeg)
                    server.perception.publish_frame(frame_path=frame_path, frame_record=frame)
                    server.perception.publish_perception(frame_record=frame)
                    write_json_atomically(latest_json_path, frame)
                    perception_text = perception_text_from_publication(publication) + "\n"
                    latest_text_path.write_text(perception_text, encoding="utf-8")
                    frame_json_path, frame_text_path = latest_json_path, latest_text_path
                    if run_dir is not None:
                        frame_json_path = perception_dir / frame_id / "perception.json"
                        frame_text_path = frame_json_path.with_name("perception.txt")
                        frame_json_path.parent.mkdir(parents=True, exist_ok=True)
                        write_json_atomically(frame_json_path, frame)
                        frame_text_path.write_text(perception_text, encoding="utf-8")
                    found = frame["perception"] or {}
                    signals, things = len(found.get("signals") or []), len(found.get("things") or [])
                    captured_at_ms = frame.get("captured_at_ms")
                    completed_at_ms = frame.get("perception_completed_at_ms")
                    state["last_capture"] = {
                        "frame_id": frame_id,
                        "frame_index": frame.get("frame_index"),
                        "captured_at_ms": captured_at_ms,
                        "front_camera": display_path(frame_path),
                    }
                    state["last_frame"] = {
                        "frame_id": frame_id,
                        "frame_index": frame.get("frame_index"),
                        "captured_at_ms": captured_at_ms,
                        "perception_completed_at_ms": completed_at_ms,
                        "perception_duration_ms": frame.get("perception_duration_ms"),
                        "capture_to_perception_ms": (
                            completed_at_ms - captured_at_ms
                            if isinstance(completed_at_ms, int) and isinstance(captured_at_ms, int)
                            else None
                        ),
                        "cycle_duration_ms": frame.get("cycle_duration_ms"),
                        "perception_json": display_path(frame_json_path),
                        "perception_text": display_path(frame_text_path),
                        "things": things,
                        "signals": signals,
                        "control": frame.get("control"),
                        "generation_id": frame.get("generation_id"),
                    }
                    last_frame_id = frame_id
                    frames_seen += 1
                    if output is not None and reports_frame(frames_seen, verbose=verbose):
                        action = (frame.get("control") or {}).get("reason")
                        line = frame_line(frame_id, signals=signals, things=things, action=action)
                        print(line, file=output, flush=True)
            state["published_view"] = server.health_payload()
            if last_frame_id is not None:
                state["readiness"] = frame_readiness(perception_view_ready(state["published_view"]))
            state["updated_at_ms"] = timestamp_ms()
            write_json_atomically(state_path, state)
            if session["status"] != "running":
                break
            time.sleep(max(0.02, configuration.interval_s))
    except KeyboardInterrupt:
        stop_error = _stop(client)
        if stop_error is None:
            finish_run(state, server, status="stopped", stop_reason="keyboard_interrupt")
        else:
            finish_run(state, server, status="error", error=stop_error)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        stop_error = _stop(client)
        finish_run(state, server, status="error",
                   error=error if stop_error is None else f"{error}; {stop_error}")
    else:
        # The host ended the run: its stop reason is how the run ended.
        reason = session["status"]
        stop_error = _stop(client)
        if reason == "error":
            finish_run(state, server, status="error", stop_reason=reason,
                       error=host.get("last_error") or "onboard cycle failed")
        elif stop_error is not None:
            finish_run(state, server, status="error", stop_reason=reason, error=stop_error)
        else:
            finish_run(state, server, status="completed" if reason == "completed" else "stopped",
                       stop_reason=reason)
    write_json_atomically(state_path, state)
    return run_result(state, state_path=state_path)


def _stop(client: OnboardRuntimeClient) -> str | None:
    """Stop the onboard run; the reason it could not, if it could not."""

    try:
        client.stop()
    except Exception as exc:  # noqa: BLE001 - recorded as the run's error
        return f"Could not stop onboard runtime: {type(exc).__name__}: {exc}"
    return None
