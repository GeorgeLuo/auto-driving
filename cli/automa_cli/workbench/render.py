"""Terminal text for workbench replay state the caller already collected."""

from __future__ import annotations

from typing import Any

from implementations.decision_cycle.catalog import PRESET_STEPS

from ..shared.text import format_browser_result
from .models import WorkbenchEvent, WorkbenchResult


def format_workbench_status(
    state: dict[str, Any],
    *,
    server_url: str | None,
    serving: bool,
) -> str:
    source = state.get("source") or {}
    progress = state.get("progress") or {}
    pipeline = (state.get("machine_detail") or {}).get("pipeline") or {}
    lines = [
        "automa decision playback workbench",
        f"phase: {state.get('phase')}",
        f"sequence: {state.get('sequence_id')}",
        f"run_id: {state.get('run_id') or '(none)'}",
        f"source: {source.get('source_path') or source.get('path') or '(none)'}",
        *(
            f"{step}: "
            + _selection_text(
                pipeline.get(f"{step}_preset"), state.get(f"active_{step}_plugin_ids")
            )
            for step in PRESET_STEPS
        ),
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
        fields = (*PRESET_STEPS, "source_read_only", "movement_control")
        lines.append(
            "cleanup: " + "; ".join(f"{field}={cleanup.get(field)}" for field in fields)
        )
    return "\n".join(lines)


def _selection_text(preset: Any, plugin_ids: Any) -> str:
    """A step's selection as the inspect commands print it: preset (plugins).

    Before the pipeline reports a preset, the plugins print alone.
    """

    plugins = ", ".join(str(item) for item in plugin_ids or []) or "none"
    return f"{preset} ({plugins})" if preset else plugins


def render_workbench_event(event: WorkbenchEvent) -> str:
    if event.state is not None:
        return format_workbench_status(event.state, server_url=event.server_url, serving=event.serving)
    return format_browser_result(event.browser) if event.browser else ""


def render_workbench_result(result: WorkbenchResult) -> str:
    if result.error is not None:
        return f"Workbench replay failed: {result.error}"
    return "viewer stopped" if result.viewer_stopped else ""
