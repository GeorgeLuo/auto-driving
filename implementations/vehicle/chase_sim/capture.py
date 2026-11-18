"""Runtime handoff for one encoded stream capture."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


@dataclass(frozen=True)
class ChaseCameraCapture:
    frame_id: str
    frame_index: int
    simulation_epoch: str
    captured_at_ms: int
    load_image: Callable[[], NDArray[np.uint8]] = field(repr=False, compare=False)
    jpeg_bytes: bytes = field(repr=False, compare=False)
