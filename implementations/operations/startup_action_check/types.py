from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from autonomy.vehicle import VehiclePulse


@dataclass(frozen=True)
class StartupActionCheckInstruction:
    """One capture -> action pulse -> capture startup check."""

    label: str
    pulse: VehiclePulse
    expect_change: bool = True
    min_mean_abs_diff_norm: float | None = None
    min_changed_pixel_ratio: float | None = None
    max_mean_abs_diff_norm: float | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pulse"] = self.pulse.to_dict()
        return data


@dataclass(frozen=True)
class StartupActionCheckPlan:
    """A reusable startup validation plan independent of vehicle implementation."""

    name: str
    version: int
    frame_endpoint: str
    instructions: tuple[StartupActionCheckInstruction, ...]
    comparison_pixel_threshold: int = 18
    default_min_mean_abs_diff_norm: float = 0.008
    default_min_mean_abs_diff_excess_norm: float = 0.003
    default_min_changed_pixel_ratio: float = 0.005
    default_min_changed_pixel_ratio_excess: float = 0.005
    default_max_still_mean_abs_diff_norm: float = 0.05
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "frame_endpoint": self.frame_endpoint,
            "comparison_pixel_threshold": self.comparison_pixel_threshold,
            "default_min_mean_abs_diff_norm": self.default_min_mean_abs_diff_norm,
            "default_min_mean_abs_diff_excess_norm": self.default_min_mean_abs_diff_excess_norm,
            "default_min_changed_pixel_ratio": self.default_min_changed_pixel_ratio,
            "default_min_changed_pixel_ratio_excess": self.default_min_changed_pixel_ratio_excess,
            "default_max_still_mean_abs_diff_norm": self.default_max_still_mean_abs_diff_norm,
            "metadata": self.metadata,
            "instructions": [instruction.to_dict() for instruction in self.instructions],
        }
