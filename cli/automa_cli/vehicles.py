from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from implementations.vehicle.chase_sim import ChaseSimCar
from implementations.vehicle.chase_sim.defaults import (
    CHASE_UI_WS_URL_ENV,
    DEFAULT_CHASE_UI_WS_URL,
)
from implementations.vehicle.chase_sim.metrics_ws import (
    MetricsUiWebSocketError,
    MetricsUiWsClient,
)
from implementations.vehicle.picar import create_local_car
from implementations.vehicle.picar.defaults import (
    DEFAULT_LOCAL_CAR_BASE_URL,
    LOCAL_CAR_BASE_URL_ENV,
)


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
    timeout_s: float = 1.0,
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

    timeout = max(0.1, float(timeout_s))
    candidates: list[Candidate] = []
    if include_picar:
        candidates.extend(_picar_candidates(picar_urls))
    if include_chase_sim:
        candidates.extend(_chase_sim_candidates(chase_ws_urls))

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


def format_active_vehicles_snapshot(
    payload: dict[str, Any],
    *,
    include_inactive: bool = False,
) -> str:
    lines = [
        f"Active vehicles: {payload.get('active_count', 0)}",
    ]
    vehicles = payload.get("vehicles")
    if not isinstance(vehicles, list) or not vehicles:
        lines.append("No active vehicles discovered.")
    else:
        for index, vehicle in enumerate(vehicles, start=1):
            if not isinstance(vehicle, dict):
                continue
            lines.extend(_format_vehicle(index, vehicle))

    if include_inactive:
        inactive = payload.get("inactive")
        if isinstance(inactive, list) and inactive:
            lines.append("")
            lines.append(f"Inactive candidates: {len(inactive)}")
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


def find_vehicle_by_id(
    payload: dict[str, Any],
    vehicle_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    vehicles = payload.get("vehicles")
    if not isinstance(vehicles, list):
        return None, "Discovery payload does not contain a vehicle list."

    matches = [
        vehicle
        for vehicle in vehicles
        if isinstance(vehicle, dict) and vehicle.get("vehicle_id") == vehicle_id
    ]
    if not matches:
        active_ids = [
            str(vehicle.get("vehicle_id"))
            for vehicle in vehicles
            if isinstance(vehicle, dict) and vehicle.get("vehicle_id") is not None
        ]
        suffix = f" Active ids: {', '.join(active_ids)}." if active_ids else ""
        return None, f"Vehicle {vehicle_id!r} was not found among active vehicles.{suffix}"

    providers = sorted({str(match.get("provider")) for match in matches})
    if len(matches) > 1 and len(providers) > 1:
        return None, (
            f"Vehicle id {vehicle_id!r} matched multiple providers: {', '.join(providers)}. "
            "Use a unique vehicle id before updating."
        )

    return matches[0], None


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

    autonomy = status.get("autonomy")
    if isinstance(autonomy, dict):
        engine = autonomy.get("engine")
        last_control = autonomy.get("last_control")
        reason = None
        if isinstance(last_control, dict):
            reason = last_control.get("reason")
        engine_line = f"   autonomy: {engine or 'unknown'}"
        if reason:
            engine_line += f" ({reason})"
        lines.append(engine_line)

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
        return _probe_chase_sim(candidate, timeout_s=timeout_s)
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
    candidates.append(Candidate("picar", "http://127.0.0.1:8887", "local-pi"))

    for url in extra_urls:
        if url.strip():
            candidates.append(Candidate("picar", _normalize_http_url(url), "cli"))

    return _dedupe_candidates(candidates)


def _chase_sim_candidates(extra_urls: tuple[str, ...]) -> list[Candidate]:
    candidates: list[Candidate] = []
    env_url = os.environ.get(CHASE_UI_WS_URL_ENV)
    if env_url:
        candidates.append(Candidate("chase-sim", env_url.strip(), f"env:{CHASE_UI_WS_URL_ENV}"))

    candidates.append(Candidate("chase-sim", DEFAULT_CHASE_UI_WS_URL, "default"))
    for url in extra_urls:
        if url.strip():
            candidates.append(Candidate("chase-sim", url.strip(), "cli"))

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


def _probe_picar(candidate: Candidate, *, timeout_s: float) -> ProbeResult:
    base_url = candidate.url.rstrip("/")
    car = create_local_car(base_url=base_url, timeout_s=timeout_s)
    capabilities = car.capabilities.to_dict()

    status, error = _get_json(base_url, "/autonomy/status", timeout_s=timeout_s)
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
                    "status_endpoint": "/autonomy/status",
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

    diagnostics = _probe_tcp_endpoint(base_url, timeout_s=timeout_s)
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


def _probe_chase_sim(candidate: Candidate, *, timeout_s: float) -> ProbeResult:
    car = ChaseSimCar(ws_url=candidate.url, timeout_s=timeout_s)
    client = MetricsUiWsClient(candidate.url, timeout_s=timeout_s)
    diagnostics: dict[str, Any] = {
        "ws_server": False,
        "frontend_connected": False,
        "chase_loaded": False,
        "front_view_ready": False,
    }

    try:
        state = client.get_state(timeout_s=timeout_s)
    except (MetricsUiWebSocketError, OSError, TimeoutError, ValueError) as exc:
        return ProbeResult(
            active=False,
            candidate=candidate,
            error=f"WS server unavailable: {exc}",
            diagnostics=diagnostics,
        )

    diagnostics["ws_server"] = True
    diagnostics["metrics_ui"] = _summarize_chase_state(state)

    try:
        debug = client.get_play_debug(timeout_s=timeout_s)
    except (MetricsUiWebSocketError, OSError, TimeoutError, ValueError) as exc:
        return ProbeResult(
            active=False,
            candidate=candidate,
            error=f"WS server reachable, but Chase Play frontend is not connected: {exc}",
            diagnostics=diagnostics,
        )

    diagnostics["frontend_connected"] = True
    diagnostics["game_id"] = debug.get("gameId")
    diagnostics["frame_index"] = debug.get("frameIndex")
    if debug.get("gameId") != "chase":
        return ProbeResult(
            active=False,
            candidate=candidate,
            error=f"frontend connected, but active game is {debug.get('gameId')!r}, not 'chase'",
            diagnostics=diagnostics,
        )
    diagnostics["chase_loaded"] = True

    try:
        snapshot = client.get_play_front_view_snapshot(width=16, height=12, timeout_s=timeout_s)
    except (MetricsUiWebSocketError, OSError, TimeoutError, ValueError) as exc:
        return ProbeResult(
            active=False,
            candidate=candidate,
            error=f"Chase frontend is connected, but front-view capture is unavailable: {exc}",
            diagnostics=diagnostics,
        )
    diagnostics["front_view_ready"] = True
    diagnostics["front_view"] = _summarize_front_view_snapshot(snapshot)

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
                    **_summarize_chase_state(state),
                    "game_id": debug.get("gameId"),
                    "frame_index": debug.get("frameIndex"),
                    "front_view_ready": True,
                },
            },
        },
    )


def _summarize_chase_state(state: dict[str, Any]) -> dict[str, Any]:
    sidebar = _find_play_sidebar_values(state)
    return {
        "sidebar_app": state.get("sidebarApp"),
        "playback": state.get("playback"),
        "viewport": state.get("viewport"),
        "scenario": sidebar.get("scenario-select"),
        "chaser_control_source": sidebar.get("chaser-control-source"),
    }


def _summarize_front_view_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    image = snapshot.get("image") if isinstance(snapshot.get("image"), dict) else {}
    return {
        "has_data_url": isinstance(image.get("dataUrl"), str),
        "has_svg": isinstance(image.get("svg"), str),
        "width": snapshot.get("width"),
        "height": snapshot.get("height"),
    }


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
                connection.settimeout(max(0.1, float(timeout_s)))
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


def _find_play_sidebar_values(state: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    sections = state.get("playSidebarSections")
    if not isinstance(sections, list):
        return values
    for section in sections:
        if not isinstance(section, dict):
            continue
        rows = section.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            if isinstance(row_id, str) and "value" in row:
                values[row_id] = row.get("value")
    return values


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
        return False, f"GET {url} returned HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, f"GET {url} failed: {exc.reason}"
    except TimeoutError:
        return False, f"GET {url} timed out"
