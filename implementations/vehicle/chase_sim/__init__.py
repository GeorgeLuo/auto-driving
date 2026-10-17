"""Chase simulator vehicle implementation."""

from .session import build_chase_session_fingerprint, compare_chase_session_fingerprints
from .capture import ChaseCameraCapture
from .car import CHASE_ATOMIC_EVALUATION_QUERY, CHASE_PASSIVE_CAMERA_ID
from .car import ChaseSimCar
from .session import ChasePassiveCaptureError
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

__all__ = [
    "CHASE_ATOMIC_EVALUATION_QUERY",
    "CHASE_PASSIVE_CAMERA_ID",
    "ChaseCameraCapture",
    "build_chase_session_fingerprint",
    "compare_chase_session_fingerprints",
    "ChaseSimCar",
    "ChasePassiveCaptureError",
    "ChaseCaptureValidationError",
    "align_candidate_with_reference",
    "build_chaser_reference",
    "evaluate_chase_evaluator_reference",
    "format_chase_frame_id",
    "score_reference_alignment_batch",
    "simulator_epoch_from_sensor_frame",
    "simulator_frame_index_from_sensor_frame",
    "validate_chase_sensor_capture",
]
