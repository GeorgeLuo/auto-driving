"""Runtime handoff for one encoded stream capture."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ChaseCameraCapture:
    frame_id: str
    frame_index: int
    simulation_epoch: str
    captured_at_ms: int
    load_image: Callable[[], Any] = field(repr=False, compare=False)
    jpeg_bytes: bytes = field(repr=False, compare=False)
