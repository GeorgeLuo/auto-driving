"""How automa probes a vehicle: a simulator over Metrics UI, a PiCar over HTTP and TCP."""

from __future__ import annotations

import json
import math
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from implementations.vehicle.chase_sim import (
    ChaseCaptureValidationError,
    ChasePassiveCaptureError,
    ChaseSimCar,
)
from implementations.vehicle.chase_sim.car import FRONTEND_DELIVERY_PROTOCOL_CODES
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError, MetricsUiWsClient
from implementations.vehicle.picar import create_picar

DEFAULT_READINESS_TIMEOUT_S = 5.0


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


def _simulator_status(endpoint: str, remaining: Callable[[], float]) -> dict[str, Any]:
    """One simulator's state; listing its agents is the second query that shows it runs."""

    client =MetricsUiWsClient(endpoint, timeout_s=remaining())
    status: dict[str, Any] = {
        "provider": "chase-sim",
        "url": chase_operator_url(endpoint),
        "state": "unreachable",
        "error": None,
        "frontend": None,
        "game": None,
        "pid": None,
    }
    failures = (OSError, ValueError, MetricsUiWebSocketError)
    with ThreadPoolExecutor(max_workers=1) as pool:
        agents = pool.submit(client.list_agents, timeout_s=remaining())
        state_error = listed = None
        try:
            client.get_state(timeout_s=remaining())
        except failures as exc:
            state_error = exc
        try:
            listed = agents.result()
        except failures:
            pass
    # A server that answers either query is running; without a browser,
    # get_state fails with a frontend delivery code.
    frontend_error = state_error if _frontend_code(state_error) else None
    if state_error is None or frontend_error is not None or listed is not None:
        status["state"] = "running"
    if state_error is not None and frontend_error is None:
        status["error"] = str(state_error)
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
    return status


def _picar_status(url: str, remaining: Callable[[], float]) -> dict[str, Any]:
    probe = _probe_picar(Candidate("picar", url, "discovered"), timeout_s=remaining())
    return {"provider": "picar", "url": url, "state": _picar_state(probe), "error": probe.error}


def _picar_state(probe: ProbeResult) -> str:
    if probe.active:
        return "answering"
    reachable = probe.diagnostics.get("runtime_state") in {"server_not_listening", "http_unhealthy"}
    return "reachable, no host listening" if reachable else "unreachable"


def _frontend_code(exc: BaseException | None) -> str | None:
    """The code Metrics UI fails a query with when no browser frontend answers it."""

    code = getattr(exc, "code", None)
    return code if code in FRONTEND_DELIVERY_PROTOCOL_CODES else None


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
