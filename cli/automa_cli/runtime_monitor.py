"""Run and monitor one session on a vehicle's runtime host.

Execution belongs to the host at ``base_url`` (a PiCar's Donkey service or a
local Chase host); both serve ``autonomy.runtime.routes``. This module starts
the session, then persists and displays its publications in the run record
and terminal lines (``run_record``) and the Mac-side runtime view.
"""
from __future__ import annotations

import base64
import os
import threading
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
    fetch_observation_sample, TransactionCache, publication_to_frame_record, perception_text_from_publication,
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
PERCEPTION_COPY_INTERVAL_S = 1 / 30
# The decision copy stays off the recording export. A batch can take longer
# than the decision stays fresh, so the copy cannot wait for that read.
DECISION_COPY_INTERVAL_S = 1 / 30
MAX_POLL_GAP_S = 0.1


def _pause_s(started: float, interval_s: float) -> float:
    """Target the interval on fast hosts; back off after slower passes.

    A 30 ms pass waits 30 ms (~16.7 Hz); a 200 ms pass waits 100 ms.
    """
    elapsed = max(0.0, time.monotonic() - started)
    return max(interval_s - elapsed, min(elapsed, MAX_POLL_GAP_S))


def _frames_received(host: dict[str, Any]) -> int:
    """The host's cumulative camera capture counter."""

    components = host.get("components") if isinstance(host.get("components"), dict) else {}
    observation = components.get("observation") if isinstance(components.get("observation"), dict) else {}
    count = observation.get("frames_received")
    if type(count) is not int or count < 0:
        raise RuntimeError("The runtime host does not report a received-frame counter; update core and autonomy.")
    return count


def _frames_dropped(host: dict[str, Any]) -> int:
    components = host.get("components") if isinstance(host.get("components"), dict) else {}
    observation = components.get("observation") if isinstance(components.get("observation"), dict) else {}
    count = observation.get("frames_dropped")
    if type(count) is not int or count < 0:
        raise RuntimeError("The runtime host does not report a dropped-frame counter; update core and autonomy.")
    return count


def _host_status(client: RuntimeClient) -> dict[str, Any]:
    autonomy = client.host_status().get("autonomy")
    if not isinstance(autonomy, dict):
        raise RuntimeError(f"{client.base_url} answers but reports no runtime host")
    return autonomy


def _copy_latest_decision(
    decision_view: DecisionViewAdapter,
    server: RuntimeViewServer,
    stop: threading.Event,
    copied: dict[str, tuple[bool, str] | None],
) -> None:
    """Publish the host's latest decision until the run loop asks it to stop.

    This thread never reads the recording. The poll can spend a long time on
    one export batch without pushing the copied decision past its fresh window.
    """

    while not stop.is_set():
        started = time.monotonic()
        try:
            published = decision_view.refresh()
            reason = "decision_view_exact_transaction_unavailable"
        except Exception as exc:  # noqa: BLE001 - publication is observational
            # Publication availability never grants or revokes authority.
            server.decision.invalidate_latest()
            published, reason = False, f"{type(exc).__name__}: {exc}"
        copied["result"] = (published, reason)
        stop.wait(_pause_s(started, DECISION_COPY_INTERVAL_S))


def _copy_latest_perception(
    base_url: str, server: RuntimeViewServer, stop: threading.Event,
    copied: dict[str, Any], *, frames_dir: Path, json_path: Path, text_path: Path,
    identity: dict[str, Any], timeout_s: float,
) -> None:
    """Perception never waits for the monitor's recording export."""
    cache = TransactionCache()
    last_capture = None
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                publication, jpeg = fetch_observation_sample(base_url, timeout_s=timeout_s, cache=cache)
                if stop.is_set():
                    break
                frame = publication_to_frame_record(publication)
                capture = (frame.get("frame_id"), frame.get("captured_at_ms"),
                           frame.get("perception_completed_at_ms"), frame.get("generation_id"))
                if jpeg is None:
                    server.perception.invalidate_latest()
                    last_capture = None
                    copied["error"] = publication.get("error") or publication.get("health")
                elif capture != last_capture and frame.get("frame_id"):
                    frame.update(identity)
                    frame_path = frames_dir / f"latest_{FRONT_CAMERA_SENSOR_ID}.jpg"
                    frame_path.write_bytes(jpeg)
                    server.perception.publish_matched(frame_path=frame_path, frame_record=frame)
                    write_json_atomically(json_path, frame)
                    text_path.write_text(perception_text_from_publication(publication) + "\n", encoding="utf-8")
                    last_capture = capture
                    copied["frame"] = frame
                    copied["error"] = None
            except Exception as exc:
                server.perception.invalidate_latest()
                last_capture = None
                copied["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                copied["ready"].set()
            stop.wait(_pause_s(started, PERCEPTION_COPY_INTERVAL_S))
    finally:
        copied["ready"].set()


def _join_decision_copy(copier: threading.Thread | None, stop: threading.Event) -> None:
    """Stop the decision copy before the view server is torn down."""

    if copier is None:
        return
    stop.set()
    # refresh() retries a skewed publication for at most a second.
    copier.join(timeout=1.0)


def monitor_runtime(*, vehicle_id: str, base_url: str, automation_dir: Path,
                            selections: dict[str, dict[str, Any]], decision: dict[str, Any],
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
    copier = None
    perception_copier = None
    perception_copy: dict[str, Any] = {"frame": None, "error": None, "ready": threading.Event()}
    stop_copy = threading.Event()
    copied: dict[str, tuple[bool, str] | None] = {"result": None}
    state = new_run_state(
        vehicle_id=vehicle_id, run_id=None, status="starting", pid=os.getpid(),
        configuration=configuration, record=record,
        control_source=control_source(configuration),
        automation_dir=automation_dir,
        front_camera_path=automation_dir / "latest" / "frames" / f"latest_{FRONT_CAMERA_SENSOR_ID}.jpg",
        run_dir=None, published_view={"status": "starting", "available": False, "url": None},
    )
    state.update(selections=selections, decision=decision)
    try:
        host = _host_status(client)
        capture_base = _frames_received(host)
        dropped_base = _frames_dropped(host)
        record_host_status(state, host)
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
        worker = threading.Thread(
            target=_copy_latest_decision,
            args=(decision_view, server, stop_copy, copied),
            name="automa-decision-copy",
            daemon=True,
        )
        worker.start()
        copier = worker
        perception_copier = threading.Thread(
            target=_copy_latest_perception, args=(base_url, server, stop_copy, perception_copy),
            kwargs={"frames_dir": frames_dir, "json_path": latest_json_path, "text_path": latest_text_path,
                    "timeout_s": timeout_s,
                    "identity": {key: state[key] for key in ("run_id", "steps", "action_policy", "control_source", "control_application")}},
            name="automa-perception-copy", daemon=True,
        )
        perception_copier.start()
        perception_copy["ready"].wait(min(1.0, timeout_s))
        last_capture = None
        frames_seen = 0
        while True:
            poll_started = time.monotonic()
            runtime = client.status()
            if runtime["host_run_id"] != run_id:
                raise RuntimeError("the runtime host restarted during this run")
            session = runtime["session"]
            host = _host_status(client)
            dropped = _frames_dropped(host)
            state.update(
                frames_dropped=max(0, dropped - dropped_base),
                session=session, execution=session["execution"],
                processed_count=session["processed_decisions"],
                frames_received=max(0, _frames_received(host) - capture_base),
            )
            record_host_status(state, host)
            if session["status"] in {"starting", "stopping"}:
                state["updated_at_ms"] = timestamp_ms()
                write_json_atomically(state_path, state)
                time.sleep(_pause_s(poll_started, MONITOR_POLL_INTERVAL_S))
                continue
            if session["status"] != "running":
                perception_copy["ready"].clear()
                perception_copy["ready"].wait(min(timeout_s, 0.5))
            # A terminal session is drained before manual observations can replace
            # its final decision in the live latest-only publication.
            if run_dir is not None and session["status"] != "running":
                break
            # Both view copiers run independently of this status/export pass.
            frame = perception_copy["frame"]
            frame_id = frame.get("frame_id") if frame else None
            capture = (frame_id, frame.get("captured_at_ms")) if frame else None
            if frame_id and capture != last_capture:
                result = copied["result"]
                if result is not None and not result[0] and state["decision"]["published"]:
                    state["decision"]["latest_frame_publish_skips"] += 1
                    state["decision"]["latest_frame_publish_skip_reason"] = result[1]
                captured_at_ms = frame.get("captured_at_ms")
                found = frame["perception"] or {}
                signals, things = len(found.get("signals") or []), len(found.get("things") or [])
                completed_at_ms = frame.get("perception_completed_at_ms")
                state["last_capture"] = {
                    "frame_id": frame_id,
                    "frame_index": frame.get("frame_index"),
                    "captured_at_ms": captured_at_ms,
                    "front_camera": display_path(frames_dir / f"latest_{FRONT_CAMERA_SENSOR_ID}.jpg"),
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
                    "perception_json": display_path(latest_json_path),
                    "perception_text": display_path(latest_text_path),
                    "things": things,
                    "signals": signals,
                    "control": frame.get("control"),
                    "generation_id": frame.get("generation_id"),
                }
                last_capture = capture
                frames_seen += 1
                if output is not None and reports_frame(frames_seen, verbose=verbose):
                    action = (frame.get("control") or {}).get("reason")
                    line = frame_line(frame_id, signals=signals, things=things, action=action)
                    print(line, file=output, flush=True)
            # One batch keeps the export near the capture rate. The stop path
            # drains whatever remains, and the decision copy does not wait here.
            if run_dir is not None:
                _sync_recording(
                    client, state, run_dir=run_dir, recording_id=recording_id, max_batches=1,
                )
            state["published_view"] = server.health_payload()
            if last_capture is not None:
                state["readiness"] = frame_readiness(perception_view_ready(state["published_view"]))
            state["updated_at_ms"] = timestamp_ms()
            write_json_atomically(state_path, state)
            if session["status"] != "running":
                break
            time.sleep(_pause_s(poll_started, MONITOR_POLL_INTERVAL_S))
    except KeyboardInterrupt:
        stop_error = _stop(client, state, run_dir=run_dir, recording_id=recording_id)
        _join_decision_copy(copier, stop_copy)
        _join_decision_copy(perception_copier, stop_copy)
        if stop_error is None:
            finish_run(state, server, status="stopped", stop_reason="keyboard_interrupt")
        else:
            finish_run(state, server, status="error", error=stop_error)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        stop_error = _stop(client, state, run_dir=run_dir, recording_id=recording_id)
        _join_decision_copy(copier, stop_copy)
        _join_decision_copy(perception_copier, stop_copy)
        finish_run(state, server, status="error",
                   error=error if stop_error is None else f"{error}; {stop_error}")
    else:
        # The host ended the run: its stop reason is how the run ended.
        reason = session["status"]
        stop_error = _stop(client, state, run_dir=run_dir, recording_id=recording_id)
        _join_decision_copy(copier, stop_copy)
        _join_decision_copy(perception_copier, stop_copy)
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
                    run_dir: Path | None, recording_id: str | None,
                    max_batches: int | None = None) -> None:
    """Copy committed cycles out of the host.

    ``max_batches`` lets a poll export one batch and write the run state.
    The stop path omits it and drains whatever the polls have not copied.
    """

    if run_dir is None:
        return
    batches = 0
    while max_batches is None or batches < max_batches:
        batch = client.read_recording(recording_id, after=state["recorded_count"])
        if batch["run_id"] != recording_id or batch["after"] != state["recorded_count"]:
            raise RuntimeError("the host recording identity or cursor changed")
        sources = {
            name: base64.b64decode(source, validate=True) for name, source in batch["sources"].items()
        }
        for item in batch["frames"]:
            frame = item["frame"]
            if frame["run_id"] != recording_id or frame["vehicle_id"] != state["vehicle_id"]:
                raise RuntimeError("a host recording frame belongs to another run or vehicle")
            write_recorded_frame(
                run_dir, frame, base64.b64decode(item["image_base64"], validate=True),
                item["image_extension"], sources,
            )
            state["recorded_count"] += 1
        batches += 1
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
