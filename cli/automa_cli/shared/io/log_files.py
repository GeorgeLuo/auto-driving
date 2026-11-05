"""Logs the CLI's processes write and command output quotes."""

from __future__ import annotations

from pathlib import Path


LOG_TAIL_LINES = 20


def read_log_tail(path: Path) -> list[str] | None:
    """The log's last ``LOG_TAIL_LINES`` lines, or ``None`` when it cannot be read."""

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return lines[-LOG_TAIL_LINES:]
