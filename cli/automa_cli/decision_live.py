"""Small read-only browser monitor for a live physical shadow decision."""

from __future__ import annotations

import base64
import math
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

from .decision import CommandResult
from .loopback_http import (
    LoopbackHTTPRequestHandler,
    LoopbackHTTPServer,
    start_server_thread,
    stop_server_thread,
)
from .physical_observation import (
    PhysicalDecisionPublicationError,
    fetch_decision_publication,
    fetch_observation_frame,
    frame_id_from_headers,
    normalize_physical_decision_publication,
    picar_base_url,
)
from .vehicles import (
    discover_active_vehicles,
    find_vehicle_by_id,
    format_active_vehicles_snapshot,
)


LIVE_MONITOR_SCHEMA = "automa_live_decision_monitor_v0"
LIVE_VIEW_HTML_PATH = Path(__file__).with_name("decision_live_view.html")


def _valid_bbox(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(part) and 0.0 <= part <= 1.0 for part in bbox):
        return None
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _source_ref_matches(thing_id: object, source_refs: list[dict[str, Any]]) -> bool:
    if not isinstance(thing_id, str) or not thing_id:
        return False
    for source_ref in source_refs:
        ref_id = source_ref.get("id") if isinstance(source_ref, dict) else None
        if not isinstance(ref_id, str):
            continue
        if ref_id == thing_id or ref_id.endswith(f":{thing_id}"):
            return True
    return False


def _current_evidence(
    source: dict[str, Any],
    source_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    observation = source.get("observation")
    observation = observation if isinstance(observation, dict) else {}
    value = observation.get("value")
    value = value if isinstance(value, dict) else {}
    things = value.get("things")
    if not isinstance(things, list):
        return []

    evidence: list[dict[str, Any]] = []
    supported_kinds = {"floor_boundary", "obstacle", "obstruction_evidence"}
    for thing in things:
        if not isinstance(thing, dict) or thing.get("kind") not in supported_kinds:
            continue
        location = thing.get("location")
        if not isinstance(location, dict) or location.get("frame") != "image":
            continue
        bbox = _valid_bbox(location.get("bbox_xyxy_norm"))
        if bbox is None:
            continue
        thing_id = thing.get("thing_id")
        evidence.append(
            {
                "thing_id": thing_id,
                "kind": thing.get("kind"),
                "label": thing.get("label"),
                "zone": location.get("zone"),
                "confidence": thing.get("confidence"),
                "bbox_xyxy_norm": bbox,
                "selected": _source_ref_matches(thing_id, source_refs),
            }
        )
    return evidence


def _live_pair(
    base_url: str,
    *,
    vehicle_id: str,
    timeout_s: float,
) -> tuple[dict[str, Any], str | None, str | None, str | None]:
    """Read image first, then decision, and retain only a matching pair."""

    deadline = time.monotonic() + min(1.0, max(0.2, float(timeout_s)))
    last_error: str | None = None
    last_normalized: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            image_bytes, image_headers = fetch_observation_frame(
                base_url,
                timeout_s=timeout_s,
            )
        except ConnectionError as exc:
            last_error = str(exc)
            time.sleep(0.04)
            continue
        image_frame_id = frame_id_from_headers(image_headers)
        try:
            raw = fetch_decision_publication(base_url, timeout_s=timeout_s)
            normalized = normalize_physical_decision_publication(
                raw,
                vehicle_id=vehicle_id,
                now_ms=int(time.time() * 1000),
            )
        except PhysicalDecisionPublicationError:
            raise
        except (ConnectionError, OSError, TimeoutError) as exc:
            last_error = str(exc)
            time.sleep(0.04)
            continue

        last_normalized = normalized
        expected_frame_id = normalized["frame_id"]
        if image_frame_id == expected_frame_id:
            return (
                normalized,
                base64.b64encode(image_bytes).decode("ascii"),
                image_frame_id,
                None,
            )
        last_error = (
            f"image frame {image_frame_id!r} did not match decision frame "
            f"{expected_frame_id!r}"
        )
        time.sleep(0.04)

    if last_normalized is not None:
        return last_normalized, None, None, last_error or "matched image unavailable"
    raise ConnectionError(last_error or "live decision publication unavailable")


def _unavailable_payload(vehicle_id: str, reason: str, message: str) -> dict[str, Any]:
    return {
        "schema": LIVE_MONITOR_SCHEMA,
        "ok": False,
        "status": "unavailable",
        "vehicle_id": vehicle_id,
        "reason": reason,
        "message": message,
        "publication": None,
        "evidence": [],
        "image": {"status": "unavailable", "frame_id": None, "jpeg_base64": None},
    }


class LiveDecisionMonitor:
    """Fetch and expose one read-only live decision snapshot per browser poll."""

    def __init__(self, *, vehicle_id: str, base_url: str, timeout_s: float = 2.0) -> None:
        self.vehicle_id = vehicle_id
        self.base_url = base_url.rstrip("/")
        self.timeout_s = max(0.1, float(timeout_s))

    def payload(self) -> dict[str, Any]:
        try:
            normalized, image_base64, image_frame_id, image_error = _live_pair(
                self.base_url,
                vehicle_id=self.vehicle_id,
                timeout_s=self.timeout_s,
            )
        except PhysicalDecisionPublicationError as exc:
            return _unavailable_payload(
                self.vehicle_id,
                exc.reason,
                exc.message_text,
            )
        except (ConnectionError, OSError, TimeoutError) as exc:
            return _unavailable_payload(
                self.vehicle_id,
                "unavailable",
                str(exc),
            )

        publication = normalized["publication"]
        decision = normalized["decision"]
        cycle = decision.get("cycle") if isinstance(decision.get("cycle"), dict) else {}
        source = cycle.get("source") if isinstance(cycle.get("source"), dict) else {}
        plan = cycle.get("plan") if isinstance(cycle.get("plan"), dict) else {}
        candidates = plan.get("candidates") if isinstance(plan.get("candidates"), list) else []
        selected_id = plan.get("selected_proposal_id")
        candidate = next(
            (
                item
                for item in candidates
                if isinstance(item, dict) and item.get("proposal_id") == selected_id
            ),
            next((item for item in candidates if isinstance(item, dict)), None),
        )
        authority = cycle.get("authority") if isinstance(cycle.get("authority"), dict) else {}
        source_refs = (
            candidate.get("source_refs")
            if isinstance(candidate, dict) and isinstance(candidate.get("source_refs"), list)
            else []
        )
        return {
            "schema": LIVE_MONITOR_SCHEMA,
            "ok": True,
            "status": "ready",
            "vehicle_id": self.vehicle_id,
            "freshness": {
                "result_age_ms": normalized["result_age_ms"],
                "stale_after_ms": normalized["max_age_ms"],
            },
            "publication": publication,
            "signals": {
                "frame_id": normalized["frame_id"],
                "source_id": normalized["source_id"],
                "run_id": normalized["run_id"],
                "generation_id": normalized["generation_id"],
                "activation_engine_id": normalized["activation_engine_id"],
                "plan": {
                    "status": plan.get("status"),
                    "selector_id": plan.get("selector_id"),
                    "selected_proposal_id": selected_id,
                    "candidate": candidate,
                },
                "authority": authority,
            },
            "evidence": _current_evidence(source, source_refs),
            "image": {
                "status": "ready" if image_base64 is not None else "unavailable",
                "frame_id": image_frame_id,
                "jpeg_base64": image_base64,
                "error": image_error,
            },
        }


class _LiveDecisionHTTPServer(LoopbackHTTPServer):
    monitor: LiveDecisionMonitor


class _LiveDecisionHandler(LoopbackHTTPRequestHandler):
    server: _LiveDecisionHTTPServer

    def do_GET(self) -> None:
        self._handle(include_body=True)

    def do_HEAD(self) -> None:
        self._handle(include_body=False)

    def _handle(self, *, include_body: bool) -> None:
        route = urlparse(self.path).path
        if route in {"/", "/index.html"}:
            try:
                body = LIVE_VIEW_HTML_PATH.read_bytes()
            except OSError as exc:
                self._send_json(500, {"error": str(exc)}, include_body=include_body)
                return
            self._send(200, body, "text/html; charset=utf-8", include_body=include_body)
            return
        if route == "/api/live":
            self._send_json(
                200,
                self.server.monitor.payload(),
                include_body=include_body,
            )
            return
        if route == "/favicon.ico":
            self._send(204, b"", "image/x-icon", include_body=False)
            return
        self._send_json(404, {"error": "not found"}, include_body=include_body)


class LiveDecisionMonitorServer:
    def __init__(self, monitor: LiveDecisionMonitor, *, port: int = 0) -> None:
        self.monitor = monitor
        self._httpd = _LiveDecisionHTTPServer.bind_with_ephemeral_fallback(
            host="127.0.0.1",
            preferred_port=int(port),
            handler=_LiveDecisionHandler,
        )
        self._httpd.monitor = monitor
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return self._httpd.loopback_url

    def start(self) -> "LiveDecisionMonitorServer":
        self._thread = start_server_thread(
            self._httpd,
            name=f"automa-live-decision-{self.monitor.vehicle_id}",
        )
        return self

    def stop(self) -> None:
        stop_server_thread(self._httpd, self._thread)


@dataclass(frozen=True)
class _ResolvedPhysicalVehicle:
    vehicle_id: str
    base_url: str


def _resolve_physical_vehicle(
    vehicle_id: str,
    *,
    timeout_s: float,
) -> tuple[_ResolvedPhysicalVehicle | None, str | None]:
    discovery = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=False,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
    if error:
        return None, "\n\n".join(
            [error, "Discovery snapshot:", format_active_vehicles_snapshot(discovery, include_inactive=True)]
        )
    if vehicle is None:
        return None, f"Vehicle {vehicle_id!r} was not found."
    if vehicle.get("provider") != "picar":
        return None, f"Live decision monitor supports PiCar only; got {vehicle.get('provider')!r}."
    base_url = picar_base_url(vehicle)
    if not base_url:
        return None, f"Vehicle {vehicle_id!r} has no physical base URL."
    return _ResolvedPhysicalVehicle(vehicle_id, base_url), None


def run_live_decision_monitor(
    *,
    vehicle_id: str,
    port: int = 0,
    open_browser: bool = False,
    timeout_s: float = 2.0,
    output: TextIO | None = None,
) -> CommandResult:
    """Serve a local read-only live monitor until Ctrl-C."""

    if not 0 <= int(port) <= 65535:
        return CommandResult(2, "--port must be between 0 and 65535.")
    try:
        resolved, error = _resolve_physical_vehicle(
            vehicle_id,
            timeout_s=max(0.1, float(timeout_s)),
        )
    except (OSError, TypeError, ValueError) as exc:
        return CommandResult(2, f"Vehicle discovery failed: {type(exc).__name__}: {exc}")
    if error is not None or resolved is None:
        return CommandResult(2, error or f"Vehicle {vehicle_id!r} could not be resolved.")

    server: LiveDecisionMonitorServer | None = None
    try:
        server = LiveDecisionMonitorServer(
            LiveDecisionMonitor(
                vehicle_id=resolved.vehicle_id,
                base_url=resolved.base_url,
                timeout_s=timeout_s,
            ),
            port=port,
        ).start()
        if output is not None:
            print(
                f"Live decision monitor: {server.url}\n"
                f"Vehicle: {resolved.vehicle_id} ({resolved.base_url})\n"
                "Read-only shadow view; no vehicle commands are sent. Ctrl-C stops it.",
                file=output,
                flush=True,
            )
        if open_browser and not webbrowser.open(server.url, new=2) and output is not None:
            print(f"Open the monitor manually: {server.url}", file=output, flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        return CommandResult(0, "Live decision monitor stopped.")
    except (OSError, ValueError, TypeError) as exc:
        return CommandResult(2, f"live decision monitor unavailable: {type(exc).__name__}: {exc}")
    finally:
        if server is not None:
            server.stop()
