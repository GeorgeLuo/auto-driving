from __future__ import annotations

import html
import math
import statistics
from pathlib import Path
from typing import Any

from .paths import display_path


IGNORED_EVIDENCE_KINDS = {"sensor_frame", "prepared_sensor_frame"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def evaluate_perception_frames(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Score representation health without claiming semantic correctness."""

    evidence_by_frame = [_spatial_evidence(frame) for frame in frames]
    all_evidence = [thing for evidence in evidence_by_frame for thing in evidence]
    valid_geometry = sum(1 for thing in all_evidence if _valid_thing_geometry(thing))
    frame_counts = [len(evidence) for evidence in evidence_by_frame]
    available_frames = sum(
        1 for frame in frames if str(frame.get("status")) not in {"error", "partial", "unavailable"}
    )
    nonempty_frames = sum(1 for evidence in evidence_by_frame if evidence)

    pair_metrics = [
        _pair_continuity(previous, current)
        for previous, current in zip(evidence_by_frame, evidence_by_frame[1:])
    ]
    match_fraction = _mean([item["match_fraction"] for item in pair_metrics])
    mean_iou = _mean([item["mean_iou"] for item in pair_metrics if item["matches"]])
    mean_count = _mean(frame_counts)
    count_cv = (
        statistics.pstdev(frame_counts) / mean_count
        if len(frame_counts) > 1 and mean_count > 0
        else 0.0
    )

    frame_total = max(len(frames), 1)
    evidence_total = max(len(all_evidence), 1)
    availability_score = available_frames / frame_total
    geometry_score = valid_geometry / evidence_total if all_evidence else 0.0
    nonempty_score = nonempty_frames / frame_total
    continuity_score = match_fraction if pair_metrics else 0.0
    count_stability_score = 1.0 / (1.0 + count_cv) if all_evidence else 0.0
    overall = (
        0.25 * availability_score
        + 0.20 * geometry_score
        + 0.20 * nonempty_score
        + 0.20 * continuity_score
        + 0.15 * count_stability_score
    )

    return {
        "schema": "perception_representation_health_v0",
        "score": round(overall, 5),
        "interpretation": (
            "Contract, availability, count stability, and adjacent-frame image-space continuity; "
            "this is not semantic accuracy or obstacle-detection recall."
        ),
        "evidence_kinds": sorted({str(thing.get("kind") or "unknown") for thing in all_evidence}),
        "availability": {
            "usable_frames": available_frames,
            "total_frames": len(frames),
            "score": round(availability_score, 5),
        },
        "geometry": {
            "valid_records": valid_geometry,
            "total_records": len(all_evidence),
            "score": round(geometry_score, 5),
        },
        "nonempty": {
            "frames": nonempty_frames,
            "score": round(nonempty_score, 5),
        },
        "count_stability": {
            "mean": round(mean_count, 5),
            "coefficient_of_variation": round(count_cv, 5),
            "score": round(count_stability_score, 5),
        },
        "continuity": {
            "frame_pairs": len(pair_metrics),
            "mean_match_fraction": round(match_fraction, 5),
            "mean_matched_iou": round(mean_iou, 5),
            "score": round(continuity_score, 5),
            "pairs": pair_metrics,
        },
    }


def write_review_html(run_dir: Path, report: dict[str, Any]) -> Path:
    path = run_dir / "review.html"
    summary = report["summary"]
    quality = summary.get("representation_health") or {}
    frame_sections = "\n".join(_frame_section(frame) for frame in report.get("frames", []))
    path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Perception Review: {html.escape(str(report.get('run_id') or 'run'))}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f5f7f4; color: #17211f; font: 15px/1.45 system-ui, sans-serif; }}
    main {{ width: min(1240px, calc(100% - 28px)); margin: 0 auto; padding: 28px 0 50px; }}
    h1, h2, p {{ margin: 0; }}
    h1 {{ font-size: 30px; }}
    h2 {{ font-size: 18px; margin-bottom: 10px; }}
    .summary {{ margin: 18px 0; padding: 16px; border: 1px solid #c9d5ce; background: white; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px; margin-top: 12px; }}
    .metric {{ padding: 10px; background: #edf1ee; }}
    .metric strong {{ display: block; font-size: 20px; }}
    .frames {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 14px; }}
    article {{ border: 1px solid #c9d5ce; background: white; padding: 12px; }}
    .images {{ display: grid; gap: 8px; }}
    figure {{ margin: 0; }}
    img {{ display: block; width: 100%; height: auto; background: #17211f; }}
    figcaption {{ margin-top: 4px; color: #596762; font-size: 12px; overflow-wrap: anywhere; }}
    .details {{ color: #596762; font-size: 13px; margin-bottom: 8px; }}
  </style>
</head>
<body>
<main>
  <h1>Perception Review</h1>
  <p>{html.escape(str(report.get('mapper', {}).get('algorithm') or 'unknown'))}</p>
  <section class="summary">
    <h2>Run Summary</h2>
    <p>{html.escape(str(quality.get('interpretation') or 'No representation-health score available.'))}</p>
    <div class="metrics">
      {_metric('Health', _number(quality.get('score'), 3))}
      {_metric('Frames', str(summary.get('frames', 0)))}
      {_metric('Failed', str(summary.get('failed_frames', 0)))}
      {_metric('Steady median', f"{summary.get('latency_ms', {}).get('steady_median', 0):.1f} ms")}
      {_metric('Peak RSS', f"{summary.get('memory_mb', {}).get('peak_rss', 0):.1f} MiB")}
    </div>
  </section>
  <section class="frames">{frame_sections}</section>
</main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return path


def _spatial_evidence(frame: dict[str, Any]) -> list[dict[str, Any]]:
    perception = frame.get("perception")
    things = perception.get("things") if isinstance(perception, dict) else None
    if not isinstance(things, (list, tuple)):
        return []
    spatial = [
        thing
        for thing in things
        if isinstance(thing, dict)
        and str(thing.get("kind") or "unknown") not in IGNORED_EVIDENCE_KINDS
        and isinstance((thing.get("location") or {}).get("bbox_xyxy_norm"), (list, tuple))
    ]
    region_proposals = [thing for thing in spatial if thing.get("kind") == "region_proposal"]
    return region_proposals or spatial


def _valid_thing_geometry(thing: dict[str, Any]) -> bool:
    location = thing.get("location")
    bbox = location.get("bbox_xyxy_norm") if isinstance(location, dict) else None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return False
    confidence_value = thing.get("confidence")
    if confidence_value is None:
        return False
    try:
        x1, y1, x2, y2 = (float(value) for value in bbox)
        confidence = float(confidence_value)
    except (TypeError, ValueError):
        return False
    return (
        all(math.isfinite(value) for value in (x1, y1, x2, y2, confidence))
        and 0.0 <= x1 <= x2 <= 1.0
        and 0.0 <= y1 <= y2 <= 1.0
        and 0.0 <= confidence <= 1.0
    )


def _pair_continuity(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, Any]:
    previous = [thing for thing in previous if _valid_thing_geometry(thing)]
    current = [thing for thing in current if _valid_thing_geometry(thing)]
    candidates: list[tuple[float, int, int]] = []
    for previous_index, left in enumerate(previous):
        for current_index, right in enumerate(current):
            if left.get("kind") != right.get("kind"):
                continue
            overlap = _bbox_iou(_bbox(left), _bbox(right))
            if overlap >= 0.05:
                candidates.append((overlap, previous_index, current_index))
    candidates.sort(reverse=True)
    used_previous: set[int] = set()
    used_current: set[int] = set()
    overlaps: list[float] = []
    for overlap, previous_index, current_index in candidates:
        if previous_index in used_previous or current_index in used_current:
            continue
        used_previous.add(previous_index)
        used_current.add(current_index)
        overlaps.append(overlap)
    denominator = max(len(previous), len(current), 1)
    return {
        "previous_count": len(previous),
        "current_count": len(current),
        "matches": len(overlaps),
        "match_fraction": round(len(overlaps) / denominator, 5),
        "mean_iou": round(_mean(overlaps), 5),
    }


def _bbox(thing: dict[str, Any]) -> tuple[float, float, float, float]:
    values = [float(value) for value in thing["location"]["bbox_xyxy_norm"]]
    return values[0], values[1], values[2], values[3]


def _bbox_iou(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _mean(values: list[float] | list[int]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _metric(label: str, value: str) -> str:
    return f'<div class="metric"><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>'


def _number(value: Any, digits: int) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _frame_section(frame: dict[str, Any]) -> str:
    images: list[tuple[str, Path]] = []
    source = frame.get("image_path")
    if isinstance(source, str) and Path(source).is_file():
        images.append(("source", Path(source)))
    perception = frame.get("perception")
    artifacts = perception.get("artifacts") if isinstance(perception, dict) else None
    if isinstance(artifacts, dict):
        for name, artifact in sorted(artifacts.items()):
            path = Path(str(artifact))
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                images.append((name, path))
    figures = "".join(
        f'<figure><img loading="lazy" src="{html.escape(path.resolve().as_uri())}" '
        f'alt="{html.escape(label)}"><figcaption>{html.escape(label)}: '
        f'{html.escape(display_path(path))}</figcaption></figure>'
        for label, path in images
    )
    return (
        f'<article><h2>{html.escape(str(frame.get("frame_id") or "frame"))}</h2>'
        f'<p class="details">status={html.escape(str(frame.get("status")))} '
        f'things={int(frame.get("thing_count") or 0)} '
        f'latency={float(frame.get("duration_ms") or 0):.1f} ms</p>'
        f'<div class="images">{figures}</div></article>'
    )
