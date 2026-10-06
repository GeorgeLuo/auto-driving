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

from autonomy.decision_cycle.activation import read_step_activation
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
    CONTROLLER_BUNDLE_KEYS,
    bundle_activation_problems,
    format_activation_problems,
    refresh_release,
    stage_activation,
    staging_vehicle,
    step_update_error,
)
from .step_hosting import load_staged_runner
from .streaming import _format_live_memory_screen, probe_live_memory
from .vehicles import DEFAULT_CHASE_READINESS_TIMEOUT_S

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
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
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
    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    if not activation_path.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No active memory preset found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(activation_path)}",
                    "Run: ./cli/automa vehicles update memory --id <vehicle_id>",
                ]
            ),
        )

    problems = bundle_activation_problems(bundle, vehicle_id, steps=("memory",))
    if problems:
        return CommandResult(2, format_activation_problems(problems))
    try:
        activation = read_step_activation(activation_path, "memory")
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return CommandResult(
            2,
            f"Could not read memory activation {display_path(activation_path)}: {exc}",
        )
    stored_bundle = activation.metadata.get("controller_bundle")
    if not isinstance(stored_bundle, dict):
        stored_bundle = {}
    bundle_root_text = stored_bundle.get("root_dir")
    if not isinstance(bundle_root_text, str) or not bundle_root_text:
        return CommandResult(
            2,
            f"Activation {display_path(activation_path)} does not record its controller bundle.",
        )
    if not Path(bundle_root_text).exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Controller bundle is missing for {vehicle_id!r}: {display_path(Path(bundle_root_text))}",
                    "Run: ./cli/automa vehicles update memory --id <vehicle_id>",
                ]
            ),
        )
    plugins_text = ", ".join(activation.plugins) or "(none)"
    try:
        runner = load_staged_runner(activation)
    except Exception as exc:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Could not load active memory for {vehicle_id!r}.",
                    f"Plugins: {plugins_text}",
                    f"Reason: {type(exc).__name__}: {exc}",
                ]
            ),
        )
    try:
        schema = runner.describe_schema()
    except Exception as exc:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Could not inspect active memory for {vehicle_id!r}.",
                    f"Plugins: {plugins_text}",
                    f"Reason: {type(exc).__name__}: {exc}",
                ]
            ),
        )
    try:
        manager = activation.plugin_manager()
        available = manager.available
    except Exception as exc:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Could not load active memory for {vehicle_id!r}.",
                    f"Plugins: {plugins_text}",
                    f"Reason: {type(exc).__name__}: {exc}",
                ]
            ),
        )

    payload: dict[str, Any] = {
        "schema": "vehicle_memory_info_v1",
        "vehicle_id": vehicle_id,
        "activation": {
            "path": display_path(activation_path),
            "preset": activation.metadata.get("preset"),
            "plugins": list(manager.selected_ids),
            "available_plugins": sorted(item.plugin_id for item in available),
            "plugin_specs": {item.plugin_id: item.entrypoint for item in available},
            "plugin_configs": {item.plugin_id: dict(item.config) for item in available},
        },
        "controller_bundle": {
            key: stored_bundle.get(key) for key in CONTROLLER_BUNDLE_KEYS
        },
        "memory_schema_source": {
            "kind": "runner_method",
            "method": "describe_schema",
            "runner": "autonomy.decision_cycle.memory.runner:MemoryRunner",
        },
        "memory_schema": schema,
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
    activation = payload["activation"]
    bundle = payload.get("controller_bundle") if isinstance(payload.get("controller_bundle"), dict) else {}
    lines = [
        f"Memory: {payload['vehicle_id']} -> {activation.get('preset') or 'unknown'}",
        f"Enabled plugins: {', '.join(activation.get('plugins', [])) or 'none'}",
        f"Available plugins: {', '.join(activation.get('available_plugins', [])) or 'none'}",
        f"Bundle: {bundle.get('root_dir', 'unknown')}",
        f"Activation: {activation['path']}",
    ]
    release = bundle.get("release") if isinstance(bundle.get("release"), dict) else {}
    if release:
        archive = release.get("archive")
        manifest = release.get("manifest")
        lines.append(f"Release: {release.get('tree_sha256', 'unknown')}")
        if archive:
            lines.append(f"Archive: {archive}")
        if manifest:
            lines.append(f"Release manifest: {manifest}")
    else:
        lines.append(
            "Release: not recorded; run `vehicles update memory` to package and attach release metadata"
        )
    schema = payload.get("memory_schema") if isinstance(payload.get("memory_schema"), dict) else None
    if schema is not None:
        source = payload.get("memory_schema_source") if isinstance(payload.get("memory_schema_source"), dict) else {}
        lines.append(f"Schema source: {source.get('runner', 'unknown')}.describe_schema()")
        lines.extend(_format_schema_sections(schema))
    live = payload.get("live")
    if isinstance(live, dict):
        lines.append("")
        lines.append(_format_live_memory_screen(vehicle_id=payload["vehicle_id"], live=live))
    return "\n".join(lines)


def _format_schema_sections(schema: dict[str, Any]) -> list[str]:
    """Section titles shared with perception info, for the fields both steps have."""

    lines = ["", "Inputs:"]
    for item in schema.get("inputs") or []:
        if not isinstance(item, dict):
            continue
        label = item.get("name") or item.get("feed_id") or "unknown"
        required = "required" if item.get("required") else "optional"
        lines.append(f"- {label} ({required})")
        if item.get("source"):
            lines.append(f"  source: {item['source']}")
        if item.get("missing_behavior"):
            lines.append(f"  missing: {item['missing_behavior']}")
    plugins = schema.get("plugins")
    if isinstance(plugins, list) and plugins:
        lines.extend(["", "Plugins:"])
        for plugin in plugins:
            if isinstance(plugin, dict):
                lines.append(
                    f"- {plugin.get('plugin_id', 'unknown')} "
                    f"[{plugin.get('spec', 'unknown')}]"
                )
    output = schema.get("output") if isinstance(schema.get("output"), dict) else {}
    lines.extend(["", "Output:", f"- schema: {output.get('schema', 'unknown')}"])
    keys = output.get("ledger_summary_keys")
    if isinstance(keys, list) and keys:
        lines.append(f"- ledger: {', '.join(map(str, keys))}")
    if output.get("missing_field_behavior"):
        lines.append(f"- missing: {output['missing_field_behavior']}")
    composition = schema.get("composition") if isinstance(schema.get("composition"), dict) else {}
    if composition:
        lines.extend(["", "Composition:"])
        for key, value in composition.items():
            lines.append(f"- {key}: {value}")
    failure = schema.get("failure_policy") if isinstance(schema.get("failure_policy"), dict) else {}
    if failure:
        lines.extend(["", "Failure policy:"])
        for key, value in failure.items():
            lines.append(f"- {key}: {value}")
    return lines

