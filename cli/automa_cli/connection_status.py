"""Connection evidence independent of terminal monitor and browser presentation."""
from __future__ import annotations

import io
import math
import time
from typing import Any

from PIL import Image
from autonomy.runtime.client import RuntimeClient
from . import runtime_hosts
from .connection_targets import ConnectionTarget, selected_scenario
from .host_publications import fetch_observation_sample
from .step_activations import bundle_activation_problems
from .vehicles import Candidate, _probe_chase

SCHEMA = "automa_connection_v1"
MAX_OBSERVATION_AGE_MS = 5000


class Deadline:
    def __init__(self, seconds: float):
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("--timeout-s must be finite and greater than zero.")
        self.end = time.monotonic() + seconds

    def remaining(self, cap: float | None = None) -> float:
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("The operation deadline expired; remaining checks were not performed.")
        return remaining if cap is None else min(cap, remaining)


def verdict(target: ConnectionTarget, state: str, code: str, message: str, *,
            layers: dict | None = None, action: str | None = None, effects: str | None = None,
            details: Any = None) -> dict:
    flag = {"repair": "--allow-repair", "reset": "--allow-reset"}.get(action)
    actions = []
    if action:
        args = target.arguments() + ([flag] if flag else [])
        verb = "status" if action == "refresh" else "connect"
        actions.append({"id": action, "label": {"repair": "Repair connection", "reset": "Reset simulator",
                       "connect": "Connect", "refresh": "Check again"}[action],
                       "requires_confirmation": action in {"repair", "reset"},
                       "effects": effects, "command": target.command(*([flag] if flag else []), verb=verb),
                       "argv": ["vehicles", verb, *args]})
    return {"schema": SCHEMA, "target": target.to_dict(), "state": state,
            "reason_code": code, "message": message, "checked_at_ms": int(time.time() * 1000),
            "layers": layers or {}, "actions": actions, "details": details}


def environment_status(target: ConnectionTarget, deadline: Deadline) -> dict:
    if target.provider != "chase-sim":
        return {"state": "not_applicable", "reason_code": None}
    probe = _probe_chase(Candidate("chase-sim", target.endpoint, "connection"),
                         timeout_s=deadline.remaining(5.0))
    return environment_from_probe(probe)


def environment_from_probe(probe) -> dict:
    if probe.active:
        passive = (probe.vehicle or {}).get("status", {}).get("passive_capture", {})
        if passive.get("status") == "available":
            return {"state": "ready", "reason_code": None, "capture": passive,
                    "vehicle": probe.vehicle}
        return {"state": "unsupported", "reason_code": passive.get("code") or "simulator_capability_missing",
                "message": "The simulator cannot prove passive session preservation.", "details": passive}
    return {"state": "failed", "reason_code": probe.diagnostics.get("error_code") or "probe_failed",
            "message": probe.error, "details": probe.diagnostics}


def _staged_target(target: ConnectionTarget, recorded: str) -> ConnectionTarget | None:
    try:
        if target.provider == "chase-sim":
            from .vehicles import normalize_chase_url
            return ConnectionTarget(target.vehicle_id, target.provider, normalize_chase_url(recorded))
        return ConnectionTarget(target.vehicle_id, "picar", recorded.rstrip("/"))
    except ValueError:
        return None


def assess_connection(target: ConnectionTarget, deadline: Deadline, *, environment: dict | None = None) -> dict:
    layers: dict[str, Any] = {name: {"state": "unknown"} for name in ("environment", "host", "observation")}
    layers["presentation"] = {"state": "optional", "command":
        f"./cli/automa vehicles stream perception --id {target.vehicle_id}"}
    def result(state: str, code: str, message: str, **kw: Any) -> dict:
        return verdict(target, state, code, message, layers=layers, **kw)
    try:
        deadline.remaining()
        layers["environment"] = environment if environment is not None else environment_status(target, deadline)
        env = layers["environment"]
        found_id = (env.get("vehicle") or {}).get("vehicle_id")
        if found_id is not None and found_id != target.vehicle_id:
            return result("blocked", "target_mismatch", f"The endpoint identifies {found_id}, not {target.vehicle_id}.")
        # The host can expose a conflicting active session even when capture failed.
        staged = runtime_hosts.staged_connection(target.vehicle_id)
        recorded_endpoint = staged.get("ws_url" if target.provider == "chase-sim" else "base_url")
        if isinstance(recorded_endpoint, str) and not target.same_endpoint(recorded_endpoint):
            staged_target = _staged_target(target, recorded_endpoint)
            return result("blocked", "target_mismatch",
                f"{target.vehicle_id} is staged for {recorded_endpoint}, not {target.endpoint}. "
                "Connect to the staged endpoint. Updates keep that endpoint.",
                details={"command": staged_target.command(verb="connect")} if staged_target is not None else None)
        base_url = (runtime_hosts.running_base_url(target.vehicle_id) if target.provider == "chase-sim"
                    else target.endpoint)
        runtime = None
        if base_url:
            try:
                runtime = RuntimeClient(base_url, timeout_s=deadline.remaining(2.0)).status()
                if not isinstance(runtime, dict) or not isinstance(runtime.get("session"), dict) or not runtime.get("host_run_id"):
                    raise ValueError("Host response lacks a runtime identity or session.")
                session = runtime["session"]
                layers["host"] = {"state": "ready", "base_url": base_url,
                                  "host_run_id": runtime["host_run_id"], "session": session}
                if session.get("status") == "running" and session.get("configuration", {}).get("mode") != "observe_only":
                    return result("blocked", "active_session_conflict",
                        "The host has an active non-observation session. Stop it explicitly before connecting.",
                        details={"command": f"./cli/automa vehicles automation stop --id {target.vehicle_id}"})
            except (RuntimeError, OSError, ValueError) as exc:
                layers["host"] = {"state": "failed", "base_url": base_url, "error": str(exc)}
                return result("blocked", "host_unresponsive", f"The selected host is not answering: {exc}",
                    details={"command": f"./cli/automa vehicles automation stop --id {target.vehicle_id}"})
        else:
            layers["host"] = {"state": "stopped"}
        if env["state"] not in {"ready", "not_applicable"}:
            code = env.get("reason_code") or "probe_failed"
            message = env.get("message") or code
            if code in {"simulator_unreachable", "frontend_disconnected", "frontend_unresponsive"}:
                return result("repair_required", code, message, action="repair",
                              effects="Restore the selected UI service or open its browser; preserve the scenario.")
            if code == "wrong_game":
                return result("reset_required", code, message, action="reset",
                    effects=f"Select Chase scenario {selected_scenario(target, {'layers': layers})} on the selected frontend; the current game is replaced.")
            return result("blocked", code, message, details=env.get("details"))
        if target.provider == "chase-sim" and runtime is None:
            problems = bundle_activation_problems(runtime_hosts.bundle_paths(target.vehicle_id), target.vehicle_id)
            if problems:
                return result("blocked", "activation_invalid", "The staged runtime configuration is invalid.", details=problems)
            try:
                runtime_hosts.release_manifest(target.vehicle_id)
            except runtime_hosts.RuntimeHostError as exc:
                return result("repair_required", "preparation_missing", str(exc), action="repair",
                              effects="Package the local controller and create missing step selections; preserve existing selections.")
            return result("ready", "host_stopped", "The environment is ready; Connect starts an observation-only host.", action="connect")
        assert runtime is not None
        session_state = runtime["session"].get("status")
        if session_state == "error":
            error = runtime["session"].get("error")
            if not error:
                host_status = RuntimeClient(base_url, timeout_s=deadline.remaining(1)).host_status()
                error = (host_status.get("autonomy") or {}).get("last_error")
            return result("repair_required", "session_failed", error or "The runtime session failed without an error detail.",
                          action="repair", effects="Retry observation once on the existing host; do not reset the simulator.")
        if session_state in {"starting", "stopping"}:
            return result("warming", "session_" + session_state, f"Runtime session is {session_state}.", action="refresh")
        if session_state not in {"running", "stopped", "completed"}:
            return result("blocked", "session_unknown", f"The host reported an unrecognized session state: {session_state!r}.")
        if session_state != "running":
            return result("ready", "session_stopped", "The host is ready; Connect starts observation only.", action="connect")
        publication, jpeg = fetch_observation_sample(base_url, timeout_s=deadline.remaining(2.0))
        health = publication.get("health", "unknown")
        layers["observation"] = {"state": health, "error": publication.get("error"),
            "age_ms": publication.get("result_age_ms"), "frame": publication.get("frame")}
        if health != "healthy":
            return result("warming" if health in {"warming", "absent"} else "repair_required",
                "observation_" + str(health), publication.get("error") or f"Observation is {health}.",
                action="refresh" if health in {"warming", "absent"} else "repair",
                effects="Retry observation and report the remaining failure without replacing the session.")
        frame = publication.get("frame") or {}
        age = publication.get("result_age_ms")
        if not isinstance(age, (int, float)) or isinstance(age, bool) or not math.isfinite(age) or age < 0 or age > MAX_OBSERVATION_AGE_MS:
            return result("repair_required", "observation_stale", "The host has no demonstrably fresh observation (maximum age 5 s).", action="repair")
        if not frame.get("frame_id") or not isinstance(publication.get("perception"), dict) or not jpeg:
            return result("blocked", "observation_invalid", "The host publication lacks a matched camera image, frame identity, or perception result.")
        with Image.open(io.BytesIO(jpeg)) as image:
            image.verify()
        # A restart between the status and image reads cannot establish a connection.
        current = RuntimeClient(base_url, timeout_s=deadline.remaining(1.0)).status()
        if current.get("host_run_id") != runtime["host_run_id"] or current.get("session", {}).get("status") != "running":
            return result("warming", "host_changed", "The host changed during verification; check again.", action="refresh")
        if current.get("session", {}).get("configuration", {}).get("mode") != "observe_only":
            return result("blocked", "active_session_conflict", "The host changed to a non-observation session during verification.")
        layers["observation"]["state"] = "ready"
        return result("connected", "connected", "Host connected; fresh observation is available. Driving is not enabled.")
    except TimeoutError as exc:
        return result("unknown", "deadline_exceeded", str(exc), action="refresh")
    except (RuntimeError, OSError, ValueError) as exc:
        return result("blocked", "observation_invalid", f"Could not validate the host observation: {exc}")


def format_connection(status: dict) -> str:
    target = status["target"]
    lines = [f"Vehicle: {target['vehicle_id']} ({target['endpoint']})",
             f"Connection: {status['state']}", f"{status['reason_code']}: {status['message']}"]
    for name, layer in status.get("layers", {}).items():
        if layer.get("error"):
            lines.append(f"{name}: {layer['error']}")
    for action in status.get("actions", []):
        if action.get("effects"):
            lines.append(f"Effect: {action['effects']}")
        lines.append(f"Next: {action['command']}")
    details = status.get("details")
    if isinstance(details, list) and all(isinstance(item, dict) and "step" in item for item in details):
        for problem in details:
            lines.extend([f"Invalid {problem['step']} activation: {problem.get('activation')}",
                          f"Reason: {problem.get('reason')}", f"Next action: {problem.get('command')}"])
    elif isinstance(details, dict) and details.get("command"):
        lines.append("Next: " + details["command"])
    elif details:
        lines.append("Details: " + json_text(details))
    if status["state"] == "connected":
        lines.append(f"Open observation: ./cli/automa vehicles stream perception --id {target['vehicle_id']}")
    return "\n".join(lines)


def json_text(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
