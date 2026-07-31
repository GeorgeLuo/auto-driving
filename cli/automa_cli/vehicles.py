from __future__ import annotations

import json
import math
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

from implementations.vehicle.chase_sim import (
    ChaseCaptureValidationError,
    ChasePassiveCaptureError,
    ChaseSimCar,
)
from implementations.vehicle.chase_sim.defaults import (
    CHASE_UI_WS_URL_ENV,
    DEFAULT_CHASE_UI_WS_URL,
)
from implementations.vehicle.picar import create_local_car
from implementations.vehicle.picar.defaults import (
    DEFAULT_LOCAL_CAR_BASE_URL,
    LOCAL_CAR_BASE_URL_ENV,
)

DEFAULT_CHASE_READINESS_TIMEOUT_S = 5.0
STATUS_SCHEMA = "automa_vehicle_status_v1"
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
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
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
        suffix = (
            f" Discoverable ids: {', '.join(active_ids)}."
            if active_ids
            else ""
        )
        return None, f"Vehicle {vehicle_id!r} was not found among discoverable vehicles.{suffix}"

    providers = sorted({str(match.get("provider")) for match in matches})
    if len(matches) > 1 and len(providers) > 1:
        return None, (
            f"Vehicle id {vehicle_id!r} matched multiple providers: {', '.join(providers)}. "
            "Use a unique vehicle id before updating."
        )

    return matches[0], None


def get_vehicle_status(
    *,
    vehicle_id: str | None = None,
    chase_url: str | None = None,
    chase_ws_url: str | None = None,
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
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
    probe = _probe_chase_sim(candidate, timeout_s=operation_timeout)
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
    deployed_by_id = {
        deployed_id: item
        for deployed_id, item in all_deployed_by_id.items()
        if _is_chase_vehicle_id(deployed_id)
    }
    other_local_deployments = [
        {
            "vehicle_id": deployed_id,
            "inspection_command": (
                "./cli/automa vehicles automation status "
                f"--id {deployed_id}"
            ),
        }
        for deployed_id in sorted(set(all_deployed_by_id) - set(deployed_by_id))
    ]
    discoverable_by_id = {
        str(item.get("vehicle_id")): item
        for item in discovery.get("vehicles", [])
        if isinstance(item, dict) and item.get("vehicle_id")
    }
    if (
        vehicle_id is not None
        and vehicle_id in all_deployed_by_id
        and vehicle_id not in deployed_by_id
    ):
        raise ValueError(
            f"{vehicle_id!r} is a non-Chase local deployment; inspect it with "
            f"`./cli/automa vehicles automation status --id {vehicle_id}`"
        )
    known_ids = set(discoverable_by_id) | set(deployed_by_id)
    if vehicle_id is not None:
        known_ids = {vehicle_id}
    elif not known_ids:
        known_ids = {"chase-sim-chaser"}

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
    checked_at_ms = int(time.time() * 1000)
    if vehicle_id is not None:
        return cards[0]
    return {
        "schema": STATUS_SCHEMA,
        "vehicle_id": None,
        "endpoint": {
            "operator_url": display_url,
            "resolved_ws_url": endpoint,
        },
        "layers": None,
        "capture": None,
        "readiness": {
            "schema": READINESS_SCHEMA,
            "status": (
                "ready"
                if cards and all(card["readiness"]["status"] == "ready" for card in cards)
                else "blocked"
            ),
            "ready_for": "inspect known vehicles",
            "checked_at_ms": checked_at_ms,
            "gates": {
                str(card["vehicle_id"]): card["readiness"]
                for card in cards
            },
            "blocking_layer": next(
                (
                    card["readiness"]["blocking_layer"]
                    for card in cards
                    if card["readiness"]["blocking_layer"] is not None
                ),
                None,
            ),
        },
        "next_action": None,
        "checked_at_ms": checked_at_ms,
        "timeout_s": operation_timeout,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "vehicles": cards,
        "other_local_deployments": other_local_deployments,
    }


def format_vehicle_status(payload: dict[str, Any]) -> str:
    """Format one status card or an aggregate list with truthful layer names."""

    cards = payload.get("vehicles")
    if isinstance(cards, list):
        lines = [f"Known Chase vehicles: {len(cards)}"]
        for card in cards:
            if isinstance(card, dict):
                lines.extend(["", *_format_vehicle_status_card(card)])
        other = payload.get("other_local_deployments")
        if isinstance(other, list) and other:
            lines.extend(["", f"Other local deployments: {len(other)}"])
            for deployment in other:
                if not isinstance(deployment, dict):
                    continue
                lines.append(
                    f"- {deployment.get('vehicle_id', 'unknown')}: "
                    f"{deployment.get('inspection_command', 'inspection unavailable')}"
                )
        return "\n".join(lines)
    return "\n".join(_format_vehicle_status_card(payload))


def _is_chase_vehicle_id(vehicle_id: str) -> bool:
    return vehicle_id == "chase-sim-chaser" or vehicle_id.startswith("chase-sim-")


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
        if process.get("running"):
            worker_state = "running"
        elif process_status in {"error", "stale"}:
            worker_state = "error"
        else:
            worker_state = "stopped"
        view_details = (
            automation.get("published_view")
            if isinstance(automation.get("published_view"), dict)
            else {}
        )
        if view_details.get("available") and worker_state == "running":
            view_state = "available"
        elif view_details.get("url") or view_details.get("status") == "stale":
            view_state = "stale"
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
                    "then": (
                        "./cli/automa vehicles status "
                        f"--id {vehicle_id} --chase-url {operator_url}"
                    ),
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
    if layers["automation_deployment"]["state"] != "deployed":
        return (
            action(
                "automation_not_deployed",
                command=(
                    "./cli/automa vehicles update perception "
                    f"--id {vehicle_id} --algorithm lightweight_observer"
                ),
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
                    f"--id {vehicle_id} --observe-only --frames 0 --open-view --log"
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
                    f"--id {vehicle_id} --observe-only --frames 0 --open-view"
                ),
                expected_state="automation_worker=running, perception_view=available",
            ),
            "automation_worker",
            "inspect perception",
        )
    if layers["perception_view"]["state"] != "available":
        return (
            action(
                "view_stale"
                if layers["perception_view"]["state"] == "stale"
                else "view_unavailable",
                command=(
                    "./cli/automa vehicles automation restart "
                    f"--id {vehicle_id} --observe-only --frames 0"
                ),
                expected_state="perception_view=available",
            ),
            "perception_view",
            "inspect perception",
        )
    return None, None, "inspect perception and stop automation"


def _format_vehicle_status_card(card: dict[str, Any]) -> list[str]:
    layers = card.get("layers") if isinstance(card.get("layers"), dict) else {}
    endpoint = card.get("endpoint") if isinstance(card.get("endpoint"), dict) else {}
    capture = card.get("capture") if isinstance(card.get("capture"), dict) else {}
    evaluator = (
        capture.get("evaluator_reference")
        if isinstance(capture.get("evaluator_reference"), dict)
        else {}
    )
    lines = [
        f"Vehicle: {card.get('vehicle_id', 'unknown')}",
        f"Endpoint: {endpoint.get('operator_url', 'unknown')} -> {endpoint.get('resolved_ws_url', 'unknown')}",
    ]
    for name in (
        "simulator_server",
        "simulator_frontend",
        "chase_game",
        "vehicle",
        "passive_capture",
        "automation_deployment",
        "automation_worker",
        "perception_view",
    ):
        layer = layers.get(name) if isinstance(layers.get(name), dict) else {}
        lines.append(f"{name}: {layer.get('state', 'unknown')}")
    worker = (
        layers.get("automation_worker")
        if isinstance(layers.get("automation_worker"), dict)
        else {}
    )
    worker_details = (
        worker.get("details") if isinstance(worker.get("details"), dict) else {}
    )
    authority = (
        worker_details.get("authority")
        if isinstance(worker_details.get("authority"), dict)
        else {}
    )
    if authority:
        lines.append(
            "authority: "
            f"control_source={authority.get('control_source', 'unknown')}, "
            f"action_policy={authority.get('action_policy', 'unknown')}, "
            f"control_application={authority.get('control_application', 'unknown')}"
        )
    lines.append(
        "evaluator_reference: "
        f"{evaluator.get('status', 'unavailable')}"
        + (
            f" ({evaluator.get('reason')})"
            if evaluator.get("reason")
            else ""
        )
    )
    readiness = (
        card.get("readiness")
        if isinstance(card.get("readiness"), dict)
        else {}
    )
    if readiness.get("status") == "ready":
        lines.append(f"Ready for: {readiness.get('ready_for')}")
    else:
        lines.append(f"Not ready for: {readiness.get('ready_for')}")
    next_action = card.get("next_action")
    if isinstance(next_action, dict):
        recovery = next_action.get("command")
        if recovery is None:
            external = next_action.get("external_change")
            recovery = _format_external_status_recovery(external)
        lines.append(f"Next action: {recovery}")
    return lines


def _format_external_status_recovery(external: Any) -> str:
    if not isinstance(external, dict):
        return str(external)
    change = external.get("change")
    then = external.get("then")
    if change and then:
        return f"{change}; then run `{then}`"
    component = str(external.get("component") or "External component")
    missing = external.get("missing_capability")
    failing_path = external.get("failing_path")
    minimum = external.get("minimum_contract")
    if missing:
        summary = f"{component} must expose {missing}"
    elif failing_path:
        summary = f"{component} must repair {failing_path}"
    else:
        summary = f"{component} change required"
    if minimum:
        summary += f": {minimum}"
    return summary


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

    for url in extra_urls:
        if url.strip():
            candidates.append(Candidate("picar", _normalize_http_url(url), "cli"))

    return _dedupe_candidates(candidates)


def _chase_sim_candidates(extra_urls: tuple[str, ...]) -> list[Candidate]:
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
        diagnostics.update(
            {
                "ws_server": exc.code != "simulator_unreachable",
                "frontend_connected": (
                    "debug_before" in completed
                    or exc.code not in {"simulator_unreachable", "frontend_disconnected"}
                ),
                "chase_loaded": exc.code
                not in {"simulator_unreachable", "frontend_disconnected", "wrong_game"},
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
