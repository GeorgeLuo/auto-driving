"""A vehicle's staged step activations in its local controller bundle.

Every cycle step is staged the same way: ``bundle/runtime/<step>/active.json``
holds a ``StepActivation`` for a vehicle ``staging_vehicle`` knows. The CLI
records who staged it (vehicle, bundle, release, time) in the activation's
``metadata``; runners do not read it.
The decision steps (proposal, plan, action) together identify a decision
generation by the content of their activations.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision_cycle.activation import (
    DECISION_STEPS,
    STEPS,
    StepActivation,
    activation_generation_id,
    read_step_activation_if_present,
    require_step,
    step_activation_path,
    write_step_activation,
)
from autonomy.decision_cycle.steps import builtin_activation, step_runner
from autonomy.plugins import DuplicatePluginIdError
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PLUGINS,
    packaged_activation,
    step_plugins,
)
from implementations.vehicle.chase_sim import ChaseSimCar

from .bundles import controller_bundle_paths, release_activation_summary, sync_controller_bundle
from .paths import display_path, safe_path_part
from .vehicles import (
    DEFAULT_CHASE_READINESS_TIMEOUT_S,
    discover_active_vehicles,
    find_vehicle_by_id,
    format_active_vehicles,
)

# Steps whose packaged plugins are staged with `vehicles update <step>`.
GENERIC_UPDATE_STEPS = ("observation", "proposal", "plan", "action")
# Steps a vehicle runs with their built-in plugin when nothing is staged.
BUILTIN_STEPS = ("observation", "plan", "action")


def vehicle_bundle(vehicle_id: str, runtime_root: Path) -> dict[str, str]:
    return controller_bundle_paths(Path(runtime_root) / safe_path_part(vehicle_id))


def bundle_activation_path(bundle: dict[str, str], step: str) -> Path:
    return step_activation_path(Path(bundle["runtime_dir"]), step)


def read_bundle_activation(bundle: dict[str, str], step: str) -> StepActivation | None:
    return read_step_activation_if_present(bundle_activation_path(bundle, step), step)


def staging_vehicle(
    vehicle_id: str,
    *,
    runtime_root: Path,
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
    offline: bool = True,
    output: TextIO | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """The vehicle every ``vehicles update <step>`` stages for, or why it is unknown.

    A Chase sim id and a vehicle with staged perception are known without the
    network; any other id must be discoverable. ``offline=False`` always
    discovers, for commands that need the live vehicle.
    """

    vehicle = None
    if offline:
        vehicle = _offline_sim_vehicle(vehicle_id) or _offline_staged_vehicle(
            vehicle_bundle(vehicle_id, runtime_root), vehicle_id
        )
    if vehicle is not None:
        _emit(output, "Using local vehicle metadata; network liveness is not required for local staging.")
        return vehicle, None

    _emit(output, f"Discovering active vehicles for id {vehicle_id!r}...")
    payload = discover_active_vehicles(
        timeout_s=timeout_s,
        include_picar=True,
        include_chase_sim=True,
        include_inactive=True,
    )
    vehicle, error = find_vehicle_by_id(payload, vehicle_id)
    if vehicle is None:
        return None, "\n\n".join(
            [
                error or f"Vehicle {vehicle_id!r} was not found.",
                "Discovery:",
                format_active_vehicles(payload, include_inactive=True),
            ]
        )
    return vehicle, None


def _offline_sim_vehicle(vehicle_id: str) -> dict[str, Any] | None:
    if vehicle_id != "chase-sim-chaser" and not vehicle_id.startswith("chase-sim-"):
        return None
    car = ChaseSimCar(vehicle_id=vehicle_id)
    return {
        "vehicle_id": vehicle_id,
        "vehicle_kind": car.capabilities.vehicle_kind,
        "provider": "chase-sim",
        "connection": {
            "ws_url": car.ws_url,
            "source": "offline-default",
        },
        "capabilities": car.capabilities.to_dict(),
        "status": {
            "ok": None,
            "note": "offline simulator metadata; WS/frontend liveness was not required for staging",
        },
    }


def _offline_staged_vehicle(bundle: dict[str, str], vehicle_id: str) -> dict[str, Any] | None:
    """The vehicle recorded by its staged perception, the step that records the provider."""

    try:
        activation = read_bundle_activation(bundle, "perception")
    except (OSError, ValueError):
        return None
    metadata = activation.metadata if activation is not None else {}
    provider = metadata.get("provider")
    if not isinstance(provider, str) or not provider:
        return None
    runtime = metadata.get("runtime")
    connection = runtime.get("connection") if isinstance(runtime, dict) else None
    return {
        "vehicle_id": vehicle_id,
        "vehicle_kind": metadata.get("vehicle_kind") or provider,
        "provider": provider,
        "connection": connection if isinstance(connection, dict) else {},
        "status": {
            "ok": None,
            "note": "offline local staging metadata; vehicle liveness was not checked",
        },
    }


def _emit(output: TextIO | None, message: str) -> None:
    if output is not None:
        print(message, file=output, flush=True)


def staging_metadata(
    *,
    vehicle_id: str | None,
    bundle: dict[str, str],
    release: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "vehicle_id": vehicle_id,
        "activated_at_ms": int(time.time() * 1000),
        "controller_bundle": {
            "root_dir": bundle["root_dir"],
            "autonomy_dir": bundle["autonomy_dir"],
            "implementations_dir": bundle["implementations_dir"],
            "runtime_dir": bundle["runtime_dir"],
            "release": release_activation_summary(release) if release is not None else None,
        },
    }
    metadata.update(deepcopy(extra or {}))
    return metadata


def stage_activation(
    bundle: dict[str, str],
    activation: StepActivation,
    *,
    vehicle_id: str | None,
    release: dict[str, Any] | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> Path:
    """Write ``activation`` for its step with the CLI's staging metadata."""

    metadata = {**dict(activation.metadata), **staging_metadata(
        vehicle_id=vehicle_id, bundle=bundle, release=release, extra=extra_metadata
    )}
    return write_step_activation(
        bundle_activation_path(bundle, activation.step), replace_metadata(activation, metadata)
    )


def replace_metadata(activation: StepActivation, metadata: dict[str, Any]) -> StepActivation:
    return StepActivation(
        step=activation.step,
        plugins=activation.plugins,
        plugin_specs=activation.plugin_specs,
        plugin_configs=activation.plugin_configs,
        metadata=metadata,
        source_path=activation.source_path,
    )


def refresh_release(bundle: dict[str, str], step: str, release: dict[str, Any]) -> Path | None:
    """Record ``release`` on an existing activation; returns its path when present."""

    activation = read_bundle_activation(bundle, step)
    if activation is None:
        return None
    metadata = deepcopy(dict(activation.metadata))
    controller_bundle = dict(metadata.get("controller_bundle") or {})
    controller_bundle["release"] = release_activation_summary(release)
    metadata["controller_bundle"] = controller_bundle
    return write_step_activation(
        bundle_activation_path(bundle, step), replace_metadata(activation, metadata)
    )


def ensure_builtin_activations(
    *,
    vehicle_id: str,
    bundle: dict[str, str],
    release: dict[str, Any],
) -> dict[str, Path]:
    """Stage the built-in observation, plan, and action plugins where nothing is staged.

    Existing activations keep their selection and record ``release``. Without
    a proposal activation the vehicle proposes nothing and holds.
    """

    paths: dict[str, Path] = {}
    for step in BUILTIN_STEPS:
        path = refresh_release(bundle, step, release)
        if path is None:
            path = stage_activation(
                bundle,
                packaged_activation(step),
                vehicle_id=vehicle_id,
                release=release,
            )
        paths[step] = path
    refresh_release(bundle, "proposal", release)
    return paths


def decision_activations(bundle: dict[str, str]) -> dict[str, StepActivation | None]:
    """The proposal, plan, and action activations a host would run, with built-ins."""

    return {
        step: read_bundle_activation(bundle, step) or builtin_activation(step)
        for step in DECISION_STEPS
    }


def decision_generation_id(activations: dict[str, StepActivation | None]) -> str:
    return activation_generation_id(activations, prefix="decision")


def decision_identity(bundle: dict[str, str]) -> dict[str, Any]:
    """The staged decision steps and the generation they identify."""

    activations = decision_activations(bundle)
    return {
        "generation_id": decision_generation_id(activations),
        "steps": {
            step: activation.to_payload() if activation is not None else None
            for step, activation in activations.items()
        },
    }


def proposal_plugin_ids(steps: dict[str, Any]) -> list[str]:
    """Selected proposal plugin IDs from a decision identity's step payloads."""

    payload = steps.get("proposal") if isinstance(steps, dict) else None
    plugins = payload.get("plugins") if isinstance(payload, dict) else None
    return list(plugins) if isinstance(plugins, list) else []


def update_vehicle_step(
    *,
    vehicle_id: str,
    step: str,
    plugins: list[str] | None = None,
    runtime_root: Path,
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
    dry_run: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> tuple[int, str]:
    """Stage packaged ``plugins`` for ``step`` (its default selection when omitted)."""

    require_step(step)

    def failed(error: str, message: str, **details: Any) -> tuple[int, str]:
        if not json_output:
            return 2, message
        return 2, json.dumps(
            {
                "schema": "vehicle_step_update_error_v0",
                "vehicle_id": vehicle_id,
                "step": step,
                "error": error,
                "message": message,
                **details,
            },
            indent=2,
            sort_keys=True,
        )

    try:
        activation = packaged_activation(step, plugins)
        # Construct the runner once so a selection that cannot load is never staged.
        step_runner(activation)
    except DuplicatePluginIdError:
        # A packaged-catalog clash is not a bad selection; the CLI reports it.
        raise
    except (TypeError, ValueError) as exc:
        return failed(
            "invalid_selection",
            f"Cannot stage {step} plugins: {exc}",
            available_plugins=sorted(step_plugins(step)),
        )

    _, unknown = staging_vehicle(
        vehicle_id, runtime_root=runtime_root, timeout_s=timeout_s, output=output if verbose else None
    )
    if unknown is not None:
        return failed("unknown_vehicle", unknown)

    bundle = vehicle_bundle(vehicle_id, runtime_root)
    path = bundle_activation_path(bundle, step)
    release: dict[str, Any] | None = None
    if not dry_run:
        release = sync_controller_bundle(bundle, output=output if verbose else None)
        path = stage_activation(bundle, activation, vehicle_id=vehicle_id, release=release)
        if step in DECISION_STEPS:
            from .decision import invalidate_latest_decision_frame

            # A restaged decision step retires the latest published decision frame.
            invalidate_latest_decision_frame(Path(bundle["root_dir"]).parent)
    payload = {
        "schema": "vehicle_step_update_v0",
        "vehicle_id": vehicle_id,
        "step": step,
        "plugins": list(activation.plugins),
        "dry_run": dry_run,
        "activation": display_path(path),
        "manifest": activation.to_payload(),
        "release": release_activation_summary(release) if release is not None else None,
    }
    if json_output:
        return 0, json.dumps(payload, indent=2, sort_keys=True)
    verb = "Would stage" if dry_run else "Staged"
    return 0, "\n".join(
        [
            f"{verb} {step}: {vehicle_id} -> {', '.join(activation.plugins) or '(no plugins)'}",
            f"Activation: {display_path(path)}",
        ]
    )


def step_info(bundle: dict[str, str]) -> dict[str, Any]:
    """Each step's staged selection, or the built-in it falls back to."""

    info: dict[str, Any] = {}
    for step in STEPS:
        staged = read_bundle_activation(bundle, step)
        fallback = None if staged is not None else builtin_activation(step)
        activation = staged or fallback
        info[step] = {
            "activation": display_path(bundle_activation_path(bundle, step)) if staged else None,
            "source": "staged" if staged else ("builtin" if fallback else "none"),
            "plugins": list(activation.plugins) if activation is not None else [],
            "available_plugins": sorted(step_plugins(step)),
            "default_plugins": list(DEFAULT_STEP_PLUGINS[step]),
        }
    return info


__all__ = [
    "BUILTIN_STEPS",
    "GENERIC_UPDATE_STEPS",
    "bundle_activation_path",
    "decision_activations",
    "decision_generation_id",
    "decision_identity",
    "ensure_builtin_activations",
    "proposal_plugin_ids",
    "read_bundle_activation",
    "refresh_release",
    "replace_metadata",
    "stage_activation",
    "staging_metadata",
    "staging_vehicle",
    "step_info",
    "update_vehicle_step",
    "vehicle_bundle",
]
