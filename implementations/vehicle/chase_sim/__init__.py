"""Chase simulator vehicle implementation."""

from .car import ChasePassiveCaptureError, ChaseSimCar
from .frame_identity import (
    ChaseCaptureValidationError,
    align_candidate_with_shadow,
    build_chase_shadow_reference,
    evaluate_chase_evaluator_reference,
    format_chase_frame_id,
    score_shadow_alignment_batch,
    simulator_epoch_from_snapshot,
    simulator_frame_index_from_snapshot,
    validate_chase_sensor_capture,
)

__all__ = [
    "ChaseSimCar",
    "ChasePassiveCaptureError",
    "ChaseCaptureValidationError",
    "align_candidate_with_shadow",
    "build_chase_shadow_reference",
    "evaluate_chase_evaluator_reference",
    "format_chase_frame_id",
    "score_shadow_alignment_batch",
    "simulator_epoch_from_snapshot",
    "simulator_frame_index_from_snapshot",
    "validate_chase_sensor_capture",
]
