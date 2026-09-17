#!/usr/bin/env python3
"""Compare deterministic hypothesis selectors with the recorded Jev choices.

This is a disposable Issue #219 evaluator.  It deliberately consumes recorded
findings rather than calling Jev or rerunning CV, so every selector is judged
on the same clusters and candidate geometry.  The labels are a small manual
pilot set; they are not production annotations.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_IOU_THRESHOLD = 0.50


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def _bbox(item: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not isinstance(item, dict):
        return None
    raw = item.get("bbox_xyxy_norm")
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    values = tuple(float(value) for value in raw)
    x1, y1, x2, y2 = values
    if not (0.0 <= x1 <= x2 <= 1.0 and 0.0 <= y1 <= y2 <= 1.0):
        raise ValueError(f"invalid normalized bbox: {values}")
    if x2 <= x1 or y2 <= y1:
        return None
    return values


def _area(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = _area((x1, y1, x2, y2))
    union = _area(left) + _area(right) - intersection
    return intersection / union if union else 0.0


def _prediction(
    item: dict[str, Any],
    *,
    cluster_id: str,
    kind: str | None = None,
    source_names: list[str] | None = None,
    confidence: float | None = None,
    probability: float | None = None,
    usable: float | None = None,
    hypothesis_id: str | None = None,
) -> dict[str, Any] | None:
    box = _bbox(item)
    if box is None:
        return None
    names = source_names if source_names is not None else item.get("source_names", [])
    if not names and item.get("source"):
        names = [item["source"]]
    if not isinstance(names, list):
        names = list(names) if isinstance(names, tuple) else []
    return {
        "bbox_xyxy_norm": list(box),
        "cluster_id": cluster_id,
        "hypothesis_id": hypothesis_id or item.get("hypothesis_id") or item.get("candidate_id"),
        "kind": kind or item.get("kind", "unknown"),
        "source_names": sorted({str(name) for name in names}),
        "confidence": confidence if confidence is not None else item.get("confidence"),
        "probability": probability,
        "usable": usable,
    }


def _hypotheses(findings: dict[str, Any], cluster_id: str) -> list[dict[str, Any]]:
    value = findings.get("hypotheses", {}).get(cluster_id, [])
    return value if isinstance(value, list) else []


def _select_raw_confidence(findings: dict[str, Any]) -> list[dict[str, Any]]:
    """Select one highest-confidence raw CV proposal per stored cluster."""
    selected: list[dict[str, Any]] = []
    for cluster_id, boxes in (findings.get("clusters") or {}).items():
        if not isinstance(boxes, list):
            continue
        candidates = [item for item in boxes if isinstance(item, dict) and _bbox(item) is not None]
        if not candidates:
            continue
        item = max(candidates, key=lambda value: (float(value.get("confidence", 0.0)), _area(_bbox(value))))
        prediction = _prediction(item, cluster_id=cluster_id, kind="raw")
        if prediction is not None:
            selected.append(prediction)
    return selected


def _select_kind(findings: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for cluster_id in (findings.get("clusters") or {}):
        candidates = [item for item in _hypotheses(findings, cluster_id) if item.get("kind") == kind]
        if not candidates:
            continue
        item = max(candidates, key=lambda value: float(value.get("confidence", 0.0)))
        prediction = _prediction(item, cluster_id=cluster_id)
        if prediction is not None:
            selected.append(prediction)
    return selected


def _select_handwritten(findings: dict[str, Any]) -> list[dict[str, Any]]:
    """Use the plugin's deterministic heuristic output verbatim."""
    selected: list[dict[str, Any]] = []
    for item in findings.get("heuristic_boxes", []):
        if not isinstance(item, dict) or item.get("kind") == "none":
            continue
        cluster_id = str(item.get("hypothesis_id", "")).split("_", 2)[0:2]
        cluster = "_".join(cluster_id) if len(cluster_id) == 2 else "unknown"
        prediction = _prediction(item, cluster_id=cluster)
        if prediction is not None:
            selected.append(prediction)
    return selected


def _select_jev(findings: dict[str, Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in (findings.get("jev") or {}).get("selected", []):
        if not isinstance(item, dict) or not item.get("hypothesis"):
            continue
        hypothesis = item["hypothesis"]
        prediction = _prediction(
            hypothesis,
            cluster_id=str(item.get("cluster_id", "unknown")),
            confidence=float(item.get("confidence", 0.0)),
            probability=float(item["probability"]) if item.get("probability") is not None else None,
            usable=float(item["usable"]) if item.get("usable") is not None else None,
        )
        if prediction is not None:
            selected.append(prediction)
    return selected


def _selectors(findings: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "raw_confidence": _select_raw_confidence(findings),
        "covering_union": _select_kind(findings, "covering_union"),
        "robust_extent": _select_kind(findings, "robust_extent"),
        "handwritten_selector": _select_handwritten(findings),
        "jev": _select_jev(findings),
    }


def _match(
    predictions: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    threshold: float,
) -> tuple[list[dict[str, Any]], set[int], set[int]]:
    pairs: list[tuple[float, int, int]] = []
    for prediction_index, prediction in enumerate(predictions):
        prediction_box = tuple(prediction["bbox_xyxy_norm"])
        for label_index, label in enumerate(labels):
            score = _iou(prediction_box, tuple(label["bbox_xyxy_norm"]))
            if score >= threshold:
                pairs.append((score, prediction_index, label_index))
    matches: list[dict[str, Any]] = []
    matched_predictions: set[int] = set()
    matched_labels: set[int] = set()
    for score, prediction_index, label_index in sorted(pairs, reverse=True):
        if prediction_index in matched_predictions or label_index in matched_labels:
            continue
        matched_predictions.add(prediction_index)
        matched_labels.add(label_index)
        matches.append(
            {
                "prediction_index": prediction_index,
                "label_index": label_index,
                "iou": round(score, 6),
            }
        )
    return matches, matched_predictions, matched_labels


def _labels_by_frame(labels: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for frame in labels.get("frames", []):
        if not isinstance(frame, dict):
            continue
        objects = []
        for item in frame.get("objects", []):
            box = _bbox(item)
            if box is None:
                continue
            objects.append(
                {
                    "object_id": item.get("object_id", f"object_{len(objects):02d}"),
                    "label": item.get("label", "visible object"),
                    "bbox_xyxy_norm": list(box),
                }
            )
        result[str(frame["frame"]).upper()] = objects
    return result


def _frame_name(path: Path) -> str:
    return f"{path.stem}.JPG".upper()


def _evaluate_method(
    method: str,
    frame_results: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    prediction_count = 0
    matched_count = 0
    label_count = 0
    matched_ious: list[float] = []
    best_ious: list[float] = []
    unmatched_predictions = 0
    single_source_selected = 0
    single_source_unmatched = 0
    source_diversity_all: list[int] = []
    source_diversity_matched: list[int] = []
    kind_counts: Counter[str] = Counter()
    per_frame: list[dict[str, Any]] = []

    for frame in frame_results:
        predictions = frame["predictions"]
        labels = frame["labels"]
        matches, matched_prediction_indices, matched_label_indices = _match(
            predictions, labels, threshold
        )
        frame_best_ious = [
            max(
                (
                    _iou(tuple(prediction["bbox_xyxy_norm"]), tuple(label["bbox_xyxy_norm"]))
                    for prediction in predictions
                ),
                default=0.0,
            )
            for label in labels
        ]
        prediction_count += len(predictions)
        matched_count += len(matches)
        label_count += len(labels)
        matched_ious.extend(item["iou"] for item in matches)
        best_ious.extend(frame_best_ious)
        unmatched_predictions += len(predictions) - len(matched_prediction_indices)
        for index, prediction in enumerate(predictions):
            diversity = len(prediction.get("source_names", []))
            source_diversity_all.append(diversity)
            kind_counts[str(prediction.get("kind", "unknown"))] += 1
            if diversity == 1:
                single_source_selected += 1
            if index not in matched_prediction_indices and diversity == 1:
                single_source_unmatched += 1
        for index in matched_prediction_indices:
            source_diversity_matched.append(len(predictions[index].get("source_names", [])))
        per_frame.append(
            {
                "frame": frame["frame"],
                "ground_truth_count": len(labels),
                "prediction_count": len(predictions),
                "matched_count": len(matches),
                "object_coverage": round(len(matched_label_indices) / len(labels), 6) if labels else None,
                "unmatched_prediction_count": len(predictions) - len(matched_prediction_indices),
                "single_source_selected_count": sum(
                    len(item.get("source_names", [])) == 1 for item in predictions
                ),
                "single_source_unmatched_count": sum(
                    index not in matched_prediction_indices
                    and len(item.get("source_names", [])) == 1
                    for index, item in enumerate(predictions)
                ),
                "mean_matched_iou": round(
                    sum(item["iou"] for item in matches) / len(matches), 6
                )
                if matches
                else None,
                "mean_best_iou": round(sum(frame_best_ious) / len(frame_best_ious), 6)
                if frame_best_ious
                else None,
                "best_iou_by_label": [round(value, 6) for value in frame_best_ious],
                "hypothesis_kinds": dict(Counter(str(item.get("kind", "unknown")) for item in predictions)),
                "matches": matches,
                "predictions": predictions,
            }
        )

    precision = matched_count / prediction_count if prediction_count else 0.0
    recall = matched_count / label_count if label_count else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "method": method,
        "frames": len(frame_results),
        "ground_truth_objects": label_count,
        "predictions": prediction_count,
        "matched_objects": matched_count,
        "object_coverage": round(recall, 6),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "mean_matched_iou": round(sum(matched_ious) / len(matched_ious), 6)
        if matched_ious
        else None,
        "mean_best_iou": round(sum(best_ious) / len(best_ious), 6) if best_ious else None,
        "unmatched_prediction_count": unmatched_predictions,
        "single_source_selected_count": single_source_selected,
        "single_source_unmatched_count": single_source_unmatched,
        "mean_source_diversity_all": round(sum(source_diversity_all) / len(source_diversity_all), 6)
        if source_diversity_all
        else None,
        "mean_source_diversity_matched": round(
            sum(source_diversity_matched) / len(source_diversity_matched), 6
        )
        if source_diversity_matched
        else None,
        "hypothesis_kinds": dict(kind_counts),
        "per_frame": per_frame,
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Issue #219 marginal-value pilot",
        "",
        "This is a manual six-object pilot on the three recorded real photos. "
        "It is not a production benchmark; labels are approximate visible-object envelopes.",
        "",
        f"IoU match threshold: `{report['iou_threshold']:.2f}`.",
        "",
        "| selector | predictions | matched | coverage/recall | precision | F1 | mean matched IoU | mean best IoU | unmatched | single-source unmatched |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["methods"]:
        lines.append(
            "| {method} | {predictions} | {matched_objects} | {recall:.3f} | {precision:.3f} | {f1:.3f} | {iou} | {best_iou} | {unmatched_prediction_count} | {single_source_unmatched_count} |".format(
                method=item["method"],
                predictions=item["predictions"],
                matched_objects=item["matched_objects"],
                recall=item["recall"],
                precision=item["precision"],
                f1=item["f1"],
                iou=f"{item['mean_matched_iou']:.3f}" if item["mean_matched_iou"] is not None else "—",
                best_iou=f"{item['mean_best_iou']:.3f}" if item["mean_best_iou"] is not None else "—",
                unmatched_prediction_count=item["unmatched_prediction_count"],
                single_source_unmatched_count=item["single_source_unmatched_count"],
            )
        )
    lines.extend(
        [
            "",
            "The `raw_confidence` selector chooses the highest-confidence raw CV proposal per cluster. "
            "`covering_union` and `robust_extent` choose only those exact recorded aggregate hypotheses. "
            "`handwritten_selector` is the plugin's existing deterministic heuristic output. "
            "`jev` uses the recorded Jev choice and omits `none` selections.",
            "",
            "Unmatched predictions are a measurable nuisance-boundary proxy, not a semantic nuisance label. "
            "Single-source counts are reported because Jev was explicitly asked to favor independent source support, "
            "yet the recorded run contains some accepted single-source choices.",
        ]
    )
    return "\n".join(lines) + "\n"


def evaluate(findings_dir: Path, labels_path: Path, threshold: float) -> dict[str, Any]:
    labels = _read_json(labels_path)
    by_frame = _labels_by_frame(labels)
    files = sorted(findings_dir.glob("*.json"))
    if not files:
        raise ValueError(f"no findings JSON files found in {findings_dir}")
    frame_results: list[dict[str, Any]] = []
    for path in files:
        findings = _read_json(path)
        frame = _frame_name(path)
        if frame not in by_frame:
            raise ValueError(f"no labels for {frame}")
        selectors = _selectors(findings)
        frame_results.append(
            {
                "frame": frame,
                "labels": by_frame[frame],
                "selectors": selectors,
            }
        )
    methods = []
    for method in ("raw_confidence", "covering_union", "robust_extent", "handwritten_selector", "jev"):
        methods.append(
            _evaluate_method(
                method,
                [
                    {
                        "frame": frame["frame"],
                        "labels": frame["labels"],
                        "predictions": frame["selectors"][method],
                    }
                    for frame in frame_results
                ],
                threshold,
            )
        )
    return {
        "schema": "issue_219_marginal_value_report_v1",
        "annotation_schema": labels.get("schema"),
        "findings_directory": findings_dir.name,
        "frame_count": len(frame_results),
        "ground_truth_object_count": sum(len(frame["labels"]) for frame in frame_results),
        "iou_threshold": threshold,
        "methods": methods,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--findings-dir", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--iou-threshold", type=float, default=DEFAULT_IOU_THRESHOLD)
    args = parser.parse_args()
    if not 0.0 < args.iou_threshold <= 1.0:
        parser.error("--iou-threshold must be in (0, 1]")
    report = evaluate(args.findings_dir, args.labels, args.iou_threshold)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "methods": report["methods"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
