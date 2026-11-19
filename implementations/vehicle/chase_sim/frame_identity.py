"""Chase simulator frame identity and evaluator-only reference helpers.

The atomic evaluation capture owns the camera image, simulation-run identity,
and a bounded control reference for one immutable simulator state. The control
reference is for post-cycle scoring only; it must never become perception,
observation, memory, or controller input.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING, Literal, NoReturn
from urllib.parse import unquote_to_bytes

from PIL import Image

from .control import ChaseControl, validate_chase_control

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray


class ChaseCaptureValidationError(ValueError):
    """Exact fail-closed diagnostic for a required atomic-capture field."""

    def __init__(self, *, code: str, path: str, message: str):
        self.code = code
        self.path = path
        self.detail = message
        super().__init__(f"{code}: {path}: {message}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "automa_cli_error",
            "error_code": self.code,
            "error": self.detail,
            "layer": "capture",
            "details": {"path": self.path},
            "recovery": None,
            "exit_code": 1,
        }


@dataclass(frozen=True)
class ChaseFrameIdentity:
    capture_id: str
    actor_id: str
    game_id: str
    simulation_epoch: str
    simulator_frame_index: int

    @property
    def frame_id(self) -> str:
        return format_chase_frame_id(self.simulator_frame_index)


@dataclass(frozen=True)
class ChaseCaptureImage:
    width: int
    height: int
    content_type: str
    raster_format: str
    validated_bytes: bytes = field(repr=False)

    def image_array(self) -> NDArray[np.uint8]:
        """Decode the retained raster without parsing its envelope again."""
        import numpy as np

        try:
            with Image.open(BytesIO(self.validated_bytes)) as raster:
                raster.load()
                return np.array(raster.convert("RGB"))
        except Exception as exc:  # Pillow raises many format-specific errors
            _capture_error(
                "capture_image_invalid",
                "sensor.image.dataUrl",
                f"decoded payload is not a valid image: {exc}",
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "encoding": "data_url",
            "width": self.width,
            "height": self.height,
            "content_type": self.content_type,
            "raster_format": self.raster_format,
        }


@dataclass(frozen=True)
class ChaseSensorCapture:
    identity: ChaseFrameIdentity
    image: ChaseCaptureImage

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "chase_sensor_capture",
            "capture_id": self.identity.capture_id,
            "actor_id": self.identity.actor_id,
            "game_id": self.identity.game_id,
            "simulation_epoch": self.identity.simulation_epoch,
            "simulator_frame_index": self.identity.simulator_frame_index,
            "frame_id": self.identity.frame_id,
            "playback_advanced": False,
            "image": self.image.to_dict(),
        }


@dataclass(frozen=True)
class ChaserControlAction:
    control: ChaseControl
    selected_action_proposal_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            **self.control.to_dict(),
            "selectedActionProposalId": self.selected_action_proposal_id,
        }


@dataclass(frozen=True)
class ChaserReference:
    identity: ChaseFrameIdentity
    scenario: str
    chaser_control_source: str
    phase: str
    action_frame_index: int
    chaser_input: ChaseControl
    chaser_action: ChaserControlAction

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "chaser_reference",
            "evaluator_only": True,
            "capture_id": self.identity.capture_id,
            "actor_id": self.identity.actor_id,
            "simulator_frame_index": self.identity.simulator_frame_index,
            "simulation_epoch": self.identity.simulation_epoch,
            "frame_id": self.identity.frame_id,
            "game_id": self.identity.game_id,
            "scenario": self.scenario,
            "chaser_control_source": self.chaser_control_source,
            "phase": self.phase,
            "action_frame_index": self.action_frame_index,
            "chaser_input": self.chaser_input.to_dict(),
            "chaser_action": self.chaser_action.to_dict(),
        }


@dataclass(frozen=True)
class ChaseEvaluatorReference:
    status: Literal["available", "unavailable", "invalid"]
    reason: str | None = None
    path: str | None = None
    message: str | None = None
    reference: ChaserReference | None = None

    def status_dict(self) -> dict[str, object]:
        result: dict[str, object] = {"status": self.status}
        for key, value in (("reason", self.reason), ("path", self.path), ("message", self.message)):
            if value is not None:
                result[key] = value
        return result

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status, "reason": self.reason, "path": self.path,
        }
        if self.message is not None:
            result["message"] = self.message
        result["reference"] = self.reference.to_dict() if self.reference is not None else None
        return result


def require_protocol_frame_index(value: object) -> int | None:
    """Return a type-strict non-negative JSON integer, or None when invalid.

    Used at the untrusted capture/evaluator wire boundary. Rejects booleans,
    numeric strings, and floats so protocol drift is not silently normalized.
    """

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def format_chase_frame_id(frame_index: int) -> str:
    """Stable frame label anchored to the simulator frame index."""

    return f"chase_frame_{int(frame_index):06d}"


def build_chaser_reference(
    capture: object,
) -> ChaserReference | None:
    """Return the optional bounded evaluator reference when it is valid."""

    try:
        sensor = validate_chase_sensor_capture(capture)
    except ChaseCaptureValidationError:
        return None
    return evaluate_chase_evaluator_reference(capture, sensor=sensor).reference


def validate_chase_sensor_capture(
    capture: object,
    *,
    expected_actor_id: str = "chaser",
    validate_pixels: bool = True,
) -> ChaseSensorCapture:
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
    frame_index = require_protocol_frame_index(identity.get("frameIndex"))
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
        validated_image = _validate_image_envelope(
            image,
            data_url,
            width=width,
            height=height,
            validate_pixels=validate_pixels,
        )
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

    return ChaseSensorCapture(
        ChaseFrameIdentity(capture_id, actor_id, game_id, simulation_epoch, frame_index),
        validated_image,
    )


def evaluate_chase_evaluator_reference(
    capture: object,
    *,
    sensor: ChaseSensorCapture,
) -> ChaseEvaluatorReference:
    """Describe optional evaluator-reference availability without weakening capture."""

    if not isinstance(capture, dict):
        return _evaluator_invalid("$", "capture is not an object")
    evaluator = capture.get("evaluator")
    if evaluator is None:
        return ChaseEvaluatorReference("unavailable", "reference_missing", "evaluator.reference")
    if not isinstance(evaluator, dict):
        return _evaluator_invalid("evaluator", "expected an object")
    if evaluator.get("classification") != "non-sensor":
        return _evaluator_invalid(
            "evaluator.classification",
            "expected 'non-sensor'",
        )
    reference = evaluator.get("reference")
    if reference is None:
        return ChaseEvaluatorReference("unavailable", "reference_missing", "evaluator.reference")
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
    action_frame_index = require_protocol_frame_index(reference.get("actionFrameIndex"))
    if action_frame_index is None:
        return _evaluator_invalid(
            "evaluator.reference.actionFrameIndex",
            "expected a non-negative integer",
        )
    frame_index = sensor.identity.simulator_frame_index
    if action_frame_index > frame_index:
        return _evaluator_invalid(
            "evaluator.reference.actionFrameIndex",
            "must not be later than frameIdentity.frameIndex",
        )
    control_input = validate_chase_control(reference.get("input"))
    if control_input is None:
        return _evaluator_invalid(
            "evaluator.reference.input",
            "expected a bounded actor input",
        )
    control_action = _bounded_action(reference.get("action"))
    if control_action is None:
        return _evaluator_invalid(
            "evaluator.reference.action",
            "expected a bounded actor action",
        )

    bounded = ChaserReference(
        sensor.identity, scenario, control_source, phase, action_frame_index,
        control_input, control_action,
    )
    return ChaseEvaluatorReference("available", reference=bounded)


def frame_indices_strictly_increasing(indices: list[int]) -> bool:
    """True when each index is strictly greater than the previous."""

    if len(indices) < 2:
        return False
    return all(indices[index] < indices[index + 1] for index in range(len(indices) - 1))


@dataclass(frozen=True)
class ChaseReferenceAlignment:
    candidate_frame_index: int | None
    reference_frame_index: int | None
    candidate_simulation_epoch: str | None
    reference_simulation_epoch: str | None

    @property
    def aligned(self) -> bool:
        return (
            self.candidate_frame_index is not None
            and self.reference_frame_index is not None
            and self.candidate_frame_index == self.reference_frame_index
            and self.candidate_simulation_epoch is not None
            and self.candidate_simulation_epoch == self.reference_simulation_epoch
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "aligned": self.aligned,
            "candidate_frame_index": self.candidate_frame_index,
            "reference_frame_index": self.reference_frame_index,
            "candidate_simulation_epoch": self.candidate_simulation_epoch,
            "reference_simulation_epoch": self.reference_simulation_epoch,
            "reason": (
                "candidate and reference share simulation epoch and frame index"
                if self.aligned
                else "candidate/reference simulation-run identity mismatch or missing"
            ),
        }


def align_candidate_with_reference(
    *,
    candidate: ChaseSensorCapture | None,
    chaser_reference: ChaserReference | None,
) -> ChaseReferenceAlignment:
    """Score validated candidate/reference simulation-run identities."""
    identity = candidate.identity if candidate is not None else None
    reference = chaser_reference.identity if chaser_reference is not None else None
    return ChaseReferenceAlignment(
        identity.simulator_frame_index if identity is not None else None,
        reference.simulator_frame_index if reference is not None else None,
        identity.simulation_epoch if identity is not None else None,
        reference.simulation_epoch if reference is not None else None,
    )


@dataclass(frozen=True)
class ChaseReferenceFrame:
    sensor: ChaseSensorCapture | None
    chaser_reference: ChaserReference | None


@dataclass(frozen=True, kw_only=True)
class ChaseReferenceBatch:
    alignments: tuple[tuple[str | None, ChaseReferenceAlignment], ...]
    simulator_frame_indices: tuple[int, ...]
    game_ids: frozenset[str]
    scenarios: frozenset[str]
    simulation_epochs: frozenset[str]
    missing_identity: int
    missing_reference: int
    missing_run_identity: int
    mismatched: int
    min_frames: int

    @property
    def advancing_simulator_frames(self) -> bool:
        return frame_indices_strictly_increasing(list(self.simulator_frame_indices))

    @property
    def consistent_run_identity(self) -> bool:
        return (
            self.missing_run_identity == 0
            and self.game_ids == frozenset({"chase"})
            and len(self.scenarios) == 1
            and len(self.simulation_epochs) == 1
        )

    @property
    def aligned_count(self) -> int:
        return sum(alignment.aligned for _, alignment in self.alignments)

    @property
    def passed(self) -> bool:
        return (
            len(self.alignments) >= self.min_frames
            and self.missing_identity == 0
            and self.missing_reference == 0
            and self.mismatched == 0
            and self.advancing_simulator_frames
            and self.consistent_run_identity
            and self.aligned_count == len(self.alignments)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "error_code": "evaluator_reference_unavailable" if self.missing_reference else None,
            "error": (
                "Reference alignment requires a valid evaluator actor-control "
                "reference for every candidate frame; sensor perception remains usable. "
                "Recovery: use sensor-only perception, or rerun the reference-dependent "
                "procedure when evaluator_reference.status=available."
                if self.missing_reference
                else None
            ),
            "frame_count": len(self.alignments),
            "aligned_count": self.aligned_count,
            "missing_identity": self.missing_identity,
            "missing_reference": self.missing_reference,
            "missing_run_identity": self.missing_run_identity,
            "mismatched": self.mismatched,
            "advancing_simulator_frames": self.advancing_simulator_frames,
            "consistent_run_identity": self.consistent_run_identity,
            "game_ids": sorted(self.game_ids),
            "scenarios": sorted(self.scenarios),
            "simulation_epochs": sorted(self.simulation_epochs),
            "simulator_frame_indices": list(self.simulator_frame_indices),
            "alignments": [
                {"frame_id": frame_id, **alignment.to_dict()}
                for frame_id, alignment in self.alignments
            ],
            "reason": (
                "live frames advance within one simulation run and align with atomic references"
                if self.passed
                else (
                    "expected ≥"
                    f"{self.min_frames} advancing frames with matching epoch/index references "
                    "and one non-empty game/scenario/simulation epoch"
                )
            ),
        }


def score_reference_alignment_batch(
    frames: list[ChaseReferenceFrame],
    *,
    min_frames: int = 2,
) -> ChaseReferenceBatch:
    """Score typed candidate records for advancing, same-run atomic alignment."""
    alignments: list[tuple[str | None, ChaseReferenceAlignment]] = []
    indices: list[int] = []
    game_ids: set[str] = set()
    scenarios: set[str] = set()
    epochs: set[str] = set()
    missing_identity = 0
    missing_reference = 0
    mismatched = 0
    for frame in frames:
        sensor, reference = frame.sensor, frame.chaser_reference
        if sensor is None:
            missing_identity += 1
        else:
            indices.append(sensor.identity.simulator_frame_index)
        if reference is None:
            missing_reference += 1
        else:
            game_ids.add(reference.identity.game_id)
            scenarios.add(reference.scenario)
            epochs.add(reference.identity.simulation_epoch)
        alignment = align_candidate_with_reference(candidate=sensor, chaser_reference=reference)
        alignments.append((sensor.identity.frame_id if sensor is not None else None, alignment))
        if sensor is not None and reference is not None and not alignment.aligned:
            mismatched += 1
    return ChaseReferenceBatch(
        alignments=tuple(alignments), simulator_frame_indices=tuple(indices),
        game_ids=frozenset(game_ids), scenarios=frozenset(scenarios), simulation_epochs=frozenset(epochs),
        missing_identity=missing_identity, missing_reference=missing_reference,
        missing_run_identity=missing_identity, mismatched=mismatched, min_frames=min_frames,
    )


def _nonempty_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _capture_error(code: str, path: str, message: str) -> NoReturn:
    raise ChaseCaptureValidationError(code=code, path=path, message=message)


_RASTER_MIME_BY_FORMAT = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


def _validate_image_envelope(
    image: dict[str, object],
    data_url: str,
    *,
    width: int,
    height: int,
    validate_pixels: bool = True,
) -> ChaseCaptureImage:
    decoded = _decode_data_url_payload(data_url)
    try:
        with Image.open(BytesIO(decoded)) as raster:
            if validate_pixels:
                raster.load()
            decoded_size = raster.size
            decoded_format = str(raster.format or "").upper()
    except Exception as exc:  # Pillow raises many format-specific errors
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            f"decoded payload is not a valid image: {exc}",
        )

    decoded_mime = _RASTER_MIME_BY_FORMAT.get(decoded_format)
    if decoded_mime is None:
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            f"decoded raster format {decoded_format or 'unknown'!r} is not supported",
        )
    data_url_mime = _data_url_mime(data_url)
    if data_url_mime != decoded_mime:
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            f"declared MIME {data_url_mime!r} does not match decoded {decoded_mime!r}",
        )
    if decoded_size != (width, height):
        _capture_error(
            "capture_image_invalid",
            "sensor.image",
            f"declared dimensions {(width, height)!r} do not match decoded {decoded_size!r}",
        )

    if "contentType" in image:
        declared = image["contentType"]
        if not isinstance(declared, str):
            _capture_error(
                "capture_image_invalid",
                "sensor.image.contentType",
                "expected a string when present",
            )
        declared = declared.strip()
        if declared:
            canonical_declared = declared.lower()
            if ";" in canonical_declared or canonical_declared != decoded_mime:
                _capture_error(
                    "capture_image_invalid",
                    "sensor.image.contentType",
                    f"declared content type {declared!r} does not match {decoded_mime!r}",
                )

    return ChaseCaptureImage(decoded_size[0], decoded_size[1], decoded_mime, decoded_format, decoded)


def _data_url_mime(data_url: str) -> str:
    header, separator, _ = data_url.partition(",")
    if separator != "," or not header.lower().startswith("data:"):
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            "expected a non-empty data URL",
        )
    mime = header[5:].split(";", 1)[0].strip().lower()
    if mime not in _RASTER_MIME_BY_FORMAT.values():
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            f"unsupported raster MIME {mime or 'missing'!r}",
        )
    return mime


def _decode_data_url_payload(data_url: str) -> bytes:
    header, separator, payload = data_url.partition(",")
    if separator != "," or not header.lower().startswith("data:") or not payload:
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            "expected a non-empty data URL",
        )
    _data_url_mime(data_url)
    parameters = [part.strip().lower() for part in header[5:].split(";")[1:]]
    if "base64" in parameters:
        try:
            decoded = base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error) as exc:
            _capture_error(
                "capture_image_invalid",
                "sensor.image.dataUrl",
                f"invalid base64 payload: {exc}",
            )
    else:
        try:
            decoded = unquote_to_bytes(payload)
        except (TypeError, ValueError) as exc:
            _capture_error(
                "capture_image_invalid",
                "sensor.image.dataUrl",
                f"invalid URL-encoded payload: {exc}",
            )
    if not decoded:
        _capture_error(
            "capture_image_invalid",
            "sensor.image.dataUrl",
            "decoded payload is empty",
        )
    return decoded


def _evaluator_invalid(path: str, message: str) -> ChaseEvaluatorReference:
    return ChaseEvaluatorReference("invalid", "reference_invalid", path, message)


def _bounded_action(value: object) -> ChaserControlAction | None:
    if not isinstance(value, dict):
        return None
    control = validate_chase_control(value)
    if control is None:
        return None
    proposal = value.get("selectedActionProposalId")
    if proposal is not None:
        proposal = _nonempty_string(proposal)
        if proposal is None:
            return None
    return ChaserControlAction(control, proposal)
