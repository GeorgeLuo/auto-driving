"""The wall-clock time the CLI stamps on its run records and replay state."""

from __future__ import annotations

import time


def timestamp_ms() -> int:
    return int(time.time() * 1000)
