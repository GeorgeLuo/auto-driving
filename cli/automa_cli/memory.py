"""Stage and inspect vehicle memory activations."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision import MEMORY_ACTIVATION_SCHEMA, read_memory_activation
from implementations.memory import (
    DEFAULT_MEMORY_IMPLEMENTATION,
    available_memory_implementation_ids,
    build_memory_activation_payload,
    memory_implementation_spec,
)

from .bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from .paths import ROOT, display_path, safe_path_part


RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def update_vehicle_memory(
    *,
    vehicle_id: str,
    implementation_id: str = DEFAULT_MEMORY_IMPLEMENTATION,
    dry_run: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    known = available_memory_implementation_ids()
    if implementation_id not in known:
        available = ", ".join(known) or "(none)"
        return CommandResult(
            2,
            f"Unknown memory implementation {implementation_id!r}. Available: {available}.",
        )

    stream = output if verbose else None
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    release: dict[str, Any] | None = None

    if not dry_run:
        release = sync_controller_bundle(bundle, output=stream)

    activation = _memory_activation(
        vehicle_id=vehicle_id,
        implementation_id=implementation_id,
        bundle=bundle,
        release=release,
    )

    if not dry_run:
        activation_path.parent.mkdir(parents=True, exist_ok=True)
        activation_path.write_text(
            json.dumps(activation, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    entry = memory_implementation_spec(implementation_id)
    payload = {
        "schema": "vehicle_memory_update_v0",
        "vehicle_id": vehicle_id,
        "implementation_id": implementation_id,
        "dry_run": dry_run,
        "activation": display_path(activation_path),
        "manifest": activation,
        "release": release_activation_summary(release) if release is not None else None,
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    verb = "Would activate" if dry_run else "Updated memory"
    return CommandResult(
        0,
        "\n".join(
            [
                f"{verb}: {vehicle_id} -> {implementation_id}",
                f"Implementation: {entry['implementation_spec']}",
                f"Activation: {display_path(activation_path)}",
            ]
        ),
    )


def ensure_vehicle_memory_activation(
    *,
    vehicle_id: str,
    bundle: dict[str, str],
    release: dict[str, Any],
    implementation_id: str = DEFAULT_MEMORY_IMPLEMENTATION,
) -> Path:
    """Ensure a current-schema memory activation exists for autonomy deploy."""

    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    if activation_path.exists():
        activation = read_memory_activation(activation_path).payload
        controller_bundle = activation.get("controller_bundle")
        if not isinstance(controller_bundle, dict):
            controller_bundle = {}
            activation["controller_bundle"] = controller_bundle
        controller_bundle["release"] = release_activation_summary(release)
        activation_path.write_text(
            json.dumps(activation, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return activation_path

    activation = _memory_activation(
        vehicle_id=vehicle_id,
        implementation_id=implementation_id,
        bundle=bundle,
        release=release,
    )
    activation_path.parent.mkdir(parents=True, exist_ok=True)
    activation_path.write_text(
        json.dumps(activation, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return activation_path


def get_vehicle_memory_info(*, vehicle_id: str, json_output: bool = False) -> CommandResult:
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    activation_path = Path(bundle["memory_runtime_dir"]) / "active.json"
    if not activation_path.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No active memory implementation found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(activation_path)}",
                    "Run: ./cli/automa vehicles update memory --id <vehicle_id>",
                ]
            ),
        )

    try:
        activation = read_memory_activation(activation_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        return CommandResult(
            2,
            f"Could not read memory activation {display_path(activation_path)}: {exc}",
        )

    memory = activation.payload.get("memory")
    if not isinstance(memory, dict):
        return CommandResult(
            2,
            f"Activation {display_path(activation_path)} has no memory section.",
        )

    payload = {
        "schema": "vehicle_memory_info_v0",
        "vehicle_id": vehicle_id,
        "activation": {
            "path": display_path(activation_path),
            "implementation_id": activation.implementation_id,
            "implementation_spec": activation.implementation_spec,
            "implementation_config": activation.implementation_config,
            "bounds": activation.bounds.to_dict(),
        },
        "description": memory.get("description"),
        "controller_bundle": activation.payload.get("controller_bundle"),
        "lifecycle": {
            "methods": ["update", "reset", "snapshot"],
            "health": ["empty", "healthy", "unavailable", "error"],
            "claims_identity": False,
        },
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_memory_info(payload))


def _memory_activation(
    *,
    vehicle_id: str,
    implementation_id: str,
    bundle: dict[str, str],
    release: dict[str, Any] | None,
) -> dict[str, Any]:
    base = build_memory_activation_payload(implementation_id)
    entry = memory_implementation_spec(implementation_id)
    activation = {
        "schema": MEMORY_ACTIVATION_SCHEMA,
        "vehicle_id": vehicle_id,
        "activated_at_ms": int(time.time() * 1000),
        "controller_bundle": {
            "root_dir": bundle["root_dir"],
            "autonomy_dir": bundle["autonomy_dir"],
            "implementations_dir": bundle["implementations_dir"],
            "memory_runtime_dir": bundle["memory_runtime_dir"],
            "release": release_activation_summary(release) if release is not None else None,
        },
        "memory": {
            "implementation_id": entry["implementation_id"],
            "description": entry["description"],
            "implementation_spec": entry["implementation_spec"],
            "implementation_config": dict(base["memory"]["implementation_config"]),
        },
    }
    return activation


def _format_memory_info(payload: dict[str, Any]) -> str:
    activation = payload["activation"]
    bounds = activation.get("bounds") if isinstance(activation.get("bounds"), dict) else {}
    return "\n".join(
        [
            f"Memory: {payload['vehicle_id']} -> {activation.get('implementation_id', 'unknown')}",
            f"Implementation: {activation.get('implementation_spec', 'unknown')}",
            f"Activation: {activation['path']}",
            (
                f"Bounds: max_records={bounds.get('max_records')} "
                f"max_age_ms={bounds.get('max_age_ms')} "
                f"eviction={bounds.get('eviction_policy')}"
            ),
            "Lifecycle: update / reset / snapshot",
            "Identity claims: false",
        ]
    )
