"""Format supplied viewer facts without opening a browser or contacting hosts."""
from __future__ import annotations

from ...shared.text import format_browser_result
from .models import ViewerEvent, ViewerResult


def render_viewer_event(event: ViewerEvent) -> str:
    return "\n".join([
        f"Viewing host {event.host_name} at {event.base_url}",
        *([f"Not live: {event.not_live}"] if event.not_live is not None else []),
        format_browser_result(event.browser),
    ])


def render_viewer_result(result: ViewerResult) -> str:
    if result.kind == "error":
        return result.error
    if result.kind == "unknown_host":
        names = ", ".join(f"{host['host_name']} at {host['url']}" for host in result.hosts or [])
        return f"No host at {result.base_url}. " + (f"Hosts: {names}." if names else "None is running.")
    if result.kind == "unavailable":
        return f"Cannot view host {result.host_name} at {result.base_url}: {result.error}"
    return ""
