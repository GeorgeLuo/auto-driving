from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

from autonomy.decision_cycle.activation import (
    load_activation_json,
    step_activation_from_payload,
    write_step_activation,
)
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim import ChaseSimCar
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError
from implementations.decision_cycle.catalog import CUSTOM_PRESET, selection_activation
from implementations.decision_cycle.perception.presets import (
    CUSTOM_PERCEPTION_DESCRIPTION,
    PERCEPTION_PRESETS,
    available_perception_preset_ids,
)

from .bundles import (
    AUTONOMY_DIR,
    IMPLEMENTATIONS_DIR,
    controller_bundle_source_summary,
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from .step_activations import ensure_builtin_activations, staging_metadata, staging_vehicle, step_update_error
from .step_hosting import load_staged_runner
from .paths import display_path, safe_path_part
from .perception_view import get_perception_view_status
from .physical_observation import (
    LATEST_FRAME_PATH,
    LATEST_JSON_PATH,
    fetch_observation_publication,
    physical_observation_dir,
    physical_view_status,
    picar_base_url,
)
from .vehicles import (
    DEFAULT_CHASE_READINESS_TIMEOUT_S,
    READINESS_SCHEMA,
    discover_active_vehicles,
    find_vehicle_by_id,
    get_vehicle_status,
)


ROOT = Path(__file__).resolve().parents[2]
PERCEPTION_IMPLEMENTATIONS_DIR = IMPLEMENTATIONS_DIR / "decision_cycle" / "perception"
RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def ensure_local_perception_runtime(
    *,
    vehicle: dict[str, Any],
    preset: str | None = None,
    plugins: list[str] | None = None,
    output: TextIO | None = None,
) -> dict[str, Any]:
    """Ensure a vehicle's local bundle reflects current perception source.

    The staged selection keeps its custom plugins or named preset (the default
    when none is staged). ``preset`` or ``plugins`` select what the returned
    manifest runs without restaging; ``update perception`` stages a selection.
    """

    vehicle_id = str(vehicle.get("vehicle_id") or "vehicle")

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    existing: dict[str, Any] | None = None
    if manifest_path.exists():
        existing = _read_manifest(manifest_path)

    existing_preset = _manifest_preset(existing) if existing is not None else None
    if existing is not None and existing_preset == CUSTOM_PRESET:
        staged = existing
    else:
        staged_preset = existing_preset if existing_preset in PERCEPTION_PRESETS else None
        staged = _activation_manifest(vehicle, selection_activation("perception", preset=staged_preset), bundle)
        if existing is not None:
            existing_release = _manifest_bundle(existing).get("release")
            if isinstance(existing_release, dict):
                _manifest_bundle(staged)["release"] = existing_release

    source = controller_bundle_source_summary()
    release_summary = _manifest_bundle(staged).get("release")
    staged_tree = release_summary.get("tree_sha256") if isinstance(release_summary, dict) else None
    bundle_present = Path(bundle["autonomy_dir"]).is_dir() and Path(bundle["implementations_dir"]).is_dir()
    refreshed = not bundle_present or staged_tree != source["tree_sha256"]

    if refreshed:
        release = sync_controller_bundle(bundle, output=output)
        _manifest_bundle(staged)["release"] = release_activation_summary(release)
    _write_manifest(manifest_path, staged)

    manifest = staged
    if preset is not None or plugins:
        manifest = _activation_manifest(
            vehicle, selection_activation("perception", preset=preset, plugins=plugins), bundle
        )
        _manifest_bundle(manifest)["release"] = _manifest_bundle(staged).get("release")

    return {
        "vehicle_id": vehicle_id,
        "preset": _manifest_preset(manifest),
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
    timeout_s: float = 3.0,
) -> CommandResult:
    vehicle_runtime_dir = RUNTIME_ROOT / safe_path_part(vehicle_id)
    bundle = controller_bundle_paths(vehicle_runtime_dir)
    manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    has_local_activation = manifest_path.exists()
    # Discovery is an enrichment read for staged inspection, not a gate.  It
    # must still run when active.json exists so a reachable PiRacer cannot be
    # silently hidden behind the local activation record.
    live = _resolve_live_vehicle(vehicle_id, timeout_s=timeout_s)
    live_vehicle: dict[str, Any] | None = None
    live_vehicle = (
        live.get("vehicle") if isinstance(live.get("vehicle"), dict) else None
    )
    live_provider = live_vehicle.get("provider") if live_vehicle is not None else None

    if not has_local_activation and live_provider != "picar":
        return CommandResult(
            2,
            "\n".join(
                [
                    f"No active perception preset found for {vehicle_id!r}.",
                    f"Expected activation: {display_path(manifest_path)}",
                    "Run: ./cli/automa vehicles update perception --id <vehicle_id>",
                ]
            ),
        )

    payload: dict[str, Any] = {
        "schema": "vehicle_perception_info_v0",
        "vehicle_id": vehicle_id,
    }

    if has_local_activation:
        try:
            manifest = _read_manifest(manifest_path)
        except ValueError as exc:
            return CommandResult(2, str(exc))

        bundle_root_text = _manifest_bundle(manifest).get("root_dir")
        if not isinstance(bundle_root_text, str) or not bundle_root_text:
            return CommandResult(
                2,
                f"Activation {display_path(manifest_path)} does not record its controller bundle.",
            )
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

        plugins_text = ", ".join(manifest["plugins"]) or "(none)"
        try:
            runner = load_staged_runner(_manifest_activation(manifest, manifest_path))
        except Exception as exc:
            return CommandResult(
                2,
                "\n".join(
                    [
                        f"Could not load active perception for {vehicle_id!r}.",
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
                        f"Could not inspect active perception for {vehicle_id!r}.",
                        f"Plugins: {plugins_text}",
                        f"Reason: {type(exc).__name__}: {exc}",
                    ]
                ),
            )

        automation_dir = Path(bundle["runtime_dir"]) / "automation"
        published_view, automation_status = _perception_view_with_automation_status(
            automation_dir
        )
        payload.update(
            {
                "activation": {
                    "path": display_path(manifest_path),
                    "preset": _manifest_preset(manifest),
                    "plugins": list(manifest["plugins"]),
                    "plugin_specs": dict(manifest["plugin_specs"]),
                    "plugin_configs": dict(manifest["plugin_configs"]),
                },
                "controller_bundle": {
                    "root_dir": display_path(bundle_root),
                    "perception_source_dir": display_path(
                        Path(_manifest_get_str(manifest, "metadata", "source_dir") or "")
                    ),
                    "release": _manifest_get_dict(manifest["metadata"], "controller_bundle", "release"),
                },
                "perception_schema_source": {
                    "kind": "runner_method",
                    "method": "describe_schema",
                    "runner": "autonomy.decision_cycle.perception.runner:PerceptionRunner",
                },
                "perception_schema": schema,
                "published_view": published_view,
                "automation": automation_status,
            }
        )
    else:
        payload.update(
            {
                "activation": None,
                "controller_bundle": None,
                "perception_schema_source": None,
                "perception_schema": None,
                "published_view": {
                    "available": False,
                    "status": "unavailable",
                    "reason": "no local staged activation; physical live view uses stream",
                },
                "automation": {"status": "not_started", "running": False},
            }
        )

    if live_provider == "picar" and live_vehicle is not None:
        payload["live_observation"] = _live_physical_observation_info(
            vehicle_id=vehicle_id,
            vehicle=live_vehicle,
            timeout_s=timeout_s,
        )
        # Prefer the physical stream view when it is running.
        live_view = payload["live_observation"].get("published_view")
        if isinstance(live_view, dict) and live_view.get("available"):
            payload["published_view"] = live_view
    else:
        payload["live_observation"] = _unavailable_live_observation(
            live,
            vehicle=live_vehicle,
        )

    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_perception_info(payload))


def update_vehicle_perception(
    *,
    vehicle_id: str,
    preset: str | None = None,
    plugins: list[str] | None = None,
    timeout_s: float = DEFAULT_CHASE_READINESS_TIMEOUT_S,
    restart: bool = False,
    dry_run: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    if preset is not None and plugins:
        return CommandResult(2, "Choose either --preset or --plugin, not both.")
    if preset is not None and preset not in PERCEPTION_PRESETS:
        available = ", ".join(available_perception_preset_ids())
        return CommandResult(
            2,
            f"Unknown perception preset {preset!r}. Available presets: {available}.",
        )
    try:
        activation = selection_activation("perception", preset=preset, plugins=plugins)
    except ValueError as exc:
        return CommandResult(2, str(exc))
    activation_name = activation.metadata["preset"]

    stream = output if verbose else None

    # --restart drives the live simulator, so it never stages from offline metadata.
    vehicle, unknown = staging_vehicle(
        vehicle_id, runtime_root=RUNTIME_ROOT, timeout_s=timeout_s, offline=not restart, output=stream
    )
    if vehicle is None:
        return CommandResult(*step_update_error(vehicle_id, "perception", "unknown_vehicle", unknown, json_output=json_output))

    provider = vehicle.get("provider")
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
    manifest = _activation_manifest(vehicle, activation, bundle)

    _emit(stream, f"Selected {vehicle_id} ({provider}).")
    _emit(stream, "Scope: local perception controller bundle.")
    _emit(stream, "Vehicle and simulator source code will not be modified.")
    _emit(stream, f"Perception preset: {activation_name} ({', '.join(manifest['plugins'])})")
    _emit(stream, f"Perception source: {manifest['metadata']['workspace_source_dir']}")
    _emit(stream, f"Controller bundle: {bundle['root_dir']}")
    _emit(stream, f"Activation manifest: {manifest_path}")

    if dry_run:
        payload = _perception_update_payload(
            vehicle_id=vehicle_id,
            preset=activation_name,
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
        if restart:
            lines.append("would restart WS controller handoff and capture a sample perception")
        return CommandResult(0, "\n".join(lines))

    perception_runtime_dir.mkdir(parents=True, exist_ok=True)
    release = sync_controller_bundle(bundle, output=stream)
    _manifest_bundle(manifest)["release"] = release_activation_summary(release)
    _write_manifest(manifest_path, manifest)
    ensure_builtin_activations(
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
                        f"Perception preset {activation_name!r} was activated for {vehicle_id}, "
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
        preset=activation_name,
        dry_run=False,
        manifest=manifest,
        bundle=bundle,
        manifest_path=manifest_path,
        release=release,
        sample_paths=sample_paths,
        restart=restart,
    )
    readiness = None
    next_action = None
    readiness_exit_code = 0
    if provider == "chase-sim" and not restart:
        connection = (
            vehicle.get("connection")
            if isinstance(vehicle.get("connection"), dict)
            else {}
        )
        ws_url = (
            connection.get("ws_url")
            if isinstance(connection.get("ws_url"), str)
            else None
        )
        status = get_vehicle_status(
            vehicle_id=vehicle_id,
            chase_ws_url=ws_url,
            timeout_s=timeout_s,
        )
        readiness, next_action = _automation_run_readiness(status)
        payload["readiness"] = readiness
        payload["next_action"] = next_action
        if readiness["status"] != "ready":
            readiness_exit_code = 2
    if json_output:
        return CommandResult(
            readiness_exit_code,
            json.dumps(payload, indent=2, sort_keys=True),
        )
    message = _success_message(
        vehicle_id=vehicle_id,
        preset=activation_name,
        bundle_root=Path(bundle["root_dir"]),
        manifest_path=manifest_path,
        sample_paths=sample_paths,
    )
    if readiness is not None:
        label = "Ready for" if readiness["status"] == "ready" else "Not ready for"
        message += f"\n{label}: {readiness['ready_for']}"
        if isinstance(next_action, dict):
            recovery = next_action.get("command")
            if recovery is None:
                recovery = json.dumps(next_action.get("external_change"), sort_keys=True)
            message += f"\nNext action: {recovery}"
    return CommandResult(
        readiness_exit_code,
        message,
    )


def _automation_run_readiness(
    status: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    layers = status.get("layers") if isinstance(status.get("layers"), dict) else {}
    required = (
        "simulator_server",
        "simulator_frontend",
        "chase_game",
        "vehicle",
        "passive_capture",
        "automation_deployment",
    )
    expected = {
        "simulator_server": "reachable",
        "simulator_frontend": "connected",
        "chase_game": "ready",
        "vehicle": "discoverable",
        "passive_capture": "available",
        "automation_deployment": "deployed",
    }
    blocking_layer = next(
        (
            name
            for name in required
            if not isinstance(layers.get(name), dict)
            or layers[name].get("state") != expected[name]
        ),
        None,
    )
    ready = blocking_layer is None
    if ready:
        vehicle_id = str(status.get("vehicle_id") or "chase-sim-chaser")
        next_action = {
            "reason": "worker_stopped",
            "command": (
                "./cli/automa vehicles automation run "
                f"--id {vehicle_id} --observe-only --frames 0 --open-view"
            ),
            "external_change": None,
            "expected_state": "automation_worker=running, perception_view=available",
        }
    else:
        next_action = (
            status.get("next_action")
            if isinstance(status.get("next_action"), dict)
            else None
        )
    return (
        {
            "schema": READINESS_SCHEMA,
            "status": "ready" if ready else "blocked",
            "ready_for": "observation-only automation",
            "checked_at_ms": status.get("checked_at_ms"),
            "gates": {
                name: (
                    status.get("readiness", {}).get("gates", {}).get(name)
                    if isinstance(status.get("readiness"), dict)
                    else None
                )
                for name in required
            },
            "blocking_layer": blocking_layer,
        },
        next_action,
    )


def ensure_vehicle_perception_activation(
    *,
    vehicle: dict[str, Any],
    preset: str,
    bundle: dict[str, str],
    release: dict[str, Any],
) -> Path:
    if preset not in PERCEPTION_PRESETS:
        raise ValueError(f"unknown perception preset: {preset}")

    activation_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    if activation_path.exists():
        manifest = _read_manifest(activation_path)
        existing_preset = _manifest_preset(manifest)
        if existing_preset in PERCEPTION_PRESETS:
            manifest = _activation_manifest(vehicle, selection_activation("perception", preset=existing_preset), bundle)
        elif existing_preset != "custom":
            manifest = _activation_manifest(vehicle, selection_activation("perception", preset=preset), bundle)
    else:
        manifest = _activation_manifest(vehicle, selection_activation("perception", preset=preset), bundle)

    _manifest_bundle(manifest)["release"] = release_activation_summary(release)
    _write_manifest(activation_path, manifest)
    return activation_path


def _perception_update_payload(
    *,
    vehicle_id: str,
    preset: str,
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
        "preset": preset,
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
    activation,
    bundle: dict[str, str],
) -> dict[str, Any]:
    preset = activation.metadata["preset"]
    preset_config = PERCEPTION_PRESETS.get(preset)
    manifest = activation.to_payload()
    manifest["metadata"] = {
        **_activation_metadata_base(vehicle, bundle),
        "preset": preset,
        "preset_description": (
            preset_config["description"] if preset_config else CUSTOM_PERCEPTION_DESCRIPTION
        ),
        "source_dir": bundle["perception_dir"],
        "workspace_source_dir": str(PERCEPTION_IMPLEMENTATIONS_DIR),
    }
    return manifest



def _activation_metadata_base(
    vehicle: dict[str, Any],
    bundle: dict[str, str],
) -> dict[str, Any]:
    return staging_metadata(
        vehicle_id=vehicle.get("vehicle_id"), bundle=bundle, vehicle=vehicle,
        extra={
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
        },
    )


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
    sensor_frame = car.read_sensors(
        SensorReadRequest(
            output_dir=perception_runtime_dir / "sample" / "sensors",
            read_id="current",
            requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
            image_extension="png",
        ),
    )

    _emit(output, "==> Run active perception")
    runner = load_staged_runner(_manifest_activation(manifest))
    perception = runner.perceive(
        build_perception_request(
            sensor_frame,
            shared_memory={},
            output_dir=sample_dir / "perception",
            metadata={
                "activation": str(perception_runtime_dir / "active.json"),
                "vehicle_id": vehicle.get("vehicle_id"),
            },
        ),
    )

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


def _emit(output: TextIO | None, message: str) -> None:
    if output is None:
        return
    print(message, file=output, flush=True)


def _success_message(
    *,
    vehicle_id: str,
    preset: str,
    bundle_root: Path,
    manifest_path: Path,
    sample_paths: dict[str, str] | None,
) -> str:
    lines = [
        f"Updated perception: {vehicle_id} -> {preset}",
        f"Bundle: {display_path(bundle_root)}",
        f"Activation: {display_path(manifest_path)}",
    ]
    if sample_paths is not None:
        lines.append(f"Sample: {display_path(Path(sample_paths['text']))}")
    return "\n".join(lines)


def _read_manifest(path: Path) -> dict[str, Any]:
    """The staged perception activation payload; raises ValueError when invalid."""

    try:
        payload = load_activation_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"Could not parse perception activation {display_path(path)}: {exc}") from exc
    return step_activation_from_payload(payload, step="perception", source_path=path).to_payload()


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    write_step_activation(path, step_activation_from_payload(manifest, step="perception"))


def _manifest_activation(manifest: dict[str, Any], path: Path | None = None):
    return step_activation_from_payload(manifest, step="perception", source_path=path)


def _manifest_preset(manifest: dict[str, Any]) -> str | None:
    preset = (manifest.get("metadata") or {}).get("preset")
    return preset if isinstance(preset, str) else None


def _manifest_bundle(manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = manifest.setdefault("metadata", {})
    bundle = metadata.get("controller_bundle")
    if not isinstance(bundle, dict):
        bundle = {}
        metadata["controller_bundle"] = bundle
    return bundle


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
    activation = payload.get("activation") if isinstance(payload.get("activation"), dict) else None
    bundle = (
        payload.get("controller_bundle")
        if isinstance(payload.get("controller_bundle"), dict)
        else None
    )
    schema = (
        payload.get("perception_schema")
        if isinstance(payload.get("perception_schema"), dict)
        else None
    )
    live = (
        payload.get("live_observation")
        if isinstance(payload.get("live_observation"), dict)
        else None
    )

    if activation is not None:
        release = bundle.get("release") if isinstance((bundle or {}).get("release"), dict) else {}
        preset = activation.get("preset") or "unknown"
        lines = [
            f"Perception: {payload['vehicle_id']} -> {preset}",
        ]
        lines.append(f"Enabled plugins: {', '.join(_configured_plugins(activation)) or 'none'}")
        lines.extend(
            [
                _format_published_view(payload.get("published_view")),
                f"Bundle: {(bundle or {}).get('root_dir', 'unknown')}",
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
            lines.append(
                "Release: not recorded; run `vehicles update perception` to package and attach release metadata"
            )
        if schema is not None:
            lines.append(
                f"Schema source: {payload['perception_schema_source']['runner']}.describe_schema()"
            )
    else:
        lines = [
            f"Perception: {payload['vehicle_id']} (no local staged activation)",
            _format_published_view(payload.get("published_view")),
        ]

    if live is not None:
        lines.extend(["", _format_live_observation(live)])

    if schema is None:
        return "\n".join(lines)

    lines.extend(
        [
            "",
            "Inputs:",
        ]
    )

    for item in schema.get("inputs", []):
        if not isinstance(item, dict):
            continue
        required = "required" if item.get("required") else "optional"
        lines.append(f"- {item.get('feed_id', 'unknown')} ({required})")
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
            feed_text = (
                ", ".join(
                    str(item.get("feed_id", "unknown"))
                    for item in inputs
                    if isinstance(item, dict)
                )
                if isinstance(inputs, list) and inputs
                else "none"
            )
            catalog_id = plugin.get("plugin_id", "unknown")
            lines.append(
                f"- {catalog_id} "
                f"[{contract.get('state_mode', 'unknown')}] "
                f"feeds={feed_text}"
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
    return (
        f"Perception view: unavailable ({reason}); "
        "for Chase run automation, for PiCar run "
        "`./cli/automa vehicles stream perception --id <vehicle_id>`"
    )


def _format_live_observation(live: dict[str, Any]) -> str:
    view = live.get("published_view") if isinstance(live.get("published_view"), dict) else {}
    if not live.get("available"):
        error = live.get("error") or live.get("reason") or "physical observation is unavailable"
        lines = [f"Live onboard observation: unavailable ({error})"]
        if view:
            lines.append(_format_live_view(view))
        return "\n".join(lines)
    frame = live.get("frame") if isinstance(live.get("frame"), dict) else {}
    control = live.get("control") if isinstance(live.get("control"), dict) else {}
    lines = [
        "Live onboard observation:",
        f"- health: {live.get('health', 'unknown')}  age_ms={live.get('result_age_ms', 'unknown')}",
        f"- preset: {live.get('preset', 'unknown')}  mode: {live.get('mode', 'unknown')}",
        f"- frame: {frame.get('frame_id', 'none')}  duration_ms={live.get('duration_ms', 'unknown')}",
        (
            f"- control: steering={control.get('steering', 'unknown')} "
            f"throttle={control.get('throttle', 'unknown')} "
            f"reason={control.get('reason', 'unknown')}"
        ),
        f"- endpoint: {live.get('base_url', 'unknown')}{LATEST_JSON_PATH}",
        f"- frame endpoint: {live.get('base_url', 'unknown')}{LATEST_FRAME_PATH}",
    ]
    lines.append(_format_live_view(view))
    return "\n".join(lines)


def _format_live_view(view: dict[str, Any]) -> str:
    if view.get("available") and view.get("url"):
        return f"- local view: {view['url']}"
    reason = view.get("reason") or "physical perception view is unavailable"
    return (
        f"- local view: unavailable ({reason}); "
        "`./cli/automa vehicles stream perception --id <vehicle_id>` starts it"
    )


def _unavailable_live_observation(
    live: dict[str, Any],
    *,
    vehicle: dict[str, Any] | None,
) -> dict[str, Any]:
    """Describe missing live enrichment without invalidating staged data."""

    provider = vehicle.get("provider") if isinstance(vehicle, dict) else None
    reason = live.get("error")
    if not isinstance(reason, str) or not reason:
        if provider is not None:
            reason = f"discovered provider {provider!r} is not a PiRacer"
        else:
            reason = "vehicle is not currently reachable"
    result: dict[str, Any] = {
        "available": False,
        "provider": provider,
        "reason": reason,
    }
    if isinstance(live.get("error"), str) and live["error"]:
        result["error"] = live["error"]
    return result


def _resolve_live_vehicle(vehicle_id: str, *, timeout_s: float) -> dict[str, Any]:
    try:
        discovery = discover_active_vehicles(
            timeout_s=timeout_s,
            include_picar=True,
            include_chase_sim=True,
            include_inactive=True,
        )
    except Exception as exc:
        return {"vehicle": None, "error": f"{type(exc).__name__}: {exc}"}
    vehicle, error = find_vehicle_by_id(discovery, vehicle_id)
    if error:
        return {"vehicle": None, "error": error}
    return {"vehicle": vehicle, "error": None}


def _live_physical_observation_info(
    *,
    vehicle_id: str,
    vehicle: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    base_url = picar_base_url(vehicle)
    if not base_url:
        return {
            "available": False,
            "provider": "picar",
            "error": f"Vehicle {vehicle_id!r} has no picar base_url connection.",
        }
    try:
        parsed_base_url = urlparse(base_url)
        usable_base_url = (
            parsed_base_url.scheme in {"http", "https"}
            and bool(parsed_base_url.netloc)
        )
    except ValueError:
        usable_base_url = False
    if not usable_base_url:
        return {
            "available": False,
            "provider": "picar",
            "base_url": base_url,
            "error": f"Vehicle {vehicle_id!r} has an invalid picar base_url connection.",
        }
    view = physical_view_status(vehicle_id)
    try:
        publication = fetch_observation_publication(base_url, timeout_s=timeout_s)
    except ConnectionError as exc:
        return {
            "available": False,
            "provider": "picar",
            "base_url": base_url,
            "error": str(exc),
            "published_view": view,
            "runtime_dir": display_path(physical_observation_dir(vehicle_id)),
        }
    frame = publication.get("frame") if isinstance(publication.get("frame"), dict) else None
    result = {
        "available": True,
        "provider": "picar",
        "base_url": base_url,
        "health": publication.get("health"),
        "ok": publication.get("ok"),
        "result_age_ms": publication.get("result_age_ms"),
        "duration_ms": publication.get("duration_ms"),
        "preset": publication.get("preset"),
        "mode": publication.get("mode") or publication.get("drive_mode"),
        "processed_count": publication.get("processed_count"),
        "skipped_count": publication.get("skipped_count"),
        "min_interval_s": publication.get("min_interval_s"),
        "control": publication.get("control"),
        "frame": frame,
        "latest_json_path": LATEST_JSON_PATH,
        "latest_frame_path": LATEST_FRAME_PATH,
        "published_view": view,
        "runtime_dir": display_path(physical_observation_dir(vehicle_id)),
    }
    if publication.get("health") not in {"healthy", "stale"}:
        error = publication.get("error")
        result["available"] = False
        result["reason"] = (
            error
            if isinstance(error, str) and error.strip()
            else f"physical observation health is {publication.get('health')!r}"
        )
        if isinstance(error, str) and error.strip():
            result["error"] = error
    return result


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
    run_id = state.get("run_id") if isinstance(state.get("run_id"), str) else None
    view = get_perception_view_status(
        automation_dir,
        expected_run_id=run_id,
        expected_worker_pid=pid if isinstance(pid, int) else None,
    )
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
    plugins = activation.get("plugins")
    if not isinstance(plugins, list):
        return []
    return [str(plugin) for plugin in plugins]
