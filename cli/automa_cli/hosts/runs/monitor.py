"""Run and monitor one session on a runtime host.

Execution belongs to the host at ``base_url`` (a PiCar's Donkey service or a
local Chase host); both serve ``autonomy.runtime.routes``. This module starts
the session, copies its status and recording into the run record (``store``),
and passes the accepted run's state to an optional ``emit`` callback. Live
pages belong to ``hosts viewer`` and ``plugins stream``; this monitor serves
none.
"""
from __future__ import annotations

import base64
import os
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from autonomy.runtime.client import RuntimeClient
from autonomy.runtime.recording import write_recorded_frame
from autonomy.runtime.session import RunConfiguration

from ...shared.io.json_files import write_json_atomically
from ...shared.paths import display_path
from ...shared.timestamps import timestamp_ms
from ..publications.host_publications import observation_component
from ..views.polling import pause_s
from .models import RunResult, run_exit_code
from .store import CONTROL_SOURCE, finish_run, new_run_state, record_host_status

MONITOR_POLL_INTERVAL_S = 0.1


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


def _copy_session(state: dict[str, Any], session: dict[str, Any]) -> None:
    """Copy the host's runtime session; ``hosts run`` waits for this copy to say running."""

    state.update(session=session, execution=session["execution"], processed_count=session["processed_decisions"])


def monitor_runtime(*, host_name: str, base_url: str, automation_dir: Path,
                    selections: dict[str, dict[str, Any]], decision: dict[str, Any],
                    configuration: RunConfiguration, timeout_s: float,
                    record: bool, emit: Callable[[dict[str, Any]], None] | None = None) -> RunResult:
    client = RuntimeClient(base_url, timeout_s=timeout_s)
    automation_dir.mkdir(parents=True, exist_ok=True)
    state_path = automation_dir / "state.json"
    run_dir = None
    recording_id = None
    host: dict[str, Any] = {}
    state = new_run_state(
        host_name=host_name, run_id=None, status="starting", pid=os.getpid(),
        configuration=configuration, record=record, control_source=CONTROL_SOURCE, run_dir=None,
    )
    state.update(selections=selections, decision=decision)
    try:
        host = _host_status(client)
        capture_base = _frame_counter(host, "frames_received")
        dropped_base = _frame_counter(host, "frames_dropped")
        record_host_status(state, host)
        response = client.start(configuration, record=True) if record else client.start(configuration)
        run_id = str(response["host_run_id"])
        accepted = response["session"]
        recording = accepted.get("recording")
        if record and not isinstance(recording, dict):
            raise RuntimeError("the runtime host does not support complete recordings; update the host")
        recording_id = recording["run_id"] if record else None
        run_dir = automation_dir / "runs" / recording_id if record else None
        state.update(run_id=run_id, status="running", run_dir=display_path(run_dir) if run_dir is not None else None)
        _copy_session(state, accepted)
        write_json_atomically(state_path, state)
        if emit is not None:
            emit(deepcopy(state))
        while True:
            poll_started = time.monotonic()
            runtime = client.status()
            if runtime["host_run_id"] != run_id:
                raise RuntimeError("the runtime host restarted during this run")
            session = runtime["session"]
            host = _host_status(client)
            _copy_session(state, session)
            state.update(
                frames_dropped=max(0, _frame_counter(host, "frames_dropped") - dropped_base),
                frames_received=max(0, _frame_counter(host, "frames_received") - capture_base),
            )
            record_host_status(state, host)
            settled = session["status"] not in {"starting", "stopping"}
            # One batch keeps the export near the capture rate; the stop path drains the rest.
            if settled and run_dir is not None:
                _sync_recording(client, state, run_dir=run_dir, recording_id=recording_id, max_batches=1)
            state["updated_at_ms"] = timestamp_ms()
            write_json_atomically(state_path, state)
            if settled and session["status"] != "running":
                break
            time.sleep(pause_s(poll_started, MONITOR_POLL_INTERVAL_S))
    except KeyboardInterrupt:
        outcome: dict[str, Any] = {"status": "stopped", "stop_reason": "keyboard_interrupt"}
    except Exception as exc:  # noqa: BLE001 - recorded as the run's error
        outcome = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    else:
        # The host ended the run: its stop reason is how the run ended.
        reason = session["status"]
        outcome = {"status": reason if reason in {"completed", "error"} else "stopped", "stop_reason": reason}
        if reason == "error":
            outcome["error"] = host.get("last_error") or "the runtime cycle failed"
    # Every ending releases control and drains the recording once, then finishes the record once.
    stop_error = _stop(client, state, run_dir=run_dir, recording_id=recording_id)
    if stop_error is not None:
        outcome["error"] = "; ".join(filter(None, (outcome.get("error"), stop_error)))
        outcome["status"] = "error"
    finish_run(state, **outcome)
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
        state["execution"] = session["execution"]
        if run_dir is not None:
            state["processed_count"] = session["processed_decisions"]
        _sync_recording(client, state, run_dir=run_dir, recording_id=recording_id)
    except Exception as exc:  # noqa: BLE001 - recorded as the run's error
        return f"Could not stop the run or finish its recording: {type(exc).__name__}: {exc}"
    return None
