"""Compatibility imports for the object separated composite cue path."""

from lab.plugins.perception.composite_box_fusion.src.issue219_cues import *  # noqa: F401,F403
from lab.plugins.perception.composite_box_fusion.src.issue219_cues import (
    _adaptive_contours,
    _bbox_area,
    _bbox_iou,
    _canny_morph,
    _contour_candidate,
    _corner_junctions,
    _cv_box_thing,
    _dedupe_cv_things,
    _gray_variants,
    _hough_fragments,
    _normalized_xywh,
    _partial_contour,
    _photometric_windows,
    _quad_rectangularity,
    _quad_score,
    _working_rgb,
    _zone_from_bbox,
)
