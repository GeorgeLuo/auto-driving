from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision_cycle.activation import StepActivation
from implementations.decision_cycle.catalog import CUSTOM_PRESET, selection_activation
from implementations.decision_cycle.perception.presets import PERCEPTION_PRESETS

from .bundles import (
    controller_bundle_source_summary,
    controller_bundle_paths,
    sync_controller_bundle,
)
from .step_activations import (
    bundle_activation_problems,
    format_activation_problems,
    read_bundle_activation,
    refresh_release,
    stage_activation,
    staged_activation,
)
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
