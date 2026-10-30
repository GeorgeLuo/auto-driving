"""Terminal writing shared by live step streams."""

from __future__ import annotations

from typing import TextIO


def write_stream_frame(output: TextIO | None, text: str, *, clear: bool) -> None:
    """Write and flush one formatted frame; the caller owns its stream policy."""

    if output is None:
        return
    if clear:
        output.write("\033[2J\033[H")
    output.write(text + "\n")
    output.flush()
