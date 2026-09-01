"""Loopback perception-memory workbench for deterministic image replays.

The workbench is deliberately a thin presentation and lifecycle boundary. It
normalizes one ordered image-directory source, feeds it through the existing
perception, observation, and memory seams, and exposes the resulting state to
both the CLI and a small loopback HTTP page.
"""

from __future__ import annotations

import json
import os
import time
import webbrowser
from typing import Any, TextIO

from .perception_runs import CommandResult
from .workbench_contract import (
    ReplayActionError,
    WORKBENCH_ACTIONS,
    WORKBENCH_DEFAULT_CADENCE_MS,
    WORKBENCH_ERROR_SCHEMA,
    WORKBENCH_HOST,
    WORKBENCH_SEQUENCE_ID,
)
from .workbench_runner import ImageReplayRunner
from .workbench_server import WorkbenchServer
from .workbench_source import (
    ImageFeed,
    ReplayFrame,
    SourceValidationError,
    WORKBENCH_DEFAULT_MAX_FRAMES,
    load_image_feed,
    normalize_image_directory,
)


def run_workbench_replay(
    source_dir: str | os.PathLike[str],
    *,
    cadence_ms: int = WORKBENCH_DEFAULT_CADENCE_MS,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
    host: str = WORKBENCH_HOST,
    port: int = 0,
    serve: bool = False,
    open_browser: bool = False,
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
            cadence_ms=cadence_ms,
            max_frames=max_frames,
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
    lines = [
        "automa perception-memory workbench",
        f"phase: {state.get('phase')}",
        f"sequence: {state.get('sequence_id')}",
        f"run_id: {state.get('run_id') or '(none)'}",
        f"source: {source.get('source_path') or source.get('path') or '(none)'}",
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
    "ReplayActionError",
    "ReplayFrame",
    "SourceValidationError",
    "WorkbenchServer",
    "WORKBENCH_ACTIONS",
    "WORKBENCH_DEFAULT_CADENCE_MS",
    "WORKBENCH_SEQUENCE_ID",
    "load_image_feed",
    "normalize_image_directory",
    "run_workbench_replay",
]
