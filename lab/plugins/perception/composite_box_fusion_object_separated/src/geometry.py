"""Compatibility imports for the object separated composite geometry path."""

from typing import Any

from lab.plugins.perception.composite_box_fusion.src.geometry import *  # noqa: F401,F403
from lab.plugins.perception.composite_box_fusion.src.geometry import (
    _context_rejection_reasons,
    _context_score,
    _json_safe,
    _split_modes,
    _split_modes_recursive,
    cluster_proposals as _cluster_proposals,
)


def cluster_proposals(
    proposals: list[GeometryProposal],
    *,
    iou_threshold: float,
    center_distance_threshold: float,
    max_clusters: int,
    split_spatial_modes: bool,
    split_center_gap: float,
    object_separated: bool = False,
) -> tuple[
    dict[str, list[GeometryProposal]],
    dict[str, list[GeometryProposal]],
    dict[str, list[str]],
    list[dict[str, Any]],
]:
    """Preserve the variant module's default and diagnostics."""
    return _cluster_proposals(
        proposals,
        iou_threshold=iou_threshold,
        center_distance_threshold=center_distance_threshold,
        max_clusters=max_clusters,
        split_spatial_modes=split_spatial_modes,
        split_center_gap=split_center_gap,
        object_separated=object_separated,
    )
