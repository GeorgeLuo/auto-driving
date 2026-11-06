"""Run and monitor one session on a runtime host.

Execution belongs to the host at ``base_url`` (a PiCar's Donkey service or a
local Chase host); both serve ``autonomy.runtime.routes``. This module starts
the session, persists its publications in the run record (``store``), and
reports changes through an optional data-event consumer. The Mac-side runtime
view stays here because the monitor publishes it.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from autonomy.runtime.session import RunConfiguration
from autonomy.runtime.recording import write_recorded_frame
from autonomy.vehicle.vehicle import FRONT_CAMERA_SENSOR_ID
from autonomy.runtime.client import RuntimeClient
from ...shared.paths import display_path
from ..publications.decision_publication import (
    perception_duration_ms,
    perception_text_from_publication,
    publication_capture,
    publication_part,
)
from ..publications.host_publications import fetch_observation_sample, observation_component, TransactionCache
from ...shared.timestamps import timestamp_ms
from .models import RunEvent, RunResult, run_exit_code
from collections.abc import Callable
from copy import deepcopy
from .store import (
    CONTROL_SOURCE, finish_run as record_completion, new_run_state,
    record_host_status, session_readiness,
)
from ...frontend.server import RuntimeViewServer
from ..views.runtime_view import runtime_view_server
from ..views.polling import PERCEPTION_COPY_INTERVAL_S, pause_s
from ..views.decision_live import DecisionViewAdapter
from ..views.observation import ObservationPublisher
from ...shared.io.json_files import write_json_atomically
from ..publications.plugin_catalog import PluginCatalogClient

MONITOR_POLL_INTERVAL_S = 0.1
# The decision copy stays off the recording export. A batch can take longer
# than the decision stays fresh, so the copy cannot wait for that read.
DECISION_COPY_INTERVAL_S = 1 / 30


def _frame_counter(host: dict[str, Any], name: str) -> int:
    """The host's cumulative ``frames_received`` or ``frames_dropped`` camera counter."""

    count = observation_component(host).get(name)
    if type(count) is not int or count < 0:
        raise RuntimeError(f"The runtime host does not report its {name} counter; update the host.")
    return count


def _host_status(client: RuntimeClient) -> dict[str, Any]:
    autonomy = client.host_status().get("host")
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
        stop.wait(pause_s(started, DECISION_COPY_INTERVAL_S))


def _copy_latest_perception(
    base_url: str, server: RuntimeViewServer, stop: threading.Event,
    copied: dict[str, Any], *, front_camera_path: Path, json_path: Path, text_path: Path,
    identity: dict[str, Any], timeout_s: float,
) -> None:
    """Perception never waits for the monitor's recording export."""
    cache = TransactionCache()
    publisher = ObservationPublisher(server.perception)
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                publication, jpeg = fetch_observation_sample(base_url, timeout_s=timeout_s, cache=cache)
                if stop.is_set():
                    break
                record = {**publication, **identity}
                if jpeg is None:
                    publisher.invalidate()
                elif publisher.publish(record, jpeg):
                    # The run's latest files follow the view, which never reads them.
                    front_camera_path.write_bytes(jpeg)
                    write_json_atomically(json_path, record)
                    text_path.write_text(perception_text_from_publication(publication) + "\n", encoding="utf-8")
                    copied["publication"] = publication
            except Exception as exc:  # noqa: BLE001 - the next poll retries this capture
                publisher.invalidate(f"{type(exc).__name__}: {exc}")
            finally:
                copied["ready"].set()
            stop.wait(pause_s(started, PERCEPTION_COPY_INTERVAL_S))
    finally:
        copied["ready"].set()


def _view_unavailable(reason: str, *, url: str | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "available": False,
        "url": url,
        "reason": reason,
    }


def _release_partial_view(
    server: RuntimeViewServer | None,
    copier: threading.Thread | None,
    perception_copier: threading.Thread | None,
    stop_copy: threading.Event,
    exc: BaseException,
) -> dict[str, Any]:
    """Stop a view that failed while starting. This never stops the runtime."""

    reasons = [f"{type(exc).__name__}: {exc}"]
    stop_copy.set()
    for thread in (copier, perception_copier):
        if thread is None or not thread.is_alive():
            continue
        try:
            thread.join(timeout=1.0)
        except Exception as cleanup:  # noqa: BLE001 - cleanup stays on the view record
            reasons.append(f"{type(cleanup).__name__}: {cleanup}")
    if server is not None:
        try:
            server.stop()
        except Exception as cleanup:  # noqa: BLE001 - cleanup stays on the view record
            reasons.append(f"{type(cleanup).__name__}: {cleanup}")
    return _view_unavailable("; ".join(reasons))


def _read_view_health(server: RuntimeViewServer | None) -> dict[str, Any] | None:
    if server is None:
        return None
    try:
        return server.health_payload()
    except Exception as exc:  # noqa: BLE001 - view health is not the run
        return _view_unavailable(f"{type(exc).__name__}: {exc}", url=server.url)


def _join_decision_copy(copier: threading.Thread | None, stop: threading.Event) -> None:
    """Stop the decision copy before the view server is torn down."""

    if copier is None:
        return
    stop.set()
    # refresh() retries a skewed publication for at most a second.
    copier.join(timeout=1.0)


def monitor_runtime(*, host_name: str, base_url: str, automation_dir: Path,
                            selections: dict[str, dict[str, Any]], decision: dict[str, Any],
                            configuration: RunConfiguration, timeout_s: float,
                            record: bool, verbose: bool, emit: Callable[[RunEvent], None] | None = None) -> RunResult:
    client = RuntimeClient(base_url, timeout_s=timeout_s)
    automation_dir.mkdir(parents=True, exist_ok=True)
    state_path = automation_dir / "state.json"
    latest_json_path = automation_dir / "latest_perception.json"
    latest_text_path = automation_dir / "latest_perception.txt"
    front_camera_path = automation_dir / "latest" / "frames" / f"latest_{FRONT_CAMERA_SENSOR_ID}.jpg"
    server = None
    run_dir = None
    recording_id = None
    copier = None
    perception_copier = None
    perception_copy: dict[str, Any] = {"publication": None, "ready": threading.Event()}
    stop_copy = threading.Event()
    copied: dict[str, tuple[bool, str] | None] = {"result": None}
    state = new_run_state(
        host_name=host_name, run_id=None, status="starting", pid=os.getpid(),
        configuration=configuration, record=record,
        control_source=CONTROL_SOURCE,
        automation_dir=automation_dir,
        front_camera_path=front_camera_path,
        run_dir=None, published_view={"status": "starting", "available": False, "url": None},
    )
    state.update(selections=selections, decision=decision)
    try:
        host = _host_status(client)
        capture_base = _frame_counter(host, "frames_received")
        dropped_base = _frame_counter(host, "frames_dropped")
        record_host_status(state, host)
        response = client.start(configuration, record=True) if record else client.start(configuration)
        run_id = str(response["host_run_id"])
        accepted = response.get("session")
        if not isinstance(accepted, dict):
            accepted = None
        recording = accepted.get("recording") if accepted is not None else None
        if record and not isinstance(recording, dict):
            raise RuntimeError("the runtime host does not support complete recordings; update the host")
        recording_id = recording["run_id"] if record else None
        run_dir = automation_dir / "runs" / recording_id if record else None
        front_camera_path.parent.mkdir(parents=True, exist_ok=True)
        # The parent reads this copy. It is written before the view exists.
        state.update(
            run_id=run_id,
            pid=os.getpid(),
            readiness=session_readiness(accepted),
            run_dir=display_path(run_dir) if run_dir is not None else None,
        )
        if accepted is not None:
            state["session"] = accepted
            if isinstance(accepted.get("status"), str):
                state["status"] = accepted["status"]
            if isinstance(accepted.get("processed_decisions"), int):
                state["processed_count"] = accepted["processed_decisions"]
            if isinstance(accepted.get("execution"), dict):
                state["execution"] = accepted["execution"]
        write_json_atomically(state_path, state)
        try:
            server = runtime_view_server(
                host_name=host_name, automation_dir=automation_dir,
                run_id=run_id, worker_pid=os.getpid(),
                plugin_catalog=PluginCatalogClient(base_url, timeout_s=timeout_s),
            )
            server.start()
            decision_view = DecisionViewAdapter(
                host_name=host_name, base_url=base_url, view_server=server, timeout_s=timeout_s,
            )
            copier = threading.Thread(
                target=_copy_latest_decision,
                args=(decision_view, server, stop_copy, copied),
                name="automa-decision-copy",
                daemon=True,
            )
            copier.start()
            perception_copier = threading.Thread(
                target=_copy_latest_perception, args=(base_url, server, stop_copy, perception_copy),
                kwargs={"front_camera_path": front_camera_path, "json_path": latest_json_path,
                        "text_path": latest_text_path, "timeout_s": timeout_s,
                        "identity": {key: state[key] for key in ("run_id", "steps", "action_policy", "control_source", "control_application")}},
                name="automa-perception-copy", daemon=True,
            )
            perception_copier.start()
            perception_copy["ready"].wait(min(1.0, timeout_s))
            state["published_view"] = server.describe()
        except Exception as exc:  # noqa: BLE001 - a view failure leaves the accepted session running
            state["published_view"] = _release_partial_view(
                server, copier, perception_copier, stop_copy, exc,
            )
            server = None
            copier = None
            perception_copier = None
        write_json_atomically(state_path, state)
        if emit is not None:
            emit(RunEvent("startup", deepcopy(state)))
        last_capture = None
        frames_seen = 0
        while True:
            poll_started = time.monotonic()
            runtime = client.status()
            if runtime["host_run_id"] != run_id:
                raise RuntimeError("the runtime host restarted during this run")
            session = runtime["session"]
            host = _host_status(client)
            state.update(
                frames_dropped=max(0, _frame_counter(host, "frames_dropped") - dropped_base),
                session=session, execution=session["execution"],
                processed_count=session["processed_decisions"],
                frames_received=max(0, _frame_counter(host, "frames_received") - capture_base),
                readiness=session_readiness(session),
            )
            record_host_status(state, host)
            if session["status"] in {"starting", "stopping"}:
                state["updated_at_ms"] = timestamp_ms()
                write_json_atomically(state_path, state)
                time.sleep(pause_s(poll_started, MONITOR_POLL_INTERVAL_S))
                continue
            if session["status"] != "running" and perception_copier is not None:
                perception_copy["ready"].clear()
                perception_copy["ready"].wait(min(timeout_s, 0.5))
            # A terminal session is drained before manual observations can replace
            # its final decision in the live latest-only publication.
            if run_dir is not None and session["status"] != "running":
                break
            # Both view copiers run independently of this status/export pass.
            try:
                publication = perception_copy["publication"]
                capture = None if publication is None else publication_capture(publication)
            except Exception as exc:  # noqa: BLE001 - publication is not the run
                state["published_view"] = _view_unavailable(f"{type(exc).__name__}: {exc}")
                capture = last_capture
            else:
                health = _read_view_health(server)
                if health is not None:
                    state["published_view"] = health
            try:
                if capture != last_capture:
                    frame = publication_part(publication, "frame")
                    frame_id = frame.get("frame_id")
                    result = copied["result"]
                    if result is not None and not result[0] and state["decision"]["published"]:
                        state["decision"]["latest_frame_publish_skips"] += 1
                        state["decision"]["latest_frame_publish_skip_reason"] = result[1]
                    captured_at_ms = frame.get("captured_at_ms")
                    found = publication_part(publication, "perception")
                    signals, things = len(found.get("signals") or []), len(found.get("things") or [])
                    completed_at_ms = frame.get("completed_at_ms")
                    timing = publication_part(publication, "timing")
                    state["last_capture"] = {
                        "frame_id": frame_id,
                        "frame_index": frame.get("frame_index"),
                        "captured_at_ms": captured_at_ms,
                        "front_camera": display_path(front_camera_path),
                    }
                    state["last_frame"] = {
                        "frame_id": frame_id,
                        "frame_index": frame.get("frame_index"),
                        "captured_at_ms": captured_at_ms,
                        "perception_completed_at_ms": completed_at_ms,
                        "perception_duration_ms": perception_duration_ms(publication),
                        "capture_to_perception_ms": (
                            completed_at_ms - captured_at_ms
                            if isinstance(completed_at_ms, int) and isinstance(captured_at_ms, int)
                            else None
                        ),
                        "cycle_duration_ms": timing.get("duration_ms", publication.get("duration_ms")),
                        "perception_json": display_path(latest_json_path),
                        "perception_text": display_path(latest_text_path),
                        "things": things,
                        "signals": signals,
                        "control": publication.get("control"),
                        "generation_id": publication.get("generation_id"),
                    }
                    last_capture = capture
                    frames_seen += 1
                    action = publication_part(publication, "control").get("reason")
                    if emit is not None:
                        emit(RunEvent("frame", dict(frame_id=frame_id, signals=signals,
                                                   things=things, action=action, count=frames_seen)))
            except Exception as exc:  # noqa: BLE001 - publication is not the run
                state["published_view"] = _view_unavailable(f"{type(exc).__name__}: {exc}")
            # One batch keeps the export near the capture rate. The stop path
            # drains whatever remains, and the decision copy does not wait here.
            if run_dir is not None:
                _sync_recording(
                    client, state, run_dir=run_dir, recording_id=recording_id, max_batches=1,
                )
            state["updated_at_ms"] = timestamp_ms()
            write_json_atomically(state_path, state)
            if session["status"] != "running":
                break
            time.sleep(pause_s(poll_started, MONITOR_POLL_INTERVAL_S))
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
    return RunResult(run_exit_code(state), state=state, state_path=state_path)


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
            if frame["run_id"] != recording_id or frame["host_name"] != state["host_name"]:
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
        previous = state.get("session") or {}
        # Cleanup releases control without erasing an observed terminal outcome.
        if previous.get("status") not in {"completed", "error"}:
            state["session"] = session
        state["execution"] = session["execution"]
        if run_dir is not None:
            state["processed_count"] = session["processed_decisions"]
        _sync_recording(client, state, run_dir=run_dir, recording_id=recording_id)
    except Exception as exc:  # noqa: BLE001 - recorded as the run's error
        return f"Could not stop the run or finish its recording: {type(exc).__name__}: {exc}"
    return None


def stop_view(
    view_server: RuntimeViewServer | None,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stop the run's view and return its last published record."""

    if view_server is None:
        if isinstance(current, dict) and current.get("reason"):
            return {**current, "available": False}
        return _view_unavailable("perception view did not start")
    try:
        view_server.stop()
    except OSError as exc:
        return {
            **view_server.describe(status="error"),
            "reason": f"{type(exc).__name__}: {exc}",
        }
    try:
        record = json.loads(view_server.record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        record = None
    return record if isinstance(record, dict) else view_server.describe(status="stopped")


def finish_run(state: dict[str, Any], view_server: RuntimeViewServer | None, **outcome: Any) -> None:
    current = state.get("published_view")
    record_completion(
        state,
        stop_view(view_server, current if isinstance(current, dict) else None),
        **outcome,
    )
