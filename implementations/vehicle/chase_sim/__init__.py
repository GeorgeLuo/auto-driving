"""Chase simulator vehicle implementation."""

from .capture import ChaseCameraCapture
from .car import CHASE_ATOMIC_EVALUATION_QUERY, CHASE_PASSIVE_CAMERA_ID, ChaseSimCar
from .frame_identity import (
    ChaseCaptureValidationError,
    align_candidate_with_reference,
    build_chaser_reference,
    evaluate_chase_evaluator_reference,
    format_chase_frame_id,
    score_reference_alignment_batch,
    simulator_epoch_from_sensor_frame,
    simulator_frame_index_from_sensor_frame,
    validate_chase_sensor_capture,
)
from .session import (
    ChasePassiveCaptureError,
    build_chase_session_fingerprint,
    compare_chase_session_fingerprints,
)

__all__ = [
    "ChaseCameraCapture",
    "CHASE_ATOMIC_EVALUATION_QUERY",
    "CHASE_PASSIVE_CAMERA_ID",
    "ChaseSimCar",
    "ChaseCaptureValidationError",
    "align_candidate_with_reference",
    "build_chaser_reference",
    "evaluate_chase_evaluator_reference",
    "format_chase_frame_id",
    "score_reference_alignment_batch",
    "simulator_epoch_from_sensor_frame",
    "simulator_frame_index_from_sensor_frame",
    "validate_chase_sensor_capture",
    "ChasePassiveCaptureError",
    "build_chase_session_fingerprint",
    "compare_chase_session_fingerprints",
]
