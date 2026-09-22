from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np


Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class GeometryProposal:
    proposal_id: str
    source: str
    family: str
    bbox: Rect
    confidence: float
    properties: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "source": self.source,
            "family": self.family,
            "bbox_xyxy_norm": list(self.bbox),
            "confidence": self.confidence,
            "properties": _json_safe(self.properties),
        }


@dataclass(frozen=True)
class GeometryHypothesis:
    hypothesis_id: str
    cluster_id: str
    kind: str
    bbox: Rect
    source_ids: tuple[str, ...]
    source_names: tuple[str, ...]
    confidence: float
    valid: bool = True
    family_names: tuple[str, ...] = ()
    family_support: tuple[tuple[str, float], ...] = ()
    support_score: float = 0.0
    none_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "cluster_id": self.cluster_id,
            "kind": self.kind,
            "bbox_xyxy_norm": list(self.bbox),
            "source_ids": list(self.source_ids),
            "source_names": list(self.source_names),
            "confidence": self.confidence,
            "valid": self.valid,
            "family_names": list(self.family_names),
            "family_support": {
                family: score for family, score in self.family_support
            },
            "support_score": self.support_score,
            "none_score": self.none_score,
        }


FIXED_JEV_PROMPT = (
    "Choose one bounded hypothesis per spatial cluster for one distinct visible "
    "object or obstacle. Use only the structured CV evidence in this request; "
    "the image is unavailable. Treat proposals from one detector family as "
    "correlated. Do not bridge separated object-sized modes with a union. "
    "Use covering_union for complementary fragments of one object, "
    "weighted_median for competing complete boundaries, robust_extent for "
    "coherent proposals with spatial outliers, raw for one compact complete "
    "proposal, and none for unsupported or background-dominated clusters."
)


def proposal_from_record(record: dict[str, Any]) -> GeometryProposal | None:
    bbox = record.get("geometry", {}).get("bbox_xyxy_norm")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    normalized = normalize_bbox(tuple(float(value) for value in bbox))
    if normalized is None or area(normalized) <= 0.0:
        return None
    return GeometryProposal(
        proposal_id=str(record.get("thing_id") or record.get("proposal_id") or "proposal"),
        source=str(record.get("source") or "unknown"),
        family=str(record.get("strategy_family") or "unknown"),
        bbox=normalized,
        confidence=clamp(float(record.get("confidence") or 0.0)),
        properties=dict(record.get("properties") or {}),
    )


def source_balanced_budget(
    proposals: list[GeometryProposal],
    *,
    limit: int,
) -> tuple[list[GeometryProposal], dict[str, list[str]]]:
    """Keep a bounded, source-balanced proposal set and explain discards."""
    limit = max(1, int(limit))
    ordered = sorted(
        proposals,
        key=lambda item: (item.confidence, area(item.bbox), item.proposal_id),
        reverse=True,
    )
    if len(ordered) <= limit:
        return ordered, {}
    sources = sorted({item.source for item in ordered})
    quota = limit // max(len(sources), 1)
    kept: list[GeometryProposal] = []
    kept_ids: set[str] = set()
    if quota:
        for source in sources:
            source_items = [item for item in ordered if item.source == source]
            for item in source_items[:quota]:
                kept.append(item)
                kept_ids.add(item.proposal_id)
    for item in ordered:
        if len(kept) >= limit:
            break
        if item.proposal_id not in kept_ids:
            kept.append(item)
            kept_ids.add(item.proposal_id)
    kept = kept[:limit]
    kept_ids = {item.proposal_id for item in kept}
    rejected = {
        item.proposal_id: ["raw_proposal_budget_exceeded"]
        for item in ordered
        if item.proposal_id not in kept_ids
    }
    return kept, rejected


def cluster_proposals(
    proposals: list[GeometryProposal],
    *,
    iou_threshold: float,
    center_distance_threshold: float,
    max_clusters: int,
    split_spatial_modes: bool,
    split_center_gap: float,
) -> tuple[
    dict[str, list[GeometryProposal]],
    dict[str, list[GeometryProposal]],
    dict[str, list[str]],
    list[dict[str, Any]],
]:
    """Build base clusters and a bounded split-mode alternative.

    Floor-like and broad image-context proposals remain in the evidence record
    but are excluded from connectivity, so they cannot bridge object modes.
    """
    rejected: dict[str, list[str]] = {}
    eligible: list[GeometryProposal] = []
    for proposal in proposals:
        reasons = _context_rejection_reasons(proposal)
        if reasons:
            rejected[proposal.proposal_id] = reasons
        else:
            eligible.append(proposal)
    if not eligible:
        return {}, {}, rejected, []

    parent = list(range(len(eligible)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(eligible)):
        for right in range(left + 1, len(eligible)):
            if (
                bbox_iou(eligible[left].bbox, eligible[right].bbox) >= iou_threshold
                or center_distance(eligible[left].bbox, eligible[right].bbox)
                <= center_distance_threshold
            ):
                union(left, right)

    grouped: dict[int, list[GeometryProposal]] = {}
    for index, proposal in enumerate(eligible):
        grouped.setdefault(find(index), []).append(proposal)
    groups = sorted(
        grouped.values(),
        key=lambda group: (max(item.confidence for item in group), len(group)),
        reverse=True,
    )
    retained_groups = groups[: max(1, int(max_clusters))]
    for group in groups[max(1, int(max_clusters)) :]:
        for proposal in group:
            rejected.setdefault(proposal.proposal_id, []).append(
                "cluster_budget_exceeded"
            )

    base: dict[str, list[GeometryProposal]] = {
        f"cluster_{index:02d}": sorted(
            group,
            key=lambda item: (item.confidence, area(item.bbox), item.proposal_id),
            reverse=True,
        )
        for index, group in enumerate(retained_groups)
    }
    split: dict[str, list[GeometryProposal]] = {}
    split_events: list[dict[str, Any]] = []
    for cluster_id, group in base.items():
        modes = _split_modes(group, split_center_gap) if split_spatial_modes else [group]
        if len(modes) == 1:
            split[cluster_id] = group
            continue
        split_events.append(
            {
                "cluster_id": cluster_id,
                "mode_count": len(modes),
                "split_center_gap": split_center_gap,
                "mode_proposal_ids": [
                    [proposal.proposal_id for proposal in mode] for mode in modes
                ],
            }
        )
        for mode_index, mode in enumerate(modes):
            split[f"{cluster_id}_mode_{mode_index:02d}"] = mode
    return base, split, rejected, split_events


def make_hypotheses(
    cluster_id: str,
    proposals: list[GeometryProposal],
    *,
    max_hypotheses: int,
    robust_low_quantile: float,
    robust_high_quantile: float,
    robust_padding: float,
) -> list[GeometryHypothesis]:
    ordered = sorted(
        proposals,
        key=lambda item: (item.confidence, area(item.bbox), item.proposal_id),
        reverse=True,
    )
    source_ids = tuple(item.proposal_id for item in ordered)
    source_names = tuple(sorted({item.source for item in ordered}))
    support, family_names, family_support = family_support_confidence(ordered)
    none_score = unsupported_cluster_score(
        ordered,
        support_score=support,
        family_names=family_names,
    )
    hypotheses: list[GeometryHypothesis] = []

    raw_limit = max(1, int(max_hypotheses) - 5)
    for proposal in ordered[:raw_limit]:
        hypotheses.append(
            GeometryHypothesis(
                hypothesis_id=f"{cluster_id}_{proposal.proposal_id}_raw",
                cluster_id=cluster_id,
                kind="raw",
                bbox=proposal.bbox,
                source_ids=(proposal.proposal_id,),
                source_names=(proposal.source,),
                confidence=proposal.confidence,
                family_names=(proposal.family,),
                family_support=((proposal.family, proposal.confidence),),
                support_score=proposal.confidence,
            )
        )

    covering = union_bbox(ordered)
    robust = robust_extent_bbox(
        ordered,
        low_quantile=robust_low_quantile,
        high_quantile=robust_high_quantile,
        padding=robust_padding,
    )
    median = weighted_median_bbox(ordered)
    overlap = intersection_bbox(ordered)
    hypotheses.extend(
        [
            GeometryHypothesis(
                hypothesis_id=f"{cluster_id}_covering_union",
                cluster_id=cluster_id,
                kind="covering_union",
                bbox=covering,
                source_ids=source_ids,
                source_names=source_names,
                confidence=support,
                family_names=family_names,
                family_support=family_support,
                support_score=support,
            ),
            GeometryHypothesis(
                hypothesis_id=f"{cluster_id}_robust_extent",
                cluster_id=cluster_id,
                kind="robust_extent",
                bbox=robust,
                source_ids=source_ids,
                source_names=source_names,
                confidence=support,
                family_names=family_names,
                family_support=family_support,
                support_score=support,
            ),
            GeometryHypothesis(
                hypothesis_id=f"{cluster_id}_weighted_median",
                cluster_id=cluster_id,
                kind="weighted_median",
                bbox=median,
                source_ids=source_ids,
                source_names=source_names,
                confidence=support,
                family_names=family_names,
                family_support=family_support,
                support_score=support,
            ),
            GeometryHypothesis(
                hypothesis_id=f"{cluster_id}_intersection",
                cluster_id=cluster_id,
                kind="intersection",
                bbox=overlap or (0.0, 0.0, 0.0, 0.0),
                source_ids=source_ids,
                source_names=source_names,
                confidence=support if overlap is not None else 0.0,
                valid=overlap is not None,
                family_names=family_names,
                family_support=family_support,
                support_score=support if overlap is not None else 0.0,
            ),
            GeometryHypothesis(
                hypothesis_id=f"{cluster_id}_none",
                cluster_id=cluster_id,
                kind="none",
                bbox=(0.0, 0.0, 0.0, 0.0),
                source_ids=(),
                source_names=(),
                confidence=none_score,
                valid=True,
                family_names=family_names,
                family_support=family_support,
                support_score=support,
                none_score=none_score,
            ),
        ]
    )
    return hypotheses[: max(6, int(max_hypotheses))]


def compare_selectors(
    hypotheses_by_cluster: dict[str, list[GeometryHypothesis]],
) -> dict[str, dict[str, GeometryHypothesis]]:
    selectors = {
        "highest_raw_confidence": {},
        "covering_union": {},
        "robust_extent": {},
        "context_aware_heuristic": {},
    }
    for cluster_id, hypotheses in hypotheses_by_cluster.items():
        valid = [item for item in hypotheses if item.valid]
        non_none = [item for item in valid if item.kind != "none"]
        raw = [item for item in non_none if item.kind == "raw"]
        by_kind = {item.kind: item for item in hypotheses}
        none = by_kind.get("none") or GeometryHypothesis(
            hypothesis_id=f"{cluster_id}_none",
            cluster_id=cluster_id,
            kind="none",
            bbox=(0.0, 0.0, 0.0, 0.0),
            source_ids=(),
            source_names=(),
            confidence=1.0,
            valid=True,
            none_score=1.0,
        )
        selectors["highest_raw_confidence"][cluster_id] = (
            max(raw, key=lambda item: item.confidence) if raw else none
        )
        covering = by_kind.get("covering_union")
        robust = by_kind.get("robust_extent")
        selectors["covering_union"][cluster_id] = (
            covering if covering is not None and covering.valid else
            (max(raw, key=lambda item: item.confidence) if raw else none)
        )
        selectors["robust_extent"][cluster_id] = (
            robust if robust is not None and robust.valid else
            (max(raw, key=lambda item: item.confidence) if raw else none)
        )
        selectors["context_aware_heuristic"][cluster_id] = (
            max(valid, key=_context_score) if valid else none
        )
    return selectors


def build_jev_request(
    frame_id: str,
    proposals: list[GeometryProposal],
    clusters: dict[str, list[GeometryProposal]],
    hypotheses_by_cluster: dict[str, list[GeometryHypothesis]],
    selectors: dict[str, dict[str, GeometryHypothesis]],
    *,
    model: str,
) -> dict[str, Any]:
    return {
        "model": model,
        "prompt": FIXED_JEV_PROMPT,
        "state": {
            "task": "compare exact deterministic geometry hypotheses for bounded object boundaries",
            "image_is_not_available_to_jev": True,
            "frame_id": frame_id,
            "raw_proposals": [proposal.to_dict() for proposal in proposals],
            "clusters": {
                cluster_id: [proposal.to_dict() for proposal in members]
                for cluster_id, members in clusters.items()
            },
            "hypotheses": {
                cluster_id: [hypothesis.to_dict() for hypothesis in hypotheses]
                for cluster_id, hypotheses in hypotheses_by_cluster.items()
            },
            "deterministic_selector_choices": {
                selector: {
                    cluster_id: hypothesis.hypothesis_id
                    for cluster_id, hypothesis in choices.items()
                }
                for selector, choices in selectors.items()
            },
        },
        "questions": {
            f"{cluster_id}_selected": {
                "type": "choice",
                "instructions": FIXED_JEV_PROMPT,
                "criteria": {
                    hypothesis.hypothesis_id: {
                        "kind": hypothesis.kind,
                        "bbox_xyxy_norm": list(hypothesis.bbox),
                        "sources": list(hypothesis.source_names),
                    }
                    for hypothesis in hypotheses
                },
            }
            for cluster_id, hypotheses in hypotheses_by_cluster.items()
        },
    }


def select_jev_hypotheses(
    response: Any,
    hypotheses_by_cluster: dict[str, list[GeometryHypothesis]],
) -> dict[str, GeometryHypothesis]:
    selected: dict[str, GeometryHypothesis] = {}
    if not isinstance(response, dict):
        return selected
    answers = response.get("answers")
    if not isinstance(answers, dict):
        return selected
    for cluster_id, hypotheses in hypotheses_by_cluster.items():
        answer = answers.get(f"{cluster_id}_selected")
        if not isinstance(answer, dict):
            continue
        choice = answer.get("choice")
        if not isinstance(choice, str):
            continue
        hypothesis = next(
            (item for item in hypotheses if item.hypothesis_id == choice and item.valid),
            None,
        )
        if hypothesis is not None:
            selected[cluster_id] = hypothesis
    return selected


def normalize_bbox(values: tuple[float, float, float, float] | list[float]) -> Rect | None:
    if len(values) != 4:
        return None
    x1, y1, x2, y2 = (float(value) for value in values)
    normalized = (
        max(0.0, min(1.0, min(x1, x2))),
        max(0.0, min(1.0, min(y1, y2))),
        max(0.0, min(1.0, max(x1, x2))),
        max(0.0, min(1.0, max(y1, y2))),
    )
    return normalized if normalized[2] > normalized[0] and normalized[3] > normalized[1] else None


def area(bbox: Rect) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def bbox_iou(left: Rect, right: Rect) -> float:
    overlap = intersection_area(left, right)
    return overlap / max(area(left) + area(right) - overlap, 1e-12)


def center_distance(left: Rect, right: Rect) -> float:
    return float(
        math.hypot(
            (left[0] + left[2]) / 2.0 - (right[0] + right[2]) / 2.0,
            (left[1] + left[3]) / 2.0 - (right[1] + right[3]) / 2.0,
        )
    )


def intersection_area(left: Rect, right: Rect) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


def union_bbox(proposals: list[GeometryProposal]) -> Rect:
    return (
        min(item.bbox[0] for item in proposals),
        min(item.bbox[1] for item in proposals),
        max(item.bbox[2] for item in proposals),
        max(item.bbox[3] for item in proposals),
    )


def robust_extent_bbox(
    proposals: list[GeometryProposal],
    *,
    low_quantile: float,
    high_quantile: float,
    padding: float,
) -> Rect:
    coordinates = np.asarray([item.bbox for item in proposals], dtype=np.float32)
    low = np.quantile(coordinates, low_quantile, axis=0)
    high = np.quantile(coordinates, high_quantile, axis=0)
    return normalize_bbox(
        (
            float(low[0]) - padding,
            float(low[1]) - padding,
            float(high[2]) + padding,
            float(high[3]) + padding,
        )
    ) or (0.0, 0.0, 0.0, 0.0)


def weighted_median_bbox(proposals: list[GeometryProposal]) -> Rect:
    representatives: dict[str, GeometryProposal] = {}
    for proposal in proposals:
        current = representatives.get(proposal.family)
        if current is None or proposal.confidence > current.confidence:
            representatives[proposal.family] = proposal
    family_proposals = list(representatives.values())
    totals = {
        proposal.family: max(0.01, proposal.confidence)
        for proposal in family_proposals
    }
    weights = [
        max(0.01, proposal.confidence) / max(sum(totals.values()), 0.01)
        for proposal in family_proposals
    ]
    coordinates: list[float] = []
    for coordinate in range(4):
        ordered = sorted(
            zip((proposal.bbox[coordinate] for proposal in family_proposals), weights),
            key=lambda item: item[0],
        )
        total = sum(weight for _, weight in ordered)
        running = 0.0
        selected = ordered[-1][0]
        for value, weight in ordered:
            running += weight
            if running >= total / 2.0:
                selected = value
                break
        coordinates.append(float(selected))
    return normalize_bbox(tuple(coordinates)) or (0.0, 0.0, 0.0, 0.0)


def intersection_bbox(proposals: list[GeometryProposal]) -> Rect | None:
    bbox = (
        max(item.bbox[0] for item in proposals),
        max(item.bbox[1] for item in proposals),
        min(item.bbox[2] for item in proposals),
        min(item.bbox[3] for item in proposals),
    )
    return bbox if area(bbox) > 0.0 else None


def support_confidence(proposals: list[GeometryProposal]) -> float:
    return family_support_confidence(proposals)[0]


def family_support_confidence(
    proposals: list[GeometryProposal],
) -> tuple[float, tuple[str, ...], tuple[tuple[str, float], ...]]:
    """Score independent detector families once each.

    Multiple edge implementations are correlated evidence.  Each family gets
    one confidence vote, represented by its strongest proposal, and family
    diversity contributes separately from within-family proposal count.
    """
    by_family: dict[str, float] = {}
    for proposal in proposals:
        by_family[proposal.family] = max(
            by_family.get(proposal.family, 0.0), proposal.confidence
        )
    family_names = tuple(sorted(by_family))
    family_support = tuple(
        (family, float(by_family[family])) for family in family_names
    )
    if not family_support:
        return 0.0, (), ()
    independent_mean = sum(score for _, score in family_support) / len(family_support)
    diversity = min(1.0, len(family_support) / 4.0)
    return clamp(0.65 * independent_mean + 0.35 * diversity), family_names, family_support


def unsupported_cluster_score(
    proposals: list[GeometryProposal],
    *,
    support_score: float,
    family_names: tuple[str, ...],
) -> float:
    """Score the explicit no-output alternative for weak/background clusters."""
    if not proposals:
        return 1.0
    max_confidence = max(item.confidence for item in proposals)
    family_count = len(family_names)
    if family_count == 1 and len(proposals) <= 2:
        if max_confidence < 0.85:
            return 0.90
        if max_confidence < 0.95:
            return 0.35
    if family_count <= 1 and max_confidence < 0.55:
        return 0.85
    if family_count <= 2 and support_score < 0.50:
        return 0.75
    return 0.0


def _context_rejection_reasons(proposal: GeometryProposal) -> list[str]:
    x1, y1, x2, y2 = proposal.bbox
    width = x2 - x1
    height = y2 - y1
    box_area = area(proposal.bbox)
    if proposal.family == "floor_family":
        return ["context_only_floor_evidence"]
    if box_area > 0.60:
        return ["broad_context_area"]
    touches = sum(
        value
        for value in (x1 <= 0.01, y1 <= 0.01, x2 >= 0.99, y2 >= 0.99)
        if value
    )
    if touches >= 2 and box_area > 0.08:
        return ["broad_image_border_context"]
    if y1 >= 0.72 and height <= 0.22:
        return ["broad_floor_context"]
    if y2 <= 0.34 and height <= 0.32 and x2 <= 0.55:
        return ["broad_wall_context"]
    return []


def _split_modes(
    proposals: list[GeometryProposal],
    split_center_gap: float,
) -> list[list[GeometryProposal]]:
    if len(proposals) < 2:
        return [proposals]
    ordered_x = sorted(
        proposals,
        key=lambda item: (center(item.bbox)[0], item.proposal_id),
    )
    x_gaps = [
        center(ordered_x[index + 1].bbox)[0] - center(ordered_x[index].bbox)[0]
        for index in range(len(ordered_x) - 1)
    ]
    max_gap = max(x_gaps, default=0.0)
    if max_gap > split_center_gap:
        split_index = x_gaps.index(max_gap) + 1
        return [ordered_x[:split_index], ordered_x[split_index:]]
    ordered_y = sorted(
        proposals,
        key=lambda item: (center(item.bbox)[1], item.proposal_id),
    )
    y_gaps = [
        center(ordered_y[index + 1].bbox)[1] - center(ordered_y[index].bbox)[1]
        for index in range(len(ordered_y) - 1)
    ]
    max_y_gap = max(y_gaps, default=0.0)
    if max_y_gap > split_center_gap:
        split_index = y_gaps.index(max_y_gap) + 1
        return [ordered_y[:split_index], ordered_y[split_index:]]
    return [proposals]


def center(bbox: Rect) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _context_score(hypothesis: GeometryHypothesis) -> float:
    if hypothesis.kind == "none":
        return hypothesis.none_score
    compactness = max(0.0, 1.0 - max(0.0, area(hypothesis.bbox) - 0.35) / 0.65)
    family_support = min(1.0, len(hypothesis.family_names) / 3.0)
    kind_bonus = {
        "covering_union": 0.12 if len(hypothesis.family_names) >= 2 else 0.0,
        "robust_extent": 0.10 if len(hypothesis.family_names) >= 2 else 0.0,
        "weighted_median": 0.04,
        "raw": 0.02,
        "intersection": -0.02,
    }.get(hypothesis.kind, 0.0)
    return (
        0.46 * hypothesis.confidence
        + 0.20 * hypothesis.support_score
        + 0.14 * family_support
        + 0.18 * compactness
        + kind_bonus
    )


def clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value
