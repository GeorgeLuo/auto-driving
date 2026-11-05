"""The vehicles and simulators commands the viewer answers, as its read and write routes.

A read serves what the command's ``--json`` prints. A write runs the command
with the request's JSON object and answers with its exit code, its result kind,
and ``text``, what the command prints.
"""

from __future__ import annotations

from typing import Any

from .providers.registry import PROVIDERS
from .simulators.render import render_simulator_result
from .simulators.service import create_simulator, delete_simulator
from .simulators.status import simulators_status
from .vehicles.discovery import discover_vehicle, remove_vehicle
from .vehicles.render import render_vehicle_result
from .vehicles.status import get_vehicles_status


def _url(body: dict[str, Any]) -> str:
    url = body.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("url must be a non-empty string")
    return url


def _answer(result: Any, text: str) -> dict[str, Any]:
    return {"ok": result.exit_code == 0, "exit_code": result.exit_code, "kind": result.kind, "text": text}


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


VIEWER_READS = {
    "/api/vehicles": get_vehicles_status,
    "/api/simulators": lambda: simulators_status().payload,
}
VIEWER_WRITES = {
    "/api/vehicles/discover": _discover,
    "/api/vehicles/remove": _remove,
    "/api/simulators/create": _create,
    "/api/simulators/delete": _delete,
}
