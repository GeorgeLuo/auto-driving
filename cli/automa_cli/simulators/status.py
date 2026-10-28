"""``automa simulators status``: whether SimEval has an online simulator deployment."""

from __future__ import annotations

from typing import Any

from ..shared.command_result import CommandResult
from .simeval import (
    DEFAULT_TIMEOUT_MS,
    DEFAULT_UI_HTTP_URL,
    _base_payload,
    _result,
    _run_simeval,
    _run_ui_verify,
    _simeval_executable,
    _skipped_summary,
    _summarize_frontend_run,
    _summarize_status_run,
)


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
