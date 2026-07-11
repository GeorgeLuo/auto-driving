from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.runtime import AutonomyManager, read_decision_activation

from .bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from .paths import ROOT, display_path, safe_path_part


RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))
DECISION_ENGINES: dict[str, dict[str, Any]] = {
    "idle": {
        "description": "Safe default engine that always holds position.",
        "engine_spec": "autonomy.runtime.engine:IdleAutonomyEngine",
        "engine_config": {},
    },
}


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def available_decision_engine_ids() -> tuple[str, ...]:
    return tuple(sorted(DECISION_ENGINES))


def update_vehicle_decision(
    *,
    vehicle_id: str,
    engine_id: str = "idle",
    dry_run: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    if engine_id not in DECISION_ENGINES:
        available = ", ".join(available_decision_engine_ids())
        return CommandResult(2, f"Unknown decision engine {engine_id!r}. Available engines: {available}.")

    stream = output if verbose else None
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    engine_config = DECISION_ENGINES[engine_id]
    manager = AutonomyManager(
        default_engine_spec=engine_config["engine_spec"],
        default_engine_config=dict(engine_config["engine_config"]),
    )
    release: dict[str, Any] | None = None

    if not dry_run:
        release = sync_controller_bundle(bundle, output=stream)

    activation = _decision_activation(
        vehicle_id=vehicle_id,
        engine_id=engine_id,
        bundle=bundle,
        release=release,
        manager=manager,
    )

    if not dry_run:
        activation_path.parent.mkdir(parents=True, exist_ok=True)
        activation_path.write_text(json.dumps(activation, indent=2, sort_keys=True), encoding="utf-8")

    payload = {
        "schema": "vehicle_decision_update_v0",
        "vehicle_id": vehicle_id,
        "engine_id": engine_id,
        "dry_run": dry_run,
        "activation": display_path(activation_path),
        "manifest": activation,
        "release": release_activation_summary(release) if release is not None else None,
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    verb = "Would activate" if dry_run else "Updated decision"
    return CommandResult(
        0,
        "\n".join(
            [
                f"{verb}: {vehicle_id} -> {engine_id}",
                f"Engine: {engine_config['engine_spec']}",
                f"Activation: {display_path(activation_path)}",
            ]
        ),
    )


def ensure_vehicle_decision_activation(
    *,
    vehicle_id: str,
    bundle: dict[str, str],
    release: dict[str, Any],
) -> Path:
    activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    if activation_path.exists():
        activation = read_decision_activation(activation_path).payload
        controller_bundle = activation.get("controller_bundle")
        if not isinstance(controller_bundle, dict):
            raise ValueError(f"decision activation has no controller_bundle: {activation_path}")
        controller_bundle["release"] = release_activation_summary(release)
        activation_path.write_text(json.dumps(activation, indent=2, sort_keys=True), encoding="utf-8")
        return activation_path

    engine_id = "idle"
    engine_config = DECISION_ENGINES[engine_id]
    manager = AutonomyManager(
        default_engine_spec=engine_config["engine_spec"],
        default_engine_config=dict(engine_config["engine_config"]),
    )
    activation = _decision_activation(
        vehicle_id=vehicle_id,
        engine_id=engine_id,
        bundle=bundle,
        release=release,
        manager=manager,
    )
    activation_path.parent.mkdir(parents=True, exist_ok=True)
    activation_path.write_text(json.dumps(activation, indent=2, sort_keys=True), encoding="utf-8")
    return activation_path


def get_vehicle_decision_info(*, vehicle_id: str, json_output: bool = False) -> CommandResult:
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    if not activation_path.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No active decision engine found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(activation_path)}",
                    "Run: ./cli/automa vehicles update decision --id <vehicle_id>",
                ]
            ),
        )

    try:
        activation = json.loads(activation_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return CommandResult(2, f"Could not parse decision activation {display_path(activation_path)}: {exc}")

    decision = activation.get("decision")
    if not isinstance(decision, dict):
        return CommandResult(2, f"Activation {display_path(activation_path)} has no decision section.")

    payload = {
        "schema": "vehicle_decision_info_v0",
        "vehicle_id": vehicle_id,
        "activation": {
            "path": display_path(activation_path),
            "engine_id": decision.get("engine_id"),
            "engine_spec": decision.get("engine_spec"),
            "engine_config": decision.get("engine_config"),
        },
        "engine_schema_source": {
            "kind": "engine_method",
            "method": "describe_schema",
            "engine_spec": decision.get("engine_spec"),
        },
        "engine_schema": decision.get("engine_schema"),
        "controller_bundle": activation.get("controller_bundle"),
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_decision_info(payload))


def load_decision_activation(bundle: dict[str, str]) -> dict[str, Any]:
    activation_path = Path(bundle["decision_runtime_dir"]) / "active.json"
    try:
        return read_decision_activation(activation_path).payload
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"{exc}; run `automa vehicles update decision --id <vehicle_id>`"
        ) from exc


def _decision_activation(
    *,
    vehicle_id: str,
    engine_id: str,
    bundle: dict[str, str],
    release: dict[str, Any] | None,
    manager: AutonomyManager,
) -> dict[str, Any]:
    engine_config = DECISION_ENGINES[engine_id]
    return {
        "schema": "automa_decision_activation_v0",
        "vehicle_id": vehicle_id,
        "activated_at_ms": int(time.time() * 1000),
        "controller_bundle": {
            "root_dir": bundle["root_dir"],
            "autonomy_dir": bundle["autonomy_dir"],
            "implementations_dir": bundle["implementations_dir"],
            "decision_dir": bundle["decision_dir"],
            "decision_runtime_dir": bundle["decision_runtime_dir"],
            "release": release_activation_summary(release) if release is not None else None,
        },
        "decision": {
            "engine_id": engine_id,
            "description": engine_config["description"],
            "engine_spec": engine_config["engine_spec"],
            "engine_config": dict(engine_config["engine_config"]),
            "engine_schema": manager.status()["engine_schema"],
        },
    }


def _format_decision_info(payload: dict[str, Any]) -> str:
    activation = payload["activation"]
    schema = payload.get("engine_schema") if isinstance(payload.get("engine_schema"), dict) else {}
    stages = schema.get("stages") if isinstance(schema.get("stages"), dict) else {}
    return "\n".join(
        [
            f"Decision: {payload['vehicle_id']} -> {activation.get('engine_id', 'unknown')}",
            f"Engine: {activation.get('engine_spec', 'unknown')}",
            f"Activation: {activation['path']}",
            f"Schema source: {payload['engine_schema_source']['engine_spec']}.describe_schema()",
            "",
            "Stages:",
            *(
                [f"- {name}: {value if value is not None else 'disabled'}" for name, value in stages.items()]
                or ["- none declared"]
            ),
            "",
            f"Output: {(schema.get('output') or {}).get('type', 'unknown') if isinstance(schema.get('output'), dict) else 'unknown'}",
        ]
    )
