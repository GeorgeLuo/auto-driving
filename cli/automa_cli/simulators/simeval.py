"""The SimEval runs ``simulators status`` and ``ensure`` make, and their payloads as text or JSON."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from ..shared.command_result import CommandResult


DEFAULT_SERVER = "http://127.0.0.1:3000/api"
DEFAULT_UI_HTTP_URL = "http://127.0.0.1:5050"
DEFAULT_TIMEOUT_MS = 2000
DEFAULT_SCENARIO_ID = "default"
MAX_COMMAND_OUTPUT_CHARS = 4000
MAX_DEPLOYMENT_ERROR_CHARS = 240


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
    chase_readiness = _dict_field(payload, "chase_readiness")
    chase_layers = _dict_field(chase_readiness, "layers")

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
            "shared Chase environment gate: "
            + _yes_no(bool(chase_readiness.get("ready"))),
            "atomic passive capture: "
            + str(
                _dict_field(chase_layers, "passive_capture").get(
                    "state",
                    "not checked",
                )
            ),
            f"usable: {_yes_no(bool(result.get('usable')))}",
        ]
    )
    readiness = _dict_field(payload, "readiness")
    blocking_layer = readiness.get("blocking_layer")
    if readiness.get("status") != "ready" and blocking_layer:
        lines.append(f"blocking layer: {blocking_layer}")
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
