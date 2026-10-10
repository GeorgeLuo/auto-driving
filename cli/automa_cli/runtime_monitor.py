"""Run and monitor one session on a vehicle's runtime host.

Execution belongs to the host at ``base_url`` (a PiCar's Donkey service or a
local Chase host); both serve ``autonomy.runtime.routes``. This module starts
the session, then persists and displays its publications in the run record
and terminal lines (``run_record``) and the Mac-side runtime view.
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any, TextIO

from autonomy.runtime.session import RunConfiguration
from autonomy.runtime.recording import write_recorded_frame
from autonomy.vehicle.vehicle import FRONT_CAMERA_SENSOR_ID
from autonomy.runtime.client import RuntimeClient
from .paths import display_path
from .perception_view import perception_view_ready
from .host_publications import (
    fetch_observation_publication, fetch_observation_frame,
    frame_id_from_headers, publication_to_frame_record, perception_text_from_publication,
)
from .run_record import (
    control_source, finish_run, frame_line, frame_readiness,
    new_run_state, record_host_status, reports_frame, run_result, startup_lines,
    timestamp_ms,
)
from .runtime_view import RuntimeViewServer
from .decision_live import DecisionViewAdapter
from .staged_bundle import write_json_atomically
from .plugin_catalog import PluginCatalogClient

MONITOR_POLL_INTERVAL_S = 0.1


def _observation_counts(host: dict[str, Any]) -> tuple[int, int]:
    """The host's cumulative camera and skipped frame counters."""

    components = host.get("components") if isinstance(host.get("components"), dict) else {}
    observation = components.get("observation") if isinstance(components.get("observation"), dict) else {}
    counts = tuple(observation.get(key) for key in ("frames_captured", "skipped_count"))
    if any(type(count) is not int or count < 0 for count in counts):
        raise RuntimeError("The runtime host does not report capture and skipped-frame counters; update core and autonomy.")
    return counts


def _host_status(client: RuntimeClient) -> dict[str, Any]:
    autonomy = client.host_status().get("autonomy")
    if not isinstance(autonomy, dict):
        raise RuntimeError(f"{client.base_url} answers but reports no runtime host")
    return autonomy


def monitor_runtime(*, vehicle_id: str, base_url: str, automation_dir: Path,
                            perception: dict[str, Any], decision: dict[str, Any],
                            step_activations: dict[str, Path],
                            configuration: RunConfiguration, timeout_s: float,
                            record: bool, verbose: bool, output: TextIO | None) -> tuple[int, str]:
    client = RuntimeClient(base_url, timeout_s=timeout_s)
    automation_dir.mkdir(parents=True, exist_ok=True)
    state_path = automation_dir / "state.json"
    latest_json_path = automation_dir / "latest_perception.json"
    latest_text_path = automation_dir / "latest_perception.txt"
    server = None
    run_dir = None
    recording_id = None
    state = new_run_state(
        vehicle_id=vehicle_id, run_id=None, status="starting", pid=os.getpid(),
        configuration=configuration, record=record,
        control_source=control_source(configuration),
        automation_dir=automation_dir,
        front_camera_path=automation_dir / "latest" / "frames" / f"latest_{FRONT_CAMERA_SENSOR_ID}.jpg",
        run_dir=None, published_view={"status": "starting", "available": False, "url": None},
    )
    state.update(perception=perception, decision=decision)
    try:
        host = _host_status(client)
        capture_base, skipped_base = _observation_counts(host)
        record_host_status(state, host, activations=step_activations)
        response = client.start(configuration, record=True) if record else client.start(configuration)
        run_id = str(response["host_run_id"])
        recording = response.get("session", {}).get("recording")
        if record and recording is None:
            raise RuntimeError("the runtime host does not support complete recordings; update core and autonomy")
        recording_id = recording["run_id"] if record else None
        run_dir = automation_dir / "runs" / recording_id if record else None
        frames_dir = automation_dir / "latest" / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        server = RuntimeViewServer(
            vehicle_id=vehicle_id, automation_dir=automation_dir,
            run_id=run_id, worker_pid=os.getpid(),
            plugin_catalog=PluginCatalogClient(base_url, timeout_s=timeout_s),
        ).start()
        decision_view = DecisionViewAdapter(
            vehicle_id=vehicle_id, base_url=base_url, view_server=server, timeout_s=timeout_s,
        )
        state.update(
            run_id=run_id, status="running", published_view=server.describe(),
            run_dir=display_path(run_dir) if run_dir is not None else None,
        )
        write_json_atomically(state_path, state)
        for line in startup_lines(state):
            if output is not None:
                print(line, file=output, flush=True)
        last_capture = None
        frames_seen = 0
        while True:
            runtime = client.status()
            if runtime["host_run_id"] != run_id:
                raise RuntimeError("the runtime host restarted during this run")
            session = runtime["session"]
            host = _host_status(client)
            capture_count, skipped_count = _observation_counts(host)
            state.update(
                session=session, execution=session["execution"],
                processed_count=session["processed_decisions"],
                frames_captured=max(0, capture_count - capture_base),
                skipped_count=max(0, skipped_count - skipped_base),
            )
            record_host_status(state, host, activations=step_activations)
            _sync_recording(client, state, run_dir=run_dir, recording_id=recording_id)
            # A terminal session is drained before manual observations can replace
            # its final decision in the live latest-only publication.
            if run_dir is not None and session["status"] != "running":
                break
            # The host captures at its own cadence; the monitor never executes
            # a decision or chooses a vehicle command.
            publication = fetch_observation_publication(base_url, timeout_s=timeout_s)
            frame = publication_to_frame_record(publication)
            frame_id = frame.get("frame_id")
            # A paused simulator repeats its frame index, so a new cycle is a new capture.
            capture = (frame_id, frame.get("captured_at_ms"))
            if frame_id and capture != last_capture and session["processed_decisions"]:
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
                    frame_path = frames_dir / f"latest_{FRONT_CAMERA_SENSOR_ID}.jpg"
                    frame_path.write_bytes(jpeg)
                    server.perception.publish_frame(frame_path=frame_path, frame_record=frame)
                    server.perception.publish_perception(frame_record=frame)
                    write_json_atomically(latest_json_path, frame)
                    perception_text = perception_text_from_publication(publication) + "\n"
                    latest_text_path.write_text(perception_text, encoding="utf-8")
                    frame_json_path, frame_text_path = latest_json_path, latest_text_path
                    captured_at_ms = frame.get("captured_at_ms")
                    found = frame["perception"] or {}
                    signals, things = len(found.get("signals") or []), len(found.get("things") or [])
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
                        "skipped_since_previous": frame.get("skipped_since_previous"),
                        "generation_id": frame.get("generation_id"),
                    }
                    last_capture = capture
                    frames_seen += 1
                    if output is not None and reports_frame(frames_seen, verbose=verbose):
                        action = (frame.get("control") or {}).get("reason")
                        line = frame_line(
                            frame_id, signals=signals, things=things, action=action,
                            skipped_since_previous=frame.get("skipped_since_previous"),
                        )
                        print(line, file=output, flush=True)
            state["published_view"] = server.health_payload()
            if last_capture is not None:
                state["readiness"] = frame_readiness(perception_view_ready(state["published_view"]))
            state["updated_at_ms"] = timestamp_ms()
            write_json_atomically(state_path, state)
            if session["status"] != "running":
                break
            time.sleep(MONITOR_POLL_INTERVAL_S)
    except KeyboardInterrupt:
        stop_error = _stop(client, state, run_dir=run_dir, recording_id=recording_id)
        if stop_error is None:
            finish_run(state, server, status="stopped", stop_reason="keyboard_interrupt")
        else:
            finish_run(state, server, status="error", error=stop_error)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        stop_error = _stop(client, state, run_dir=run_dir, recording_id=recording_id)
        finish_run(state, server, status="error",
                   error=error if stop_error is None else f"{error}; {stop_error}")
    else:
        # The host ended the run: its stop reason is how the run ended.
        reason = session["status"]
        stop_error = _stop(client, state, run_dir=run_dir, recording_id=recording_id)
        if reason == "error":
            finish_run(state, server, status="error", stop_reason=reason,
                       error=host.get("last_error") or "the runtime cycle failed")
        elif stop_error is not None:
            finish_run(state, server, status="error", stop_reason=reason, error=stop_error)
        else:
            finish_run(state, server, status="completed" if reason == "completed" else "stopped",
                       stop_reason=reason)
    write_json_atomically(state_path, state)
    return run_result(state, state_path=state_path)


def _sync_recording(client: RuntimeClient, state: dict[str, Any], *,
                    run_dir: Path | None, recording_id: str | None) -> None:
    if run_dir is None:
        return
    while True:
        batch = client.read_recording(recording_id, after=state["recorded_count"])
        if batch["run_id"] != recording_id or batch["after"] != state["recorded_count"]:
            raise RuntimeError("the host recording identity or cursor changed")
        for item in batch["frames"]:
            frame = item["frame"]
            if frame["run_id"] != recording_id or frame["vehicle_id"] != state["vehicle_id"]:
                raise RuntimeError("a host recording frame belongs to another run or vehicle")
            write_recorded_frame(
                run_dir, frame, base64.b64decode(item["image_base64"], validate=True),
                item["image_extension"],
            )
            state["recorded_count"] += 1
        if state["recorded_count"] == batch["recorded_count"]:
            return
        if not batch["frames"]:
            raise RuntimeError("the host recording history is incomplete")


def _stop(client: RuntimeClient, state: dict[str, Any], *,
          run_dir: Path | None, recording_id: str | None) -> str | None:
    """Release control first, then drain every committed cycle of this run."""
    try:
        session = client.stop()["session"]
        state["execution"] = session["execution"]
        if run_dir is not None:
            state["processed_count"] = session["processed_decisions"]
        _sync_recording(client, state, run_dir=run_dir, recording_id=recording_id)
    except Exception as exc:  # noqa: BLE001 - recorded as the run's error
        return f"Could not stop the run or finish its recording: {type(exc).__name__}: {exc}"
    return None
