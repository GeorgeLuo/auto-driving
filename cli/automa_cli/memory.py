"""Stage memory and report the staged schema.

Inspect and reset live in ``memory_runs``. Stream and probe live in
``streaming``, beside perception's.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.plugins import DuplicatePluginIdError
from implementations.decision_cycle.catalog import selection_activation
from implementations.decision_cycle.memory.presets import (
    MEMORY_PRESETS,
    available_memory_preset_ids,
)

from .bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from .paths import ROOT, display_path, safe_path_part
from .step_activations import (
    apply_staged,
    format_apply_staged,
    refresh_release,
    stage_activation,
    staging_vehicle,
    step_update_error,
)
from .step_schema import format_staged_step, staged_step_info
from .streaming import _format_live_memory_screen, probe_live_memory
from .vehicles import DEFAULT_READINESS_TIMEOUT_S

RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def update_vehicle_memory(
    *,
    vehicle_id: str,
    preset: str | None = None,
    plugins: list[str] | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    dry_run: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    activation, error = _selected_memory(preset, plugins)
    if error is not None:
        return error

    selected = list(activation.plugins)
    stream = output if verbose else None
    vehicle, unknown = staging_vehicle(vehicle_id, runtime_root=RUNTIME_ROOT, timeout_s=timeout_s, output=stream)
    if unknown is not None:
        return CommandResult(*step_update_error(vehicle_id, "memory", "unknown_vehicle", unknown, json_output=json_output))
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    release: dict[str, Any] | None = None

    if not dry_run:
        release = sync_controller_bundle(bundle, output=stream)
        stage_activation(bundle, activation, vehicle_id=vehicle_id, release=release, vehicle=vehicle)

    payload = {
        "schema": "vehicle_memory_update_v1",
        "vehicle_id": vehicle_id,
        "preset": activation.metadata["preset"],
        "plugins": selected,
        "dry_run": dry_run,
        "activation": display_path(activation_path),
        "manifest": activation.to_payload(),
        "release": release_activation_summary(release) if release is not None else None,
        "apply": apply_staged(vehicle_id, vehicle.get("provider"), "memory"),
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    verb = "Would activate" if dry_run else "Updated memory"
    return CommandResult(
        0,
        "\n".join(
            [
                f"{verb}: {vehicle_id} -> {', '.join(selected)}",
                f"Preset: {activation.metadata['preset']}",
                *(f"Plugin: {plugin_id} ({activation.plugin_specs[plugin_id]})" for plugin_id in selected),
                f"Activation: {display_path(activation_path)}",
                *format_apply_staged(payload["apply"]),
            ]
        ),
    )


def ensure_vehicle_memory_activation(
    *,
    vehicle_id: str,
    bundle: dict[str, str],
    release: dict[str, Any],
    plugins: list[str] | None = None,
) -> Path:
    """Ensure a memory activation exists for autonomy deploy, recording ``release``."""

    path = refresh_release(bundle, "memory", release)
    if path is not None:
        return path
    return stage_activation(
        bundle,
        selection_activation("memory", plugins=plugins),
        vehicle_id=vehicle_id,
        release=release,
    )


def get_vehicle_memory_info(
    *,
    vehicle_id: str,
    json_output: bool = False,
    include_live: bool = True,
    timeout_s: float = 3.0,
) -> CommandResult:
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    staged, error = staged_step_info(bundle, vehicle_id, "memory")
    if error is not None:
        return CommandResult(2, error)
    payload: dict[str, Any] = {
        "schema": "vehicle_memory_info_v1",
        "vehicle_id": vehicle_id,
        **staged,
        "live": None,
    }
    if include_live:
        payload["live"] = probe_live_memory(
            vehicle_id=vehicle_id,
            timeout_s=timeout_s,
        )
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_memory_info(payload))


def _selected_memory(preset: str | None, plugins: list[str] | None) -> tuple[Any, CommandResult | None]:
    """The memory selection a command names, or the error to report for it."""

    if preset is not None and plugins:
        return None, CommandResult(2, "Choose either --preset or --plugin, not both.")
    if preset is not None and preset not in MEMORY_PRESETS:
        available = ", ".join(available_memory_preset_ids())
        return None, CommandResult(
            2, f"Unknown memory preset {preset!r}. Available presets: {available}."
        )
    try:
        return selection_activation("memory", preset=preset, plugins=plugins or None), None
    except DuplicatePluginIdError:
        # A packaged-catalog clash is not a bad selection; the CLI reports it.
        raise
    except ValueError as exc:
        return None, CommandResult(2, str(exc))


def _format_memory_info(payload: dict[str, Any]) -> str:
    lines = format_staged_step("memory", payload)
    live = payload.get("live")
    if isinstance(live, dict):
        lines.append("")
        lines.append(_format_live_memory_screen(vehicle_id=payload["vehicle_id"], live=live))
    return "\n".join(lines)

