"""``automa simulators ensure``: launch and configure a known Chase environment."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import time
from typing import Any

from implementations.vehicle.chase_sim.defaults import (
    get_default_chase_ui_ws_url,
)

from ..shared.command_result import CommandResult
from ..vehicles.probes import DEFAULT_READINESS_TIMEOUT_S
from .readiness import get_vehicle_status
from .simeval import (
    DEFAULT_SCENARIO_ID,
    DEFAULT_TIMEOUT_MS,
    DEFAULT_UI_HTTP_URL,
    SimevalRun,
    base_payload,
    parse_json,
    run_process,
    run_simeval,
    run_ui_verify,
    simeval_executable,
    simulator_result,
    skipped_summary,
    summarize_frontend_run,
    summarize_status_run,
)


DEFAULT_FRONTEND_CONNECT_TIMEOUT_MS = 8000
DEFAULT_FRONTEND_STABILITY_OBSERVE_MS = 1500


def ensure_simulator(
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    scenario_id: str = DEFAULT_SCENARIO_ID,
    json_output: bool = False,
) -> CommandResult:
    executable = simeval_executable()
    payload = base_payload("automa_simulator_ensure_v0", executable=executable)
    payload["desired"] = {
        "deployment": "default simeval local deployment",
        "ui_app": "play",
        "scenario": scenario_id,
        "frontend_url": DEFAULT_UI_HTTP_URL,
    }
    if executable is None:
        error = "simeval was not found on PATH. Set AUTOMA_SIMEVAL_BIN or install simeval."
        payload["result"] = {
            "status": "unavailable",
            "reason_code": "simeval_missing",
            "usable": False,
            "launch_attempted": False,
            "launched": False,
            "error": error,
            "errors": [error],
            "recovery": (
                "Install simeval or set AUTOMA_SIMEVAL_BIN, then rerun "
                "./cli/automa simulators ensure."
            ),
        }
        payload["commands"] = []
        return simulator_result(payload, json_output=json_output, exit_code=2)

    commands: list[dict[str, Any]] = []
    initial_run = run_simeval(executable, ["status", "--all", "--timeout", str(timeout_ms)])
    initial_status = summarize_status_run(initial_run)
    commands.append(initial_run.to_dict())

    launched = False
    launch_run: SimevalRun | None = None
    if not initial_status["online"]:
        launched = True
        launch_run = run_simeval(executable, ["deploy", "start"], timeout_s=60)
        commands.append(launch_run.to_dict())

    browser_open_run: SimevalRun | None = None
    launch_failed = launch_run is not None and launch_run.exit_code != 0
    if launch_failed:
        frontend_before = skipped_summary("simulator backend launch failed")
        frontend_after = skipped_summary("simulator backend launch failed")
        frontend_ready = False
        ui_summary = skipped_summary("simulator backend launch failed")
        scenario_summary = skipped_summary("simulator backend launch failed")
        scenario_summary["scenario"] = scenario_id
        play_debug_summary = skipped_summary("simulator backend launch failed")
    else:
        frontend_before_run = run_ui_verify(executable, auto_serve=True, observe_ms=500)
        frontend_before = summarize_frontend_run(frontend_before_run)
        commands.append(frontend_before_run.to_dict())

        frontend_after = frontend_before
        if not frontend_before["frontend_connected"]:
            browser_open_run = _open_frontend_browser(DEFAULT_UI_HTTP_URL)
            commands.append(browser_open_run.to_dict())
            frontend_after, frontend_poll_runs = _wait_for_frontend(
                executable,
                timeout_ms=DEFAULT_FRONTEND_CONNECT_TIMEOUT_MS,
            )
            commands.extend(run.to_dict() for run in frontend_poll_runs)

        frontend_ready = bool(frontend_after["frontend_connected"])

        if frontend_ready:
            ui_summary, scenario_summary, play_debug_summary, setup_runs = _configure_play_frontend(
                executable,
                scenario_id=scenario_id,
            )
            commands.extend(run.to_dict() for run in setup_runs)

            if not _play_frontend_ready(ui_summary, scenario_summary, play_debug_summary):
                if browser_open_run is None:
                    browser_open_run = _open_frontend_browser(DEFAULT_UI_HTTP_URL)
                    commands.append(browser_open_run.to_dict())
                frontend_after, frontend_poll_runs = _wait_for_frontend(
                    executable,
                    timeout_ms=DEFAULT_FRONTEND_CONNECT_TIMEOUT_MS,
                )
                commands.extend(run.to_dict() for run in frontend_poll_runs)
                frontend_ready = bool(frontend_after["frontend_connected"])
                if frontend_ready:
                    ui_summary, scenario_summary, play_debug_summary, setup_runs = _configure_play_frontend(
                        executable,
                        scenario_id=scenario_id,
                    )
                    commands.extend(run.to_dict() for run in setup_runs)
        else:
            ui_summary = skipped_summary("frontend tab is not connected")
            scenario_summary = skipped_summary("frontend tab is not connected")
            scenario_summary["scenario"] = scenario_id
            play_debug_summary = skipped_summary("frontend tab is not connected")

    if frontend_ready and _play_frontend_ready(ui_summary, scenario_summary, play_debug_summary):
        stability_frontend_run = run_ui_verify(
            executable,
            auto_serve=False,
            observe_ms=DEFAULT_FRONTEND_STABILITY_OBSERVE_MS,
        )
        stability_frontend = summarize_frontend_run(stability_frontend_run)
        commands.append(stability_frontend_run.to_dict())
        stability_debug_run = run_simeval(executable, ["ui", "play-debug", "--summary"])
        stability_debug = _summarize_play_debug_run(stability_debug_run)
        commands.append(stability_debug_run.to_dict())
        stability_summary = {
            "ok": bool(stability_frontend["frontend_connected"] and stability_debug["ok"]),
            "observe_ms": DEFAULT_FRONTEND_STABILITY_OBSERVE_MS,
            "frontend": stability_frontend,
            "play_debug": stability_debug,
        }
    else:
        stability_summary = skipped_summary("initial Play frontend setup did not complete")

    final_run = run_simeval(executable, ["status", "--all", "--timeout", str(timeout_ms)])
    final_status = summarize_status_run(final_run)
    commands.append(final_run.to_dict())

    browser_open_failed = browser_open_run is not None and browser_open_run.exit_code != 0
    configured_usable = bool(
        final_status["online"]
        and frontend_ready
        and ui_summary["ok"]
        and scenario_summary["ok"]
        and play_debug_summary["ok"]
        and stability_summary["ok"]
        and not launch_failed
    )
    chase_status: dict[str, Any] | None = None
    chase_gate_ready = False
    if configured_usable:
        chase_status = get_vehicle_status(
            host_name="chase-sim-chaser",
            chase_ws_url=get_default_chase_ui_ws_url(),
            timeout_s=DEFAULT_READINESS_TIMEOUT_S,
        )
        expected_layers = {
            "simulator_server": "reachable",
            "simulator_frontend": "connected",
            "chase_game": "ready",
            "vehicle": "discoverable",
            "passive_capture": "available",
        }
        status_layers = (
            chase_status.get("layers")
            if isinstance(chase_status.get("layers"), dict)
            else {}
        )
        chase_gate_ready = all(
            isinstance(status_layers.get(name), dict)
            and status_layers[name].get("state") == expected
            for name, expected in expected_layers.items()
        )
    usable = configured_usable and chase_gate_ready
    errors = []
    if launch_failed:
        errors.append("simeval deploy start failed")
    if not final_status["online"]:
        errors.append("no online simulator deployment after ensure")
    if browser_open_failed and not frontend_ready:
        errors.append("could not open the Metrics UI browser tab")
    if not frontend_ready:
        errors.append(
            f"no connected Metrics UI frontend tab; open {DEFAULT_UI_HTTP_URL} and rerun"
        )
    if not ui_summary["ok"]:
        errors.append("could not select the Play simulator UI")
    if not scenario_summary["ok"]:
        errors.append(f"could not select the {scenario_id!r} simulator scenario")
    if not play_debug_summary["ok"]:
        errors.append("Chase Play debug is not available")
    if not stability_summary["ok"] and not stability_summary.get("skipped"):
        errors.append("Chase frontend did not remain usable after setup")
    if configured_usable and not chase_gate_ready:
        next_action = (
            chase_status.get("next_action")
            if isinstance(chase_status, dict)
            and isinstance(chase_status.get("next_action"), dict)
            else {}
        )
        errors.append(
            "configured Chase frontend did not pass the shared passive sensor-capture gate"
            + (
                f": {next_action.get('reason')}"
                if next_action.get("reason")
                else ""
            )
        )

    payload["initial_status"] = initial_status
    payload["launch"] = {
        "attempted": launched,
        "ok": launch_run is None or launch_run.exit_code == 0,
    }
    payload["frontend"] = {
        "before": frontend_before,
        "after": frontend_after,
        "browser_open": {
            "attempted": browser_open_run is not None,
            "ok": browser_open_run is None or browser_open_run.exit_code == 0,
            "url": DEFAULT_UI_HTTP_URL,
        },
    }
    payload["ui"] = ui_summary
    payload["scenario"] = scenario_summary
    payload["play_debug"] = play_debug_summary
    payload["stability"] = stability_summary
    payload["chase_readiness"] = (
        {
            "endpoint": chase_status.get("endpoint"),
            "layers": {
                name: chase_status.get("layers", {}).get(name)
                for name in (
                    "simulator_server",
                    "simulator_frontend",
                    "chase_game",
                    "vehicle",
                    "passive_capture",
                )
            },
            "capture": chase_status.get("capture"),
            "timeout_s": chase_status.get("timeout_s"),
            "elapsed_ms": chase_status.get("elapsed_ms"),
            "ready": chase_gate_ready,
        }
        if isinstance(chase_status, dict)
        else {
            "ready": False,
            "reason": "configured simulator gate did not run",
        }
    )
    chase_readiness_gates = (
        payload["chase_readiness"].get("layers")
        if isinstance(payload["chase_readiness"].get("layers"), dict)
        else {}
    )
    chase_blocking_layer = next(
        (
            name
            for name, expected in (
                ("simulator_server", "reachable"),
                ("simulator_frontend", "connected"),
                ("chase_game", "ready"),
                ("vehicle", "discoverable"),
                ("passive_capture", "available"),
            )
            if not isinstance(chase_readiness_gates.get(name), dict)
            or chase_readiness_gates[name].get("state") != expected
        ),
        None,
    )
    payload["readiness"] = {
        "schema": "automa_cli_readiness_v1",
        "status": "ready" if usable else "blocked",
        "ready_for": "inspect vehicle status and create a host",
        "checked_at_ms": int(time.time() * 1000),
        "gates": chase_readiness_gates,
        "blocking_layer": chase_blocking_layer,
    }
    payload["final_status"] = final_status
    result_status, reason_code = _simulator_ensure_result_status(
        usable=usable,
        launch_failed=launch_failed,
        final_online=bool(final_status["online"]),
        frontend_ready=frontend_ready,
        ui_ready=bool(ui_summary["ok"]),
        scenario_ready=bool(scenario_summary["ok"]),
        play_debug_ready=bool(play_debug_summary["ok"]),
        stability_summary=stability_summary,
    )
    if configured_usable and not chase_gate_ready:
        result_status = "failed"
        reason_code = "chase_capture_not_ready"
    recovery = _simulator_ensure_recovery(
        reason_code=reason_code,
        scenario_id=scenario_id,
    )
    if reason_code == "chase_capture_not_ready" and isinstance(chase_status, dict):
        next_action = (
            chase_status.get("next_action")
            if isinstance(chase_status.get("next_action"), dict)
            else {}
        )
        if isinstance(next_action.get("command"), str):
            recovery = next_action["command"]
        elif isinstance(next_action.get("external_change"), dict):
            recovery = (
                "Metrics UI external change required: "
                + json.dumps(next_action["external_change"], sort_keys=True)
            )
    payload["result"] = {
        "status": result_status,
        "reason_code": reason_code,
        "usable": usable,
        "launch_attempted": launched,
        "launched": launched and not launch_failed,
        "error": errors[0] if errors else None,
        "errors": errors,
        "recovery": recovery,
    }
    payload["commands"] = commands

    return simulator_result(payload, json_output=json_output, exit_code=0 if usable else 2)


def _simulator_ensure_result_status(
    *,
    usable: bool,
    launch_failed: bool,
    final_online: bool,
    frontend_ready: bool,
    ui_ready: bool,
    scenario_ready: bool,
    play_debug_ready: bool,
    stability_summary: dict[str, Any],
) -> tuple[str, str]:
    if usable:
        return "ready", "ready"
    if launch_failed:
        return "failed", "launch_failed"
    if not final_online:
        return "failed", "backend_offline"
    if not frontend_ready:
        return "failed", "frontend_missing"
    if not stability_summary.get("skipped") and not stability_summary.get("ok"):
        return "failed", "frontend_unstable"
    if not (ui_ready and scenario_ready and play_debug_ready):
        return "failed", "play_setup_failed"
    return "failed", "unknown"


def _simulator_ensure_recovery(*, reason_code: str, scenario_id: str) -> str | None:
    if reason_code == "ready":
        return None
    if reason_code == "launch_failed":
        return (
            "Run simeval deploy start directly to inspect the backend failure, then rerun "
            "./cli/automa simulators ensure."
        )
    if reason_code == "backend_offline":
        return "Restore the simulator backend, then rerun ./cli/automa simulators ensure."
    if reason_code == "frontend_missing":
        return f"Open {DEFAULT_UI_HTTP_URL}, then rerun ./cli/automa simulators ensure."
    if reason_code == "frontend_unstable":
        return (
            f"Reload the Metrics UI Play tab at {DEFAULT_UI_HTTP_URL}, then rerun "
            "./cli/automa simulators ensure."
        )
    if reason_code == "play_setup_failed":
        return (
            f"Open the Metrics UI Play tab, select scenario {scenario_id!r}, then rerun "
            "./cli/automa simulators ensure."
        )
    return "Inspect ./cli/automa simulators ensure --json, then retry."


def _wait_for_frontend(
    executable: str,
    *,
    timeout_ms: int,
) -> tuple[dict[str, Any], list[SimevalRun]]:
    deadline = time.monotonic() + max(0.5, timeout_ms / 1000.0)
    runs: list[SimevalRun] = []
    latest: dict[str, Any] = skipped_summary("frontend check did not run")
    while True:
        run = run_ui_verify(executable, auto_serve=True, observe_ms=500)
        runs.append(run)
        latest = summarize_frontend_run(run)
        if latest["frontend_connected"] or time.monotonic() >= deadline:
            return latest, runs
        time.sleep(0.5)


def _configure_play_frontend(
    executable: str,
    *,
    scenario_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[SimevalRun]]:
    runs: list[SimevalRun] = []

    ui_run = run_simeval(executable, ["ui", "subapp", "--app", "play"])
    ui_summary = _summarize_ui_run(ui_run)
    runs.append(ui_run)

    scenario_run = run_simeval(
        executable,
        [
            "ui",
            "play-game-action",
            "--action-id",
            "scenario-select",
            "--value",
            json.dumps(scenario_id),
        ],
    )
    scenario_summary = _summarize_ui_action_run(scenario_run)
    scenario_summary["scenario"] = scenario_id
    runs.append(scenario_run)

    debug_run = run_simeval(executable, ["ui", "play-debug", "--summary"])
    play_debug_summary = _summarize_play_debug_run(debug_run)
    runs.append(debug_run)

    return ui_summary, scenario_summary, play_debug_summary, runs


def _play_frontend_ready(
    ui_summary: dict[str, Any],
    scenario_summary: dict[str, Any],
    play_debug_summary: dict[str, Any],
) -> bool:
    return bool(ui_summary.get("ok") and scenario_summary.get("ok") and play_debug_summary.get("ok"))


def _open_frontend_browser(url: str) -> SimevalRun:
    command = _browser_open_command(url)
    if command is None:
        return SimevalRun(
            args=("open-browser", url),
            exit_code=127,
            stdout="",
            stderr="No browser opener found for this platform.",
            duration_ms=0,
        )
    return run_process(command, timeout_s=8)


def _browser_open_command(url: str) -> list[str] | None:
    override = os.environ.get("AUTOMA_BROWSER_OPEN_COMMAND")
    if override:
        return [*shlex.split(override), url]
    if sys.platform == "darwin":
        return ["open", url]
    if sys.platform.startswith("linux"):
        opener = shutil.which("xdg-open")
        return [opener, url] if opener else None
    if sys.platform.startswith("win"):
        return ["cmd", "/c", "start", "", url]
    return None


def _summarize_ui_run(run: SimevalRun) -> dict[str, Any]:
    parsed = parse_json(run.stdout)
    if isinstance(parsed, dict):
        return {
            "ok": run.exit_code == 0 and parsed.get("status") == "success",
            "status": parsed.get("status"),
            "app": parsed.get("app"),
            "ui_url": parsed.get("uiUrl"),
            "command_exit_code": run.exit_code,
        }
    return {
        "ok": run.exit_code == 0,
        "status": "unknown",
        "app": None,
        "ui_url": None,
        "command_exit_code": run.exit_code,
    }


def _summarize_ui_action_run(run: SimevalRun) -> dict[str, Any]:
    parsed = parse_json(run.stdout)
    if isinstance(parsed, dict):
        return {
            "ok": run.exit_code == 0 and parsed.get("status") == "success",
            "status": parsed.get("status"),
            "command_exit_code": run.exit_code,
        }
    return {
        "ok": run.exit_code == 0,
        "status": "unknown",
        "command_exit_code": run.exit_code,
    }


def _summarize_play_debug_run(run: SimevalRun) -> dict[str, Any]:
    parsed = parse_json(run.stdout)
    if isinstance(parsed, dict):
        return {
            "ok": run.exit_code == 0,
            "game_id": parsed.get("gameId"),
            "frame_index": parsed.get("frameIndex"),
            "command_exit_code": run.exit_code,
        }
    return {
        "ok": run.exit_code == 0,
        "game_id": None,
        "frame_index": None,
        "command_exit_code": run.exit_code,
    }
