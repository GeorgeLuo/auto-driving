"""Format supplied viewer facts without opening a browser or contacting hosts."""
from __future__ import annotations

from ...shared.text import format_browser_result
from .models import StillResult, ViewerEvent, ViewerResult


def render_viewer_event(event: ViewerEvent) -> str:
    return "\n".join([
        f"Viewing host {event.host_name} at {event.base_url}",
        *([f"Not live: {event.not_live}"] if event.not_live is not None else []),
        format_browser_result(event.browser),
    ])


def render_still_result(result: StillResult) -> str:
    if result.kind == "error":
        return result.error
    if result.kind == "unavailable":
        return f"Cannot read a frame from host {result.host_name} at {result.base_url}: {result.error}"
    if result.kind == "missing":
        return f"No frame from host {result.host_name}. {result.missing}"
    age = f"{result.age_ms} ms" if result.age_ms is not None else "unknown"
    lines = [
        f"Still of host {result.host_name} at {result.base_url}",
        f"frame: {result.frame_id}",
        f"age: {age}",
        f"health: {result.health}",
        result.path,
    ]
    if result.browser is not None:
        lines.append(format_browser_result(result.browser))
    return "\n".join(lines)


def render_viewer_result(result: ViewerResult) -> str:
    if result.kind == "error":
        return result.error
    if result.kind == "unknown_host":
        names = ", ".join(f"{host['host_name']} at {host['url']}" for host in result.hosts or [])
        return f"No host at {result.base_url}. " + (f"Hosts: {names}." if names else "None is running.")
    if result.kind == "unavailable":
        return f"Cannot view host {result.host_name} at {result.base_url}: {result.error}"
    return ""
