"""CLI replay lifecycle for one ordered image directory.

The operation starts the runner and, when asked, the viewer. It reports
runner state and operational notices through ``emit``. The command formats
that state. Browser payloads for the workbench page stay in ``presentation``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from implementations.decision_cycle.catalog import selection_activation

from ..frontend.workbench_contract import (
    WORKBENCH_DEFAULT_CADENCE_MS,
    WORKBENCH_DEFAULT_PACE,
    ReplayActionError,
)
from ..replay.source import (
    WORKBENCH_DEFAULT_MAX_FRAMES,
    SourceValidationError,
)
from ..shared.command_result import CommandResult
from ..shared.open_view import open_view_message
from .models import WorkbenchEvent
from .runner import ImageReplayRunner


def run_workbench_replay(
    source_dir: str | os.PathLike[str],
    *,
    selections: Mapping[str, tuple[str | None, Sequence[str] | None]] | None = None,
    cadence_ms: int = WORKBENCH_DEFAULT_CADENCE_MS,
    pace: str = WORKBENCH_DEFAULT_PACE,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
    host: str | None = None,
    port: int = 0,
    serve: bool = False,
    open_browser: bool = False,
    emit: Callable[[WorkbenchEvent], None] | None = None,
    view_server: Callable[..., Any],
) -> CommandResult:
    """Run one CLI replay, optionally keeping the viewer's workbench available.

    Recorded frames restore their executable step selections. Explicit CLI
    ``selections``, a ``(preset, plugins)`` pair by step, override those steps; an unrecorded step uses its packaged
    default. Page selections override that step for the replay pass, retaining
    configs when the selected IDs are unchanged. Offline replay computes the
    action's authorization without host delivery. ``view_server`` builds the
    viewer that serves the replay. ``emit`` receives runner state and notices;
    this function does not format them.
    """

    try:
        activations = {}
        for step, (preset, plugins) in (selections or {}).items():
            if preset is not None or plugins is not None:
                activations[step] = selection_activation(step, preset=preset, plugins=plugins)
    except ValueError as exc:
        return CommandResult(2, f"Workbench replay failed: {exc}")
    if open_browser:
        serve = True
    runner: ImageReplayRunner | None = None
    server = None
    displayed_url: str | None = None
    try:
        runner = ImageReplayRunner(
            source_dir,
            activations=activations,
            cadence_ms=cadence_ms,
            pace=pace,
            max_frames=max_frames,
            loop=serve,
        )
        if serve:
            # Without ``host`` the viewer binds its own loopback default.
            server = view_server(
                host_name="viewer",
                automation_dir=None,
                port=port,
                connected=False,
                replay_runner=runner,
                **({} if host is None else {"host": host}),
            ).start()
            displayed_url = server.workbench_url
        runner.start()
        if serve and emit is not None:
            emit(WorkbenchEvent(state=runner.state(), server_url=displayed_url, serving=True))
        if serve:
            try:
                if open_browser and displayed_url:
                    notice = open_view_message(displayed_url)
                    if emit is not None:
                        emit(WorkbenchEvent(line=notice))
                while True:
                    time.sleep(0.25)
            except KeyboardInterrupt:
                return CommandResult(0, "viewer stopped")
        state = runner.wait()
        exit_code = 0 if state.get("phase") == "completed" else 2
        if emit is not None:
            emit(WorkbenchEvent(state=state, server_url=displayed_url, serving=False))
        return CommandResult(exit_code, "")
    except (ReplayActionError, SourceValidationError) as exc:
        return CommandResult(2, f"Workbench replay failed: {exc}")
    finally:
        if server is not None:
            server.stop()
        elif runner is not None:
            runner.close()
