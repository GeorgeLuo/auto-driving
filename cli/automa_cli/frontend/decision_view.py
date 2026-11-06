"""Project retained evidence onto the completed report's exact source image."""
from __future__ import annotations

import json
import math
from typing import Any

from autonomy.decision_cycle.memory.interface import evidence_records


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def _supported_image_bbox(location: Any) -> bool:
    """Return whether D2 can render this location without coercion or guessing."""

    if not isinstance(location, dict) or location.get("frame") != "image":
        return False
    raw = location.get("bbox_xyxy_norm")
    if not isinstance(raw, list) or len(raw) != 4:
        return False
    values: list[float] = []
    for value in raw:
        if type(value) not in {int, float} or not math.isfinite(value):
            return False
        normalized = float(value)
        if not 0.0 <= normalized <= 1.0:
            return False
        values.append(normalized)
    x1, y1, x2, y2 = values
    return x2 > x1 and y2 > y1


def _evidence_projection(
    *,
    transaction_id: str,
    frame_id: str,
    observation: Any,
    evidence_input: Any,
) -> dict[str, Any]:
    """Project only evidence that is exactly attributable to the current image.

    The accepted cycle stays on the report. This projection is the
    machine-visible rendering gate: an unavailable record carries its raw
    origin but never carries renderable geometry.
    """

    raw_records = evidence_input if isinstance(evidence_input, list) else []
    observation_id = (
        observation.get("observation_id")
        if isinstance(observation, dict)
        else None
    )
    observation_things = (
        observation.get("things")
        if isinstance(observation, dict)
        else None
    )
    things = observation_things if isinstance(observation_things, list) else []
    observation_plugin_id = (
        observation.get("perception_plugin_id")
        if isinstance(observation, dict)
        else None
    )

    projected: list[dict[str, Any]] = []
    for record in raw_records:
        record_id = record.get("record_id") if isinstance(record, dict) else None
        origin = record.get("origin") if isinstance(record, dict) else None
        reason = ""
        matched_thing: dict[str, Any] | None = None
        if not isinstance(record, dict) or type(record_id) is not str or not record_id:
            reason = "invalid_record"
        elif not isinstance(origin, dict):
            reason = "provenance_unavailable"
        elif origin.get("frame_id") != frame_id:
            reason = "source_image_unavailable"
        elif type(observation_id) is not str or not observation_id:
            reason = "observation_unavailable"
        elif origin.get("observation_id") != observation_id:
            reason = "observation_mismatch"
        elif type(origin.get("observed_id")) is not str or not origin.get("observed_id"):
            reason = "provenance_unavailable"
        else:
            matches = [
                thing
                for thing in things
                if isinstance(thing, dict)
                and thing.get("thing_id") == origin["observed_id"]
            ]
            if not matches:
                reason = "evidence_missing"
            elif len(matches) != 1:
                reason = "evidence_ambiguous"
            else:
                matched_thing = matches[0]

        if not reason and matched_thing is not None:
            source_plugin_id = matched_thing.get("source_plugin_id")
            if source_plugin_id is None:
                source_plugin_id = observation_plugin_id
            if (
                origin.get("coordinate_frame") != "image"
                or origin.get("source_plugin_id") != source_plugin_id
            ):
                reason = "provenance_mismatch"
            elif not _supported_image_bbox(record.get("location")):
                reason = "unsupported_geometry"
            elif record.get("location") != matched_thing.get("location"):
                reason = "geometry_mismatch"
            elif (
                record.get("kind") != matched_thing.get("kind")
                or record.get("label") != matched_thing.get("label")
            ):
                reason = "evidence_mismatch"

        available = not reason
        projected.append(
            {
                "record_id": record_id if type(record_id) is str else None,
                "status": "available" if available else "unavailable",
                "reason": reason,
                "origin": _json_copy(origin) if isinstance(origin, dict) else None,
                "record": _json_copy(record) if available else None,
            }
        )

    available_count = sum(item["status"] == "available" for item in projected)
    if available_count == len(projected) and projected:
        status = "available"
        reason = ""
    elif available_count:
        status = "partial"
        reason = "evidence_association_partial"
    else:
        status = "unavailable"
        reason = "evidence_association_unavailable"
    return {
        "status": status,
        "reason": reason,
        "transaction_id": transaction_id,
        "frame_id": frame_id,
        "available_count": available_count,
        "record_count": len(projected),
        "records": projected,
    }


def report_evidence(report: dict[str, Any]) -> dict[str, Any]:
    """Retained evidence attributable to this completed frame's observation."""
    cycle = report.get("cycle") or {}
    return _evidence_projection(
        transaction_id=report["frame_id"], frame_id=report["frame_id"],
        observation=cycle.get("observation"),
        evidence_input=evidence_records(cycle.get("memory")),
    )
