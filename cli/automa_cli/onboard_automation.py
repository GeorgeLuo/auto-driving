"""Local publication monitor for an onboard shared runtime.

Execution belongs to the host behind OnboardRuntimeClient. This module only
persists and displays its publications, using the same worker state/view as a
locally hosted run.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TextIO

from autonomy.runtime.session import RunConfiguration
from implementations.runtime.donkeycar.client import OnboardRuntimeClient
from .picar_observation import (
    fetch_observation_publication, fetch_observation_frame, frame_id_from_headers,
    publication_to_frame_record, perception_text_from_publication,
)
from .runtime_view import RuntimeViewServer
from .decision_live import PicarDecisionViewAdapter
from .staged_bundle import write_json_atomically


def monitor_onboard_runtime(*, vehicle_id: str, base_url: str, automation_dir: Path,
                            configuration: RunConfiguration, timeout_s: float,
                            record: bool, verbose: bool, output: TextIO | None) -> tuple[int, str]:
    client = OnboardRuntimeClient(base_url, timeout_s=timeout_s)
    automation_dir.mkdir(parents=True, exist_ok=True)
    server = None
    state = {
        "schema": "automa_automation_run_state_v0", "vehicle_id": vehicle_id,
        "pid": os.getpid(), "status": "starting", "started_at_ms": int(time.time() * 1000),
        "frames_captured": 0, "processed_count": 0, "skipped_count": 0,
        "max_frames": configuration.frames or None, "interval_s": configuration.interval_s,
        "recording": record, "action_policy": configuration.mode,
        "control_source": "onboard", "control_application": (
            "shared_execution" if configuration.mode == "autonomy" else "not_applied"
        ),
    }
    state_path = automation_dir / "state.json"
    exit_code = 0
    try:
        response = client.start(configuration)
        state["run_id"] = response["host_run_id"]
        state["status"] = "running"
        server = RuntimeViewServer(
            vehicle_id=vehicle_id, automation_dir=automation_dir,
            run_id=state["run_id"], worker_pid=os.getpid(),
        ).start()
        decision_view = PicarDecisionViewAdapter(
            vehicle_id=vehicle_id, base_url=base_url, view_server=server, timeout_s=timeout_s
        )
        state["published_view"] = server.describe()
        write_json_atomically(state_path, state)
        frames_dir = automation_dir / "runs" / str(state["run_id"]) if record else automation_dir / "latest"
        frames_dir.mkdir(parents=True, exist_ok=True)
        last_frame_id = None
        while True:
            runtime = client.status()
            if runtime["host_run_id"] != state["run_id"]:
                raise RuntimeError("onboard host restarted during this run")
            session = runtime["session"]
            state["session"] = session
            state["execution"] = session["execution"]
            state["processed_count"] = session["processed_frames"]
            # Onboard capture has its own cadence; the monitor never executes
            # a decision or chooses a vehicle command.
            try:
                decision_view.refresh()
            except Exception:
                # A vehicle without proposals still has perception; decision
                # publication availability does not grant or revoke authority.
                server.decision.invalidate_latest()
            publication = fetch_observation_publication(base_url, timeout_s=timeout_s)
            frame = publication_to_frame_record(publication)
            frame_id = frame.get("frame_id")
            if frame_id and frame_id != last_frame_id and session["processed_frames"]:
                jpeg, headers = fetch_observation_frame(base_url, timeout_s=timeout_s)
                if frame_id_from_headers(headers) == frame_id:
                    frame["run_id"] = state["run_id"]
                    frame_path = frames_dir / (f"{frame_id}.jpg" if record else "front_camera.jpg")
                    frame_path.write_bytes(jpeg)
                    server.perception.publish_frame(frame_path=frame_path, frame_record=frame)
                    server.perception.publish_perception(frame_record=frame)
                    write_json_atomically(automation_dir / "latest_perception.json", frame)
                    (automation_dir / "latest_perception.txt").write_text(
                        perception_text_from_publication(publication) + "\n", encoding="utf-8"
                    )
                    if record:
                        write_json_atomically(frames_dir / f"{frame_id}.json", frame)
                    state["frames_captured"] += 1
                    state["last_capture"] = frame
                    state["last_frame"] = frame
                    last_frame_id = frame_id
                    if verbose and output is not None:
                        print(f"{frame_id}: {session['execution']['application']['reason']}", file=output, flush=True)
            state["published_view"] = server.health_payload()
            state["updated_at_ms"] = int(time.time() * 1000)
            write_json_atomically(state_path, state)
            if session["status"] != "running":
                state["status"] = session["status"]
                break
            time.sleep(max(0.02, configuration.interval_s))
    except KeyboardInterrupt:
        state["status"] = "stopped"
        exit_code = 130
    except Exception as exc:
        state["status"] = "error"
        state["error"] = f"{type(exc).__name__}: {exc}"
        exit_code = 2
    finally:
        try:
            client.stop()
        except Exception as exc:
            state["status"] = "error"
            state["error"] = f"Could not stop onboard runtime: {exc}"
            exit_code = 2
        if server is not None:
            server.stop()
        state["completed_at_ms"] = state["updated_at_ms"] = int(time.time() * 1000)
        state["published_view"] = {"available": False, "status": "stopped"}
        write_json_atomically(state_path, state)
    return exit_code, f"Automation {state['status']}: {vehicle_id}" + (
        f"\n{state['error']}" if state.get("error") else ""
    )
