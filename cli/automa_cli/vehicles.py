from __future__ import annotations

import json
import math
import os
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

from implementations.vehicle.chase_sim import (
    ChaseCaptureValidationError,
    ChasePassiveCaptureError,
    ChaseSimCar,
)
from implementations.vehicle.chase_sim.car import FRONTEND_DELIVERY_PROTOCOL_CODES
from implementations.vehicle.chase_sim.defaults import (
    CHASE_UI_WS_URL_ENV,
    DEFAULT_CHASE_UI_WS_URL,
)
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError, MetricsUiWsClient
from implementations.vehicle.picar import create_picar
from implementations.vehicle.picar.defaults import (
    get_default_picar_base_url,
    DEFAULT_LOCAL_CAR_BASE_URL,
    LOCAL_CAR_BASE_URL_ENV,
)

from .host_publications import host_state

DEFAULT_READINESS_TIMEOUT_S = 5.0
STATUS_SCHEMA = "automa_vehicle_status_v1"
SIMULATOR_STATUS_SCHEMA = "automa_simulator_status_v1"
READINESS_SCHEMA = "automa_cli_readiness_v1"


@dataclass(frozen=True)
class Candidate:
    provider: str
    url: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProbeResult:
    active: bool
    candidate: Candidate
    vehicle: dict[str, Any] | None = None
    error: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    checked_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        data = {
            "active": self.active,
            "candidate": self.candidate.to_dict(),
            "checked_at_ms": self.checked_at_ms,
        }
        if self.vehicle is not None:
            data["vehicle"] = self.vehicle
        if self.error is not None:
            data["error"] = self.error
        if self.diagnostics:
            data["diagnostics"] = self.diagnostics
        return data


def discover_active_vehicles(
    *,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    picar_urls: tuple[str, ...] = (),
    chase_ws_urls: tuple[str, ...] = (),
    include_picar: bool = True,
    include_chase_sim: bool = True,
    include_inactive: bool = False,
) -> dict[str, Any]:
    """Probe configured vehicle endpoints and return active devices.

    Discovery is intentionally conservative: it only performs read-only status
    checks and never sends drive or mode-change commands.
    """

    timeout = _require_positive_timeout(timeout_s)
    candidates: list[Candidate] = []
    if include_picar:
        candidates.extend(_picar_candidates(picar_urls))
    if include_chase_sim:
        candidates.extend(_chase_candidates(chase_ws_urls))

    results = [_probe_candidate(candidate, timeout_s=timeout) for candidate in candidates]
    active = [result.vehicle for result in results if result.active and result.vehicle is not None]
    payload: dict[str, Any] = {
        "schema": "automa_vehicle_discovery_v0",
        "checked_at_ms": int(time.time() * 1000),
        "active_count": len(active),
        "vehicles": active,
        "discovery": {
            "candidate_count": len(candidates),
            "providers": sorted({candidate.provider for candidate in candidates}),
            "timeout_s": timeout,
        },
    }
    if include_inactive:
        payload["inactive"] = [
            result.to_dict()
            for result in results
            if not result.active
        ]
    return payload


def format_active_vehicles(
    payload: dict[str, Any],
    *,
    include_inactive: bool = False,
) -> str:
    lines = [
        f"Discoverable vehicles: {payload.get('active_count', 0)}",
    ]
    vehicles = payload.get("vehicles")
    if not isinstance(vehicles, list) or not vehicles:
        lines.append("No discoverable vehicles found.")
    else:
        for index, vehicle in enumerate(vehicles, start=1):
            if not isinstance(vehicle, dict):
                continue
            lines.extend(_format_vehicle(index, vehicle))

    if include_inactive:
        inactive = payload.get("inactive")
        if isinstance(inactive, list) and inactive:
            lines.append("")
            lines.append(f"Undiscoverable candidates: {len(inactive)}")
            for item in inactive:
                if not isinstance(item, dict):
                    continue
                candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
                provider = candidate.get("provider", "unknown")
                url = candidate.get("url", "unknown")
                error = item.get("error", "no error detail")
                diagnostics = item.get("diagnostics") if isinstance(item.get("diagnostics"), dict) else {}
                detail = _inactive_detail(diagnostics)
                suffix = f" [{detail}]" if detail else ""
                lines.append(f"- {provider} at {url}: {error}{suffix}")

    return "\n".join(lines)


def get_vehicle_status(
    *,
    vehicle_id: str,
    chase_url: str | None = None,
    chase_ws_url: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
) -> dict[str, Any]:
    """Read aggregate simulator, vehicle, deployment, worker, and view state."""

    if chase_url is not None and chase_ws_url is not None:
        raise ValueError("--chase-url and --chase-ws-url cannot be used together")
    operation_timeout = _require_positive_timeout(timeout_s)
    operator_url = (
        chase_url
        or chase_ws_url
        or os.environ.get(CHASE_UI_WS_URL_ENV, DEFAULT_CHASE_UI_WS_URL)
    ).strip()
    endpoint = normalize_chase_url(operator_url)
    display_url = chase_operator_url(endpoint)
    started = time.monotonic()
    candidate = Candidate("chase-sim", endpoint, "cli")
    probe = _probe_chase(candidate, timeout_s=operation_timeout)
    discovery = {
        "schema": "automa_vehicle_discovery_v0",
        "checked_at_ms": int(time.time() * 1000),
        "active_count": 1 if probe.active else 0,
        "vehicles": [probe.vehicle] if probe.active and probe.vehicle is not None else [],
        "inactive": [] if probe.active else [probe.to_dict()],
        "discovery": {
            "candidate_count": 1,
            "providers": ["chase-sim"],
            "timeout_s": operation_timeout,
        },
    }

    # Imported lazily because automation consumes vehicle discovery.
    from .automation import _collect_automation_status

    remaining = operation_timeout - (time.monotonic() - started)
    automation = _collect_automation_status(
        vehicle_id=vehicle_id,
        view_timeout_s=remaining if remaining >= 0.05 else 0.0,
    )
    all_deployed_by_id = {
        str(item.get("vehicle_id")): item
        for item in automation
        if isinstance(item, dict) and item.get("vehicle_id")
    }
    deployed_by_id = all_deployed_by_id
    discoverable_by_id = {
        str(item.get("vehicle_id")): item
        for item in discovery.get("vehicles", [])
        if isinstance(item, dict) and item.get("vehicle_id")
    }
    known_ids = {vehicle_id}

    inactive = next(
        (
            item
            for item in discovery.get("inactive", [])
            if isinstance(item, dict)
            and isinstance(item.get("candidate"), dict)
            and item["candidate"].get("provider") == "chase-sim"
            and item["candidate"].get("url") == endpoint
        ),
        None,
    )
    cards = [
        _build_vehicle_status_card(
            vehicle_id=known_id,
            endpoint=endpoint,
            operator_url=display_url,
            timeout_s=operation_timeout,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            discoverable=discoverable_by_id.get(known_id),
            inactive=inactive,
            automation=deployed_by_id.get(known_id),
        )
        for known_id in sorted(known_ids)
    ]
    return cards[0]


def get_chase_simulator_status(
    *,
    vehicle_id: str | None = None,
    chase_url: str | None = None,
    chase_ws_url: str | None = None,
    picar_url: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
) -> dict[str, Any]:
    """The Chase simulator, and the state of each vehicle host.

    The simulator runs when its server answers; its frontend is connected when
    a browser has the page open. Hosts come from explicit local records; the
    simulator reports which of those hosts are attached. Every
    host's ``/host/status`` is read in parallel within the one deadline.
    """

    from .runtime_hosts import local_hosts

    if chase_url is not None and chase_ws_url is not None:
        raise ValueError("--chase-url and --chase-ws-url cannot be used together")
    operation_timeout = _require_positive_timeout(timeout_s)
    endpoint = normalize_chase_url(
        (chase_url or chase_ws_url or os.environ.get(CHASE_UI_WS_URL_ENV, DEFAULT_CHASE_UI_WS_URL)).strip()
    )
    client = MetricsUiWsClient(endpoint, timeout_s=operation_timeout)
    deadline = time.monotonic() + operation_timeout

    def remaining() -> float:
        # The client refuses a zero timeout; a spent budget times out at once instead.
        return max(deadline - time.monotonic(), 1e-3)

    status: dict[str, Any] = {
        "schema": SIMULATOR_STATUS_SCHEMA,
        "vehicle_id": vehicle_id,
        "operator_url": chase_operator_url(endpoint),
        "state": "unreachable",
        "error": None,
        "frontend": None,
        "game": None,
        "hosts": [],
        "hosts_error": None,
    }
    failures = (OSError, ValueError, MetricsUiWebSocketError)
    hosts: dict[str, dict[str, Any]] = {}
    reads: dict[str, Any] = {}

    with ThreadPoolExecutor() as pool:
        picar_endpoint = (picar_url or get_default_picar_base_url()).rstrip("/")
        picar_probe = pool.submit(_probe_picar, Candidate("picar", picar_endpoint, "cli"), timeout_s=remaining())

        def add(host: dict[str, Any]) -> dict[str, Any]:
            key = host["vehicle_id"]
            hosts[key] = host
            if host["url"] is None:
                host["state"] = "not running"
            else:
                reads[key] = pool.submit(
                    host_state, host["url"], vehicle_id=host["vehicle_id"], timeout_s=remaining()
                )
            return host

        for local in local_hosts():
            if vehicle_id in (None, local["vehicle_id"]):
                add(_host_entry(**local))
        agents = pool.submit(client.list_agents, timeout_s=remaining())
        state_error = listed = list_error = None
        try:
            client.get_state(timeout_s=remaining())
        except failures as exc:
            state_error = exc
        try:
            listed = agents.result()
        except failures as exc:
            list_error = exc
        # A server that answers either query is running; without a browser,
        # get_state fails with a frontend delivery code.
        frontend_error = state_error if _frontend_code(state_error) else None
        if state_error is None or frontend_error is not None or listed is not None:
            status["state"] = "running"
        if state_error is not None and frontend_error is None:
            status["error"] = str(state_error)
        if listed is None:
            if status["state"] == "running":
                status["hosts_error"] = str(list_error)
        else:
            for host in hosts.values():
                host["attached"] = False
            for agent in listed:
                client_info = agent.get("client")
                if (
                    not isinstance(client_info, dict)
                    or client_info.get("kind") != "vehicle-host"
                    or not isinstance(client_info.get("url"), str)
                    or vehicle_id not in (None, client_info.get("vehicle_id"))
                ):
                    continue
                url = client_info["url"].rstrip("/")
                host = hosts.get(client_info.get("vehicle_id"))
                if host is None or host["url"] != url:
                    continue
                host.update(attached=True, attached_since=agent.get("connectedAt"))
                if host["pid"] is None:
                    host["pid"] = client_info.get("pid")
        if status["state"] == "running" and frontend_error is None:
            try:
                debug = client.get_play_debug(timeout_s=remaining())
            except failures as exc:
                frontend_error = exc
            else:
                status["frontend"], status["game"] = "connected", debug.get("gameId")
        if frontend_error is not None:
            code = _frontend_code(frontend_error)
            status["frontend"] = "not answering" if code == "frontend_unresponsive" else "not connected"
            if code is None:
                status["error"] = status["error"] or str(frontend_error)

    probe = picar_probe.result()
    reachable = probe.diagnostics.get("runtime_state") in {"server_not_listening", "http_unhealthy"}
    status["picar"] = {"url": picar_endpoint, "state": "answering" if probe.active else
                       "reachable, no host listening" if reachable else "unreachable", "error": probe.error}
    for host_id, read in reads.items():
        state = read.result()
        state.pop("reply")
        # A bounded run ends "completed", but the CLI's closing stop leaves it "stopped".
        if state["state"] in {"stopped", "completed"}:
            state["state"] = "finished_run"
        hosts[host_id].update(state)
    status["hosts"] = sorted(hosts.values(), key=lambda host: (str(host["vehicle_id"]), host["url"]))
    if vehicle_id is not None and not status["hosts"]:
        status["hosts"].append({
            **_host_entry(vehicle_id, None),
            "attached": False if listed is not None else None,
            "state": "not found",
        })
    return status


def _frontend_code(exc: BaseException | None) -> str | None:
    """The code Metrics UI fails a query with when no browser frontend answers it."""

    code = getattr(exc, "code", None)
    return code if code in FRONTEND_DELIVERY_PROTOCOL_CODES else None


def _host_entry(vehicle_id: Any, url: str | None, pid: Any = None, local: str | None = None,
                provider: str | None = None, vehicle_url: str | None = None) -> dict[str, Any]:
    return {
        "vehicle_id": vehicle_id, "url": url, "pid": pid, "attached": None, "attached_since": None,
        "provider": provider, "vehicle_url": vehicle_url,
        "local": local, "state": None, "mode": None, "decision_count": None, "error_count": None,
        "last_frame_at_ms": None, "error": None,
    }


def host_state_text(host: dict[str, Any]) -> str:
    """A host's state in words, with the mode of a run that has control: ``running, observe only``."""

    state = host.get("state")
    text = {"pre_start": "not started", "finished_run": "finished run"}.get(state, str(state or "unknown"))
    mode = host.get("mode")
    # Manual is the mode whenever no run has control, so it adds nothing.
    return f"{text}, {mode.replace('_', ' ')}" if isinstance(mode, str) and mode and mode != "manual" else text


def format_chase_simulator_status(status: dict[str, Any]) -> str:
    url = status.get("operator_url", "unknown")
    # Each environment's heading names the --provider that create host takes for it.
    if status.get("state") == "running":
        simulator = [f"Simulator (chase-sim)  running at {url}"]
        if status.get("frontend") == "connected" and status.get("game"):
            simulator.append(f"  frontend connected, game {status['game']}")
        elif status.get("frontend"):
            simulator.append(f"  frontend {status['frontend']}")
    else:
        simulator = [f"Simulator (chase-sim)  unreachable at {url}"]
    simulator += _indented(status.get("error"))
    if status.get("hosts_error"):
        simulator.append(f"  cannot list attached hosts: {status['hosts_error']}")
    blocks = ["\n".join(simulator)]
    picar = status.get("picar")
    if picar:
        blocks.append(f"PiCar (picar)  {picar['state']} at {picar['url']}" +
                      (f": {picar['error']}" if picar.get("error") else ""))
    hosts = status.get("hosts") or []
    if not hosts:
        blocks.append("No hosts found")
    blocks.extend(_host_block(host) for host in hosts)
    return "\n\n".join(blocks)


def _host_block(host: dict[str, Any]) -> str:
    lines = [f"Host {host.get('vehicle_id')}  {host_state_text(host)}"]
    if host.get("vehicle_url"):
        kind = "chaser" if host.get("provider") == "chase-sim" else "PiCar"
        lines.append(f"  {kind} at {host['vehicle_url']}")
    activity = [
        _count(host.get("decision_count"), "decision"),
        _count(host.get("error_count"), "error"),
        _frame_age(host.get("last_frame_at_ms")),
    ]
    if any(activity):
        lines.append("  " + ", ".join(part for part in activity if part))
    lines += _indented(host.get("error"))
    attachment = _attachment_text(host)
    if attachment:
        lines.append(f"  {attachment}")
    address = [host.get("url"), None if host.get("pid") is None else f"pid {host['pid']}"]
    if any(address):
        lines.append("  " + ", ".join(part for part in address if part))
    return "\n".join(lines)


def _attachment_text(host: dict[str, Any]) -> str | None:
    if host.get("state") == "not found":
        return None
    if host.get("attached"):
        since = host.get("attached_since")
        return f"attached since {_local_time(since)}" if since else "attached"
    local = "local host record" if host.get("local") == "host record" else host.get("local")
    # A PiCar is not a simulator host.
    if local == "staged connection" or host.get("attached") is None:
        return local
    return f"not attached to this simulator ({local})" if local else "not attached to this simulator"


def _count(value: Any, noun: str) -> str | None:
    if type(value) is not int:
        return None
    return f"{value} {noun}" + ("" if value == 1 else "s")


def _frame_age(completed_at_ms: Any) -> str | None:
    if type(completed_at_ms) is not int:
        return None
    age_s = max(0.0, time.time() - completed_at_ms / 1000)
    if age_s < 60:
        return f"last frame {age_s:.1f}s ago"
    return f"last frame at {datetime.fromtimestamp(completed_at_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')}"


def _indented(text: Any) -> list[str]:
    return [f"  {line}" for line in str(text).splitlines()] if text else []


def _local_time(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)


def _build_vehicle_status_card(
    *,
    vehicle_id: str,
    endpoint: str,
    operator_url: str,
    timeout_s: float,
    elapsed_ms: int,
    discoverable: dict[str, Any] | None,
    inactive: dict[str, Any] | None,
    automation: dict[str, Any] | None,
) -> dict[str, Any]:
    diagnostics = (
        inactive.get("diagnostics")
        if isinstance(inactive, dict) and isinstance(inactive.get("diagnostics"), dict)
        else {}
    )
    vehicle_status = (
        discoverable.get("status")
        if isinstance(discoverable, dict) and isinstance(discoverable.get("status"), dict)
        else {}
    )
    passive = (
        vehicle_status.get("passive_capture")
        if isinstance(vehicle_status.get("passive_capture"), dict)
        else {}
    )
    passive_status = str(
        passive.get("status")
        or diagnostics.get("passive_capture")
        or "unavailable"
    )
    error_code = diagnostics.get("error_code")
    server_state = (
        "reachable"
        if discoverable is not None or diagnostics.get("ws_server")
        else "unreachable"
    )
    frontend_state = (
        "connected"
        if discoverable is not None or diagnostics.get("frontend_connected")
        else "disconnected"
    )
    if discoverable is not None:
        game_state = "ready"
    elif error_code == "wrong_game":
        game_state = "wrong_game"
    else:
        game_state = "unavailable"

    if automation is None:
        deployment_state = "not_deployed"
        worker_state = "stopped"
        view_state = "unavailable"
        worker_details: dict[str, Any] = {}
        view_details: dict[str, Any] = {}
    else:
        bundle_exists = bool(automation.get("bundle_root"))
        deployment_state = (
            "deployed"
            if automation.get("deployed")
            else "invalid"
            if bundle_exists
            else "not_deployed"
        )
        process = (
            automation.get("process")
            if isinstance(automation.get("process"), dict)
            else {}
        )
        process_status = process.get("status")
        # Prefer explicit error over a still-running PID bit so post-ready
        # failures are not masked as a healthy worker.
        if process_status in {"error", "stale"}:
            worker_state = "error"
        elif process.get("running"):
            worker_state = "running"
        else:
            worker_state = "stopped"
        view_details = (
            automation.get("published_view")
            if isinstance(automation.get("published_view"), dict)
            else {}
        )
        view_status = view_details.get("status")
        # available | unavailable | stale — do not treat "warming" as stale.
        if view_details.get("available") and worker_state == "running":
            view_state = "available"
        elif view_status == "stale":
            view_state = "stale"
        elif view_status == "warming":
            view_state = "unavailable"
        elif view_details.get("url") and worker_state != "running":
            view_state = "stale"
        elif view_details.get("url"):
            view_state = "unavailable"
        else:
            view_state = "unavailable"
        worker_details = {
            **process,
            "authority": (
                automation.get("state")
                if isinstance(automation.get("state"), dict)
                else {}
            ),
        }

    layers = {
        "simulator_server": {"state": server_state},
        "simulator_frontend": {"state": frontend_state},
        "chase_game": {"state": game_state},
        "vehicle": {
            "state": "discoverable" if discoverable is not None else "undiscoverable"
        },
        "passive_capture": {
            "state": passive_status
            if passive_status in {"available", "unavailable", "unsupported"}
            else "unavailable",
            "code": passive.get("code") or error_code,
            "session_preservation": passive.get("session_preservation"),
            "mutation_attempted": passive.get("mutation_attempted"),
            "allowed_operations": passive.get("allowed_operations"),
        },
        "automation_deployment": {
            "state": deployment_state,
            "details": automation,
        },
        "automation_worker": {
            "state": worker_state,
            "details": worker_details,
        },
        "perception_view": {
            "state": view_state,
            "details": view_details,
        },
    }
    capture = {
        "sensor": passive.get("sensor"),
        "evaluator_reference": passive.get("evaluator_reference")
        or {
            "status": "unavailable",
            "reason": "no_valid_capture",
            "path": "evaluator.reference",
        },
        "error": {
            "schema": "automa_cli_error_v1",
            "error": error_code,
            "layer": "capture",
            "message": inactive.get("error") if isinstance(inactive, dict) else None,
            "details": {"path": diagnostics.get("error_path")},
            "recovery": None,
            "exit_code": 1,
        }
        if error_code
        else None,
    }
    next_action, blocking_layer, ready_for = _vehicle_next_action(
        vehicle_id=vehicle_id,
        operator_url=operator_url,
        endpoint=endpoint,
        layers=layers,
        diagnostics=diagnostics,
    )
    checked_at_ms = int(time.time() * 1000)
    gates = {
        name: {"status": "ready" if value["state"] in _ready_layer_states(name) else "blocked"}
        for name, value in layers.items()
    }
    readiness = {
        "schema": READINESS_SCHEMA,
        "status": "ready" if next_action is None else "blocked",
        "ready_for": ready_for,
        "checked_at_ms": checked_at_ms,
        "gates": gates,
        "blocking_layer": blocking_layer,
    }
    return {
        "schema": STATUS_SCHEMA,
        "vehicle_id": vehicle_id,
        "endpoint": {
            "operator_url": operator_url,
            "resolved_ws_url": endpoint,
        },
        "layers": layers,
        "capture": capture,
        "readiness": readiness,
        "next_action": next_action,
        "checked_at_ms": checked_at_ms,
        "timeout_s": timeout_s,
        "elapsed_ms": elapsed_ms,
        "diagnostics": diagnostics,
    }


def _ready_layer_states(layer: str) -> set[str]:
    return {
        "simulator_server": {"reachable"},
        "simulator_frontend": {"connected"},
        "chase_game": {"ready"},
        "vehicle": {"discoverable"},
        "passive_capture": {"available"},
        "automation_deployment": {"deployed"},
        "automation_worker": {"running"},
        "perception_view": {"available"},
    }[layer]


def _vehicle_next_action(
    *,
    vehicle_id: str,
    operator_url: str,
    endpoint: str,
    layers: dict[str, dict[str, Any]],
    diagnostics: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, str]:
    def action(
        reason: str,
        *,
        command: str | None = None,
        external_change: dict[str, Any] | None = None,
        expected_state: str,
    ) -> dict[str, Any]:
        return {
            "reason": reason,
            "command": command,
            "external_change": external_change,
            "expected_state": expected_state,
        }

    if layers["simulator_server"]["state"] != "reachable":
        return (
            action(
                "simulator_unreachable; this explicit recovery may launch/configure a simulator",
                command=(
                    "./cli/automa simulators ensure "
                    "--scenario chaser-depth-obstacles"
                ),
                expected_state="simulator_server=reachable",
            ),
            "simulator_server",
            "passively attach to Chase",
        )
    if layers["simulator_frontend"]["state"] != "connected":
        return (
            action(
                "frontend_disconnected",
                external_change={
                    "component": "Metrics UI browser frontend",
                    "change": f"Open or reload {operator_url}",
                },
                expected_state="simulator_frontend=connected",
            ),
            "simulator_frontend",
            "passively attach to Chase",
        )
    if layers["chase_game"]["state"] != "ready":
        return (
            action(
                "wrong_game; this explicit recovery changes the selected scenario",
                command=(
                    "./cli/automa simulators ensure "
                    "--scenario chaser-depth-obstacles"
                ),
                expected_state="chase_game=ready",
            ),
            "chase_game",
            "passively attach to Chase",
        )
    capture_error = diagnostics.get("error_code")
    if capture_error in {"capture_identity_invalid", "capture_image_invalid"}:
        return (
            action(
                str(capture_error),
                external_change={
                    "component": "Metrics UI atomic evaluation capture",
                    "failing_path": diagnostics.get("error_path"),
                    "minimum_contract": (
                        "Return the required actor/frame identity and a decodable "
                        "front-camera image without advancing playback."
                    ),
                    "mutation_attempted": False,
                },
                expected_state="sensor_capture=available",
            ),
            "capture",
            "capture a valid front-camera frame",
        )
    if layers["vehicle"]["state"] != "discoverable":
        return (
            action(
                str(diagnostics.get("error_code") or "front_view_unavailable"),
                external_change={
                    "component": "Metrics UI",
                    "missing_capability": "atomic-evaluation-capture for actor chaser",
                    "protocol_evidence": diagnostics.get("error_details")
                    or diagnostics.get("error_path"),
                    "minimum_contract": (
                        "Expose a non-advancing chaser camera capture with required "
                        "frame identity and image fields."
                    ),
                    "endpoint": endpoint,
                },
                expected_state="vehicle=discoverable",
            ),
            "vehicle",
            "capture a front-camera frame",
        )
    if layers["passive_capture"]["state"] != "available":
        return (
            action(
                str(
                    layers["passive_capture"].get("code")
                    or "simulator_capability_missing"
                ),
                external_change={
                    "component": "Metrics UI",
                    "missing_capability": "passive session-preservation proof",
                    "protocol_evidence": layers["passive_capture"].get(
                        "session_preservation"
                    ),
                    "minimum_contract": (
                        "Expose all required session fingerprint fields or a "
                        "preserveSession=true atomic-capture receipt."
                    ),
                    "mutation_attempted": False,
                },
                expected_state="passive_capture=available",
            ),
            "passive_capture",
            "run observation-only automation",
        )
    deployment = layers["automation_deployment"].get("details") or {}
    problems = deployment.get("activation_problems") or []
    if problems:
        return (
            action(
                "step_activation_invalid",
                command=problems[0]["command"],
                expected_state="automation_deployment=deployed",
            ),
            "automation_deployment",
            "run observation-only automation",
        )
    if layers["automation_deployment"]["state"] != "deployed":
        return (
            action(
                "automation_not_deployed",
                command=f"./cli/automa vehicles update host --id {vehicle_id}",
                expected_state="automation_deployment=deployed",
            ),
            "automation_deployment",
            "run observation-only automation",
        )
    if layers["automation_worker"]["state"] == "error":
        return (
            action(
                "worker_start_failed",
                command=(
                    "./cli/automa vehicles automation run "
                    f"--id {vehicle_id} --observe-only --num-decisions 0 --open-view --log"
                ),
                expected_state="automation_worker=running",
            ),
            "automation_worker",
            "inspect perception",
        )
    if layers["automation_worker"]["state"] != "running":
        return (
            action(
                "worker_stopped",
                command=(
                    "./cli/automa vehicles automation run "
                    f"--id {vehicle_id} --observe-only --num-decisions 0 --open-view"
                ),
                expected_state="automation_worker=running, perception_view=available",
            ),
            "automation_worker",
            "inspect perception",
        )
    if layers["perception_view"]["state"] != "available":
        view_details = (
            layers["perception_view"].get("details")
            if isinstance(layers["perception_view"].get("details"), dict)
            else {}
        )
        # Live worker still publishing: the view becomes available without a restart.
        if (
            layers["automation_worker"]["state"] == "running"
            and view_details.get("status") == "warming"
        ):
            return (
                action("view_warming", expected_state="perception_view=available"),
                "perception_view",
                "inspect perception",
            )
        return (
            action(
                "view_stale"
                if layers["perception_view"]["state"] == "stale"
                else "view_unavailable",
                command=(
                    "./cli/automa vehicles automation restart "
                    f"--id {vehicle_id} --observe-only --num-decisions 0"
                ),
                expected_state="perception_view=available",
            ),
            "perception_view",
            "inspect perception",
        )
    return None, None, "inspect perception and stop automation"


def _format_vehicle(index: int, vehicle: dict[str, Any]) -> list[str]:
    provider = vehicle.get("provider", "unknown")
    vehicle_id = vehicle.get("vehicle_id", "unknown")
    kind = vehicle.get("vehicle_kind", "unknown")
    connection = vehicle.get("connection") if isinstance(vehicle.get("connection"), dict) else {}
    status = vehicle.get("status") if isinstance(vehicle.get("status"), dict) else {}
    capabilities = vehicle.get("capabilities") if isinstance(vehicle.get("capabilities"), dict) else {}

    lines = [
        "",
        f"{index}. {vehicle_id} ({provider})",
        f"   id: {vehicle_id}",
        f"   kind: {kind}",
        f"   endpoint: {_connection_label(connection)}",
    ]

    mode = status.get("drive_mode")
    runtime = status.get("runtime")
    if isinstance(runtime, dict) and runtime.get("state"):
        lines.append(f"   runtime: {runtime['state']}")
    if mode is not None:
        lines.append(f"   mode: {mode}")

    autonomy = status.get("host")
    if isinstance(autonomy, dict):
        steps = autonomy.get("steps") if isinstance(autonomy.get("steps"), dict) else {}
        action = steps.get("action") if isinstance(steps.get("action"), dict) else {}
        action_plugins = action.get("plugin_ids") if isinstance(action.get("plugin_ids"), list) else []
        last_control = autonomy.get("last_control")
        reason = None
        if isinstance(last_control, dict):
            reason = last_control.get("reason")
        autonomy_line = f"   autonomy: action={','.join(action_plugins) or 'unknown'}"
        if reason:
            autonomy_line += f" ({reason})"
        lines.append(autonomy_line)

    metrics_ui = status.get("metrics_ui")
    if isinstance(metrics_ui, dict):
        scenario = metrics_ui.get("scenario")
        control_source = metrics_ui.get("chaser_control_source")
        playback = metrics_ui.get("playback") if isinstance(metrics_ui.get("playback"), dict) else {}
        playback_state = "playing" if playback.get("isPlaying") else "paused"
        sim_bits = []
        if scenario:
            sim_bits.append(f"scenario={scenario}")
        if control_source:
            sim_bits.append(f"control={control_source}")
        sim_bits.append(playback_state)
        lines.append(f"   sim: {', '.join(sim_bits)}")
    passive = (
        status.get("passive_capture")
        if isinstance(status.get("passive_capture"), dict)
        else {}
    )
    if passive:
        evaluator = (
            passive.get("evaluator_reference")
            if isinstance(passive.get("evaluator_reference"), dict)
            else {}
        )
        lines.append(
            "   passive capture: "
            f"{passive.get('status', 'unknown')}; "
            f"evaluator reference={evaluator.get('status', 'unavailable')}"
        )

    sensor_line = _sensor_summary(capabilities)
    if sensor_line:
        lines.append(f"   sensors: {sensor_line}")

    return lines


def _connection_label(connection: dict[str, Any]) -> str:
    if "base_url" in connection:
        source = connection.get("source")
        endpoint = connection.get("status_endpoint")
        label = str(connection["base_url"])
        if endpoint:
            label += f" {endpoint}"
        if source:
            label += f" [{source}]"
        return label
    if "ws_url" in connection:
        source = connection.get("source")
        label = str(connection["ws_url"])
        if source:
            label += f" [{source}]"
        return label
    return "unknown"


def _sensor_summary(capabilities: dict[str, Any]) -> str:
    sensors = capabilities.get("sensors")
    if not isinstance(sensors, dict) or not sensors:
        return ""
    labels: list[str] = []
    for sensor_id, sensor in sensors.items():
        if not isinstance(sensor, dict):
            labels.append(str(sensor_id))
            continue
        kind = sensor.get("sensor_kind", "sensor")
        pose = sensor.get("pose")
        label = f"{sensor_id}:{kind}"
        if pose:
            label += f"/{pose}"
        labels.append(label)
    return ", ".join(labels)


def _probe_candidate(candidate: Candidate, *, timeout_s: float) -> ProbeResult:
    if candidate.provider == "picar":
        return _probe_picar(candidate, timeout_s=timeout_s)
    if candidate.provider == "chase-sim":
        return _probe_chase(candidate, timeout_s=timeout_s)
    return ProbeResult(
        active=False,
        candidate=candidate,
        error=f"unknown provider {candidate.provider!r}",
    )


def _picar_candidates(extra_urls: tuple[str, ...]) -> list[Candidate]:
    candidates: list[Candidate] = []
    env_url = os.environ.get(LOCAL_CAR_BASE_URL_ENV)
    if env_url:
        candidates.append(Candidate("picar", _normalize_http_url(env_url), f"env:{LOCAL_CAR_BASE_URL_ENV}"))

    candidates.append(Candidate("picar", _normalize_http_url(DEFAULT_LOCAL_CAR_BASE_URL), "default"))

    for url in extra_urls:
        if url.strip():
            candidates.append(Candidate("picar", _normalize_http_url(url), "cli"))

    return _dedupe_candidates(candidates)


def _chase_candidates(extra_urls: tuple[str, ...]) -> list[Candidate]:
    candidates: list[Candidate] = []
    env_url = os.environ.get(CHASE_UI_WS_URL_ENV)
    if env_url:
        candidates.append(
            Candidate(
                "chase-sim",
                normalize_chase_url(env_url),
                f"env:{CHASE_UI_WS_URL_ENV}",
            )
        )

    candidates.append(
        Candidate("chase-sim", normalize_chase_url(DEFAULT_CHASE_UI_WS_URL), "default")
    )
    for url in extra_urls:
        if url.strip():
            candidates.append(Candidate("chase-sim", normalize_chase_url(url), "cli"))

    return _dedupe_candidates(candidates)


def _dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, str]] = set()
    unique: list[Candidate] = []
    for candidate in candidates:
        key = (candidate.provider, candidate.url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _normalize_http_url(url: str) -> str:
    value = url.strip().rstrip("/")
    if "://" not in value:
        value = f"http://{value}"
    return value


def normalize_chase_url(url: str) -> str:
    """Normalize an operator HTTP/WS Metrics UI URL to its control WebSocket."""

    value = url.strip()
    if not value:
        raise ValueError("Chase URL must not be empty")
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https", "ws", "wss"}:
        raise ValueError(
            "Chase URL scheme must be http, https, ws, or wss "
            f"(got {parsed.scheme or 'none'!r})"
        )
    if not parsed.netloc:
        raise ValueError(f"Chase URL has no host: {url!r}")

    scheme = {
        "http": "ws",
        "https": "wss",
        "ws": "ws",
        "wss": "wss",
    }[parsed.scheme]
    path = parsed.path
    if parsed.scheme in {"http", "https"} or path in {"", "/"}:
        path = "/ws/control"
    return urlunparse((scheme, parsed.netloc, path, "", parsed.query, ""))


def _require_positive_timeout(value: float) -> float:
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite number greater than zero")
    return timeout


def chase_operator_url(ws_url: str) -> str:
    """Return the reloadable browser origin for a normalized Chase endpoint."""

    parsed = urlparse(ws_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunparse((scheme, parsed.netloc, "/", "", "", ""))


def _probe_picar(candidate: Candidate, *, timeout_s: float) -> ProbeResult:
    deadline = time.monotonic() + timeout_s
    base_url = candidate.url.rstrip("/")
    car = create_picar(base_url=base_url, timeout_s=timeout_s)
    capabilities = car.capabilities.to_dict()

    status, error = _get_json(base_url, "/host/status", timeout_s=timeout_s)
    if status is not None:
        return ProbeResult(
            active=True,
            candidate=candidate,
            vehicle={
                "vehicle_id": capabilities["vehicle_id"],
                "vehicle_kind": capabilities["vehicle_kind"],
                "provider": "picar",
                "connection": {
                    "base_url": base_url,
                    "status_endpoint": "/host/status",
                    "source": candidate.source,
                },
                "capabilities": capabilities,
                "status": {
                    **status,
                    "runtime": {
                        "state": "ready",
                        "tcp_listener": True,
                        "http_ready": True,
                    },
                },
            },
            diagnostics={"runtime_state": "ready", "tcp_listener": True, "http_ready": True},
        )

    diagnostics = _probe_tcp_endpoint(base_url, timeout_s=max(0.001, deadline - time.monotonic()))
    runtime_state = diagnostics.get("runtime_state")
    if runtime_state == "server_not_listening":
        probe_error = (
            f"PiCar host resolved, but its server is not listening: "
            f"{diagnostics.get('tcp_error', 'connection refused')}"
        )
    elif runtime_state == "http_unhealthy":
        probe_error = f"PiCar TCP listener is reachable, but HTTP readiness failed: {error}"
    else:
        probe_error = error or "no PiCar endpoint responded"
    return ProbeResult(
        active=False,
        candidate=candidate,
        error=probe_error,
        diagnostics=diagnostics,
    )


def _probe_chase(candidate: Candidate, *, timeout_s: float) -> ProbeResult:
    car = ChaseSimCar(ws_url=candidate.url, timeout_s=timeout_s)
    diagnostics: dict[str, Any] = {
        "ws_server": False,
        "frontend_connected": False,
        "chase_loaded": False,
        "front_view_ready": False,
        "passive_capture": "unavailable",
        "timeout_s": timeout_s,
    }
    started = time.monotonic()

    try:
        passive = car.inspect_passive_capture(timeout_s=timeout_s)
    except ChaseCaptureValidationError as exc:
        diagnostics.update(
            {
                "ws_server": True,
                "frontend_connected": True,
                "chase_loaded": True,
                "error_code": exc.code,
                "error_path": exc.path,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        )
        return ProbeResult(
            active=False,
            candidate=candidate,
            error=str(exc),
            diagnostics=diagnostics,
        )
    except ChasePassiveCaptureError as exc:
        completed = set(
            exc.details.get("phases", {})
            if isinstance(exc.details.get("phases"), dict)
            else {}
        )
        # Frontend delivery failures (absent / unresponsive / mid-request drop)
        # always block at simulator_frontend even if an earlier phase completed.
        frontend_delivery_failure = exc.code == "frontend_disconnected"
        diagnostics.update(
            {
                "ws_server": exc.code != "simulator_unreachable",
                "frontend_connected": (
                    not frontend_delivery_failure
                    and exc.code != "simulator_unreachable"
                    and "debug_before" in completed
                ),
                "chase_loaded": (
                    not frontend_delivery_failure
                    and exc.code
                    not in {"simulator_unreachable", "frontend_disconnected", "wrong_game"}
                ),
                "error_code": exc.code,
                "error_details": exc.details,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        )
        recovery = {
            "simulator_unreachable": (
                "No passive session exists. Explicit configuration-changing option: "
                "./cli/automa simulators ensure --scenario chaser-depth-obstacles"
            ),
            "frontend_disconnected": (
                f"Open or reload {chase_operator_url(candidate.url)}, then rerun discovery."
            ),
            "wrong_game": (
                "The current game was preserved. Select Chase yourself, or explicitly "
                "run ./cli/automa simulators ensure --scenario chaser-depth-obstacles."
            ),
        }.get(exc.code)
        return ProbeResult(
            active=False,
            candidate=candidate,
            error=f"{exc.detail} Recovery: {recovery}" if recovery else exc.detail,
            diagnostics=diagnostics,
        )

    environment = (
        passive.get("environment")
        if isinstance(passive.get("environment"), dict)
        else {}
    )
    sensor = passive.get("sensor") if isinstance(passive.get("sensor"), dict) else {}
    diagnostics.update(
        {
            "ws_server": True,
            "frontend_connected": True,
            "chase_loaded": environment.get("game_id") == "chase",
            "front_view_ready": True,
            "passive_capture": passive.get("status"),
            "error_code": passive.get("code"),
            "game_id": environment.get("game_id"),
            "frame_index": sensor.get("simulator_frame_index"),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "phases": passive.get("phases"),
        }
    )

    capabilities = car.capabilities.to_dict()
    return ProbeResult(
        active=True,
        candidate=candidate,
        vehicle={
            "vehicle_id": capabilities["vehicle_id"],
            "vehicle_kind": capabilities["vehicle_kind"],
            "provider": "chase-sim",
            "connection": {
                "ws_url": candidate.url,
                "source": candidate.source,
            },
            "capabilities": capabilities,
            "status": {
                "ok": True,
                "metrics_ui": {
                    "playback": environment.get("playback"),
                    "scenario": environment.get("scenario_id"),
                    "chaser_control_source": environment.get("control_source"),
                    "game_id": environment.get("game_id"),
                    "simulation_epoch": environment.get("simulation_epoch"),
                    "frame_index": sensor.get("simulator_frame_index"),
                    "front_view_ready": True,
                },
                "passive_capture": {
                    key: value
                    for key, value in passive.items()
                    if key not in {"image"}
                },
            },
        },
        diagnostics=diagnostics,
    )


def _inactive_detail(diagnostics: dict[str, Any]) -> str:
    parts: list[str] = []
    runtime_state = diagnostics.get("runtime_state")
    if runtime_state is not None:
        parts.append(f"runtime={runtime_state}")
    if "tcp_listener" in diagnostics:
        parts.append(f"tcp={'ok' if diagnostics.get('tcp_listener') else 'no'}")
    if "http_ready" in diagnostics:
        parts.append(f"http={'ok' if diagnostics.get('http_ready') else 'no'}")
    for key, label in (
        ("ws_server", "ws"),
        ("frontend_connected", "frontend"),
        ("chase_loaded", "chase"),
        ("front_view_ready", "front-view"),
    ):
        if key in diagnostics:
            parts.append(f"{label}={'ok' if diagnostics.get(key) else 'no'}")
    game_id = diagnostics.get("game_id")
    if game_id is not None:
        parts.append(f"game={game_id!r}")
    return ", ".join(parts)


def _probe_tcp_endpoint(base_url: str, *, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    diagnostics: dict[str, Any] = {
        "runtime_state": "endpoint_unreachable",
        "tcp_listener": False,
        "http_ready": False,
    }
    if not host:
        diagnostics["tcp_error"] = "endpoint has no hostname"
        return diagnostics

    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        diagnostics["tcp_error"] = str(exc)
        return diagnostics

    addresses = sorted(addresses, key=lambda item: 0 if item[0] == socket.AF_INET else 1)
    seen: set[tuple[int, tuple[Any, ...]]] = set()
    last_error = "no address available"
    for family, socktype, protocol, _, sockaddr in addresses:
        key = (family, sockaddr)
        if key in seen:
            continue
        seen.add(key)
        try:
            with socket.socket(family, socktype, protocol) as connection:
                connection.settimeout(max(0.001, deadline - time.monotonic()))
                connection.connect(sockaddr)
            diagnostics.update(
                {
                    "runtime_state": "http_unhealthy",
                    "tcp_listener": True,
                    "tcp_address": str(sockaddr[0]),
                }
            )
            return diagnostics
        except ConnectionRefusedError as exc:
            diagnostics.update(
                {
                    "runtime_state": "server_not_listening",
                    "tcp_address": str(sockaddr[0]),
                    "tcp_error": str(exc),
                }
            )
            return diagnostics
        except OSError as exc:
            last_error = str(exc)

    diagnostics["tcp_error"] = last_error
    return diagnostics


def _get_json(base_url: str, endpoint: str, *, timeout_s: float) -> tuple[dict[str, Any] | None, str | None]:
    ok, body_or_error = _get(base_url, endpoint, timeout_s=timeout_s)
    if not ok:
        return None, body_or_error
    try:
        data = json.loads(body_or_error)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON from {endpoint}: {exc}"
    if not isinstance(data, dict):
        return None, f"expected JSON object from {endpoint}"
    return data, None


def _get(base_url: str, endpoint: str, *, timeout_s: float) -> tuple[bool, str]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,text/html,*/*",
            "User-Agent": "automa/0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read()
            return True, body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        exc.close()
        return False, f"GET {url} returned HTTP {exc.code}"
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            return False, f"GET {url} did not answer within {timeout_s:g} s"
        return False, f"GET {url} failed: {exc.reason}"
    except TimeoutError:
        return False, f"GET {url} did not answer within {timeout_s:g} s"
