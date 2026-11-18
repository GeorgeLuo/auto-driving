"""The viewer a continuous text stream names.

A running terminal stream prints the command that opens the runtime viewer
on the same host's stream. It serves no page of its own.
"""

from __future__ import annotations

from dataclasses import replace

from ...hosts.host_formatting import viewer_command
from .models import StreamEvent


def with_view(show_view: bool, event: StreamEvent, *, host_name: str, **fields: object) -> StreamEvent:
    """``event`` with the viewer command when ``show_view``, and ``fields``."""

    return replace(event, view=viewer_command(host_name) if show_view else None, host_name=host_name, **fields)
