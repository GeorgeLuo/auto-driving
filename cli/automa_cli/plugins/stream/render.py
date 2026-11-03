"""Text and JSON for a plugin stream. No host reads and no writes of its own."""

from __future__ import annotations

import json
from typing import Any

from autonomy.decision_cycle.memory.interface import BOUNDS, EPOCH_ID, HEALTH, RECORD_COUNT
from autonomy.runtime.frame_loop import LATEST_FRAME_PATH, LATEST_JSON_PATH

from ...hosts.publications.decision_publication import (
    perception_text_from_publication,
    presets_text,
    publication_to_frame_record,
)
from ...hosts.views.view_feed import view_page_lines
from ...shared.memory_report import evidence_publisher, plugin_ledgers
from ..step_formatting import format_step_record, format_step_timing
from .models import MemoryPublication, StreamEvent

_PERCEPTION_PAGES = (("view", ""),)
_MEMORY_PAGES = (("memory map", "/memory"), ("perception view", "/perception"))
_DECISION_PAGES = (("perception view", "/perception"),)


def render_stream(event: StreamEvent) -> str:
    """The terminal screen, or one JSON probe line without its trailing newline."""

    if event.json_output:
        return json.dumps(event.live, sort_keys=True)
    if event.step == "perception":
        return _perception_screen(event)
    if event.step == "memory":
        return _memory_screen(event)
    return _decision_screen(event)


def _perception_screen(event: StreamEvent) -> str:
    """The perception screen: the probe verdict and the host's latest cycle."""

    publication = event.source if isinstance(event.source, dict) else {}
    record = publication_to_frame_record(publication)
    perception = record.get("perception") if isinstance(record.get("perception"), dict) else {}
    control = record.get("control") if isinstance(record.get("control"), dict) else {}
    signals = perception.get("signals")
    things = perception.get("things")
    cadence = {
        "processed": publication.get("processed_count"),
        "cycle_ms": publication.get("duration_ms"),
        # The host computes the result age on its own clock.
        "age_ms": publication.get("result_age_ms"),
    }
    body = perception_text_from_publication(publication) if publication else ""
    lines = [
        "automa perception stream",
        "",
        f"host: {event.host_name}",
        f"host: {event.base_url}",
        f"status: {event.live.get('status', 'unknown')}",
        *([f"error: {event.live['error']}"] if event.live.get("error") else []),
        f"presets: {presets_text(publication.get('presets'))}  mode: {_shown(publication.get('mode'))}",
        (
            f"control: steering={_shown(control.get('steering'))}  "
            f"throttle={_shown(control.get('throttle'))}  reason={_shown(control.get('reason'))}"
        ),
        "cadence: " + "  ".join(f"{key}={_shown(value)}" for key, value in cadence.items()),
        (
            f"latest: frame={_shown(record.get('frame_id'), 'none')}  "
            f"captured_at_ms={_shown(record.get('captured_at_ms'))}  "
            f"signals={_shown(len(signals) if isinstance(signals, list) else None)}  "
            f"things={_shown(len(things) if isinstance(things, list) else None)}"
        ),
        *view_page_lines(event.view, *_PERCEPTION_PAGES),
        (
            f"publication: {publication.get('health') or 'unavailable'}  "
            f"json: {LATEST_JSON_PATH}  frame: {LATEST_FRAME_PATH}"
        ),
        "",
        "latest perception",
        "-----------------",
        body.strip() or "(no latest perception yet)",
    ]
    return "\n".join(lines)


def _memory_screen(event: StreamEvent) -> str:
    """The memory screen: the host's memory step, the views, and the memory
    report its latest cycle published."""

    source = event.source if isinstance(event.source, MemoryPublication) else MemoryPublication()
    lines = [
        format_live_memory_screen(host_name=event.host_name, live=event.live),
        "",
        *view_page_lines(event.view, *_MEMORY_PAGES),
    ]
    if source.error:
        lines.append(f"publication memory: {source.error}")
    elif isinstance(source.report, dict):
        lines.append(
            f"publication memory: evidence publisher {evidence_publisher(source.report) or 'none'}"
        )
        lines.extend(_format_plugin_ledgers(plugin_ledgers(source.report)))
    return "\n".join(lines)


def _decision_screen(event: StreamEvent) -> str:
    live = event.live
    text = _format_stream_frame(event.step, live) if live.get("status") == "live" else str(live.get("error") or "")
    if event.view is None:
        return text
    return "\n".join([text, "", *view_page_lines(event.view, *_DECISION_PAGES)])


def _format_stream_frame(step: str, frame: dict[str, Any]) -> str:
    freshness = frame["freshness"]
    lines = [
        f"{step.capitalize()} stream: {frame['host_name']} frame={frame['frame_id']} "
        f"run={frame['run_id']} age_ms={freshness['age_ms']} max_age_ms={freshness['max_age_ms']}",
        f"Generation: {frame['generation_id']}",
        f"Plugins: {', '.join(frame['plugins']) or 'none'}",
        *format_step_record(
            step, frame["record"], observation=frame.get("observation"), memory=frame.get("memory"),
        ),
    ]
    if step == "action":
        application = frame["application"]
        lines.append(
            "Host application: "
            f"applied={str(application.get('applied') is True).lower()} "
            f"mode={application.get('mode')} reason={application.get('reason')} "
            f"steering={application.get('steering')} throttle={application.get('throttle')}"
        )
    lines.append(format_step_timing(step, frame["timing"]))
    return "\n".join(lines)


def format_live_memory_screen(*, host_name: str, live: dict[str, Any]) -> str:
    return format_live_step_screen("memory", host_name=host_name, live=live)


def format_live_step_screen(step: str, *, host_name: str, live: dict[str, Any]) -> str:
    status = str(live.get("status") or "unknown")
    lines = [
        f"Live {step}: {host_name} [{status}]",
    ]
    if live.get("endpoint"):
        lines.append(f"Endpoint: {live.get('endpoint')}")
    if live.get("mode") is not None:
        lines.append(f"Mode: {live.get('mode')}  session: {live.get('session_status') or 'unknown'}")
    if status == "live":
        lines.append(f"Applied plugins: {', '.join(live.get('plugin_ids', [])) or 'none'}")
        if step == "memory":
            lines.extend(_format_plugin_ledgers(_live_plugins(live)))
            lines.extend(
                [
                    f"Evidence publisher: {live.get('evidence_publisher') or 'none'}",
                    (
                        f"Counters: updates={live.get('update_count')} "
                        f"resets={live.get('reset_count')} "
                        f"failures={live.get('failure_count')}"
                    ),
                ]
            )
        else:
            lines.append(
                f"Counters: runs={live.get('run_count')} failures={live.get('failure_count')}"
            )
        if live.get("last_duration_ms") is not None:
            lines.append(f"Last duration: {live.get('last_duration_ms')} ms")
        if live.get("last_error"):
            lines.append(f"Last error: {live.get('last_error')}")
    else:
        if live.get("error"):
            lines.append(f"Detail: {live.get('error')}")
    return "\n".join(lines)


def _live_plugins(live: dict[str, Any]) -> list[dict[str, Any]]:
    """The plugin entries of a live probe; none unless it is live."""

    plugins = live.get("plugins") if live.get("status") == "live" else None
    return [
        entry
        for entry in (plugins if isinstance(plugins, list) else [])
        if isinstance(entry, dict) and isinstance(entry.get("plugin_id"), str)
    ]


def _format_plugin_ledgers(ledgers: list[dict[str, Any]]) -> list[str]:
    """One line per plugin: its ledger health, epoch, records and any bounds."""

    lines = []
    for ledger in ledgers:
        line = (
            f"  {ledger['plugin_id']}: health={ledger.get(HEALTH) or 'unknown'} "
            f"epoch={ledger.get(EPOCH_ID) or '-'} records={ledger.get(RECORD_COUNT)}"
        )
        bounds = ledger.get(BOUNDS)
        if isinstance(bounds, dict) and bounds:
            line += (
                f" max_records={bounds.get('max_records')} "
                f"max_age_ms={bounds.get('max_age_ms')} "
                f"eviction={bounds.get('eviction_policy')}"
            )
        lines.append(line)
    return lines


def _shown(value: Any, default: str = "unknown") -> Any:
    return default if value is None else value
