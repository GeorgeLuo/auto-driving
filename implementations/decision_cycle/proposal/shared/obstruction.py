"""Resolve foreground and lateral cues from image-relative obstruction evidence."""

from __future__ import annotations

from autonomy.decision_cycle.memory.evidence import RetainedEvidence

OBSTRUCTION_KINDS = ("floor_boundary", "obstacle", "obstruction_evidence")


def _bbox_mid_x(record: RetainedEvidence) -> float | None:
    location = record.location
    if location is None:
        return None
    bbox = location.bbox_xyxy_norm
    if bbox is None:
        return None
    return (bbox[0] + bbox[2]) / 2.0


# Zones that mean "no explicit left/right" so bbox mid_x may supply the cue.
# "unknown" is ViewLocation.from_dict's canonical missing-zone value.
_BBOX_FALLBACK_ZONES = frozenset({None, "", "center", "unknown"})
_IMAGE_LATERAL_ZONES = {
    "left": "left",
    "right": "right",
    "near_left": "left",
    "mid_left": "left",
    "far_left": "left",
    "near_right": "right",
    "mid_right": "right",
    "far_right": "right",
}


def _explicit_lateral_side(record: RetainedEvidence) -> str | None:
    """Resolve the horizontal side from an image-relative zone, if present."""

    location = record.location
    if location is None:
        return None
    return _IMAGE_LATERAL_ZONES.get(location.zone)


def has_lateral_cue(record: RetainedEvidence) -> bool:
    """True when a lateral zone or a finite bbox supplies the cue."""

    location = record.location
    if location is None:
        return False
    if _explicit_lateral_side(record) is not None:
        return True
    # Bbox mid_x is a cue only for missing / unknown / exact "center".
    zone = location.zone
    if zone not in _BBOX_FALLBACK_ZONES:
        return False
    return _bbox_mid_x(record) is not None


def lateral_side(record: RetainedEvidence) -> str | None:
    """Resolved side for command emission; center-band cue yields None (try next)."""

    location = record.location
    if location is None:
        return None
    explicit_side = _explicit_lateral_side(record)
    if explicit_side is not None:
        return explicit_side
    zone = location.zone
    if zone not in _BBOX_FALLBACK_ZONES:
        return None
    mid_x = _bbox_mid_x(record)
    if mid_x is None:
        return None
    if mid_x < 0.45:
        return "left"
    if mid_x > 0.55:
        return "right"
    return None


def horizontal_span(record: RetainedEvidence) -> tuple[float, float] | None:
    """Resolve an image span for clearance scoring, including center cues."""
    location = record.location
    if location is None or location.frame != "image":
        return None
    bbox = location.bbox_xyxy_norm
    if bbox is not None:
        return bbox[0], bbox[2]
    side = _explicit_lateral_side(record)
    if side == "left":
        return 0.0, 0.40
    if side == "right":
        return 0.60, 1.0
    if location.zone in {"center", "near_center", "mid_center", "far_center"}:
        return 0.35, 0.65
    return None


def bbox_bottom(record: RetainedEvidence) -> float:
    """Return the lower image edge for a deterministic foreground tie-break."""

    location = record.location
    if location is None or location.bbox_xyxy_norm is None:
        return -1.0
    return location.bbox_xyxy_norm[3]
