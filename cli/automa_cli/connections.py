"""CLI connection orchestration shared by human output and frontend events."""
from __future__ import annotations

import fcntl
import hashlib
import json
import sys
import time
import uuid
from contextlib import contextmanager
from typing import TextIO

from autonomy.runtime.client import RuntimeClient
from . import runtime_hosts
from .connection_targets import ConnectionTarget, selected_scenario, remember_scenario
from .connection_status import Deadline, assess_connection, format_connection, verdict
from .connection_recovery import RecoveryError, managed_ui, recover_simulator


class ConnectionEvents:
    def __init__(self, target: ConnectionTarget | None, *, output: TextIO, events: bool = False,
                 quiet: bool = False):
        self.target = target
        self.output = output
        self.events = events
        self.quiet = quiet
        self.operation_id = uuid.uuid4().hex
        self.sequence = 0
        self.completed_actions: list[dict] = []

    def emit(self, event: str, *, phase: str = "connection", **data) -> None:
        if event == "action_completed":
            self.completed_actions.append({"phase": phase, **data})
        self.sequence += 1
        payload = {"schema": "automa_connection_event_v1", "operation_id": self.operation_id,
                   "sequence": self.sequence, "event": event, "phase": phase,
                   "target": self.target.to_dict() if self.target else None,
                   "timestamp_ms": int(time.time() * 1000), **data}
        if self.events:
            print(json.dumps(payload, sort_keys=True), file=self.output, flush=True)
        elif not self.quiet and event not in {"result", "status"}:
            message = data.get("message")
            if message:
                print(message, file=self.output, flush=True)


@contextmanager
def connection_lock(target: ConnectionTarget):
    """Serialize both endpoint recovery and vehicle host creation across CLI processes."""
    directory = runtime_hosts.RUNTIME_ROOT / ".connections"
    directory.mkdir(parents=True, exist_ok=True)
    files = []
    try:
        for key in sorted({target.vehicle_id, target.endpoint}):
            path = directory / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
            handle = path.open("a")
            files.append(handle)
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RecoveryError("operation_in_progress", "A connection operation already owns this target. Check status while it finishes.") from exc
        yield
    finally:
        for handle in reversed(files):
            handle.close()


def prepare_runtime(target: ConnectionTarget, status: dict, deadline: Deadline, events: ConnectionEvents) -> None:
    """Package the selected target without rediscovering a default endpoint."""
    from .deploy import _update_local_autonomy
    vehicle = status["layers"]["environment"].get("vehicle")
    if not vehicle or vehicle.get("vehicle_id") != target.vehicle_id:
        raise RecoveryError("target_mismatch", "Passive capture did not identify the selected vehicle.")
    deadline.remaining()
    events.emit("action_started", phase="prepare", message="Preparing the local controller; preserving existing selections.")
    result = _update_local_autonomy(vehicle_id=target.vehicle_id, dry_run=False, restart=False,
                                   json_output=False, output=None, vehicle=vehicle, preserve_selections=True)
    events.emit("diagnostic", phase="prepare", message=result.message)
    if result.exit_code:
        raise RecoveryError("preparation_failed", result.message)
    deadline.remaining()
    events.emit("action_completed", phase="prepare", message="Local controller prepared.")


def _attach(target: ConnectionTarget, status: dict, deadline: Deadline, events: ConnectionEvents) -> None:
    host = status["layers"]["host"]
    base_url = host.get("base_url")
    if not base_url:
        events.emit("action_started", phase="host", message="Starting the prepared runtime host.")
        base_url = runtime_hosts.runtime_base_url(target.vehicle_id, start=True, timeout_s=deadline.remaining())
        events.emit("action_completed", phase="host", message=f"Host serving at {base_url}.")
    client = RuntimeClient(base_url, timeout_s=deadline.remaining(3))
    session = client.status()["session"]
    if session.get("status") == "running":
        if session.get("configuration", {}).get("mode") != "observe_only":
            raise RecoveryError("active_session_conflict", "A non-observation session started before attachment; stop it explicitly.")
    else:
        events.emit("action_started", phase="observation", message="Starting observation only; driving remains disabled.")
        client.timeout_s = deadline.remaining(5)
        client.connect_observation()
        events.emit("action_completed", phase="observation", message="Observation requested; waiting for a fresh camera result.")


def connect(target: ConnectionTarget, *, timeout_s: float = 60, allow_repair: bool = False,
            allow_reset: bool = False, output: TextIO | None = None, events: bool = False,
            json_output: bool = False) -> tuple[int, dict]:
    stream = output or sys.stdout
    reporter = ConnectionEvents(target, output=stream, events=events, quiet=json_output)
    history: list[dict] = []
    completed: list[str] = []
    status = verdict(target, "checking", "checking", "Checking the selected connection.")
    deadline = Deadline(timeout_s)
    def assess() -> dict:
        reporter.emit("assessment_started", message="Checking simulator, host, and observation readiness.")
        value = assess_connection(target, deadline)
        remember_scenario(target, value)
        reporter.emit("status", status=value, reason_code=value["reason_code"])
        return value
    try:
        with connection_lock(target):
            status = assess()
            repaired: set[str] = set()
            reset = False
            attached = False
            while True:
                state, code = status["state"], status["reason_code"]
                if state == "connected" or state in {"blocked", "unknown"}:
                    break
                if state == "ready":
                    if attached:
                        break
                    _attach(target, status, deadline, reporter)
                    attached = True
                    completed.append("observation_started_or_reused")
                elif state == "warming":
                    reporter.emit("waiting", phase="observation", message=status["message"], reason_code=code)
                    time.sleep(min(0.5, deadline.remaining()))
                elif state == "repair_required" and code not in repaired and (allow_repair or allow_reset):
                    history.append(status)
                    repaired.add(code)
                    if code == "preparation_missing":
                        prepare_runtime(target, status, deadline, reporter)
                    elif code == "session_failed":
                        _attach(target, status, deadline, reporter)
                    elif code.startswith("observation_"):
                        reporter.emit("waiting", message="Retrying the existing observation session.", reason_code=code)
                        time.sleep(min(1.0, deadline.remaining()))
                    else:
                        recover_simulator(target, reset=False, reason=code, deadline=deadline, emit=reporter.emit)
                        # Browser delivery is asynchronous; give it a bounded chance to attach.
                        for _ in range(8):
                            status = assess()
                            if status["reason_code"] not in {"frontend_disconnected", "frontend_unresponsive", "simulator_unreachable"}:
                                break
                            reporter.emit("waiting", phase="browser", message="Waiting for the selected browser frontend.")
                            time.sleep(min(0.5, deadline.remaining()))
                    completed.append("minor_repair")
                elif state == "reset_required" and allow_reset and not reset:
                    history.append(status)
                    reset = True
                    # Recheck active session immediately before the disruptive action.
                    check = assess()
                    if check["state"] == "connected" or check["state"] == "blocked":
                        status = check
                        break
                    scenario = selected_scenario(target, status)
                    reporter.emit("action_started", phase="reset", message=f"Reset authorized for {target.endpoint}; restore scenario {scenario}.")
                    recover_simulator(target, reset=True, reason=code, deadline=deadline, emit=reporter.emit, scenario=scenario)
                    completed.append("simulator_reset")
                elif state == "repair_required" and code in repaired and not reset and target.provider == "chase-sim" and code in {
                    "frontend_disconnected", "frontend_unresponsive", "simulator_unreachable", "observation_stale", "observation_unavailable"
                }:
                    # Offer a bounce only when ownership and restart metadata can be proven.
                    record = managed_ui(target, deadline, reporter.emit)
                    status = verdict(target, "reset_required", code, status["message"], layers=status["layers"],
                        action="reset", effects=f"Restart only {record['url']} from {record['uiDir']}; "
                        f"restore scenario {selected_scenario(target, status)}.",
                        details={"deployment": record})
                    if allow_reset:
                        continue
                    break
                else:
                    break
                status = assess()
    except KeyboardInterrupt:
        status = verdict(target, "cancelled", "cancelled", "Connection cancelled; completed effects are listed below.", layers=status.get("layers"))
    except TimeoutError as exc:
        history.append(status)
        status = verdict(target, "unknown", "deadline_exceeded", str(exc), layers=status.get("layers"), action="refresh")
    except (RecoveryError, runtime_hosts.RuntimeHostError, OSError, ValueError, RuntimeError) as exc:
        history.append(status)
        status = verdict(target, "blocked", getattr(exc, "code", "connection_failed"), str(exc), layers=status.get("layers"))
    status["history"] = history
    status["completed_effects"] = completed
    status["completed_actions"] = reporter.completed_actions
    status["operation_id"] = reporter.operation_id
    if status["state"] in {"repair_required", "reset_required"}:
        reporter.emit("escalation_required", message=status["message"], reason_code=status["reason_code"], actions=status["actions"])
    code = 0 if status["state"] == "connected" else 1
    reporter.emit("result", result=status, exit_code=code, reason_code=status["reason_code"])
    if json_output:
        print(json.dumps(status, indent=2, sort_keys=True), file=stream)
    elif not events:
        print(format_connection(status), file=stream)
        if history and history[0]["reason_code"] != status["reason_code"]:
            print(f"Original problem: {history[0]['reason_code']}: {history[0]['message']}", file=stream)
        if completed:
            print("Completed: " + ", ".join(completed), file=stream)
    return code, status
