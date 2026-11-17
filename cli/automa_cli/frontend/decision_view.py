"""Project retained evidence onto the completed report's exact source image."""
from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.memory.interface import evidence_records
from autonomy.decision_cycle.memory.evidence import RetainedEvidence
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.perception.evidence.values import PerceivedThing, ViewLocation
from autonomy.runtime.report import HostReport
from autonomy.fields import require_nested


def _supported_image_bbox(location: ViewLocation | None) -> bool:
    """Whether the view supports this validated location's geometry."""

    if location is None or location.frame != "image" or location.bbox_xyxy_norm is None:
        return False
    x1, y1, x2, y2 = location.bbox_xyxy_norm
    return x2 > x1 and y2 > y1


def _evidence_projection(
    *,
    frame_id: str,
    observation: Observation | None,
    evidence_input: tuple[RetainedEvidence, ...],
) -> dict[str, Any]:
    """Project only evidence that is exactly attributable to the current image.

    The accepted cycle stays on the report. This projection is the
    machine-visible rendering gate: an unavailable record carries its raw
    origin but never carries renderable geometry.
    """

    observation_id = None if observation is None else observation.observation_id
    things = () if observation is None else observation.things
    observation_plugin_id = None if observation is None else observation.perception_plugin_id

    projected: list[dict[str, Any]] = []
    for record in evidence_input:
        record_id = record.record_id
        origin = record.origin
        reason = ""
        matched_thing: PerceivedThing | None = None
        if origin.frame_id != frame_id:
            reason = "source_image_unavailable"
        elif observation_id is None:
            reason = "observation_unavailable"
        elif origin.observation_id != observation_id:
            reason = "observation_mismatch"
        else:
            matches = [
                thing
                for thing in things
                if thing.thing_id == origin.observed_id
            ]
            if not matches:
                reason = "evidence_missing"
            elif len(matches) != 1:
                reason = "evidence_ambiguous"
            else:
                matched_thing = matches[0]

        if not reason and matched_thing is not None:
            source_plugin_id = matched_thing.source_plugin_id
            if source_plugin_id is None:
                source_plugin_id = observation_plugin_id
            if (
                origin.coordinate_frame != "image"
                or origin.source_plugin_id != source_plugin_id
            ):
                reason = "provenance_mismatch"
            elif not _supported_image_bbox(record.location):
                reason = "unsupported_geometry"
            elif record.location != matched_thing.location:
                reason = "geometry_mismatch"
            elif (
                record.kind != matched_thing.kind
                or record.label != matched_thing.label
            ):
                reason = "evidence_mismatch"

        available = not reason
        projected.append(
            {
                "record_id": record_id,
                "status": "available" if available else "unavailable",
                "reason": reason,
                "origin": origin.to_dict(),
                "record": record.to_dict() if available else None,
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
        "frame_id": frame_id,
        "available_count": available_count,
        "record_count": len(projected),
        "records": projected,
    }


def report_evidence(report: HostReport) -> dict[str, object]:
    """Associate the typed observation with evidence in the plugin's JSON summary."""
    cycle = report.cycle
    observation = cycle.observation
    records = evidence_records(cycle.memory) or []
    return _evidence_projection(
        frame_id=report.frame_id,
        observation=observation,
        evidence_input=tuple(require_nested(RetainedEvidence.from_dict, record, field_name=f"records.{index}")
                             for index, record in enumerate(records)),
    )
