from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import StepActivation
from implementations.decision_cycle.catalog import selection_activation

from .bundles import controller_bundle_paths
from .step_activations import (
    bundle_activation_problems,
    format_activation_problems,
    read_bundle_activation,
    staged_activation,
)
from .step_schema import format_staged_step, staged_step_info
from .paths import safe_path_part
from .perception_view import get_perception_view_status
from .view_discovery import discover_runtime_view
from .host_publications import (
    LATEST_FRAME_PATH,
    LATEST_JSON_PATH,
    fetch_observation_publication,
    presets_text,
)
from .runtime_hosts import RuntimeHostError, runtime_base_url


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "hosts"))


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def live_perception_selection(
    vehicle: dict[str, Any],
    *,
    preset: str | None = None,
    plugins: list[str] | None = None,
) -> StepActivation:
    """The perception selection a live inspect of ``vehicle`` runs; nothing is written.

    An explicit ``preset`` or ``plugins`` wins, then the vehicle's staged
    selection, then the default preset. Like the staged selection, the others
    run the vehicle's bundle code when it has a bundle, else the installed
    package.
    """

    host_id = _host_id(vehicle)
    if host_id is None:
        # A discovered environment does not identify a controller host.
        return selection_activation("perception", preset=preset, plugins=plugins)
    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(host_id))
    problems = bundle_activation_problems(bundle, host_id, steps=("perception",))
    if problems:
        raise ValueError(format_activation_problems(problems))
    staged = read_bundle_activation(bundle, "perception")
    if staged is not None and preset is None and plugins is None:
        return staged
    selection = selection_activation("perception", preset=preset, plugins=plugins)
    if not (Path(bundle["autonomy_dir"]).is_dir() and Path(bundle["implementations_dir"]).is_dir()):
        return selection
    return staged_activation(
        bundle,
        selection,
        host_id=_host_id(vehicle),
        vehicle=vehicle,
        release_summary=_activation_release(staged),
    )


def get_host_perception_info(
    *,
    host_id: str,
    json_output: bool = False,
    timeout_s: float = 3.0,
) -> CommandResult:
    """The staged perception, the local view, and the runtime host's latest publication."""

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(host_id))
    staged, error = staged_step_info(bundle, host_id, "perception")
    if error is not None:
        return CommandResult(2, error)
    payload: dict[str, Any] = {
        "schema": "host_perception_info",
        "host_id": host_id,
        **staged,
        "published_view": discover_runtime_view(
            host_id, get_perception_view_status, runtime_root=RUNTIME_ROOT
        ),
        "live_observation": _live_observation_info(host_id, timeout_s=timeout_s),
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_perception_info(payload))


def _host_id(vehicle: dict[str, Any]) -> str | None:
    host_id = vehicle.get("host_id")
    return host_id if isinstance(host_id, str) else None


def _activation_release(activation: StepActivation | None) -> dict[str, Any] | None:
    if activation is None:
        return None
    bundle = activation.metadata.get("controller_bundle")
    if not isinstance(bundle, dict):
        return None
    release = bundle.get("release")
    return dict(release) if isinstance(release, dict) else None


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
        "`./cli/automa hosts stream perception --id <host_id>` starts one"
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
        f"- presets: {presets_text(live.get('presets'))}  mode: {live.get('mode', 'unknown')}",
        f"- frame: {frame.get('frame_id', 'none')}  duration_ms={live.get('duration_ms', 'unknown')}",
        (
            f"- control: steering={control.get('steering', 'unknown')} "
            f"throttle={control.get('throttle', 'unknown')} "
            f"reason={control.get('reason', 'unknown')}"
        ),
        f"- endpoint: {live.get('base_url', 'unknown')}{LATEST_JSON_PATH}",
        f"- frame endpoint: {live.get('base_url', 'unknown')}{LATEST_FRAME_PATH}",
    ])


def _live_observation_info(host_id: str, *, timeout_s: float) -> dict[str, Any]:
    """The runtime host's latest observation publication, for any vehicle."""

    try:
        base_url = runtime_base_url(host_id)
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
        "presets": publication.get("presets"),
        "mode": publication.get("mode"),
        "processed_count": publication.get("processed_count"),
        "frames_received": publication.get("frames_received"),
        "frames_dropped": publication.get("frames_dropped"),
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
