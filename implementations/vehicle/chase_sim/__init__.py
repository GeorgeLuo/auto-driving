"""Chase simulator vehicle implementation."""

from .car import ChaseSimCar
from .frame_identity import (
    align_candidate_with_shadow,
    build_chase_shadow_reference,
    format_chase_frame_id,
    score_shadow_alignment_batch,
    simulator_epoch_from_snapshot,
    simulator_frame_index_from_snapshot,
)

__all__ = [
    "ChaseSimCar",
    "align_candidate_with_shadow",
    "build_chase_shadow_reference",
    "format_chase_frame_id",
    "score_shadow_alignment_batch",
    "simulator_epoch_from_snapshot",
    "simulator_frame_index_from_snapshot",
]
