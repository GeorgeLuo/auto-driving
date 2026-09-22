"""Loopback decision playback workbench for deterministic image replays.

The workbench is deliberately a thin presentation and lifecycle boundary. It
normalizes one ordered image-directory source, feeds it through the existing
perception, observation, memory, and shadow decision seams, and exposes the
resulting state to both the CLI and a small loopback HTTP page.
"""

from __future__ import annotations

import json
import os
import time
import webbrowser
from pathlib import Path
from typing import Any, TextIO

from .perception_runs import CommandResult
from .workbench_contract import (
    ReplayActionError,
    WORKBENCH_ACTIONS,
    WORKBENCH_DEFAULT_CADENCE_MS,
    WORKBENCH_DEFAULT_PACE,
    WORKBENCH_ERROR_SCHEMA,
    WORKBENCH_HOST,
    WORKBENCH_PACES,
    WORKBENCH_SEQUENCE_ID,
)
from .workbench_runner import ImageReplayRunner
from .workbench_plugins import (
    PluginCatalog,
    PluginCatalogError,
    build_plugin_catalog,
    discover_plugin_catalog,
    packaged_plugin_catalog,
)
from .workbench_server import WorkbenchServer
from .workbench_source import (
    ImageFeed,
    ReplayFrame,
    SourceValidationError,
    WORKBENCH_DEFAULT_MAX_FRAMES,
    load_image_feed,
    normalize_image_directory,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
BRIGHT_TRACKS_CAPTURE = (
    _REPO_ROOT
    / "lab/runs/cv-synthesis-20260921/experiment-3/bright-motion-20s-20260921-133121/frames"
)
BRIGHT_TRACKS_PLUGIN_DIR = _REPO_ROOT / "lab/plugins/perception"
BRIGHT_TRACKS_SETTINGS = (
    BRIGHT_TRACKS_PLUGIN_DIR
    / "multi_obstruction_tracks/configs/bright-settings.json"
)
BRIGHT_TRACKS_PLUGIN_ID = "multi_obstruction_tracks"
BRIGHT_TRACKS_FIELDS = (
    {"key": "canny_low", "label": "Canny low", "kind": "int", "min": 1, "max": 200, "step": 1},
    {"key": "canny_high", "label": "Canny high", "kind": "int", "min": 2, "max": 250, "step": 1},
    {"key": "floor_cutoff_y", "label": "Floor cutoff", "kind": "float", "min": 0.35, "max": 0.95, "step": 0.01},
    {"key": "minimum_output_confidence", "label": "Confidence floor", "kind": "float", "min": 0, "max": 1, "step": 0.01},
    {"key": "output_bbox_shrink_y", "label": "Vertical shrink", "kind": "float", "min": 0.25, "max": 1, "step": 0.05},
    {"key": "max_tracks", "label": "Max tracks", "kind": "int", "min": 1, "max": 4, "step": 1},
    {"key": "minimum_object_height", "label": "Minimum height", "kind": "float", "min": 0.02, "max": 1, "step": 0.01},
    {"key": "association_distance", "label": "Association distance", "kind": "float", "min": 0.05, "max": 1, "step": 0.01},
    {"key": "smoothing_alpha", "label": "Smoothing", "kind": "float", "min": 0.05, "max": 1, "step": 0.05},
    {"key": "max_missed_frames", "label": "Hold frames", "kind": "int", "min": 0, "max": 6, "step": 1},
    {
        "key": "contrast_normalization",
        "label": "Contrast",
        "kind": "choice",
        "choices": ["none", "clahe", "stretch", "gamma"],
    },
)


def bright_tracks_editor(config: dict[str, Any]) -> dict[str, Any]:
    """Operator-facing controls for the bright obstruction-track profile."""

    fields = []
    for spec in BRIGHT_TRACKS_FIELDS:
        field = dict(spec)
        if spec["key"] in config:
            field["value"] = config[spec["key"]]
        fields.append(field)
    return {
        "plugin_id": BRIGHT_TRACKS_PLUGIN_ID,
        "title": "Bright obstruction tracks",
        "note": (
            "This is the bright cardboard-box profile on the high-rate drive. "
            "Play runs the frames in order. Changing a setting replays from the "
            "first frame through the one you are on, and the tracks are carried "
            "in memory from one frame to the next."
        ),
        "fields": fields,
    }


def load_bright_tracks_config() -> dict[str, Any]:
    payload = json.loads(BRIGHT_TRACKS_SETTINGS.read_text(encoding="utf-8"))
    config = payload.get("config")
    if not isinstance(config, dict):
        raise ValueError(f"{BRIGHT_TRACKS_SETTINGS} has no config object")
    return dict(config)


def run_workbench_replay(
    source_dir: str | os.PathLike[str],
    *,
    plugin_dir: str | os.PathLike[str] | None = None,
    active_plugin_ids: list[str] | tuple[str, ...] | None = None,
    plugin_config: dict[str, dict[str, Any]] | None = None,
    parameter_editor: dict[str, Any] | None = None,
    ordered_replay: bool = False,
    cadence_ms: int = WORKBENCH_DEFAULT_CADENCE_MS,
    pace: str = WORKBENCH_DEFAULT_PACE,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
    host: str = WORKBENCH_HOST,
    port: int = 0,
    serve: bool = False,
    open_browser: bool = False,
    loop: bool | None = None,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    """Run one CLI replay, optionally keeping the loopback workbench alive."""

    if json_output and (serve or open_browser):
        return CommandResult(2, "--json cannot be combined with --serve or --open")
    if open_browser:
        serve = True
    runner: ImageReplayRunner | None = None
    server: WorkbenchServer | None = None
    displayed_url: str | None = None
    try:
        runner = ImageReplayRunner(
            source_dir,
            plugin_dir=plugin_dir,
            active_plugin_ids=active_plugin_ids,
            plugin_config=plugin_config,
            parameter_editor=parameter_editor,
            ordered_replay=ordered_replay,
            cadence_ms=cadence_ms,
            pace=pace,
            max_frames=max_frames,
            loop=serve if loop is None else loop,
        )
        if serve:
            server = WorkbenchServer(runner, host=host, port=port).start()
            displayed_url = server.url
        runner.start()
        if output is not None and serve:
            print(
                _format_workbench_status(
                    runner.state(),
                    server_url=displayed_url,
                    serving=True,
                ),
                file=output,
            )
        if open_browser and displayed_url:
            webbrowser.open(displayed_url)
        if serve:
            try:
                while True:
                    time.sleep(0.25)
            except KeyboardInterrupt:
                return CommandResult(0, "workbench server stopped")
        state = runner.wait()
        exit_code = 0 if state.get("phase") == "completed" else 2
        if json_output:
            return CommandResult(
                exit_code,
                json.dumps(state, indent=2, sort_keys=True),
            )
        return CommandResult(
            exit_code,
            _format_workbench_status(
                state,
                server_url=displayed_url,
                serving=False,
            ),
        )
    except (ReplayActionError, SourceValidationError) as exc:
        state = runner.state() if runner is not None else None
        if json_output:
            payload = {
                "schema": WORKBENCH_ERROR_SCHEMA,
                "ok": False,
                "boundary": getattr(exc, "boundary", "input"),
                "message": str(exc),
            }
            if state is not None:
                payload["state"] = state
            return CommandResult(2, json.dumps(payload, indent=2, sort_keys=True))
        return CommandResult(2, f"Workbench replay failed: {exc}")
    finally:
        if server is not None:
            server.stop()
        elif runner is not None:
            runner.close()


def _format_workbench_status(
    state: dict[str, Any],
    *,
    server_url: str | None,
    serving: bool,
) -> str:
    source = state.get("source") or {}
    progress = state.get("progress") or {}
    active_plugins = state.get("run_active_plugin_ids") or state.get("active_plugin_ids") or []
    active_plugins_text = ", ".join(str(item) for item in active_plugins) or "(none)"
    lines = [
        "automa decision playback workbench",
        f"phase: {state.get('phase')}",
        f"sequence: {state.get('sequence_id')}",
        f"run_id: {state.get('run_id') or '(none)'}",
        f"source: {source.get('source_path') or source.get('path') or '(none)'}",
        f"plugin_dir: {state.get('plugin_dir') or '(packaged default)'}",
        f"active_plugins: {active_plugins_text}",
        f"plugin_order: {active_plugins_text}",
        f"catalog_digest: {state.get('run_catalog_digest') or state.get('catalog_digest') or '(none)'}",
        f"progress: {progress.get('completed', 0)}/{progress.get('total', 0)}",
    ]
    if server_url:
        lines.append(f"workbench: {server_url}")
    if serving:
        lines.append("server: persistent loopback mode; press Ctrl-C to stop")
    failure = state.get("failure")
    if isinstance(failure, dict):
        lines.append(f"failure: {failure.get('message')}")
    recovery = state.get("recovery_action")
    if recovery:
        lines.append(f"recovery: {recovery}")
    cleanup = state.get("cleanup")
    if isinstance(cleanup, dict):
        lines.append(
            "cleanup: mapper={mapper}; memory={memory}; "
            "source_read_only={source_read_only}; "
            "movement_control={movement_control}".format(
                mapper=cleanup.get("mapper"),
                memory=cleanup.get("memory"),
                source_read_only=cleanup.get("source_read_only"),
                movement_control=cleanup.get("movement_control"),
            )
        )
    return "\n".join(lines)


__all__ = [
    "ImageFeed",
    "ImageReplayRunner",
    "PluginCatalog",
    "PluginCatalogError",
    "ReplayActionError",
    "ReplayFrame",
    "SourceValidationError",
    "WorkbenchServer",
    "WORKBENCH_ACTIONS",
    "WORKBENCH_DEFAULT_CADENCE_MS",
    "WORKBENCH_DEFAULT_PACE",
    "WORKBENCH_PACES",
    "WORKBENCH_SEQUENCE_ID",
    "load_image_feed",
    "normalize_image_directory",
    "run_workbench_replay",
    "build_plugin_catalog",
    "discover_plugin_catalog",
    "packaged_plugin_catalog",
]
