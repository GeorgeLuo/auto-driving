"""The vehicles, simulators and hosts commands the viewer answers, as its read and write routes.

A read serves what the command's ``--json`` prints. A write runs the command
with the request's JSON object and answers with its result's fields and
``text``, what the command prints: a host command's progress lines, then its
result.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .hosts.deployment.render import render_create, render_delete, render_progress, render_update
from .hosts.deployment.service import create_host, delete_host, update_host
from .hosts.progress import ProgressEvent
from .hosts.runs.render import render_lifecycle_result
from .hosts.runs.service import restart_host_automation, start_host_automation_background, stop_host_automation
from .hosts.runs.status import hosts_status
from .providers.registry import PROVIDERS
from .simulators.render import render_simulator_result
from .simulators.service import create_simulator, delete_simulator
from .simulators.status import simulators_status
from .vehicles.discovery import discover_vehicle, remove_vehicle
from .vehicles.render import render_vehicle_result
from .vehicles.status import vehicles_status


def _url(body: dict[str, Any]) -> str:
    url = body.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("url must be a non-empty string")
    return url


def _name(body: dict[str, Any]) -> str:
    name = body.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    return name


def _flag(body: dict[str, Any], key: str) -> bool:
    value = body.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false")
    return value


def _answer(result: Any, text: str) -> dict[str, Any]:
    # A host command's result can carry paths; they answer as the strings the CLI prints.
    fields = json.loads(json.dumps(asdict(result), default=str))
    return {"ok": result.exit_code == 0, **fields, "text": text}


class _Printed:
    """A host command's progress, as the lines it prints before its result."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, event: ProgressEvent) -> None:
        self.lines.append(render_progress(event))

    def text(self, result: str) -> str:
        return "\n".join([*self.lines, result] if result else self.lines)


def _discover(body: dict[str, Any]) -> dict[str, Any]:
    provider, force = body.get("provider"), body.get("force", False)
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
    if not isinstance(force, bool):
        raise ValueError("force must be true or false")
    result = discover_vehicle(provider=provider, url=_url(body), force=force)
    return _answer(result, render_vehicle_result(result))


def _remove(body: dict[str, Any]) -> dict[str, Any]:
    result = remove_vehicle(url=_url(body))
    return _answer(result, render_vehicle_result(result))


def _create(body: dict[str, Any]) -> dict[str, Any]:
    result = create_simulator()
    return _answer(result, render_simulator_result(result))


def _delete(body: dict[str, Any]) -> dict[str, Any]:
    result = delete_simulator(url=_url(body))
    return _answer(result, render_simulator_result(result))


def _create_host(body: dict[str, Any]) -> dict[str, Any]:
    printed = _Printed()
    result = create_host(host_name=_name(body), provider=body.get("provider"), url=_url(body), emit=printed)
    return _answer(result, printed.text(render_create(result, json_output=False)))


def _update_host(body: dict[str, Any]) -> dict[str, Any]:
    printed = _Printed()
    result = update_host(host_name=_name(body), emit=printed)
    return _answer(result, printed.text(render_update(result, json_output=False)))


def _delete_host(body: dict[str, Any]) -> dict[str, Any]:
    printed = _Printed()
    result = delete_host(host_name=_name(body), emit=printed)
    return _answer(result, printed.text(render_delete(result)))


def _run_flags(body: dict[str, Any]) -> dict[str, Any]:
    """hosts run's and restart's flags: --observe-only, --num-decisions N or --unbounded, --record."""

    count = body.get("num_decisions")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int)):
        raise ValueError("num_decisions must be a whole number or null")
    return {"num_decisions": count, "unbounded": _flag(body, "unbounded"),
            "take_control": not _flag(body, "observe_only"), "record": _flag(body, "record")}


def _run_host(body: dict[str, Any]) -> dict[str, Any]:
    result = start_host_automation_background(host_name=_name(body), **_run_flags(body))
    return _answer(result, render_lifecycle_result(result))


def _stop_host(body: dict[str, Any]) -> dict[str, Any]:
    result = stop_host_automation(host_name=_name(body))
    return _answer(result, render_lifecycle_result(result))


def _restart_host(body: dict[str, Any]) -> dict[str, Any]:
    result = restart_host_automation(host_name=_name(body), **_run_flags(body))
    return _answer(result, render_lifecycle_result(result))


# /api/hosts is the v1 home page's host list, so hosts status reads at /api/hosts/status.
VIEWER_READS = {
    "/api/vehicles": lambda: vehicles_status()[1],
    "/api/simulators": lambda: simulators_status()[1],
    "/api/hosts/status": lambda: hosts_status(include_runs=True)[1],
}
VIEWER_WRITES = {
    "/api/vehicles/discover": _discover,
    "/api/vehicles/remove": _remove,
    "/api/simulators/create": _create,
    "/api/simulators/delete": _delete,
    "/api/hosts/create": _create_host,
    "/api/hosts/update": _update_host,
    "/api/hosts/delete": _delete_host,
    "/api/hosts/run": _run_host,
    "/api/hosts/stop": _stop_host,
    "/api/hosts/restart": _restart_host,
}
