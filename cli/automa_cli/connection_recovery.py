"""Targeted Metrics UI recovery. Chase runs in this UI, not any online SimEval backend."""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse
from typing import Callable

from .connection_status import Deadline
from .connection_targets import ConnectionTarget
from .simulators import _simeval_executable, _run_process, _parse_json, _browser_open_command
from .vehicles import chase_operator_url


class RecoveryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def local_origin(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    # Only equivalent loopback names are folded; no DNS-based ownership inference.
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    return host, parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)


def managed_ui(target: ConnectionTarget, deadline: Deadline, emit: Callable) -> dict:
    executable = _simeval_executable()
    if not executable:
        raise RecoveryError("simeval_missing", "Install simeval or set AUTOMA_SIMEVAL_BIN, then retry the same command.")
    run = _run_process([executable, "status", "--all", "--timeout", str(max(1, int(deadline.remaining(1) * 1000)))],
                       timeout_s=deadline.remaining(), emit=emit)
    data = _parse_json(run.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("ui"), dict):
        raise RecoveryError("simulator_status_invalid", "simeval did not report UI deployment records. " + (run.stderr or run.stdout)[-1000:])
    candidates = data["ui"].get("deployments", [])
    matches = [item for item in candidates if isinstance(item, dict) and item.get("url")
               and local_origin(item["url"]) == local_origin(target.endpoint)]
    if local_origin(target.endpoint)[0] != "loopback" or len(matches) != 1:
        raise RecoveryError("deployment_unmanaged", "No unique managed local UI deployment matches the selected endpoint. "
                            "Start/register that exact UI with simeval ui serve, then retry. Other deployments were left alone.")
    record = matches[0]
    if not record.get("uiDir") or record.get("mode") not in {"dev", "start"}:
        raise RecoveryError("deployment_incomplete", "The selected UI record lacks its workspace or launch mode; repair its simeval registration.")
    return record


def recover_simulator(target: ConnectionTarget, *, reset: bool, reason: str, deadline: Deadline,
                      emit: Callable, scenario: str | None = None) -> dict:
    if target.provider != "chase-sim":
        raise RecoveryError("recovery_unsupported", "Simulator recovery does not apply to PiCar; inspect its runtime service.")
    executable = _simeval_executable()
    if not executable:
        raise RecoveryError("simeval_missing", "Install simeval or set AUTOMA_SIMEVAL_BIN, then retry.")
    ui_url = chase_operator_url(target.endpoint)
    def run(args: list[str]) -> dict:
        emit("action_started", message="simeval " + " ".join(args), phase="simulator")
        result = _run_process([executable, *args], timeout_s=deadline.remaining(), emit=emit)
        if result.exit_code:
            raise RecoveryError("simulator_command_failed", f"simeval {' '.join(args)} failed ({result.exit_code}): "
                                + (result.stderr or result.stdout)[-2000:])
        emit("action_completed", message="Completed simeval " + " ".join(args), phase="simulator")
        return _parse_json(result.stdout) or {}
    if reset or reason == "simulator_unreachable":
        record = managed_ui(target, deadline, emit)
        args = ["ui", "serve", "--ui-host", record["host"], "--ui-port", str(record["port"]),
                    "--ui-dir", record["uiDir"], "--ui-mode", record["mode"]]
        storage_flags = {"dataRoot": "ui-data-root", "uploadRoot": "ui-upload-root",
            "uploadIndexFile": "ui-upload-index-file", "derivationPluginRoot": "ui-derivation-plugin-root",
            "derivationPluginIndexFile": "ui-derivation-plugin-index-file",
            "visualizationPluginRoot": "ui-visualization-plugin-root",
            "visualizationPluginIndexFile": "ui-visualization-plugin-index-file",
            "captureSourcesFile": "ui-capture-sources-file", "dashboardStateFile": "ui-dashboard-state-file"}
        for name, value in (record.get("storage") or {}).items():
            if name not in storage_flags:
                raise RecoveryError("deployment_incomplete", f"Cannot preserve unknown UI storage setting {name!r}.")
            if value:
                args.extend(["--" + storage_flags[name], str(value)])
        if reset and reason != "wrong_game" and record.get("processAlive"):
            run(["ui", "shutdown", "--ui", ui_url])
            wait_ui(ui_url, ready=False, deadline=deadline, emit=emit)
        if reason != "wrong_game":
            run(args)
            wait_ui(ui_url, ready=True, deadline=deadline, emit=emit)
    opener = _browser_open_command(ui_url)
    if opener is None:
        raise RecoveryError("browser_unavailable", f"No browser opener is configured. Open {ui_url}, then retry Connect.")
    # Opening an existing browser URL does not authorize reload or scenario replacement.
    emit("action_started", phase="browser", message=f"Opening {ui_url}.")
    opened = _run_process(opener, timeout_s=deadline.remaining(8), emit=emit)
    if opened.exit_code:
        raise RecoveryError("browser_open_failed", opened.stderr or f"Open {ui_url} manually, then retry.")
    emit("action_completed", phase="browser", message="Browser open requested; waiting for the frontend.")
    if reset:
        # Selecting Play itself establishes the debug surface; waiting for Play
        # debug first deadlocks when the browser opened a different sub-app.
        while True:
            check = _run_process([executable, "ui", "subapp", "--app", "play", "--ui", target.endpoint],
                                 timeout_s=deadline.remaining(3), emit=emit)
            if check.exit_code == 0:
                break
            emit("waiting", phase="browser", message="Waiting for the selected frontend to accept Play selection.")
            time.sleep(min(0.5, deadline.remaining()))
        # Sub-app selection can acknowledge before its new browser connection is ready.
        while True:
            debug = _run_process([executable, "ui", "play-debug", "--summary", "--ui", target.endpoint],
                                 timeout_s=deadline.remaining(3), emit=emit)
            if debug.exit_code == 0 and isinstance(_parse_json(debug.stdout), dict):
                break
            emit("waiting", phase="browser", message="Play selected; waiting for its debug surface.")
            time.sleep(min(0.5, deadline.remaining()))
        chosen = scenario or "chaser-depth-obstacles"
        run(["ui", "play-game-action", "--action-id", "scenario-select", "--value", json.dumps(chosen),
             "--ui", target.endpoint])
        return {"scenario": chosen, "endpoint": target.endpoint}
    return {"endpoint": target.endpoint}


def wait_ui(url: str, *, ready: bool, deadline: Deadline, emit: Callable) -> None:
    emit("waiting", phase="simulator", message="Waiting for the selected UI to " + ("start." if ready else "stop."))
    while True:
        timeout = deadline.remaining(0.5)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                running = True
                is_ui = bool(response.headers.get("x-metrics-ui-agent-ws"))
        except (OSError, urllib.error.URLError):
            running, is_ui = False, False
        if (ready and is_ui) or (not ready and not running):
            return
        time.sleep(min(0.2, deadline.remaining()))
