from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision_cycle.activation import (
    StepActivation,
    load_activation_json,
    step_activation_from_payload,
)
from autonomy.decision_cycle.perception.inputs import build_perception_request
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.runtime.chase_sim.control import ChaseControlTarget
from implementations.vehicle.chase_sim import ChaseSimCar
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError
from implementations.decision_cycle.catalog import CUSTOM_PRESET, selection_activation
from implementations.decision_cycle.perception.presets import (
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
from .step_activations import (
    BUILTIN_STEPS,
    apply_staged,
    bundle_activation_problems,
    changed_plugins,
    ensure_builtin_activations,
    format_activation_problems,
    format_apply_staged,
    keep_staged_configs,
    read_bundle_activation,
    refresh_release,
    stage_activation,
    staged_activation,
    staging_vehicle,
    step_update_error,
    valid_bundle_activation,
)
from .step_hosting import load_staged_runner
from .step_schema import format_staged_step, staged_step_info
from .paths import display_path, safe_path_part
from .perception_view import get_perception_view_status
from .view_discovery import discover_runtime_view
from .host_publications import (
    LATEST_FRAME_PATH,
    LATEST_JSON_PATH,
    fetch_observation_publication,
)
from .runtime_hosts import RuntimeHostError, runtime_base_url
from .vehicles import (
    DEFAULT_READINESS_TIMEOUT_S,
    READINESS_SCHEMA,
    get_vehicle_status,
)


ROOT = Path(__file__).resolve().parents[2]
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
    """Ensure a vehicle's local bundle reflects the current controller source.

    A named preset is rebuilt from the catalog through ``stage_activation``,
    so its plugin list tracks the preset. A custom selection is kept, and
    ``refresh_release`` records a repackaged bundle on it. With nothing
    staged, the default preset is staged. ``preset`` or ``plugins`` select
    what the returned manifest runs without restaging; ``update perception``
    stages a selection.
    """

    vehicle_id = str(vehicle.get("vehicle_id") or "vehicle")

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    manifest_path = Path(bundle["perception_runtime_dir"]) / "active.json"
    problems = bundle_activation_problems(bundle, vehicle_id, steps=("perception",))
    if problems:
        raise ValueError(format_activation_problems(problems))
    existing = read_bundle_activation(bundle, "perception")
    existing_preset = _activation_preset(existing)
    release_summary = _activation_release(existing)

    source = controller_bundle_source_summary()
    staged_tree = release_summary.get("tree_sha256") if release_summary is not None else None
    bundle_present = Path(bundle["autonomy_dir"]).is_dir() and Path(bundle["implementations_dir"]).is_dir()
    refreshed = not bundle_present or staged_tree != source["tree_sha256"]
    release = sync_controller_bundle(bundle, output=output) if refreshed else None

    if existing is not None and existing_preset == CUSTOM_PRESET:
        if release is not None:
            refresh_release(bundle, "perception", release)
    else:
        staged_preset = existing_preset if existing_preset in PERCEPTION_PRESETS else None
        stage_activation(
            bundle,
            selection_activation("perception", preset=staged_preset),
            vehicle_id=_vehicle_id(vehicle),
            release=release,
            vehicle=vehicle,
            release_summary=None if release is not None else release_summary,
        )

    staged = read_bundle_activation(bundle, "perception")
    if staged is None:
        raise ValueError(f"perception activation was not staged: {display_path(manifest_path)}")
    staged_payload = staged.to_payload()
    manifest = staged_payload
    if preset is not None or plugins:
        manifest = staged_activation(
            bundle,
            selection_activation("perception", preset=preset, plugins=plugins),
            vehicle_id=_vehicle_id(vehicle),
            vehicle=vehicle,
            release_summary=_activation_release(staged),
        ).to_payload()

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
    """The staged perception, the local view, and the runtime host's latest publication."""

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    staged, error = staged_step_info(bundle, vehicle_id, "perception")
    if error is not None:
        return CommandResult(2, error)
    payload: dict[str, Any] = {
        "schema": "vehicle_perception_info_v1",
        "vehicle_id": vehicle_id,
        **staged,
        "published_view": discover_runtime_view(
            vehicle_id, get_perception_view_status, runtime_root=RUNTIME_ROOT
        ),
        "live_observation": _live_observation_info(vehicle_id, timeout_s=timeout_s),
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_perception_info(payload))


def update_vehicle_perception(
    *,
    vehicle_id: str,
    preset: str | None = None,
    plugins: list[str] | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
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
    previous = valid_bundle_activation(bundle, "perception")
    if plugins:
        activation = keep_staged_configs(bundle, activation)
    perception_runtime_dir = Path(bundle["perception_runtime_dir"])
    manifest_path = perception_runtime_dir / "active.json"
    manifest = staged_activation(
        bundle, activation, vehicle_id=vehicle_id, vehicle=vehicle,
    ).to_payload()

    _emit(stream, f"Selected {vehicle_id} ({provider}).")
    _emit(stream, "Scope: local perception controller bundle.")
    _emit(stream, "Vehicle and simulator source code will not be modified.")
    _emit(stream, f"Perception preset: {activation_name} ({', '.join(manifest['plugins'])})")
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

    # Built-in staging also refreshes an existing proposal's release metadata.
    # Validate the documents it reads before packaging or writing.
    problems = bundle_activation_problems(bundle, vehicle_id, steps=(*BUILTIN_STEPS, "proposal"))
    if problems:
        return CommandResult(*step_update_error(
            vehicle_id, "perception", "invalid_activation", format_activation_problems(problems),
            json_output=json_output, activation_problems=problems,
        ))

    release = sync_controller_bundle(bundle, output=stream)
    manifest_path = stage_activation(
        bundle, activation, vehicle_id=vehicle_id, release=release, vehicle=vehicle,
    )
    manifest = step_activation_from_payload(
        load_activation_json(manifest_path.read_text(encoding="utf-8")),
        step="perception",
        source_path=manifest_path,
    ).to_payload()
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
    payload["apply"] = apply_staged(vehicle_id, provider, "perception",
                                    changed=None if restart else changed_plugins(previous, activation))
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
    message += "\n" + "\n".join(format_apply_staged(payload["apply"]))
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
                f"--id {vehicle_id} --observe-only --num-decisions 0 --open-view"
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
    """Stage perception for deploy through the shared activation writers.

    A named preset is rebuilt from the catalog with ``stage_activation``,
    so a deployed preset picks up the catalog's plugin list. A custom
    selection keeps its plugins and ``refresh_release`` records ``release``.
    With nothing staged, or a preset name this catalog does not know,
    ``preset`` is staged.
    """

    if preset not in PERCEPTION_PRESETS:
        raise ValueError(f"unknown perception preset: {preset}")

    existing = read_bundle_activation(bundle, "perception")
    existing_preset = _activation_preset(existing)
    if existing is not None and existing_preset == CUSTOM_PRESET:
        path = refresh_release(bundle, "perception", release)
        if path is None:
            raise ValueError("custom perception activation disappeared before its release was recorded")
        return path
    selected = existing_preset if existing_preset in PERCEPTION_PRESETS else preset
    return stage_activation(
        bundle,
        selection_activation("perception", preset=selected),
        vehicle_id=_vehicle_id(vehicle),
        release=release,
        vehicle=vehicle,
    )


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


def _vehicle_id(vehicle: dict[str, Any]) -> str | None:
    vehicle_id = vehicle.get("vehicle_id")
    return vehicle_id if isinstance(vehicle_id, str) else None


def _activation_preset(activation: StepActivation | None) -> str | None:
    if activation is None:
        return None
    preset = activation.metadata.get("preset")
    return preset if isinstance(preset, str) else None


def _activation_release(activation: StepActivation | None) -> dict[str, Any] | None:
    if activation is None:
        return None
    bundle = activation.metadata.get("controller_bundle")
    if not isinstance(bundle, dict):
        return None
    release = bundle.get("release")
    return dict(release) if isinstance(release, dict) else None


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

    preparation = ChaseControlTarget(car).acquire()
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


def _manifest_activation(manifest: dict[str, Any], path: Path | None = None):
    return step_activation_from_payload(manifest, step="perception", source_path=path)


def _manifest_preset(manifest: dict[str, Any]) -> str | None:
    preset = (manifest.get("metadata") or {}).get("preset")
    return preset if isinstance(preset, str) else None


def _format_perception_info(payload: dict[str, Any]) -> str:
    lines = format_staged_step(
        "perception", payload, view=_format_published_view(payload.get("published_view"))
    )
    lines.extend(["", _format_live_observation(payload["live_observation"])])
    return "\n".join(lines)


def _format_published_view(value: Any) -> str:
    view = value if isinstance(value, dict) else {}
    if view.get("available") and view.get("url"):
        return f"Perception view: {view['url']}"
    reason = view.get("reason") or "no view is running"
    return (
        f"Perception view: unavailable ({reason}); "
        "`./cli/automa vehicles stream perception --id <vehicle_id>` starts one"
    )


def _format_live_observation(live: dict[str, Any]) -> str:
    if not live.get("available"):
        error = live.get("error") or live.get("reason") or "the runtime host published no observation"
        return f"Live observation: unavailable ({error})"
    frame = live.get("frame") if isinstance(live.get("frame"), dict) else {}
    control = live.get("control") if isinstance(live.get("control"), dict) else {}
    return "\n".join([
        "Live observation:",
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
    ])


def _live_observation_info(vehicle_id: str, *, timeout_s: float) -> dict[str, Any]:
    """The runtime host's latest observation publication, for any vehicle."""

    try:
        base_url = runtime_base_url(vehicle_id)
    except RuntimeHostError as exc:
        return {"available": False, "error": str(exc)}
    try:
        publication = fetch_observation_publication(base_url, timeout_s=timeout_s)
    except ConnectionError as exc:
        return {"available": False, "base_url": base_url, "error": str(exc)}
    result = {
        "available": True,
        "base_url": base_url,
        "health": publication.get("health"),
        "ok": publication.get("ok"),
        "result_age_ms": publication.get("result_age_ms"),
        "duration_ms": publication.get("duration_ms"),
        "preset": publication.get("preset"),
        "mode": publication.get("mode"),
        "processed_count": publication.get("processed_count"),
        "skipped_count": publication.get("skipped_count"),
        "skipped_since_previous": publication.get("skipped_since_previous"),
        "frames_captured": publication.get("frames_captured"),
        "interval_s": publication.get("interval_s"),
        "control": publication.get("control"),
        "frame": publication.get("frame") if isinstance(publication.get("frame"), dict) else None,
        "latest_json_path": LATEST_JSON_PATH,
        "latest_frame_path": LATEST_FRAME_PATH,
    }
    if publication.get("health") not in {"healthy", "stale"}:
        error = publication.get("error")
        result["available"] = False
        result["reason"] = (
            error
            if isinstance(error, str) and error.strip()
            else f"observation health is {publication.get('health')!r}"
        )
        if isinstance(error, str) and error.strip():
            result["error"] = error
    return result
