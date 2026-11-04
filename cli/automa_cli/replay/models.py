"""Replay inputs and outcomes shared by loaders, runners, and presentation."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autonomy.runtime.execution import ControlApplication

WORKBENCH_ADAPTER = "image_directory"


@dataclass(frozen=True)
class ReplayFrame:
    source_id: str
    frame_id: str
    frame_index: int
    position: int
    timestamp_ms: int
    image_path: Path | None
    absence_reason: str | None = None
    content_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # The inputs the recording host wrote, or None where it recorded none.
    mode: str | None = None
    user_steering: float | None = None
    user_throttle: float | None = None
    # The window the recording host published, or None when it recorded none.
    control_applications: tuple[ControlApplication, ...] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def absent(self) -> bool:
        return self.absence_reason is not None or self.image_path is None

    def to_dict(self, *, include_path: bool = True) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "frame_id": self.frame_id,
            "frame_index": self.frame_index,
            "position": self.position,
            "timestamp_ms": self.timestamp_ms,
            "image_path": str(self.image_path)
            if include_path and self.image_path
            else None,
            "image_name": self.image_path.name if self.image_path else None,
            "absence_reason": self.absence_reason,
            "absent": self.absent,
            "content_type": self.content_type,
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass(frozen=True)
class ImageSource:
    source_path: Path
    source_id: str
    frames: tuple[ReplayFrame, ...]
    manifest_path: Path | None = None
    adapter: str = WORKBENCH_ADAPTER

    def to_dict(self) -> dict[str, Any]:
        """Return the public source summary, not the internal frame inventory."""

        return {
            "adapter": self.adapter,
            "source_path": str(self.source_path),
            "source_id": self.source_id,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "frame_count": len(self.frames),
        }


class SourceValidationError(ValueError):
    """A user-visible source contract failure."""

    boundary = "source"


@dataclass(frozen=True)
class FrameOutcome:
    result: Any
    decision: dict[str, Any] | None
    # Each step runner's plugin report after the frame, by step.
    plugin_reports: dict[str, dict[str, Any] | None]
