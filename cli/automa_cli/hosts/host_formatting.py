"""Text from supplied host status records, with no host discovery or probing."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ..providers.metadata import PROVIDERS
from ..shared.text import indented


def host_state_text(host: dict[str, Any]) -> str:
    """A host's state in words, with the mode of a run that has control: ``running, observe only``."""

    state = host.get("state")
    text = {"pre_start": "not started", "finished_run": "finished run"}.get(state, str(state or "unknown"))
    mode = host.get("mode")
    # Manual is the mode whenever no run has control, so it adds nothing.
    return f"{text}, {mode.replace('_', ' ')}" if isinstance(mode, str) and mode and mode != "manual" else text


def host_block(host: dict[str, Any]) -> str:
    lines = [f"Host {host.get('host_name')}  {host_state_text(host)}"]
    if host.get("vehicle_url"):
        lines.append(f"  {PROVIDERS[host['provider']].label} at {host['vehicle_url']}")
    activity = [
        _count(host.get("decision_count"), "decision"),
        _count(host.get("error_count"), "error"),
        _frame_age(host.get("last_frame_at_ms")),
    ]
    if any(activity):
        lines.append("  " + ", ".join(part for part in activity if part))
    lines += indented(host.get("error"))
    attachment = _attachment_text(host)
    if attachment:
        lines.append(f"  {attachment}")
    address = [host.get("url"), None if host.get("pid") is None else f"pid {host['pid']}"]
    if any(address):
        lines.append("  " + ", ".join(part for part in address if part))
    return "\n".join(lines)


def _attachment_text(host: dict[str, Any]) -> str | None:
    if host.get("state") == "not found":
        return None
    if host.get("attached"):
        since = host.get("attached_since")
        return f"attached since {_local_time(since)}" if since else "attached"
    local = "local host record" if host.get("local") == "host record" else host.get("local")
    # A PiCar is not a simulator host.
    if local == "staged connection" or host.get("attached") is None:
        return local
    return f"not attached to its simulator ({local})" if local else "not attached to its simulator"


def _count(value: Any, noun: str) -> str | None:
    if type(value) is not int:
        return None
    return f"{value} {noun}" + ("" if value == 1 else "s")


def _frame_age(completed_at_ms: Any) -> str | None:
    if type(completed_at_ms) is not int:
        return None
    age_s = max(0.0, time.time() - completed_at_ms / 1000)
    if age_s < 60:
        return f"last frame {age_s:.1f}s ago"
    return f"last frame at {datetime.fromtimestamp(completed_at_ms / 1000).strftime('%Y-%m-%d %H:%M:%S')}"


def _local_time(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)
