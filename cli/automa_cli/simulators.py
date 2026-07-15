from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any


DEFAULT_SERVER = "http://127.0.0.1:3000/api"
DEFAULT_UI_HTTP_URL = "http://127.0.0.1:5050"
DEFAULT_TIMEOUT_MS = 2000
DEFAULT_FRONTEND_CONNECT_TIMEOUT_MS = 8000
DEFAULT_FRONTEND_STABILITY_OBSERVE_MS = 1500
DEFAULT_SCENARIO_ID = "default"
MAX_COMMAND_OUTPUT_CHARS = 4000
MAX_DEPLOYMENT_ERROR_CHARS = 240


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


@dataclass(frozen=True)
class SimevalRun:
    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "args": list(self.args),
            "exit_code": self.exit_code,
            "stdout": _trim_output(self.stdout.strip(), MAX_COMMAND_OUTPUT_CHARS),
            "stdout_truncated": len(self.stdout.strip()) > MAX_COMMAND_OUTPUT_CHARS,
            "stderr": _trim_output(self.stderr.strip(), MAX_COMMAND_OUTPUT_CHARS),
            "stderr_truncated": len(self.stderr.strip()) > MAX_COMMAND_OUTPUT_CHARS,
            "duration_ms": self.duration_ms,
        }


def get_simulator_status(
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    json_output: bool = False,
) -> CommandResult:
    executable = _simeval_executable()
    payload = _base_payload("automa_simulator_status_v0", executable=executable)
    if executable is None:
        error = "simeval was not found on PATH. Set AUTOMA_SIMEVAL_BIN or install simeval."
        payload["status"] = {
            "online": False,
            "error": error,
        }
        payload["frontend"] = _skipped_summary("simeval is unavailable")
        payload["result"] = {
            "status": "unavailable",
            "reason_code": "simeval_missing",
            "usable": False,
            "error": error,
            "recovery": (
                "Install simeval or set AUTOMA_SIMEVAL_BIN, then rerun "
                "./cli/automa simulators status."
            ),
        }
        payload["commands"] = []
        return _result(payload, json_output=json_output, exit_code=2)

    status_run = _run_simeval(executable, ["status", "--all", "--timeout", str(timeout_ms)])
    summary = _summarize_status_run(status_run)
    frontend_run = _run_ui_verify(executable, auto_serve=False, observe_ms=500)
    payload["status"] = summary
    payload["frontend"] = _summarize_frontend_run(frontend_run)
    payload["result"] = _simulator_status_result(
        status=summary,
        frontend=payload["frontend"],
    )
    payload["commands"] = [status_run.to_dict(), frontend_run.to_dict()]

    return _result(payload, json_output=json_output, exit_code=0)


def ensure_simulator(
    *,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    scenario_id: str = DEFAULT_SCENARIO_ID,
    json_output: bool = False,
) -> CommandResult:
    executable = _simeval_executable()
    payload = _base_payload("automa_simulator_ensure_v0", executable=executable)
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
        return _result(payload, json_output=json_output, exit_code=2)

    commands: list[dict[str, Any]] = []
    initial_run = _run_simeval(executable, ["status", "--all", "--timeout", str(timeout_ms)])
    initial_status = _summarize_status_run(initial_run)
    commands.append(initial_run.to_dict())

    launched = False
    launch_run: SimevalRun | None = None
    if not initial_status["online"]:
        launched = True
        launch_run = _run_simeval(executable, ["deploy", "start"], timeout_s=60)
        commands.append(launch_run.to_dict())

    browser_open_run: SimevalRun | None = None
    launch_failed = launch_run is not None and launch_run.exit_code != 0
    if launch_failed:
        frontend_before = _skipped_summary("simulator backend launch failed")
        frontend_after = _skipped_summary("simulator backend launch failed")
        frontend_ready = False
        ui_summary = _skipped_summary("simulator backend launch failed")
        scenario_summary = _skipped_summary("simulator backend launch failed")
        scenario_summary["scenario"] = scenario_id
        play_debug_summary = _skipped_summary("simulator backend launch failed")
    else:
        frontend_before_run = _run_ui_verify(executable, auto_serve=True, observe_ms=500)
        frontend_before = _summarize_frontend_run(frontend_before_run)
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
            ui_summary = _skipped_summary("frontend tab is not connected")
            scenario_summary = _skipped_summary("frontend tab is not connected")
            scenario_summary["scenario"] = scenario_id
            play_debug_summary = _skipped_summary("frontend tab is not connected")

    if frontend_ready and _play_frontend_ready(ui_summary, scenario_summary, play_debug_summary):
        stability_frontend_run = _run_ui_verify(
            executable,
            auto_serve=False,
            observe_ms=DEFAULT_FRONTEND_STABILITY_OBSERVE_MS,
        )
        stability_frontend = _summarize_frontend_run(stability_frontend_run)
        commands.append(stability_frontend_run.to_dict())
        stability_debug_run = _run_simeval(executable, ["ui", "play-debug", "--summary"])
        stability_debug = _summarize_play_debug_run(stability_debug_run)
        commands.append(stability_debug_run.to_dict())
        stability_summary = {
            "ok": bool(stability_frontend["frontend_connected"] and stability_debug["ok"]),
            "observe_ms": DEFAULT_FRONTEND_STABILITY_OBSERVE_MS,
            "frontend": stability_frontend,
            "play_debug": stability_debug,
        }
    else:
        stability_summary = _skipped_summary("initial Play frontend setup did not complete")

    final_run = _run_simeval(executable, ["status", "--all", "--timeout", str(timeout_ms)])
    final_status = _summarize_status_run(final_run)
    commands.append(final_run.to_dict())

    browser_open_failed = browser_open_run is not None and browser_open_run.exit_code != 0
    usable = bool(
        final_status["online"]
        and frontend_ready
        and ui_summary["ok"]
        and scenario_summary["ok"]
        and play_debug_summary["ok"]
        and stability_summary["ok"]
        and not launch_failed
    )
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
    payload["result"] = {
        "status": result_status,
        "reason_code": reason_code,
        "usable": usable,
        "launch_attempted": launched,
        "launched": launched and not launch_failed,
        "error": errors[0] if errors else None,
        "errors": errors,
        "recovery": _simulator_ensure_recovery(
            reason_code=reason_code,
            scenario_id=scenario_id,
        ),
    }
    payload["commands"] = commands

    return _result(payload, json_output=json_output, exit_code=0 if usable else 2)


def _base_payload(schema: str, *, executable: str | None) -> dict[str, Any]:
    return {
        "schema": schema,
        "checked_at_ms": int(time.time() * 1000),
        "simeval": {
            "available": executable is not None,
            "executable": executable,
        },
        "server": os.environ.get("SIMEVAL_SERVER", DEFAULT_SERVER),
    }


def _simulator_status_result(
    *,
    status: dict[str, Any],
    frontend: dict[str, Any],
) -> dict[str, Any]:
    online = bool(status.get("online"))
    frontend_connected = bool(frontend.get("frontend_connected"))
    if online and frontend_connected:
        return {
            "status": "ready",
            "reason_code": "ready",
            "usable": True,
            "error": None,
            "recovery": None,
        }
    if not online:
        return {
            "status": "degraded",
            "reason_code": "backend_offline",
            "usable": False,
            "error": "No online simulator deployment is available.",
            "recovery": "./cli/automa simulators ensure",
        }
    return {
        "status": "degraded",
        "reason_code": "frontend_missing",
        "usable": False,
        "error": "The Metrics UI frontend tab is not connected.",
        "recovery": (
            f"Open {DEFAULT_UI_HTTP_URL}, then run ./cli/automa simulators ensure."
        ),
    }


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


def _simeval_executable() -> str | None:
    override = os.environ.get("AUTOMA_SIMEVAL_BIN")
    if override:
        return override
    return shutil.which("simeval")


def _run_simeval(
    executable: str,
    args: list[str],
    *,
    timeout_s: float = 20,
) -> SimevalRun:
    return _run_process([executable, *args], timeout_s=timeout_s)


def _run_process(command: list[str], *, timeout_s: float = 20) -> SimevalRun:
    started = time.perf_counter()
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return SimevalRun(
            args=tuple(command),
            exit_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _subprocess_text(exc.stdout)
        stderr = _subprocess_text(exc.stderr)
        return SimevalRun(
            args=tuple(command),
            exit_code=124,
            stdout=stdout,
            stderr=stderr + f"\nCommand timed out after {timeout_s:g}s.",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except OSError as exc:
        return SimevalRun(
            args=tuple(command),
            exit_code=127,
            stdout="",
            stderr=str(exc),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def _run_ui_verify(
    executable: str,
    *,
    auto_serve: bool,
    observe_ms: int,
) -> SimevalRun:
    return _run_simeval(
        executable,
        [
            "ui",
            "verify",
            "--auto-serve",
            "true" if auto_serve else "false",
            "--shutdown-on-exit",
            "false",
            "--observe-ms",
            str(observe_ms),
        ],
        timeout_s=max(5.0, observe_ms / 1000.0 + 5.0),
    )


def _wait_for_frontend(
    executable: str,
    *,
    timeout_ms: int,
) -> tuple[dict[str, Any], list[SimevalRun]]:
    deadline = time.monotonic() + max(0.5, timeout_ms / 1000.0)
    runs: list[SimevalRun] = []
    latest: dict[str, Any] = _skipped_summary("frontend check did not run")
    while True:
        run = _run_ui_verify(executable, auto_serve=True, observe_ms=500)
        runs.append(run)
        latest = _summarize_frontend_run(run)
        if latest["frontend_connected"] or time.monotonic() >= deadline:
            return latest, runs
        time.sleep(0.5)


def _configure_play_frontend(
    executable: str,
    *,
    scenario_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[SimevalRun]]:
    runs: list[SimevalRun] = []

    ui_run = _run_simeval(executable, ["ui", "subapp", "--app", "play"])
    ui_summary = _summarize_ui_run(ui_run)
    runs.append(ui_run)

    scenario_run = _run_simeval(
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

    debug_run = _run_simeval(executable, ["ui", "play-debug", "--summary"])
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
    return _run_process(command, timeout_s=8)


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


def _summarize_status_run(run: SimevalRun) -> dict[str, Any]:
    parsed = _parse_json(run.stdout)
    if not isinstance(parsed, dict):
        return {
            "online": False,
            "online_count": 0,
            "status": "unknown",
            "deployments": [],
            "command_exit_code": run.exit_code,
            "parse_error": "simeval status did not return JSON",
        }

    deployments = _status_deployments(parsed)
    online_count = sum(1 for item in deployments if item["online"])
    return {
        "online": online_count > 0,
        "online_count": online_count,
        "status": str(parsed.get("status", "unknown")),
        "deployments": deployments,
        "command_exit_code": run.exit_code,
    }


def _status_deployments(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    deployments: list[dict[str, Any]] = []
    local = parsed.get("local")
    if isinstance(local, dict):
        for item in _list_items(local.get("deployments")):
            deployments.append(_deployment_summary(item, source="local"))
    morphcloud = parsed.get("morphcloud")
    if isinstance(morphcloud, dict):
        for item in _list_items(morphcloud.get("instances")):
            deployments.append(_deployment_summary(item, source="morphcloud"))
    return deployments


def _deployment_summary(item: dict[str, Any], *, source: str) -> dict[str, Any]:
    health = _dict_field(item, "health")
    status = _dict_field(item, "status")
    overall = str(item.get("overall", "unknown"))
    online = (
        overall in {"running", "healthy", "online", "ready"}
        or health.get("ok") is True
        or status.get("ok") is True
    )
    error = _normalize_error(health.get("error") or status.get("error"))
    return {
        "source": source,
        "key": item.get("key") or item.get("id") or item.get("port"),
        "server": item.get("server") or item.get("apiUrl") or item.get("publicUrl"),
        "workspace": item.get("workspace"),
        "pid": item.get("pid"),
        "process_alive": item.get("processAlive"),
        "overall": overall,
        "online": online,
        "health_ok": health.get("ok"),
        "status_ok": status.get("ok"),
        "error": _trim_output(str(error), MAX_DEPLOYMENT_ERROR_CHARS) if error is not None else None,
    }


def _summarize_ui_run(run: SimevalRun) -> dict[str, Any]:
    parsed = _parse_json(run.stdout)
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
    parsed = _parse_json(run.stdout)
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
    parsed = _parse_json(run.stdout)
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


def _summarize_frontend_run(run: SimevalRun) -> dict[str, Any]:
    parsed = _parse_json(run.stdout)
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "ui_server": False,
            "frontend_connected": False,
            "status": "unknown",
            "url": DEFAULT_UI_HTTP_URL,
            "auto_served": False,
            "command_exit_code": run.exit_code,
            "error": _first_nonempty(run.stderr, run.stdout, "simeval ui verify did not return JSON"),
        }

    latest_sample = _latest_sample(parsed)
    server = _dict_field(parsed, "server")
    return {
        "ok": True,
        "ui_server": True,
        "frontend_connected": bool(latest_sample.get("frontendConnected")),
        "status": parsed.get("status", "unknown"),
        "url": server.get("url") or DEFAULT_UI_HTTP_URL,
        "auto_served": bool(server.get("autoServed")),
        "command_exit_code": run.exit_code,
        "error": _verify_error(parsed),
    }


def _latest_sample(parsed: dict[str, Any]) -> dict[str, Any]:
    samples = parsed.get("samples")
    if isinstance(samples, list):
        for item in reversed(samples):
            if isinstance(item, dict):
                return item
    return {}


def _verify_error(parsed: dict[str, Any]) -> str | None:
    failures = parsed.get("failures")
    if not isinstance(failures, list) or not failures:
        return None
    messages = []
    for failure in failures:
        if isinstance(failure, dict) and failure.get("type") == "no-captures":
            continue
        if isinstance(failure, dict) and failure.get("message"):
            messages.append(str(failure["message"]))
    return "; ".join(messages) if messages else None


def _trim_output(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}... [truncated {omitted} chars]"


def _normalize_error(error: Any) -> str | None:
    if error is None:
        return None
    text = str(error).strip()
    lower = text.lower()
    if lower.startswith("<!doctype html") or "<html" in lower[:200]:
        return "HTML error response"
    return text


def _skipped_summary(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "skipped": True,
        "reason": reason,
    }


def _parse_json(text: str) -> Any:
    text = text.strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _list_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _result(payload: dict[str, Any], *, json_output: bool, exit_code: int) -> CommandResult:
    if json_output:
        return CommandResult(exit_code, json.dumps(payload, indent=2, sort_keys=True))
    if payload["schema"] == "automa_simulator_status_v0":
        return CommandResult(exit_code, _format_status(payload))
    return CommandResult(exit_code, _format_ensure(payload))


def _format_status(payload: dict[str, Any]) -> str:
    status = _dict_field(payload, "status")
    frontend = _dict_field(payload, "frontend")
    result = _dict_field(payload, "result")
    lines = [
        "Simulator status",
        "----------------",
        f"simeval: {_simeval_label(payload)}",
        f"result: {result.get('status', 'unknown')}",
    ]
    if not _dict_field(payload, "simeval").get("available"):
        _append_result_details(lines, result)
        return "\n".join(lines)

    lines.extend(
        [
            f"online: {_yes_no(bool(status.get('online')))}",
            f"online deployments: {status.get('online_count', 0)}",
            f"simeval status: {status.get('status', 'unknown')}",
            f"ui server: {_yes_no(bool(frontend.get('ui_server')))}",
            f"frontend tab connected: {_yes_no(bool(frontend.get('frontend_connected')))}",
        ]
    )
    lines.extend(_format_deployments(status.get("deployments")))
    _append_result_details(lines, result)
    return "\n".join(lines)


def _format_ensure(payload: dict[str, Any]) -> str:
    result = _dict_field(payload, "result")
    initial = _dict_field(payload, "initial_status")
    final = _dict_field(payload, "final_status")
    launch = _dict_field(payload, "launch")
    frontend = _dict_field(payload, "frontend")
    frontend_after = _dict_field(frontend, "after")
    browser_open = _dict_field(frontend, "browser_open")
    ui = _dict_field(payload, "ui")
    scenario = _dict_field(payload, "scenario")
    play_debug = _dict_field(payload, "play_debug")
    stability = _dict_field(payload, "stability")

    lines = [
        "Simulator ensure",
        "----------------",
        f"simeval: {_simeval_label(payload)}",
        f"result: {result.get('status', 'unknown')}",
    ]
    if result.get("error") and not payload.get("commands"):
        _append_result_details(lines, result)
        return "\n".join(lines)

    lines.extend(
        [
            f"initial online: {_yes_no(bool(initial.get('online')))}",
            f"launch attempted: {_yes_no(bool(result.get('launch_attempted')))}",
            f"launched: {_yes_no(bool(result.get('launched')))}",
            f"launch ok: {_yes_no(bool(launch.get('ok')))}",
            f"browser open attempted: {_yes_no(bool(browser_open.get('attempted')))}",
            f"frontend tab connected: {_yes_no(bool(frontend_after.get('frontend_connected')))}",
            f"play UI selected: {_yes_no(bool(ui.get('ok')))}",
            f"scenario selected: {scenario.get('scenario', DEFAULT_SCENARIO_ID)} "
            f"({_yes_no(bool(scenario.get('ok')))})",
            f"chase debug ready: {_yes_no(bool(play_debug.get('ok')))}",
            "frontend stable: "
            + (
                "not checked"
                if stability.get("skipped")
                else _yes_no(bool(stability.get("ok")))
            ),
            f"final online: {_yes_no(bool(final.get('online')))}",
            f"usable: {_yes_no(bool(result.get('usable')))}",
        ]
    )
    _append_result_details(lines, result)
    return "\n".join(lines)


def _append_result_details(lines: list[str], result: dict[str, Any]) -> None:
    error = result.get("error")
    if isinstance(error, str) and error:
        lines.append(f"problem: {error}")
    recovery = result.get("recovery")
    if isinstance(recovery, str) and recovery:
        lines.append(f"next: {recovery}")


def _format_deployments(deployments: Any) -> list[str]:
    if not isinstance(deployments, list) or not deployments:
        return ["deployments: none recorded"]

    lines = ["deployments:"]
    for item in deployments:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or "unknown"
        source = item.get("source") or "unknown"
        overall = item.get("overall") or "unknown"
        server = item.get("server") or "no server"
        online = "online" if item.get("online") else "offline"
        error = item.get("error")
        line = f"- {source}:{key} {online} overall={overall} server={server}"
        if error:
            line += f" error={error}"
        lines.append(line)
    return lines


def _simeval_label(payload: dict[str, Any]) -> str:
    simeval = _dict_field(payload, "simeval")
    if not simeval.get("available"):
        return "not found"
    return str(simeval.get("executable"))


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text.splitlines()[0]
    return ""


def _dict_field(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    return value if isinstance(value, dict) else {}


def _subprocess_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
