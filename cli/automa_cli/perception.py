from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .command_result import CommandResult
from .bundles import controller_bundle_paths
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


def get_host_perception_info(
    *,
    host_name: str,
    json_output: bool = False,
    timeout_s: float = 3.0,
) -> CommandResult:
    """The staged perception, the local view, and the runtime host's latest publication."""

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(host_name))
    staged, error = staged_step_info(bundle, host_name, "perception")
    if error is not None:
        return CommandResult(2, error)
    payload: dict[str, Any] = {
        "schema": "host_perception_info",
        "host_name": host_name,
        **staged,
        "published_view": discover_runtime_view(
            host_name, get_perception_view_status, runtime_root=RUNTIME_ROOT
        ),
        "live_observation": _live_observation_info(host_name, timeout_s=timeout_s),
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(0, _format_perception_info(payload))


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
        "`./cli/automa hosts stream perception --name <host_name>` starts one"
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


def _live_observation_info(host_name: str, *, timeout_s: float) -> dict[str, Any]:
    """The runtime host's latest observation publication, for any vehicle."""

    try:
        base_url = runtime_base_url(host_name)
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
