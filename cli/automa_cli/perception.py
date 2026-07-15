from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.perception import (
    build_perception_request,
    instantiate_perception_mapper,
)
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim import ChaseSimCar
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError
from implementations.perception.catalog import (
    DEFAULT_PERCEPTION_ALGORITHM,
    PERCEPTION_ALGORITHMS,
    available_perception_algorithm_ids,
)

from .bundles import (
    AUTONOMY_DIR,
    IMPLEMENTATIONS_DIR,
    controller_bundle_source_summary,
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from .decision import ensure_vehicle_decision_activation
from .lab_plugins import PerceptionCandidate, candidate_status, get_candidate
from .paths import display_path, safe_path_part
from .perception_view import get_perception_view_status
from .vehicles import discover_active_vehicles, find_vehicle_by_id, format_active_vehicles_snapshot


ROOT = Path(__file__).resolve().parents[2]
PERCEPTION_IMPLEMENTATIONS_DIR = IMPLEMENTATIONS_DIR / "perception"
RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))
LAB_CANDIDATE_MAPPER_SPEC = "cli.automa_cli.lab_plugins:LabPerceptionMapper"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def ensure_local_perception_runtime(
    *,
    vehicle: dict[str, Any],
    algorithm: str | None = None,
    output: TextIO | None = None,
) -> dict[str, Any]:
    """Ensure a vehicle's local bundle reflects current perception source."""

    vehicle_id = str(vehicle.get("vehicle_id") or "vehicle")
    if algorithm is not None and algorithm not in PERCEPTION_ALGORITHMS:
        raise ValueError(f"unknown perception algorithm: {algorithm}")

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    existing: dict[str, Any] | None = None
    if manifest_path.exists():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded

    selected_algorithm = algorithm
    existing_algorithm: str | None = None
    if existing is not None:
        existing_perception = existing.get("perception")
        if isinstance(existing_perception, dict):
            existing_algorithm = existing_perception.get("algorithm")
    if selected_algorithm is None:
        if isinstance(existing_algorithm, str) and existing_algorithm in PERCEPTION_ALGORITHMS:
            selected_algorithm = existing_algorithm
    selected_algorithm = selected_algorithm or DEFAULT_PERCEPTION_ALGORITHM

    preserve_existing = existing_algorithm == "custom" or (
        isinstance(existing_algorithm, str) and existing_algorithm.startswith("candidate:")
    )
    if existing is not None and algorithm is None and preserve_existing:
        manifest = existing
    else:
        manifest = _activation_manifest(vehicle, selected_algorithm, bundle)
        if existing is not None:
            existing_bundle = existing.get("controller_bundle")
            existing_release = (
                existing_bundle.get("release")
                if isinstance(existing_bundle, dict)
                else None
            )
            if isinstance(existing_release, dict):
                manifest["controller_bundle"]["release"] = existing_release

    source = controller_bundle_source_summary()
    controller_bundle = manifest.get("controller_bundle")
    release_summary = controller_bundle.get("release") if isinstance(controller_bundle, dict) else None
    staged_tree = release_summary.get("tree_sha256") if isinstance(release_summary, dict) else None
    bundle_present = Path(bundle["autonomy_dir"]).is_dir() and Path(bundle["implementations_dir"]).is_dir()
    refreshed = not bundle_present or staged_tree != source["tree_sha256"]

    if refreshed:
        release = sync_controller_bundle(bundle, output=output)
        manifest["controller_bundle"]["release"] = release_activation_summary(release)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "vehicle_id": vehicle_id,
        "algorithm": manifest.get("perception", {}).get("algorithm"),
        "bundle": bundle,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "refreshed": refreshed,
        "source": source,
    }


def get_vehicle_perception_info(
    *,
    vehicle_id: str,
    json_output: bool = False,
) -> CommandResult:
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    if not manifest_path.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No active perception algorithm found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(manifest_path)}",
                    "Run: ./cli/automa vehicles update perception --id <vehicle_id>",
                ]
            ),
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return CommandResult(2, f"Could not parse perception activation {display_path(manifest_path)}: {exc}")

    mapper_spec = _manifest_get_str(manifest, "perception", "mapper_spec")
    if mapper_spec is None:
        return CommandResult(2, f"Activation {display_path(manifest_path)} does not define perception.mapper_spec.")
    mapper_config = _manifest_get_dict(manifest, "perception", "mapper_config")
    bundle_root_text = _manifest_get_str(manifest, "controller_bundle", "root_dir")
    if bundle_root_text is None:
        return CommandResult(2, f"Activation {display_path(manifest_path)} does not define controller_bundle.root_dir.")
    bundle_root = Path(bundle_root_text)
    if not bundle_root.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Controller bundle is missing for {vehicle_id!r}: {display_path(bundle_root)}",
                    "Run: ./cli/automa vehicles update perception --id <vehicle_id>",
                ]
            ),
        )

    try:
        mapper = _load_mapper(mapper_spec, mapper_config, bundle_root=bundle_root)
    except Exception as exc:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Could not load active perception for {vehicle_id!r}.",
                    f"Mapper: {mapper_spec}",
                    f"Reason: {type(exc).__name__}: {exc}",
                ]
            ),
        )
    try:
        describe = getattr(mapper, "describe_schema", None)
        if not callable(describe):
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Active mapper {mapper_spec} does not expose describe_schema().",
                        f"Activation: {display_path(manifest_path)}",
                    ]
                ),
            )
        schema = describe()
    except Exception as exc:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Could not inspect active perception for {vehicle_id!r}.",
                    f"Mapper: {mapper_spec}",
                    f"Reason: {type(exc).__name__}: {exc}",
                ]
            ),
        )
    finally:
        _close_mapper(mapper)
    automation_dir = Path(bundle["runtime_dir"]) / "automation"
    published_view, automation_status = _perception_view_with_automation_status(
        automation_dir
    )
    payload = {
        "schema": "vehicle_perception_info_v0",
        "vehicle_id": vehicle_id,
        "activation": {
            "path": display_path(manifest_path),
            "algorithm": _manifest_get_str(manifest, "perception", "algorithm"),
            "mapper_spec": mapper_spec,
            "mapper_config": mapper_config,
        },
        "controller_bundle": {
            "root_dir": display_path(bundle_root),
            "perception_source_dir": display_path(Path(_manifest_get_str(manifest, "perception", "source_dir") or "")),
            "release": _manifest_get_dict(manifest, "controller_bundle", "release"),
        },
        "algorithm_schema_source": {
            "kind": "mapper_method",
            "method": "describe_schema",
            "mapper_spec": mapper_spec,
        },
        "algorithm_schema": schema,
        "published_view": published_view,
        "automation": automation_status,
    }

    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_perception_info(payload))


def set_vehicle_perception_plugin(
    *,
    vehicle_id: str,
    plugin_id: str,
    enabled: bool,
    json_output: bool = False,
) -> CommandResult:
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    if not manifest_path.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No active perception algorithm found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(manifest_path)}",
                    "Run: ./cli/automa vehicles update perception --id <vehicle_id>",
                ]
            ),
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return CommandResult(2, f"Could not parse perception activation {display_path(manifest_path)}: {exc}")

    mapper_spec = _manifest_get_str(manifest, "perception", "mapper_spec")
    if mapper_spec is None:
        return CommandResult(2, f"Activation {display_path(manifest_path)} does not define perception.mapper_spec.")

    bundle_root_text = _manifest_get_str(manifest, "controller_bundle", "root_dir")
    if bundle_root_text is None:
        return CommandResult(2, f"Activation {display_path(manifest_path)} does not define controller_bundle.root_dir.")
    bundle_root = Path(bundle_root_text)
    if not bundle_root.exists():
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Controller bundle is missing for {vehicle_id!r}: {display_path(bundle_root)}",
                    "Run: ./cli/automa vehicles update perception --id <vehicle_id>",
                ]
            ),
        )

    perception = manifest.get("perception")
    if not isinstance(perception, dict):
        return CommandResult(2, f"Activation {display_path(manifest_path)} does not define a perception section.")

    mapper_config = _manifest_get_dict(manifest, "perception", "mapper_config")
    before = _configured_plugins({"mapper_config": mapper_config})
    try:
        available = _available_plugins(
            mapper_spec=mapper_spec,
            mapper_config=mapper_config,
            bundle_root=bundle_root,
        )
    except Exception as exc:
        return CommandResult(2, f"Could not inspect deployed mapper plugins: {exc}")
    if plugin_id not in available:
        return CommandResult(
            2,
            "\n".join(
                [
                    f"Plugin {plugin_id!r} is not available in the deployed bundle for {vehicle_id!r}.",
                    f"Available plugins: {', '.join(available) or 'none'}",
                    "Run `./cli/automa vehicles update perception --id <vehicle_id>` if local plugin code has changed.",
                ]
            ),
        )

    after = list(before)
    changed = False
    if enabled and plugin_id not in after:
        after.append(plugin_id)
        changed = True
    elif not enabled and plugin_id in after:
        after = [plugin for plugin in after if plugin != plugin_id]
        changed = True

    if changed:
        mapper_config["plugins"] = after
        try:
            mapper = _load_mapper(mapper_spec, mapper_config, bundle_root=bundle_root)
            _close_mapper(mapper)
        except Exception as exc:
            return CommandResult(2, f"Plugin change would make the mapper fail to load: {exc}")

        previous_algorithm = perception.get("algorithm")
        if previous_algorithm != "custom":
            perception["base_algorithm"] = previous_algorithm
        perception["algorithm"] = "custom"
        perception["algorithm_description"] = "Manual perception plugin selection."
        perception["mapper_config"] = mapper_config
        perception["last_plugin_change"] = {
            "plugin": plugin_id,
            "enabled": enabled,
            "changed_at_ms": int(time.time() * 1000),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    payload = {
        "schema": "vehicle_perception_plugin_update_v0",
        "vehicle_id": vehicle_id,
        "activation": display_path(manifest_path),
        "mapper_spec": mapper_spec,
        "plugin": plugin_id,
        "enabled": enabled,
        "changed": changed,
        "plugins_before": before,
        "plugins_after": after,
        "available_plugins": available,
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_plugin_update(payload))


def update_vehicle_perception(
    *,
    vehicle_id: str,
    algorithm: str | None = None,
    candidate_id: str | None = None,
    timeout_s: float = 1.0,
    restart: bool = False,
    dry_run: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    if algorithm is not None and candidate_id is not None:
        return CommandResult(2, "Choose either --algorithm or --candidate, not both.")

    candidate = None
    candidate_info: dict[str, Any] | None = None
    selected_algorithm = algorithm or DEFAULT_PERCEPTION_ALGORITHM
    if candidate_id is not None:
        try:
            candidate = get_candidate(candidate_id)
        except ValueError as exc:
            return CommandResult(2, str(exc))
        candidate_info = candidate_status(candidate)
        if not candidate_info.get("ready"):
            return CommandResult(
                2,
                f"Perception candidate {candidate_id!r} is not ready. "
                f"Run: {candidate_info['setup_command']}",
            )
        activation_name = f"candidate:{candidate_id}"
    elif selected_algorithm not in PERCEPTION_ALGORITHMS:
        available = ", ".join(available_perception_algorithm_ids())
        return CommandResult(
            2,
            f"Unknown perception algorithm {selected_algorithm!r}. Available algorithms: {available}.",
        )
    else:
        activation_name = selected_algorithm

    stream = output if verbose else None

    vehicle = _offline_sim_vehicle(vehicle_id) if not restart else None
    if vehicle is None and not restart:
        vehicle = _offline_staged_vehicle(vehicle_id)
    if vehicle is not None:
        _emit(
            stream,
            "Using local vehicle metadata; network liveness is not required for local perception staging.",
        )
    else:
        _emit(stream, f"Discovering active vehicles for id {vehicle_id!r}...")
        payload = discover_active_vehicles(
            timeout_s=timeout_s,
            include_picar=True,
            include_chase_sim=True,
            include_inactive=True,
        )
        vehicle, error = find_vehicle_by_id(payload, vehicle_id)
        if error:
            return CommandResult(
                2,
                "\n\n".join(
                    [
                        error,
                        "Discovery snapshot:",
                        format_active_vehicles_snapshot(payload, include_inactive=True),
                    ]
                ),
            )
        if vehicle is None:
            return CommandResult(2, f"Vehicle {vehicle_id!r} was not found.")

    provider = vehicle.get("provider")
    if candidate is not None and provider != "chase-sim":
        return CommandResult(
            2,
            f"Perception candidate {candidate_id!r} uses a local isolated runtime and can only "
            "be activated for a Chase simulator vehicle.",
        )
    if restart and provider != "chase-sim":
        return CommandResult(
            2,
            f"Vehicle {vehicle_id!r} is provider {provider!r}; --restart is only "
            "available for the WS-controlled simulator.",
        )

    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    perception_runtime_dir = Path(bundle["perception_runtime_dir"])
    manifest_path = perception_runtime_dir / "active.json"
    manifest = (
        _candidate_activation_manifest(
            vehicle,
            candidate,
            candidate_info or {},
            bundle,
        )
        if candidate is not None
        else _activation_manifest(vehicle, selected_algorithm, bundle)
    )

    _emit(stream, f"Selected {vehicle_id} ({provider}).")
    _emit(stream, "Scope: local perception controller bundle.")
    _emit(stream, "Vehicle and simulator source code will not be modified.")
    _emit(stream, f"Perception algorithm: {activation_name} ({manifest['perception']['mapper_spec']})")
    _emit(stream, f"Perception source: {manifest['perception']['workspace_source_dir']}")
    _emit(stream, f"Controller bundle: {bundle['root_dir']}")
    _emit(stream, f"Activation manifest: {manifest_path}")

    if dry_run:
        payload = _perception_update_payload(
            vehicle_id=vehicle_id,
            algorithm=activation_name,
            dry_run=True,
            manifest=manifest,
            bundle=bundle,
            manifest_path=manifest_path,
            release=None,
            sample_paths=None,
            restart=restart,
        )
        if json_output:
            return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
        lines = [
            f"Perception update dry run for {vehicle_id}",
            f"would package controller core {AUTONOMY_DIR}",
            f"would package controller implementations {IMPLEMENTATIONS_DIR}",
            f"would extract packaged bundle -> {bundle['root_dir']}",
            f"would write {manifest_path}",
            json.dumps(manifest, indent=2, sort_keys=True),
        ]
        if candidate is not None:
            lines.insert(3, f"would reference isolated candidate -> {candidate.directory}")
        if restart:
            lines.append("would restart WS controller handoff and capture a sample perception")
        return CommandResult(0, "\n".join(lines))

    perception_runtime_dir.mkdir(parents=True, exist_ok=True)
    release = sync_controller_bundle(bundle, output=stream)
    manifest["controller_bundle"]["release"] = release_activation_summary(release)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    ensure_vehicle_decision_activation(
        vehicle_id=vehicle_id,
        bundle=bundle,
        release=release,
    )
    _emit(stream, "Perception activation written.")

    sample_paths: dict[str, str] | None = None
    if restart:
        try:
            sample_paths = _restart_and_sample_sim_controller(
                vehicle=vehicle,
                manifest=manifest,
                perception_runtime_dir=perception_runtime_dir,
                timeout_s=timeout_s,
                verbose=verbose,
                output=stream,
            )
        except MetricsUiWebSocketError as exc:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Perception algorithm {activation_name!r} was activated for {vehicle_id}, "
                        "but restart/sample failed.",
                        f"Reason: {exc}",
                        f"Activation: {display_path(manifest_path)}",
                    ]
                ),
            )
        _emit(stream, "Sample perception:")
        for line in sample_paths["text_body"].splitlines():
            _emit(stream, f"  {line}")
        _emit(stream, f"Sample perception text: {sample_paths['text']}")
        _emit(stream, f"Sample perception JSON: {sample_paths['json']}")

    payload = _perception_update_payload(
        vehicle_id=vehicle_id,
        algorithm=activation_name,
        dry_run=False,
        manifest=manifest,
        bundle=bundle,
        manifest_path=manifest_path,
        release=release,
        sample_paths=sample_paths,
        restart=restart,
    )
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(
        0,
        _success_message(
            vehicle_id=vehicle_id,
            algorithm=activation_name,
            bundle_root=Path(bundle["root_dir"]),
            manifest_path=manifest_path,
            sample_paths=sample_paths,
        ),
    )


def ensure_vehicle_perception_activation(
    *,
    vehicle: dict[str, Any],
    algorithm: str,
    bundle: dict[str, str],
    release: dict[str, Any],
) -> Path:
    if algorithm not in PERCEPTION_ALGORITHMS:
        raise ValueError(f"unknown perception algorithm: {algorithm}")

    activation_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    if activation_path.exists():
        manifest = json.loads(activation_path.read_text(encoding="utf-8"))
        perception = manifest.get("perception")
        existing_algorithm = perception.get("algorithm") if isinstance(perception, dict) else None
        if existing_algorithm in PERCEPTION_ALGORITHMS:
            manifest = _activation_manifest(vehicle, existing_algorithm, bundle)
        elif existing_algorithm != "custom":
            manifest = _activation_manifest(vehicle, algorithm, bundle)
    else:
        manifest = _activation_manifest(vehicle, algorithm, bundle)

    controller_bundle = manifest.get("controller_bundle")
    if not isinstance(controller_bundle, dict):
        raise ValueError(f"perception activation has no controller_bundle: {activation_path}")
    controller_bundle["release"] = release_activation_summary(release)
    activation_path.parent.mkdir(parents=True, exist_ok=True)
    activation_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return activation_path


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


def _offline_staged_vehicle(vehicle_id: str) -> dict[str, Any] | None:
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    activation_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    if not activation_path.is_file():
        return None
    try:
        activation = json.loads(activation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    provider = activation.get("provider")
    vehicle_kind = activation.get("vehicle_kind")
    if not isinstance(provider, str) or not provider:
        return None
    runtime = activation.get("runtime")
    connection = runtime.get("connection") if isinstance(runtime, dict) else None
    return {
        "vehicle_id": vehicle_id,
        "vehicle_kind": vehicle_kind or provider,
        "provider": provider,
        "connection": connection if isinstance(connection, dict) else {},
        "status": {
            "ok": None,
            "note": "offline local staging metadata; vehicle liveness was not checked",
        },
    }


def _perception_update_payload(
    *,
    vehicle_id: str,
    algorithm: str,
    dry_run: bool,
    manifest: dict[str, Any],
    bundle: dict[str, str],
    manifest_path: Path,
    release: dict[str, Any] | None,
    sample_paths: dict[str, str] | None,
    restart: bool,
) -> dict[str, Any]:
    return {
        "schema": "vehicle_perception_update_v0",
        "vehicle_id": vehicle_id,
        "algorithm": algorithm,
        "dry_run": dry_run,
        "restart_requested": restart,
        "would_write": {
            "bundle_root": display_path(Path(bundle["root_dir"])),
            "activation": display_path(manifest_path),
        },
        "manifest": manifest,
        "release": release_activation_summary(release) if release is not None else None,
        "sample": sample_paths,
    }


def _activation_manifest(
    vehicle: dict[str, Any],
    algorithm: str,
    bundle: dict[str, str],
) -> dict[str, Any]:
    algorithm_config = PERCEPTION_ALGORITHMS[algorithm]
    manifest = _activation_manifest_base(vehicle, bundle)
    manifest["perception"] = {
        "algorithm": algorithm,
        "algorithm_description": algorithm_config["description"],
        "source_dir": bundle["perception_dir"],
        "workspace_source_dir": str(PERCEPTION_IMPLEMENTATIONS_DIR),
        "mapper_spec": algorithm_config["mapper_spec"],
        "mapper_config": dict(algorithm_config["mapper_config"]),
        "output_contract": dict(algorithm_config["output_contract"]),
    }
    return manifest


def _candidate_activation_manifest(
    vehicle: dict[str, Any],
    candidate: PerceptionCandidate,
    candidate_info: dict[str, Any],
    bundle: dict[str, str],
) -> dict[str, Any]:
    manifest = _activation_manifest_base(vehicle, bundle)
    manifest["perception"] = {
        "algorithm": f"candidate:{candidate.candidate_id}",
        "algorithm_description": str(
            candidate.manifest.get("description") or candidate.candidate_id
        ),
        "source_dir": str(candidate.directory),
        "workspace_source_dir": str(candidate.directory),
        "mapper_spec": LAB_CANDIDATE_MAPPER_SPEC,
        "mapper_config": {
            "candidate_id": candidate.candidate_id,
            "timeout_s": 180.0,
        },
        "output_contract": dict(candidate.manifest.get("output") or {}),
        "candidate": {
            "id": candidate.candidate_id,
            "manifest_path": str(candidate.manifest_path),
            "source_tree_sha256": candidate_info.get("source_tree_sha256"),
            "runtime": dict(candidate_info.get("runtime") or {}),
            "model": dict(candidate_info.get("model") or {}),
        },
    }
    return manifest


def _activation_manifest_base(
    vehicle: dict[str, Any],
    bundle: dict[str, str],
) -> dict[str, Any]:
    provider = vehicle.get("provider")
    runtime_kind = "ws_cli_controller" if provider == "chase-sim" else "onboard_controller"
    return {
        "schema": "automa_perception_activation_v0",
        "vehicle_id": vehicle.get("vehicle_id"),
        "vehicle_kind": vehicle.get("vehicle_kind"),
        "provider": vehicle.get("provider"),
        "activated_at_ms": int(time.time() * 1000),
        "runtime": {
            "kind": runtime_kind,
            "connection": vehicle.get("connection"),
        },
        "controller_bundle": {
            "root_dir": bundle["root_dir"],
            "autonomy_dir": bundle["autonomy_dir"],
            "implementations_dir": bundle["implementations_dir"],
            "perception_dir": bundle["perception_dir"],
            "runtime_dir": bundle["runtime_dir"],
            "perception_runtime_dir": bundle["perception_runtime_dir"],
            "copied_from": {
                "autonomy": str(AUTONOMY_DIR),
                "implementations": str(IMPLEMENTATIONS_DIR),
            },
        },
    }


def _restart_and_sample_sim_controller(
    *,
    vehicle: dict[str, Any],
    manifest: dict[str, Any],
    perception_runtime_dir: Path,
    timeout_s: float,
    verbose: bool,
    output: TextIO | None,
) -> dict[str, str]:
    raw_connection = vehicle.get("connection")
    connection: dict[str, Any] = raw_connection if isinstance(raw_connection, dict) else {}
    ws_url = connection.get("ws_url") if isinstance(connection.get("ws_url"), str) else None
    sample_dir = perception_runtime_dir / "sample"
    if sample_dir.exists():
        shutil.rmtree(sample_dir)

    _emit(output, "==> Restart simulator WS controller handoff")
    car = ChaseSimCar(ws_url=ws_url, timeout_s=timeout_s)
    try:
        debug = car.client.get_play_debug(timeout_s=timeout_s)
    except MetricsUiWebSocketError as exc:
        raise MetricsUiWebSocketError(
            "Chase Play frontend is not connected; open the Metrics UI Play/Chase frontend "
            "before using --restart. "
            f"Underlying WS response: {exc}"
        ) from exc
    if debug.get("gameId") != "chase":
        raise MetricsUiWebSocketError(
            f"Chase Play frontend is connected, but active gameId is {debug.get('gameId')!r}; "
            "load the Chase example first."
        )

    preparation = car.prepare_for_external_control()
    if verbose:
        _emit(output, json.dumps(preparation, indent=2, sort_keys=True))

    _emit(output, "==> Capture simulator front-view sample")
    snapshot = car.read_sensors(
        SensorReadRequest(
            output_dir=perception_runtime_dir / "sample" / "sensors",
            read_id="current",
            requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
            image_extension="png",
        ),
    )

    _emit(output, "==> Run active perception mapper")
    mapper = _load_mapper(
        manifest["perception"]["mapper_spec"],
        manifest["perception"]["mapper_config"],
        bundle_root=Path(manifest["controller_bundle"]["root_dir"]),
    )
    try:
        perception = mapper.perceive(
            build_perception_request(
                snapshot,
                output_dir=sample_dir / "perception",
                metadata={
                    "activation": str(perception_runtime_dir / "active.json"),
                    "vehicle_id": vehicle.get("vehicle_id"),
                },
            ),
        )
    finally:
        _close_mapper(mapper)

    json_path = sample_dir / "perception.json"
    text_path = sample_dir / "perception.txt"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(perception.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    text_path.write_text(perception.text + "\n", encoding="utf-8")
    return {
        "json": str(json_path),
        "text": str(text_path),
        "text_body": perception.text,
    }


def _load_mapper(
    mapper_spec: str,
    mapper_config: dict[str, Any],
    *,
    bundle_root: Path | None = None,
):
    module_name, separator, class_name = mapper_spec.partition(":")
    if not separator:
        raise ValueError("mapper spec must be 'module.path:ClassName'")
    if bundle_root is None or mapper_spec == LAB_CANDIDATE_MAPPER_SPEC:
        return instantiate_perception_mapper(mapper_spec, mapper_config)
    return _instantiate_mapper_from_bundle(
        module_name,
        class_name,
        mapper_config,
        bundle_root,
    )


def _instantiate_mapper_from_bundle(
    module_name: str,
    class_name: str,
    mapper_config: dict[str, Any],
    bundle_root: Path,
):
    """Construct a staged mapper while all of its imports resolve to one bundle."""

    bundle_root_text = str(bundle_root)
    staged_prefixes = ("autonomy", "implementations")
    cached = {
        name: module
        for name, module in list(sys.modules.items())
        if name in staged_prefixes or any(name.startswith(f"{prefix}.") for prefix in staged_prefixes)
    }
    for name in cached:
        sys.modules.pop(name, None)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.path.insert(0, bundle_root_text)
    try:
        module = importlib.import_module(module_name)
        mapper_cls = getattr(module, class_name)
        mapper = mapper_cls(**mapper_config)
        for method_name in ("reset", "describe_schema", "perceive"):
            if not callable(getattr(mapper, method_name, None)):
                raise TypeError(
                    f"staged perception mapper {module_name}:{class_name} "
                    f"does not implement {method_name}()"
                )
        mapper.reset()
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
        try:
            sys.path.remove(bundle_root_text)
        except ValueError:
            pass
        for name in [
            name
            for name in list(sys.modules)
            if name in staged_prefixes or any(name.startswith(f"{prefix}.") for prefix in staged_prefixes)
        ]:
            sys.modules.pop(name, None)
        sys.modules.update(cached)
    return mapper


def _close_mapper(mapper: Any) -> None:
    close = getattr(mapper, "close", None)
    if callable(close):
        close()


def _emit(output: TextIO | None, message: str) -> None:
    if output is None:
        return
    print(message, file=output, flush=True)


def _success_message(
    *,
    vehicle_id: str,
    algorithm: str,
    bundle_root: Path,
    manifest_path: Path,
    sample_paths: dict[str, str] | None,
) -> str:
    lines = [
        f"Updated perception: {vehicle_id} -> {algorithm}",
        f"Bundle: {display_path(bundle_root)}",
        f"Activation: {display_path(manifest_path)}",
    ]
    if sample_paths is not None:
        lines.append(f"Sample: {display_path(Path(sample_paths['text']))}")
    return "\n".join(lines)


def _manifest_get_str(manifest: dict[str, Any], section: str, key: str) -> str | None:
    value = manifest.get(section)
    if not isinstance(value, dict):
        return None
    found = value.get(key)
    return found if isinstance(found, str) else None


def _manifest_get_dict(manifest: dict[str, Any], section: str, key: str) -> dict[str, Any]:
    value = manifest.get(section)
    if not isinstance(value, dict):
        return {}
    found = value.get(key)
    return dict(found) if isinstance(found, dict) else {}


def _format_perception_info(payload: dict[str, Any]) -> str:
    activation = payload["activation"]
    bundle = payload["controller_bundle"]
    schema = payload["algorithm_schema"]
    release = bundle.get("release") if isinstance(bundle.get("release"), dict) else {}
    algorithm = activation.get("algorithm") or "unknown"
    lines = [
        f"Perception: {payload['vehicle_id']} -> {algorithm}",
        f"Mapper: {activation['mapper_spec']}",
    ]
    if isinstance(algorithm, str) and algorithm.startswith("candidate:"):
        lines.append(f"Candidate: {algorithm.removeprefix('candidate:')} (isolated local runtime)")
    else:
        lines.append(f"Enabled plugins: {', '.join(_configured_plugins(activation)) or 'none'}")
    lines.extend(
        [
            _format_published_view(payload.get("published_view")),
            f"Bundle: {bundle['root_dir']}",
            f"Activation: {activation['path']}",
        ]
    )
    if release:
        archive = release.get("archive")
        manifest = release.get("manifest")
        lines.append(f"Release: {release.get('tree_sha256', 'unknown')}")
        if archive:
            lines.append(f"Archive: {archive}")
        if manifest:
            lines.append(f"Release manifest: {manifest}")
    else:
        lines.append("Release: not recorded; run `vehicles update perception` to package and attach release metadata")
    lines.extend(
        [
            f"Schema source: {payload['algorithm_schema_source']['mapper_spec']}.describe_schema()",
            "",
            "Inputs:",
        ]
    )

    for item in schema.get("inputs", []):
        if not isinstance(item, dict):
            continue
        required = "required" if item.get("required") else "optional"
        lines.append(f"- {item.get('component_id', 'unknown')} ({required})")
        required_by = item.get("required_by")
        if isinstance(required_by, list) and required_by:
            lines.append(f"  requested by: {', '.join(map(str, required_by))}")
        source = item.get("source")
        if source:
            lines.append(f"  source: {source}")
        missing = item.get("missing_behavior")
        if missing:
            lines.append(f"  missing: {missing}")
        translations = item.get("translations")
        if isinstance(translations, list) and translations:
            lines.append("  translations:")
            for translation in translations:
                if not isinstance(translation, dict):
                    continue
                emits = translation.get("emits")
                emit_text = f" -> {', '.join(map(str, emits))}" if isinstance(emits, list) and emits else ""
                lines.append(
                    f"  - {translation.get('name', 'unnamed')} "
                    f"[{translation.get('implementation', 'unknown')}]{emit_text}"
                )

    plugins = schema.get("plugins")
    if isinstance(plugins, list) and plugins:
        lines.extend(["", "Plugins:"])
        for plugin in plugins:
            if not isinstance(plugin, dict):
                continue
            contract = plugin.get("contract") if isinstance(plugin.get("contract"), dict) else {}
            inputs = contract.get("inputs")
            component_text = (
                ", ".join(
                    str(item.get("component_id", "unknown"))
                    for item in inputs
                    if isinstance(item, dict)
                )
                if isinstance(inputs, list) and inputs
                else "none"
            )
            lines.append(
                f"- {plugin.get('plugin_id', 'unknown')} "
                f"[{contract.get('state_mode', 'unknown')}] components={component_text}"
            )

    output_schema = schema.get("output") if isinstance(schema.get("output"), dict) else {}
    lines.extend(
        [
            "",
            "Output:",
            f"- schema: {output_schema.get('schema', 'unknown')}",
            f"- format: {output_schema.get('format', 'unknown')}",
        ]
    )
    records = output_schema.get("records")
    if isinstance(records, list) and records:
        lines.append("- records:")
        for record in records:
            if not isinstance(record, dict):
                continue
            lines.append(f"  - {_format_output_record(record)}")
    limits = output_schema.get("limits")
    if isinstance(limits, list) and limits:
        lines.append("- limits:")
        for limit in limits:
            lines.append(f"  - {limit}")
    return "\n".join(lines)


def _format_published_view(value: Any) -> str:
    view = value if isinstance(value, dict) else {}
    if view.get("available") and view.get("url"):
        return f"Perception view: {view['url']}"
    reason = view.get("reason") or "automation view is not running"
    if view.get("status") == "starting":
        return f"Perception view: starting ({reason})"
    if view.get("status") == "error":
        return f"Perception view: unavailable ({reason})"
    return f"Perception view: unavailable ({reason}); start or restart the automation worker"


def _perception_view_with_automation_status(
    automation_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = _read_json_file(automation_dir / "state.json")
    process = _read_json_file(automation_dir / "process.json")
    state = state if isinstance(state, dict) else {}
    process = process if isinstance(process, dict) else {}
    pid = state.get("pid") if isinstance(state.get("pid"), int) else process.get("pid")
    running = _process_alive(pid) if isinstance(pid, int) else False
    status = str(state.get("status") or "not_started")
    runtime = {
        "status": status,
        "pid": pid,
        "running": running,
        "state_path": display_path(automation_dir / "state.json"),
        "error": state.get("error") if isinstance(state.get("error"), str) else None,
    }
    view = get_perception_view_status(automation_dir)
    if view.get("available"):
        return view, runtime
    if status in {"launching", "starting"}:
        if running:
            reason = f"automation worker PID {pid} is still initializing"
            return {**view, "status": "starting", "reason": reason}, runtime
        reason = f"automation worker exited during startup (recorded PID {pid})"
        return {**view, "status": "error", "reason": reason}, runtime
    if status == "error":
        detail = runtime["error"] or "automation worker reported a startup or runtime error"
        summary = next(
            (line.strip() for line in str(detail).splitlines() if line.strip()),
            "automation worker reported an error",
        ).rstrip(".;:")
        reason = f"{summary}; details: {runtime['state_path']}"
        return {**view, "status": "error", "reason": reason}, runtime
    return view, runtime


def _read_json_file(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _format_output_record(record: dict[str, Any]) -> str:
    if isinstance(record.get("record"), str):
        described_parts = [record["record"]]
        if isinstance(record.get("meaning"), str):
            described_parts.append(f"- {record['meaning']}")
        return " ".join(described_parts)

    parts: list[str] = []
    if isinstance(record.get("thing_id"), str):
        parts.append(record["thing_id"])
    if isinstance(record.get("thing_kind"), str):
        parts.append(f"kind={record['thing_kind']}")
    if isinstance(record.get("frame"), str):
        parts.append(f"frame={record['frame']}")
    if isinstance(record.get("zone"), str):
        parts.append(f"zone={record['zone']}")
    if isinstance(record.get("when"), str):
        parts.append(f"when={record['when']}")
    if isinstance(record.get("meaning"), str):
        parts.append(f"- {record['meaning']}")
    return " ".join(parts) if parts else json.dumps(record, sort_keys=True)


def _configured_plugins(activation: dict[str, Any]) -> list[str]:
    mapper_config = activation.get("mapper_config")
    if not isinstance(mapper_config, dict):
        return []
    plugins = mapper_config.get("plugins")
    if not isinstance(plugins, list):
        return []
    return [str(plugin) for plugin in plugins]


def _available_plugins(*, mapper_spec: str, mapper_config: dict[str, Any], bundle_root: Path) -> list[str]:
    module_name, separator, _class_name = mapper_spec.partition(":")
    if not separator:
        raise ValueError("mapper spec must be 'module.path:ClassName'")

    mapper = _load_mapper(mapper_spec, mapper_config, bundle_root=bundle_root)
    try:
        describe = getattr(mapper, "describe_schema", None)
        if not callable(describe):
            return []
        schema = describe()
    finally:
        _close_mapper(mapper)
    configuration = schema.get("configuration") if isinstance(schema, dict) else {}
    available = configuration.get("available_plugins") if isinstance(configuration, dict) else []
    if isinstance(available, list):
        return sorted(str(plugin) for plugin in available)
    return []


def _format_plugin_update(payload: dict[str, Any]) -> str:
    action = "enabled" if payload["enabled"] else "disabled"
    status = "Updated" if payload["changed"] else "No change"
    return "\n".join(
        [
            f"{status}: {payload['plugin']} {action} for {payload['vehicle_id']}",
            f"Activation: {payload['activation']}",
            f"Enabled plugins: {', '.join(payload['plugins_after']) or 'none'}",
        ]
    )
