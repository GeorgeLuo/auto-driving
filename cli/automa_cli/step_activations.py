"""A vehicle's staged step activations in its local controller bundle.

Every cycle step is staged the same way: ``bundle/runtime/<step>/active.json``
holds a ``StepActivation`` for a vehicle ``staging_vehicle`` knows. The CLI
records who staged it (vehicle, bundle, release, time) in the activation's
``metadata``. CLI hosts use its bundle path to load staged plugin code;
the core step runners do not read it.
The decision steps (proposal, plan, action) together identify a decision
generation by the content of their activations.
"""

from __future__ import annotations

import json
import shlex
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
from autonomy.runtime.cycle_host import LIVE_SELECTION_STEPS
from autonomy.plugins import DuplicatePluginIdError
from implementations.decision_cycle.catalog import (
    packaged_activation,
    step_plugins,
)
from implementations.vehicle.chase_sim import ChaseSimCar

from .bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
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


def bundle_activation_problems(
    bundle: dict[str, str], vehicle_id: str, *, steps: tuple[str, ...] = STEPS,
) -> list[dict[str, str]]:
    """Diagnose existing staged documents using the core's common step reader.

    Missing optional steps retain their built-in or empty behavior. An invalid
    document needs explicit restaging; checking never changes its selection or
    configs. Each diagnosis includes the owning step's update command. Callers
    pass the ``steps`` they read, so a command reports exactly what blocks it.
    """

    problems: list[dict[str, str]] = []
    for step in steps:
        try:
            read_bundle_activation(bundle, step)
        except (OSError, TypeError, ValueError) as exc:
            problems.append({
                "step": step,
                "activation": display_path(bundle_activation_path(bundle, step)),
                "reason": str(exc),
                "command": f"./cli/automa vehicles update {step} --id {shlex.quote(vehicle_id)}",
            })
    return problems


def format_activation_problems(problems: list[dict[str, str]]) -> str:
    """Render the same step-specific recovery for startup and status callers."""

    return "\n".join(
        line
        for problem in problems
        for line in (
            f"Invalid {problem['step']} activation: {problem['activation']}",
            f"Reason: {problem['reason']}",
            f"Restage with your intended selection: {problem['command']}",
        )
    )


def apply_staged(vehicle_id: str, provider: Any, step: str) -> dict[str, Any]:
    """How a vehicle's running autonomy picks up ``step`` as ``vehicles update`` staged it.

    The Chase worker hosts the local bundle; the PiCar hosts the copy that
    ``vehicles update autonomy`` installs onboard. Either host selects a
    restaged live-selection step's plugins on its next frame
    (``selection_command`` is ``None`` when nothing needs to run); other
    steps, and changed plugin specs or configs, take effect after
    ``restart_command``.
    """

    live = step in LIVE_SELECTION_STEPS
    if provider == "picar":
        install = f"./cli/automa vehicles update autonomy --id {vehicle_id}"
        selection, restart = install, f"{install} --restart"
    else:
        selection = None
        restart = f"./cli/automa vehicles automation restart --id {vehicle_id}"
    return {
        "live_selection": live,
        "selection_command": selection if live else restart,
        "restart_command": restart,
    }


def format_apply_staged(apply: dict[str, Any]) -> list[str]:
    if not apply.get("live_selection"):
        return [f"Apply: {apply.get('restart_command')}"]
    selection = apply.get("selection_command") or "automatic on the running worker's next frame"
    return [
        f"Apply selection: {selection}",
        f"Apply changed specs or configs: {apply.get('restart_command')}",
    ]


def absent_step_error(step: str, vehicle_id: str, provider: Any) -> str:
    """The same guidance for every vehicle whose running autonomy lacks ``step``."""

    restart = apply_staged(vehicle_id, provider, step)["restart_command"]
    return (
        f"{vehicle_id} runs no {step} step. Stage it with "
        f"./cli/automa vehicles update {step} --id {vehicle_id}, then restart: {restart}"
    )


def staging_vehicle(
    vehicle_id: str,
    *,
    runtime_root: Path,
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
    offline: bool = True,
    output: TextIO | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """The vehicle every ``vehicles update <step>`` stages for, or why it is unknown.

    A Chase sim id and a vehicle whose identity was recorded by any staged
    step are known without discovery. Identity must match the requested id;
    directory names alone do not establish it. The newest valid matching
    record supplies the connection. Any other id must be discoverable.
    ``offline=False`` always discovers, for commands that need the live vehicle.
    ``timeout_s`` bounds each discovery probe, not the whole update command.
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
    """The newest matching vehicle identity recorded by any valid staged step."""

    records: list[dict[str, Any]] = []
    for step in STEPS:
        try:
            activation = read_bundle_activation(bundle, step)
        except (OSError, ValueError):
            continue
        metadata = dict(activation.metadata) if activation is not None else {}
        if metadata.get("vehicle_id") != vehicle_id:
            continue
        provider = metadata.get("provider")
        if isinstance(provider, str) and provider:
            records.append(metadata)
    if not records:
        return None
    # Historical perception records already carry this timestamp. Records
    # without one remain usable, behind identities written by current staging.
    metadata = max(
        records,
        key=lambda item: item["activated_at_ms"] if isinstance(item.get("activated_at_ms"), int) else 0,
    )
    provider = metadata["provider"]
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


# Keys of ``metadata.controller_bundle`` on every staged step.
CONTROLLER_BUNDLE_KEYS = (
    "root_dir",
    "autonomy_dir",
    "implementations_dir",
    "runtime_dir",
    "release",
)


def staging_metadata(
    *,
    vehicle_id: str | None,
    bundle: dict[str, str],
    release: dict[str, Any] | None = None,
    vehicle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Provenance recorded on every step's staged activation.

    ``controller_bundle`` has the same keys for every step
    (``CONTROLLER_BUNDLE_KEYS``). A packaged ``release`` is summarized;
    otherwise ``release`` is null until a caller records one. Vehicle
    identity (``provider``, ``vehicle_kind``, ``runtime``) is recorded
    when ``vehicle`` is supplied. Steps do not add their own keys.
    """

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
    if vehicle is not None:
        metadata.update({
            "provider": vehicle.get("provider"),
            "vehicle_kind": vehicle.get("vehicle_kind"),
            "runtime": {
                "kind": "ws_cli_controller" if vehicle.get("provider") == "chase-sim" else "onboard_controller",
                "connection": deepcopy(vehicle.get("connection")),
            },
        })
    return metadata


def staged_activation(
    bundle: dict[str, str],
    activation: StepActivation,
    *,
    vehicle_id: str | None,
    release: dict[str, Any] | None = None,
    vehicle: dict[str, Any] | None = None,
    release_summary: dict[str, Any] | None = None,
) -> StepActivation:
    """``activation`` plus staging provenance, not yet written.

    ``release`` is a bundle just packaged by ``sync_controller_bundle``
    and wins. ``release_summary`` is an already recorded summary kept
    when the bundle is not repackaged.
    """

    metadata = {**dict(activation.metadata), **staging_metadata(
        vehicle_id=vehicle_id, bundle=bundle, release=release, vehicle=vehicle,
    )}
    if release is None and release_summary is not None:
        controller_bundle = dict(metadata.get("controller_bundle") or {})
        controller_bundle["release"] = deepcopy(release_summary)
        metadata["controller_bundle"] = controller_bundle
    return replace_metadata(activation, metadata)


def stage_activation(
    bundle: dict[str, str],
    activation: StepActivation,
    *,
    vehicle_id: str | None,
    release: dict[str, Any] | None = None,
    vehicle: dict[str, Any] | None = None,
    release_summary: dict[str, Any] | None = None,
) -> Path:
    """Write ``activation`` with the provenance ``staged_activation`` builds."""

    return write_step_activation(
        bundle_activation_path(bundle, activation.step),
        staged_activation(
            bundle,
            activation,
            vehicle_id=vehicle_id,
            release=release,
            vehicle=vehicle,
            release_summary=release_summary,
        ),
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


def step_update_error(
    vehicle_id: str, step: str, error: str, message: str,
    *, json_output: bool, **details: Any,
) -> tuple[int, str]:
    """One staging error envelope; vehicle resolution failures use it for every step."""

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

    try:
        activation = packaged_activation(step, plugins)
        # Construct the runner once so a selection that cannot load is never staged.
        step_runner(activation)
    except DuplicatePluginIdError:
        # A packaged-catalog clash is not a bad selection; the CLI reports it.
        raise
    except (TypeError, ValueError) as exc:
        return step_update_error(
            vehicle_id, step,
            "invalid_selection",
            f"Cannot stage {step} plugins: {exc}",
            json_output=json_output,
            available_plugins=sorted(step_plugins(step)),
        )

    vehicle, unknown = staging_vehicle(
        vehicle_id, runtime_root=runtime_root, timeout_s=timeout_s, output=output if verbose else None
    )
    if unknown is not None:
        return step_update_error(vehicle_id, step, "unknown_vehicle", unknown, json_output=json_output)

    bundle = vehicle_bundle(vehicle_id, runtime_root)
    path = bundle_activation_path(bundle, step)
    release: dict[str, Any] | None = None
    if not dry_run:
        release = sync_controller_bundle(bundle, output=output if verbose else None)
        path = stage_activation(bundle, activation, vehicle_id=vehicle_id, release=release, vehicle=vehicle)
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
        "apply": apply_staged(vehicle_id, vehicle.get("provider"), step),
    }
    if json_output:
        return 0, json.dumps(payload, indent=2, sort_keys=True)
    verb = "Would stage" if dry_run else "Staged"
    return 0, "\n".join(
        [
            f"{verb} {step}: {vehicle_id} -> {', '.join(activation.plugins) or '(no plugins)'}",
            f"Activation: {display_path(path)}",
            *format_apply_staged(payload["apply"]),
        ]
    )


__all__ = [
    "BUILTIN_STEPS",
    "GENERIC_UPDATE_STEPS",
    "absent_step_error",
    "apply_staged",
    "format_apply_staged",
    "bundle_activation_path",
    "bundle_activation_problems",
    "decision_activations",
    "decision_generation_id",
    "decision_identity",
    "ensure_builtin_activations",
    "format_activation_problems",
    "proposal_plugin_ids",
    "CONTROLLER_BUNDLE_KEYS",
    "read_bundle_activation",
    "refresh_release",
    "replace_metadata",
    "stage_activation",
    "staged_activation",
    "staging_metadata",
    "staging_vehicle",
    "step_update_error",
    "update_vehicle_step",
    "vehicle_bundle",
]
