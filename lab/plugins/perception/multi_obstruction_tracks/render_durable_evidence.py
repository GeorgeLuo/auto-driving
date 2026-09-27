"""Render readable P2 diagnostic panels from retained computational evidence only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def _box_point(box: list[float], x0: int, y0: int, w: int, h: int) -> tuple[int, int]:
    return (x0 + int(((box[0] + box[2]) / 2) * w), y0 + int(((box[1] + box[3]) / 2) * h))


def _panel(title: str, width: int = 1200, height: int = 700) -> np.ndarray:
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.putText(image, title, (25, 35), cv2.FONT_HERSHEY_SIMPLEX, .75, (20, 20, 20), 2, cv2.LINE_AA)
    return image


def _plot_points(image: np.ndarray, points: list[tuple[int, int]], color: tuple[int, int, int], label: str, dashed: bool = False) -> None:
    for first, second in zip(points, points[1:]):
        if dashed:
            for fraction in np.linspace(0, 1, 12)[::2]:
                a = tuple(int(first[i] + fraction * (second[i] - first[i])) for i in (0, 1))
                b = tuple(int(first[i] + (fraction + .08) * (second[i] - first[i])) for i in (0, 1))
                cv2.line(image, a, b, color, 2)
        else:
            cv2.line(image, first, second, color, 3)
    if points and label:
        cv2.putText(image, label, (points[-1][0] + 8, points[-1][1] - 8), cv2.FONT_HERSHEY_SIMPLEX, .48, color, 1, cv2.LINE_AA)


def _draw_observation(image: np.ndarray, obs: dict[str, Any], x0: int, y0: int, w: int, h: int, color: tuple[int, int, int], radius: int = 8) -> tuple[int, int]:
    point = _box_point(obs["bbox_xyxy_norm"], x0, y0, w, h)
    cv2.circle(image, point, radius, color, -1 if obs.get("source_status") == "observed" else 2)
    cv2.putText(image, f"{obs['frame_id']}@{obs['timestamp_ms']}ms", (point[0] + 8, point[1] + 15), cv2.FONT_HERSHEY_SIMPLEX, .4, color, 1, cv2.LINE_AA)
    return point


def _decision_lookup(decisions: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    tracks = {d["track_id"]: d for d in decisions}
    observations = {o["observation_id"]: o for d in decisions for o in d["observations"]}
    return tracks, observations


def render_fork(run: Path, output: Path, name: str, title: str) -> dict[str, Any]:
    decisions = json.loads((run / "decisions.json").read_text())["decisions"]
    tracks, observations = _decision_lookup(decisions)
    image = _panel(title)
    x0, y0, w, h = 80, 100, 950, 470
    all_boxes = [o["bbox_xyxy_norm"] for d in decisions for o in d["observations"]]
    min_x = min((b[0] + b[2]) / 2 for b in all_boxes) - .04; max_x = max((b[0] + b[2]) / 2 for b in all_boxes) + .04
    min_y = min((b[1] + b[3]) / 2 for b in all_boxes) - .06; max_y = max((b[1] + b[3]) / 2 for b in all_boxes) + .06
    def local_point(obs: dict[str, Any]) -> tuple[int, int]:
        cx = (obs["bbox_xyxy_norm"][0] + obs["bbox_xyxy_norm"][2]) / 2; cy = (obs["bbox_xyxy_norm"][1] + obs["bbox_xyxy_norm"][3]) / 2
        return (x0 + int((cx - min_x) / max(.001, max_x - min_x) * w), y0 + int((cy - min_y) / max(.001, max_y - min_y) * h))
    cv2.rectangle(image, (x0, y0), (x0 + w, y0 + h), (210, 210, 210), 1)
    refs: list[dict[str, Any]] = []
    for d in decisions:
        actual = [o for o in d["observations"] if o["source_status"] == "observed"]
        pts = [local_point(o) for o in actual]
        for point in pts:
            cv2.circle(image, point, 8, (30, 130, 30) if d["verdict"] == "durable" else (0, 140, 220), -1)
        _plot_points(image, pts, (30, 130, 30) if d["verdict"] == "durable" else (0, 140, 220), "")
        cv2.putText(image, f"{d['track_id']} actual anchor/continuation", (90, 570 + len(refs) * 20), cv2.FONT_HERSHEY_SIMPLEX, .42, (20, 100, 20), 1, cv2.LINE_AA)
        refs.extend({"track_id": d["track_id"], "observation_id": o["observation_id"], "frame_id": o["frame_id"], "timestamp_ms": o["timestamp_ms"], "role": "actual"} for o in actual)
    alternatives = json.loads((run / "tracks.json").read_text())["association_alternatives"]
    for item in alternatives:
        if item.get("reason") not in {"competing_identity", "selected"}:
            continue
        obs = observations.get(item.get("observation_id"))
        if not obs:
            continue
        point = local_point(obs); cv2.circle(image, point, 10, (210, 30, 30) if not item.get("selected") else (30, 30, 210), 2)
        track = tracks.get(f"track_{item['track_id']:04d}")
        anchor = next((o for o in track["observations"] if o["source_status"] == "observed"), None) if track else None
        if anchor:
            anchor_point = local_point(anchor)
            cv2.line(image, anchor_point, point, (210, 30, 30) if not item.get("selected") else (30, 30, 210), 3, cv2.LINE_AA)
        refs.append({"track_id": item.get("track_id"), "observation_id": item.get("observation_id"), "frame_id": item.get("frame_id"), "selected": item.get("selected"), "cost": item.get("cost"), "margin": item.get("margin", .05)})
    actual_refs = ", ".join(f"{r['observation_id']}@{r['timestamp_ms']}ms" for r in refs if r.get("role") == "actual")
    cv2.putText(image, f"actual references: {actual_refs[:130]}", (80, 550), cv2.FONT_HERSHEY_SIMPLEX, .4, (20, 100, 20), 1, cv2.LINE_AA)
    for row, item in enumerate([r for r in refs if "selected" in r]):
        color = (30, 30, 210) if item["selected"] else (210, 30, 30)
        cv2.putText(image, f"{'SELECTED' if item['selected'] else 'ALTERNATIVE'} track={item['track_id']} obs={item['observation_id']} frame={item['frame_id']} cost={item['cost']:.4f} margin=0.05", (80, 485 + row * 18), cv2.FONT_HERSHEY_SIMPLEX, .38, color, 1, cv2.LINE_AA)
    cv2.putText(image, "solid red=selected link; blue circle/dashed=alternative; filled=actual; margin=0.05", (80, 625), cv2.FONT_HERSHEY_SIMPLEX, .5, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.imwrite(str(output), image)
    return {"panel": name, "path": str(output), "type": "assignment_geometry", "references": refs, "thresholds": {"association_competition_margin": .05}}


def render_bridge(run: Path, output: Path) -> dict[str, Any]:
    decisions = json.loads((run / "decisions.json").read_text())["decisions"]
    frame_rows = json.loads((run / "per-frame.json").read_text())
    tracks, observations = _decision_lookup(decisions)
    all_observations = {o["observation_id"]: o for row in frame_rows for o in row["observations"]}
    image = _panel("Prediction bridge: actual anchors, unattached prediction, rejected link")
    x0, y0, w, h = 80, 100, 950, 470
    refs: list[dict[str, Any]] = []
    for d in decisions:
        actual = [o for o in d["observations"] if o["source_status"] == "observed"]
        pts = [_box_point(o["bbox_xyxy_norm"], x0, y0, w, h) for o in actual]
        for idx, (point, obs) in enumerate(zip(pts, actual)):
            cv2.circle(image, point, 8, (30, 130, 30), -1)
            cv2.putText(image, f"actual {idx + 1}", (point[0] + 8, point[1] - 8), cv2.FONT_HERSHEY_SIMPLEX, .35, (30, 100, 30), 1, cv2.LINE_AA)
        _plot_points(image, pts, (30, 130, 30), "")
        for idx, obs in enumerate(actual):
            cv2.putText(image, f"{d['track_id']} actual {obs['frame_id']} / {obs['timestamp_ms']}ms", (80, 520 + idx * 18), cv2.FONT_HERSHEY_SIMPLEX, .38, (30, 100, 30), 1, cv2.LINE_AA)
    prediction = next((o for o in all_observations.values() if o.get("source_status") == "predicted"), None)
    if prediction:
        p = _draw_observation(image, prediction, x0, y0, w, h, (0, 0, 220), 11)
        cv2.putText(image, "UNATTACHED PREDICTION", (p[0] + 8, p[1] + 35), cv2.FONT_HERSHEY_SIMPLEX, .45, (0, 0, 180), 1, cv2.LINE_AA)
        refs.append({"observation_id": prediction["observation_id"], "frame_id": prediction["frame_id"], "timestamp_ms": prediction["timestamp_ms"], "source_status": prediction["source_status"], "association": prediction.get("association")})
        for d in decisions:
            for o in d["observations"]:
                if o["source_status"] == "observed" and o["timestamp_ms"] < prediction["timestamp_ms"]:
                    anchor = _box_point(o["bbox_xyxy_norm"], x0, y0, w, h)
                    for fraction in np.linspace(0, 1, 12)[::2]:
                        a = tuple(int(anchor[i] + fraction * (p[i] - anchor[i])) for i in (0, 1)); b = tuple(int(anchor[i] + (fraction + .08) * (p[i] - anchor[i])) for i in (0, 1)); cv2.line(image, a, b, (0, 0, 220), 2, cv2.LINE_AA)
                    break
    cv2.putText(image, "green=actual; hollow red=prediction; dashed red=rejected evidence link, not identity", (80, 625), cv2.FONT_HERSHEY_SIMPLEX, .48, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.imwrite(str(output), image)
    return {"panel": "prediction-bridge", "path": str(output), "type": "non_actual_geometry", "references": refs, "thresholds": {"association_max_center_distance": .20, "maximum_gap_duration_s": .75}}


def render_real(run: Path, output: Path, name: str) -> list[dict[str, Any]]:
    decisions = json.loads((run / "decisions.json").read_text())["decisions"]
    policy = json.loads((run / "tracking-config.json").read_text())
    center_threshold = float(policy["maximum_center_trend_residual"]["value"]); scale_threshold = float(policy["maximum_scale_change_residual"]["value"]); gap_threshold = float(policy["maximum_gap_duration"]["value"])
    selected: list[tuple[dict[str, Any], str]] = []
    for predicate, kind in [(lambda d: d["verdict"] == "durable", "durable"), (lambda d: d["material_competition_evidence"], "competing"), (lambda d: d["gaps"], "gap")]:
        item = next((d for d in decisions if predicate(d)), None)
        if item and not any(kind == existing_kind for _, existing_kind in selected): selected.append((item, kind))
    panels = []
    for index, (decision, sample_kind) in enumerate(selected):
        image = _panel(f"{name} {sample_kind} {decision['track_id']} ({decision['verdict']})", 1100, 620)
        x0, y0, w, h = 70, 90, 850, 390
        actual = [o for o in decision["observations"] if o["source_status"] == "observed"]
        points = [_box_point(o["bbox_xyxy_norm"], x0, y0, w, h) for o in actual]
        for idx, (point, obs) in enumerate(zip(points, actual)):
            color = (30, 130, 30) if decision["verdict"] == "durable" else (0, 140, 220)
            cv2.circle(image, point, 7, color, -1)
            if idx == 0 or idx == len(actual) - 1 or idx % 3 == 0:
                cv2.putText(image, f"{obs['frame_id']}@{obs['timestamp_ms']}ms", (point[0] + 8, point[1] - 8 - (idx % 2) * 18), cv2.FONT_HERSHEY_SIMPLEX, .34, color, 1, cv2.LINE_AA)
        _plot_points(image, points, (30, 130, 30) if decision["verdict"] == "durable" else (0, 140, 220), decision["track_id"])
        local = decision["metrics"]
        cv2.putText(image, f"frames={actual[0]['frame_id']}..{actual[-1]['frame_id']} local residual center={local['local_center_residual']:.3f}/{center_threshold:g} scale={local['local_scale_residual']:.3f}/{scale_threshold:g}", (25, 540), cv2.FONT_HERSHEY_SIMPLEX, .43, (20, 20, 20), 1, cv2.LINE_AA)
        gap_text = "; ".join(f"{g['start_frame_id']}->{g['end_frame_id']} {g['elapsed_s']:.3f}s/{gap_threshold:g}s" for g in decision["gaps"][:2]) or "no gap in sampled span"
        cv2.putText(image, gap_text, (25, 575), cv2.FONT_HERSHEY_SIMPLEX, .43, (20, 20, 20), 1, cv2.LINE_AA)
        path = output.parent / f"{name}-{index:02d}-{decision['track_id']}.png"; cv2.imwrite(str(path), image)
        panels.append({"panel": str(path.name), "type": "real_track_geometry", "sample_kind": sample_kind, "track_id": decision["track_id"], "verdict": decision["verdict"], "observation_ids": [o["observation_id"] for o in actual], "frame_ids": [o["frame_id"] for o in actual], "time_window_ms": [actual[0]["timestamp_ms"], actual[-1]["timestamp_ms"]], "thresholds": {"center_residual": center_threshold, "scale_residual": scale_threshold, "gap_s": gap_threshold}})
    return panels


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt04", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    for fixture, renderer, name in [("fixtures/fork-output", render_fork, "fork"), ("fixtures/competition-output", render_fork, "competition")]:
        index.append(renderer(args.attempt04 / fixture, args.output / f"{name}.png", name, f"{name} assignment geometry"))
    index.append(render_bridge(args.attempt04 / "fixtures/prediction-bridge-output", args.output / "prediction-bridge.png"))
    index.extend(render_real(args.attempt04 / "local-bright", args.output, "bright"))
    index.extend(render_real(args.attempt04 / "local-dark", args.output, "dark"))
    (args.output / "diagnostic-index.json").write_text(json.dumps({"schema": "p2_attempt05_visual_index_v1", "coverage": "explicit sampled fixture and real tracks; complete computational JSON remains in attempt-04", "panels": index}, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
