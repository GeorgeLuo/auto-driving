"""A host's readiness at a Chase simulator, layer by layer, with the next action."""

from __future__ import annotations

import os
import time
from typing import Any

from implementations.vehicle.chase_sim.defaults import (
    CHASE_UI_WS_URL_ENV,
    DEFAULT_CHASE_UI_WS_URL,
)

from ..vehicles.probes import (
    DEFAULT_READINESS_TIMEOUT_S,
    Candidate,
    _probe_chase,
    _require_positive_timeout,
    chase_operator_url,
    normalize_chase_url,
)
from ..vehicles.providers import CHASE_SIM

STATUS_SCHEMA = "automa_host_readiness"
READINESS_SCHEMA = "automa_cli_readiness_v1"


def get_vehicle_status(
    *,
    host_name: str,
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
    candidate = Candidate(CHASE_SIM.name, endpoint, "cli")
    probe = _probe_chase(candidate, timeout_s=operation_timeout)
    discovery = {
        "schema": "automa_vehicle_discovery_v0",
        "checked_at_ms": int(time.time() * 1000),
        "active_count": 1 if probe.active else 0,
        "vehicles": [probe.vehicle] if probe.active and probe.vehicle is not None else [],
        "inactive": [] if probe.active else [probe.to_dict()],
        "discovery": {
            "candidate_count": 1,
            "providers": [CHASE_SIM.name],
            "timeout_s": operation_timeout,
        },
    }

    # Imported lazily because automation consumes vehicle discovery.
    from ..hosts.automation_status import _collect_automation_status

    remaining = operation_timeout - (time.monotonic() - started)
    automation = _collect_automation_status(
        host_name=host_name,
        view_timeout_s=remaining if remaining >= 0.05 else 0.0,
    )
    all_deployed_by_id = {
        str(item.get("host_name")): item
        for item in automation
        if isinstance(item, dict) and item.get("host_name")
    }
    deployed_by_id = all_deployed_by_id
    # The probe describes the environment at the requested endpoint, not a
    # host whose name happens to match a vehicle capability identifier.
    discovered_vehicle = probe.vehicle if probe.active else None
    known_ids = {host_name}

    inactive = next(
        (
            item
            for item in discovery.get("inactive", [])
            if isinstance(item, dict)
            and isinstance(item.get("candidate"), dict)
            and item["candidate"].get("provider") == CHASE_SIM.name
            and item["candidate"].get("url") == endpoint
        ),
        None,
    )
    cards = [
        _build_vehicle_status_card(
            host_name=known_id,
            endpoint=endpoint,
            operator_url=display_url,
            timeout_s=operation_timeout,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            discoverable=discovered_vehicle,
            inactive=inactive,
            automation=deployed_by_id.get(known_id),
        )
        for known_id in sorted(known_ids)
    ]
    return cards[0]


def _build_vehicle_status_card(
    *,
    host_name: str,
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
        host_name=host_name,
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
        "host_name": host_name,
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
    host_name: str,
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
                command=f"./cli/automa hosts update --name {host_name}",
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
                    "./cli/automa hosts run "
                    f"--name {host_name} --observe-only --num-decisions 0 --open-view --log"
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
                    "./cli/automa hosts run "
                    f"--name {host_name} --observe-only --num-decisions 0 --open-view"
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
                    "./cli/automa hosts restart "
                    f"--name {host_name} --observe-only --num-decisions 0"
                ),
                expected_state="perception_view=available",
            ),
            "perception_view",
            "inspect perception",
        )
    return None, None, "inspect perception and stop automation"
