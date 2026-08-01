"""Chase simulator frame identity and evaluator-only reference helpers.

The atomic evaluation capture owns the camera image, simulation-run identity,
and a bounded control reference for one immutable simulator state. The control
reference is for post-cycle scoring only; it must never become perception,
observation, memory, or controller input.
"""

from __future__ import annotations

import base64
import binascii
import math
from io import BytesIO
from typing import Any
from urllib.parse import unquote_to_bytes

from PIL import Image


class ChaseCaptureValidationError(ValueError):
    """Exact fail-closed diagnostic for a required atomic-capture field."""

    def __init__(self, *, code: str, path: str, message: str):
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code}: {path}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "automa_cli_error_v1",
            "error": self.code,
            "layer": "capture",
            "message": self.detail,
            "details": {"path": self.path},
            "recovery": None,
            "exit_code": 1,
        }


def coerce_simulator_frame_index(value: Any) -> int | None:
    """Return a non-negative int frame index, or None when absent/invalid."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip(), 10)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def format_chase_frame_id(frame_index: int) -> str:
    """Stable frame label anchored to the simulator frame index."""

    return f"chase_frame_{int(frame_index):06d}"


def simulator_frame_index_from_snapshot(snapshot: Any) -> int | None:
    """Extract simulator frame index from a SensorSnapshot or its dict form."""

    for metadata in _snapshot_metadata_records(snapshot):
        for key in ("simulator_frame_index", "frame_index", "frameIndex"):
            index = coerce_simulator_frame_index(metadata.get(key))
            if index is not None:
                return index
    return None


def simulator_epoch_from_snapshot(snapshot: Any) -> str | None:
    """Extract the simulation-run epoch from a SensorSnapshot or dict form."""

    for metadata in _snapshot_metadata_records(snapshot):
        for key in ("simulation_epoch", "simulationEpoch"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def build_chase_shadow_reference(
    capture: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the optional bounded evaluator reference when it is valid."""

    try:
        sensor = validate_chase_sensor_capture(capture)
    except ChaseCaptureValidationError:
        return None
    evaluation = evaluate_chase_evaluator_reference(capture, sensor=sensor)
    reference = evaluation.get("reference")
    return reference if evaluation.get("status") == "available" and isinstance(reference, dict) else None


def validate_chase_sensor_capture(
    capture: dict[str, Any] | None,
    *,
    expected_actor_id: str = "chaser",
) -> dict[str, Any]:
    """Validate required sensor/image identity independently from evaluator data."""

    if not isinstance(capture, dict):
        _capture_error("capture_identity_invalid", "$", "expected an object")
    version = capture.get("contractVersion")
    if isinstance(version, bool) or version != 1:
        _capture_error("capture_identity_invalid", "contractVersion", "expected integer 1")
    capture_id = _nonempty_string(capture.get("captureId"))
    if capture_id is None:
        _capture_error("capture_identity_invalid", "captureId", "expected a non-empty string")
    actor_id = _nonempty_string(capture.get("actorId"))
    if actor_id != expected_actor_id:
        _capture_error(
            "capture_identity_invalid",
            "actorId",
            f"expected {expected_actor_id!r}, got {actor_id!r}",
        )

    playback = capture.get("playback")
    if not isinstance(playback, dict):
        _capture_error("capture_identity_invalid", "playback", "expected an object")
    if playback.get("advanced") is not False:
        _capture_error(
            "capture_identity_invalid",
            "playback.advanced",
            "passive capture must report false",
        )

    identity = capture.get("frameIdentity")
    if not isinstance(identity, dict):
        _capture_error("capture_identity_invalid", "frameIdentity", "expected an object")
    game_id = _nonempty_string(identity.get("gameId"))
    if game_id != "chase":
        _capture_error(
            "capture_identity_invalid",
            "frameIdentity.gameId",
            f"expected 'chase', got {game_id!r}",
        )
    simulation_epoch = _nonempty_string(identity.get("simulationEpoch"))
    if simulation_epoch is None:
        _capture_error(
            "capture_identity_invalid",
            "frameIdentity.simulationEpoch",
            "expected a non-empty string",
        )
    frame_index = coerce_simulator_frame_index(identity.get("frameIndex"))
    if frame_index is None:
        _capture_error(
            "capture_identity_invalid",
            "frameIdentity.frameIndex",
            "expected a non-negative integer",
        )

    sensor = capture.get("sensor")
    if not isinstance(sensor, dict):
        _capture_error("capture_image_invalid", "sensor", "expected an object")
    image = sensor.get("image")
    if not isinstance(image, dict):
        _capture_error("capture_image_invalid", "sensor.image", "expected an object")
    width = image.get("width")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        _capture_error(
            "capture_image_invalid",
            "sensor.image.width",
            "expected a positive integer",
        )
    height = image.get("height")
    if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
        _capture_error(
            "capture_image_invalid",
            "sensor.image.height",
            "expected a positive integer",
        )

    data_url = image.get("dataUrl")
    svg = image.get("svg")
    if isinstance(data_url, str) and data_url:
        _validate_data_url(data_url)
        encoding = "data_url"
    elif isinstance(svg, str) and svg.strip():
        # Metrics UI may advertise SVG as a fallback image type, but Automa's
        # observation worker always requests a raster path (typically .png).
        # Accepting SVG-only here deferred a generic write-path ValueError; fail
        # closed at the sensor boundary with an exact path instead.
        _capture_error(
            "capture_image_invalid",
            "sensor.image.svg",
            "SVG-only captures are not accepted; provide a decodable raster "
            "dataUrl compatible with the worker .png output",
        )
    else:
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            "expected a decodable raster data URL",
        )

    return {
        "schema": "chase_sensor_capture_v1",
        "capture_id": capture_id,
        "actor_id": actor_id,
        "game_id": game_id,
        "simulation_epoch": simulation_epoch,
        "simulator_frame_index": frame_index,
        "frame_id": format_chase_frame_id(frame_index),
        "playback_advanced": False,
        "image": {
            "width": width,
            "height": height,
            "encoding": encoding,
            "content_type": _image_content_type(image),
        },
    }


def evaluate_chase_evaluator_reference(
    capture: dict[str, Any] | None,
    *,
    sensor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe optional evaluator-reference availability without weakening capture."""

    if not isinstance(capture, dict):
        return _evaluator_invalid("$", "capture is not an object")
    if sensor is None:
        try:
            sensor = validate_chase_sensor_capture(capture)
        except ChaseCaptureValidationError as exc:
            return _evaluator_invalid(exc.path, "required sensor capture is invalid")
    evaluator = capture.get("evaluator")
    if evaluator is None:
        return {
            "status": "unavailable",
            "reason": "reference_missing",
            "path": "evaluator.reference",
            "reference": None,
        }
    if not isinstance(evaluator, dict):
        return _evaluator_invalid("evaluator", "expected an object")
    if evaluator.get("classification") != "non-sensor":
        return _evaluator_invalid(
            "evaluator.classification",
            "expected 'non-sensor'",
        )
    reference = evaluator.get("reference")
    if reference is None:
        return {
            "status": "unavailable",
            "reason": "reference_missing",
            "path": "evaluator.reference",
            "reference": None,
        }
    if not isinstance(reference, dict):
        return _evaluator_invalid("evaluator.reference", "expected an object")
    if reference.get("kind") != "actor-control-reference":
        return _evaluator_invalid(
            "evaluator.reference.kind",
            "expected 'actor-control-reference'",
        )

    scenario = _nonempty_string(reference.get("scenarioId"))
    if scenario is None:
        return _evaluator_invalid(
            "evaluator.reference.scenarioId",
            "expected a non-empty string",
        )
    control_source = _nonempty_string(reference.get("controlSource"))
    if control_source is None:
        return _evaluator_invalid(
            "evaluator.reference.controlSource",
            "expected a non-empty string",
        )
    phase = _nonempty_string(reference.get("phase"))
    if phase is None:
        return _evaluator_invalid(
            "evaluator.reference.phase",
            "expected a non-empty string",
        )
    action_frame_index = coerce_simulator_frame_index(reference.get("actionFrameIndex"))
    if action_frame_index is None:
        return _evaluator_invalid(
            "evaluator.reference.actionFrameIndex",
            "expected a non-negative integer",
        )
    frame_index = int(sensor["simulator_frame_index"])
    if action_frame_index > frame_index:
        return _evaluator_invalid(
            "evaluator.reference.actionFrameIndex",
            "must not be later than frameIdentity.frameIndex",
        )
    control_input = _bounded_control(reference.get("input"), include_proposal=False)
    if control_input is None:
        return _evaluator_invalid(
            "evaluator.reference.input",
            "expected a bounded actor input",
        )
    control_action = _bounded_control(reference.get("action"), include_proposal=True)
    if control_action is None:
        return _evaluator_invalid(
            "evaluator.reference.action",
            "expected a bounded actor action",
        )

    bounded = {
        "schema": "chase_shadow_reference_v1",
        "evaluator_only": True,
        "capture_id": sensor["capture_id"],
        "actor_id": sensor["actor_id"],
        "simulator_frame_index": frame_index,
        "simulation_epoch": sensor["simulation_epoch"],
        "frame_id": format_chase_frame_id(frame_index),
        "game_id": sensor["game_id"],
        "scenario": scenario,
        "chaser_control_source": control_source,
        "phase": phase,
        "action_frame_index": action_frame_index,
        "chaser_input": control_input,
        "chaser_action": control_action,
    }
    return {
        "status": "available",
        "reason": None,
        "path": None,
        "reference": bounded,
    }


def frame_indices_strictly_increasing(indices: list[int]) -> bool:
    """True when each index is strictly greater than the previous."""

    if len(indices) < 2:
        return False
    return all(indices[index] < indices[index + 1] for index in range(len(indices) - 1))


def align_candidate_with_shadow(
    *,
    candidate_frame_index: int | None,
    candidate_simulation_epoch: str | None,
    shadow_reference: dict[str, Any] | None,
) -> dict[str, Any]:
    """Score candidate/reference alignment using full simulation-run identity."""

    shadow_index = None
    shadow_epoch = None
    if isinstance(shadow_reference, dict):
        shadow_index = coerce_simulator_frame_index(
            shadow_reference.get("simulator_frame_index")
            if shadow_reference.get("simulator_frame_index") is not None
            else shadow_reference.get("frame_index")
        )
        shadow_epoch = _nonempty_string(shadow_reference.get("simulation_epoch"))
    candidate_epoch = _nonempty_string(candidate_simulation_epoch)
    matched = (
        candidate_frame_index is not None
        and shadow_index is not None
        and int(candidate_frame_index) == int(shadow_index)
        and candidate_epoch is not None
        and candidate_epoch == shadow_epoch
    )
    return {
        "aligned": matched,
        "candidate_frame_index": candidate_frame_index,
        "shadow_frame_index": shadow_index,
        "candidate_simulation_epoch": candidate_epoch,
        "shadow_simulation_epoch": shadow_epoch,
        "reason": (
            "candidate and shadow share simulation epoch and frame index"
            if matched
            else "candidate/shadow simulation-run identity mismatch or missing"
        ),
    }


def score_shadow_alignment_batch(
    frames: list[dict[str, Any]],
    *,
    min_frames: int = 2,
) -> dict[str, Any]:
    """Score candidate records for advancing, same-run atomic alignment."""

    alignments: list[dict[str, Any]] = []
    indices: list[int] = []
    game_ids: set[str] = set()
    scenarios: set[str] = set()
    epochs: set[str] = set()
    missing_identity = 0
    missing_shadow = 0
    missing_run_identity = 0
    mismatched = 0
    for frame in frames:
        if not isinstance(frame, dict):
            missing_identity += 1
            continue
        index = coerce_simulator_frame_index(
            frame.get("simulator_frame_index")
            if frame.get("simulator_frame_index") is not None
            else frame.get("frame_index")
        )
        candidate_epoch = _nonempty_string(frame.get("simulation_epoch"))
        shadow = frame.get("shadow_reference")
        if not isinstance(shadow, dict):
            shadow = None
            missing_shadow += 1
        if index is None:
            missing_identity += 1
        else:
            indices.append(int(index))
        if candidate_epoch is None:
            missing_run_identity += 1

        if shadow is not None:
            game = _nonempty_string(shadow.get("game_id"))
            scenario = _nonempty_string(shadow.get("scenario"))
            epoch = _nonempty_string(shadow.get("simulation_epoch"))
            if game is None or scenario is None or epoch is None:
                missing_run_identity += 1
            else:
                game_ids.add(game)
                scenarios.add(scenario)
                epochs.add(epoch)

        alignment = align_candidate_with_shadow(
            candidate_frame_index=index,
            candidate_simulation_epoch=candidate_epoch,
            shadow_reference=shadow,
        )
        alignments.append({"frame_id": frame.get("frame_id"), **alignment})
        if index is not None and shadow is not None and not alignment["aligned"]:
            mismatched += 1

    advancing = frame_indices_strictly_increasing(indices)
    consistent_run_identity = (
        missing_run_identity == 0
        and game_ids == {"chase"}
        and len(scenarios) == 1
        and len(epochs) == 1
    )
    aligned_count = sum(1 for item in alignments if item.get("aligned"))
    passed = (
        len(alignments) >= min_frames
        and missing_identity == 0
        and missing_shadow == 0
        and mismatched == 0
        and advancing
        and consistent_run_identity
        and aligned_count == len(alignments)
    )
    return {
        "passed": passed,
        "error": (
            {
                "schema": "automa_cli_error_v1",
                "error": "evaluator_reference_unavailable",
                "layer": "capture",
                "message": (
                    "Shadow alignment requires a valid evaluator actor-control "
                    "reference for every candidate frame; sensor perception remains usable."
                ),
                "details": {
                    "required_procedure": "shadow_reference_alignment",
                    "missing_reference_frames": missing_shadow,
                },
                "recovery": (
                    "Use sensor-only perception, or rerun the reference-dependent "
                    "procedure when evaluator_reference.status=available."
                ),
                "exit_code": 1,
            }
            if missing_shadow
            else None
        ),
        "frame_count": len(alignments),
        "aligned_count": aligned_count,
        "missing_identity": missing_identity,
        "missing_shadow": missing_shadow,
        "missing_run_identity": missing_run_identity,
        "mismatched": mismatched,
        "advancing_simulator_frames": advancing,
        "consistent_run_identity": consistent_run_identity,
        "game_ids": sorted(game_ids),
        "scenarios": sorted(scenarios),
        "simulation_epochs": sorted(epochs),
        "simulator_frame_indices": indices,
        "alignments": alignments,
        "reason": (
            "live frames advance within one simulation run and align with atomic references"
            if passed
            else (
                "expected ≥"
                f"{min_frames} advancing frames with matching epoch/index references "
                "and one non-empty game/scenario/simulation epoch"
            )
        ),
    }


def _snapshot_metadata_records(snapshot: Any) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    metadata: dict[str, Any] = {}
    readings: dict[str, Any] = {}
    if isinstance(snapshot, dict):
        maybe_metadata = snapshot.get("metadata")
        maybe_readings = snapshot.get("readings")
    else:
        maybe_metadata = getattr(snapshot, "metadata", None)
        maybe_readings = getattr(snapshot, "readings", None)
    if isinstance(maybe_metadata, dict):
        metadata = maybe_metadata
    if isinstance(maybe_readings, dict):
        readings = maybe_readings

    records = [metadata]
    for reading in readings.values():
        maybe = reading.get("metadata") if isinstance(reading, dict) else getattr(reading, "metadata", None)
        if isinstance(maybe, dict):
            records.append(maybe)
    return records


def _nonempty_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _capture_error(code: str, path: str, message: str) -> None:
    raise ChaseCaptureValidationError(code=code, path=path, message=message)


def _validate_data_url(data_url: str) -> None:
    header, separator, payload = data_url.partition(",")
    if separator != "," or not header.startswith("data:") or not payload:
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            "expected a non-empty data URL",
        )
    if ";base64" not in header:
        try:
            decoded = unquote_to_bytes(payload)
        except (TypeError, ValueError) as exc:
            _capture_error(
                "capture_image_invalid",
                "sensor.image.dataUrl",
                f"invalid URL-encoded payload: {exc}",
            )
    else:
        try:
            decoded = base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error) as exc:
            _capture_error(
                "capture_image_invalid",
                "sensor.image.dataUrl",
                f"invalid base64 payload: {exc}",
            )
    if not decoded:
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            "decoded payload is empty",
        )
    try:
        with Image.open(BytesIO(decoded)) as image:
            image.load()
    except Exception as exc:  # Pillow raises many format-specific errors
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            f"decoded payload is not a valid image: {exc}",
        )


def _image_content_type(image: dict[str, Any]) -> str:
    explicit = _nonempty_string(image.get("contentType"))
    if explicit is not None:
        return explicit
    data_url = image.get("dataUrl")
    if isinstance(data_url, str) and data_url.startswith("data:"):
        return data_url.removeprefix("data:").split(";", 1)[0].split(",", 1)[0]
    return "image/svg+xml" if isinstance(image.get("svg"), str) else "application/octet-stream"


def _evaluator_invalid(path: str, message: str) -> dict[str, Any]:
    return {
        "status": "invalid",
        "reason": "reference_invalid",
        "path": path,
        "message": message,
        "reference": None,
    }


def _bounded_control(value: Any, *, include_proposal: bool) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    source = _nonempty_string(value.get("source"))
    forward = value.get("forward")
    reverse = value.get("reverse")
    steering = value.get("steering")
    if (
        source is None
        or not isinstance(forward, bool)
        or not isinstance(reverse, bool)
        or isinstance(steering, bool)
        or not isinstance(steering, (int, float))
        or not math.isfinite(float(steering))
        or float(steering) < -1.0
        or float(steering) > 1.0
    ):
        return None
    result: dict[str, Any] = {
        "source": source,
        "forward": forward,
        "reverse": reverse,
        "steering": float(steering),
    }
    if include_proposal:
        proposal = value.get("selectedActionProposalId")
        if proposal is not None:
            proposal = _nonempty_string(proposal)
            if proposal is None:
                return None
        result["selectedActionProposalId"] = proposal
    return result
