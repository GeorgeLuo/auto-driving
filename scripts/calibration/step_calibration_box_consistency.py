#!/usr/bin/env python3
"""
Seeded box-face step calibration with an optional VLM seed provider.

This is a targeted verifier:
  1. Pick one visible rectangular face in frame 0.
  2. Track that face across a forward-pulse image sequence with Shi-Tomasi
     corners + pyramidal Lucas-Kanade optical flow.
  3. Estimate a per-frame homography with RANSAC and propagate the face corners.
  4. Measure apparent face height/width in each frame.
  5. Fit the relative step model:
         observed_size_i ~= C / (Z0 - i)

The VLM, when used, only provides the initial seed quadrilateral. All metric
and consistency calculations after that seed are classical CV.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import mimetypes
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

try:
    import matplotlib.pyplot as plt
except Exception:
    plt = None


@dataclass
class SeedObservation:
    provider: str
    image_path: str
    image_width_px: int
    image_height_px: int
    quad_uv: list[list[float]]
    confidence: float | None
    label: str | None
    notes: list[str]
    raw_response: Any | None = None


@dataclass
class SeedImageTransform:
    mode: str
    input_path: str
    input_width_px: int
    input_height_px: int
    crop_xyxy_original: list[float] | None
    scale_x: float
    scale_y: float


@dataclass
class TrackStep:
    from_frame: int
    to_frame: int
    candidate_points: int
    tracked_points: int
    ransac_inliers: int
    ransac_inlier_ratio: float


@dataclass
class FrameObservation:
    frame: int
    quad_uv: list[list[float]]
    height_px: float
    width_px: float
    fully_inside_image: bool
    min_border_margin_px: float


@dataclass
class SideEdgeSupport:
    frame: int
    side: str
    sample_count: int
    hit_count: int
    support_fraction: float
    mean_distance_px: float | None
    median_distance_px: float | None
    mean_alignment: float | None
    passed: bool


@dataclass
class FrameEdgeSupport:
    frame: int
    side_support: list[SideEdgeSupport]
    mean_support_fraction: float
    min_support_fraction: float
    mean_distance_px: float | None
    passed: bool


@dataclass
class SideEdgeLock:
    frame: int
    side: str
    sample_count: int
    best_offset_px: float | None
    best_support_fraction: float
    zero_band_support_fraction: float
    zero_to_best_ratio: float | None
    passed: bool


@dataclass
class FrameEdgeLock:
    frame: int
    side_locks: list[SideEdgeLock]
    mean_best_support_fraction: float
    max_abs_best_offset_px: float | None
    min_zero_to_best_ratio: float | None
    passed: bool


@dataclass
class SizeFit:
    axis: str
    z0_steps: float
    size_px_steps: float
    mean_recovered_size_px_steps: float
    std_recovered_size_px_steps: float
    coefficient_of_variation: float
    rmse_px: float
    residual_pct_by_frame: list[float]


@dataclass
class RollingPrediction:
    frame: int
    predicted_px: float
    observed_px: float
    error_pct: float


def discover_frames(image_dir: Path, pattern: str) -> list[Path]:
    paths = sorted(image_dir.glob(pattern))
    if len(paths) < 3:
        raise SystemExit(f"need at least 3 images matching {image_dir / pattern}")
    return paths


def preprocess(img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def quad_height_width(quad: np.ndarray) -> tuple[float, float]:
    q = np.asarray(quad, dtype=np.float32)
    left_h = np.linalg.norm(q[3] - q[0])
    right_h = np.linalg.norm(q[2] - q[1])
    top_w = np.linalg.norm(q[1] - q[0])
    bottom_w = np.linalg.norm(q[2] - q[3])
    return float((left_h + right_h) / 2.0), float((top_w + bottom_w) / 2.0)


def image_inside_stats(quad: np.ndarray, image_shape: tuple[int, ...]) -> tuple[bool, float]:
    height, width = image_shape[:2]
    q = np.asarray(quad, dtype=np.float32)
    margins = np.column_stack([
        q[:, 0],
        width - 1 - q[:, 0],
        q[:, 1],
        height - 1 - q[:, 1],
    ])
    min_margin = float(np.min(margins))
    return bool(min_margin >= 0), min_margin


def parse_quad(value: str) -> np.ndarray:
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--seed-quad must be JSON: {exc}") from exc
    return normalize_quad(data)


def normalize_quad(data: Any) -> np.ndarray:
    if isinstance(data, dict):
        data = data.get("quad_uv") or data.get("quad") or data.get("points")
    arr = np.asarray(data, dtype=np.float32)
    if arr.shape != (4, 2):
        raise SystemExit(f"seed quad must be four [x, y] points, got shape {arr.shape}")
    if not np.isfinite(arr).all():
        raise SystemExit("seed quad contains non-finite coordinates")
    return order_quad_points(arr)


def order_quad_points(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32)
    center = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    ordered = pts[np.argsort(angles)]

    # Convert clockwise/counter-clockwise ring into TL, TR, BR, BL.
    sums = ordered[:, 0] + ordered[:, 1]
    start = int(np.argmin(sums))
    ordered = np.roll(ordered, -start, axis=0)
    if ordered[1, 0] < ordered[3, 0]:
        ordered = np.array([ordered[0], ordered[3], ordered[2], ordered[1]], dtype=np.float32)
    return ordered.astype(np.float32)


def validate_quad(quad: np.ndarray, image_shape: tuple[int, ...]) -> None:
    height, width = image_shape[:2]
    if np.any(quad[:, 0] < 0) or np.any(quad[:, 0] >= width) or np.any(quad[:, 1] < 0) or np.any(quad[:, 1] >= height):
        raise SystemExit("seed quad has points outside the first image")
    area = abs(float(cv2.contourArea(quad.astype(np.float32))))
    if area < 100:
        raise SystemExit(f"seed quad area is too small: {area:.1f}px^2")
    if not cv2.isContourConvex(np.rint(quad).astype(np.int32)):
        raise SystemExit("seed quad is not convex")


def load_seed_from_json(path: Path) -> SeedObservation:
    data = json.loads(path.read_text(encoding="utf-8"))
    quad = normalize_quad(data)
    return SeedObservation(
        provider=str(data.get("provider", "json")),
        image_path=str(data.get("image_path", "")),
        image_width_px=int(data.get("image_width_px", 0)),
        image_height_px=int(data.get("image_height_px", 0)),
        quad_uv=np.round(quad, 3).tolist(),
        confidence=data.get("confidence"),
        label=data.get("label"),
        notes=list(data.get("notes", [])),
        raw_response=data.get("raw_response"),
    )


def make_manual_seed(image_path: Path, image_shape: tuple[int, ...], seed_quad: str) -> SeedObservation:
    quad = parse_quad(seed_quad)
    validate_quad(quad, image_shape)
    height, width = image_shape[:2]
    return SeedObservation(
        provider="manual",
        image_path=str(image_path),
        image_width_px=width,
        image_height_px=height,
        quad_uv=np.round(quad, 3).tolist(),
        confidence=1.0,
        label="manual_box_face",
        notes=["Seed supplied by --seed-quad."],
    )


def seed_from_command(image_path: Path, image_shape: tuple[int, ...], args: argparse.Namespace) -> SeedObservation:
    if not args.seed_command:
        raise SystemExit("--seed-command is required when --seed-provider command")
    height, width = image_shape[:2]
    command = shlex.split(args.seed_command) + [
        str(image_path),
        str(width),
        str(height),
        args.target_description,
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(
            f"seed command failed with exit {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}"
        )
    data = extract_json_object(completed.stdout)
    quad = normalize_quad(data)
    validate_quad(quad, image_shape)
    return SeedObservation(
        provider="command",
        image_path=str(image_path),
        image_width_px=width,
        image_height_px=height,
        quad_uv=np.round(quad, 3).tolist(),
        confidence=data.get("confidence"),
        label=data.get("label"),
        notes=list(data.get("notes", [])),
        raw_response=data,
    )


def seed_prompt(
    width: int,
    height: int,
    target_description: str,
    context_frame_index: int | None = None,
    has_target_hint: bool = False,
    crop_mode: bool = False,
) -> str:
    context_text = ""
    if crop_mode:
        context_text += (
            "The supplied image is an upscaled crop from frame 0, chosen to isolate the target area. "
            "Return coordinates in this crop image's pixel coordinate system, not in the original full frame. "
            "The crop may include carpet/floor around the target; do not select the carpet/floor. "
        )
    if context_frame_index is not None:
        hint_text = ""
        if has_target_hint:
            hint_text = (
                "Panel A also contains a yellow rough target hint. It identifies which "
                "physical face/object to refine, but its geometry may be only a bounding "
                "box and may be wrong. Do not copy the yellow hint coordinates. "
            )
        context_text = (
            "The supplied image is a two-panel context sheet. The left panel A is frame 0, "
            f"the coordinate target, at original size {width}x{height}. The right panel B "
            f"is frame {context_frame_index}, a later closer view, and is evidence only. "
            f"{hint_text}"
            "Select the same physical planar face visible in both panels, using panel B "
            "to understand which cardboard face is angled and where its true outer edges are. "
            "Return coordinates only for panel A / frame 0, using the original frame 0 "
            f"coordinate system where x is 0..{width - 1} and y is 0..{height - 1}. "
            "Do not return coordinates from panel B and do not return coordinates in the "
            "full stitched-sheet coordinate system. "
        )
    return (
        "Return only JSON. Identify the best visible physical planar face "
        "near the center of the image to track for relative step calibration. "
        f"{context_text}"
        "Prefer a rigid surface with clear true outer edges and corners. "
        "A cardboard box face is ideal, but a book cover, flat sign, panel, "
        "cabinet face, or other trackable planar patch is also valid. "
        "Return the four image coordinates of the actual projected corners of that "
        "physical planar face, in this order: top-left, top-right, bottom-right, bottom-left. "
        "Do not return an axis-aligned bounding box, square crop, label rectangle, "
        "printed graphic boundary, or regularized rectangle. Preserve perspective: "
        "opposite edges should only appear parallel if they actually look parallel in the image, "
        "and the nearer side of a face may appear longer. "
        "Use the outer visible boundary of the selected planar face, not the whole 3D object. "
        "Do not select the floor, wall, shadows, curved objects, soft irregular objects, "
        "or a full object silhouette when only one visible part is planar. "
        "If a true face corner is partly hidden or uncertain, estimate it from the visible "
        "face edges, lower the confidence, and explain the uncertainty in notes. "
        f"Target preference: {target_description}. "
        "Return this schema exactly: "
        "{\"quad_uv\":[[x_tl,y_tl],[x_tr,y_tr],[x_br,y_br],[x_bl,y_bl]],"
        "\"label\":\"short label\",\"confidence\":0.0,\"notes\":[\"short note\"]}. "
        f"The image size is {width}x{height}. Coordinates must be pixel coordinates in the original image."
    )


def seed_from_openai_compatible(
    *,
    provider: str,
    api_url: str,
    api_key_env: str,
    default_model: str,
    image_path: Path,
    image_shape: tuple[int, ...],
    args: argparse.Namespace,
) -> SeedObservation:
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise SystemExit(f"{api_key_env} is required when --seed-provider {provider}")

    height, width = image_shape[:2]
    context_path = getattr(args, "seed_context_sheet_path", None)
    request_image_path = Path(getattr(args, "seed_request_image_path", image_path))
    request_shape = getattr(args, "seed_request_image_shape", image_shape)
    request_height, request_width = request_shape[:2]
    image_url = data_url(Path(context_path) if context_path else request_image_path)
    context_frame_index = getattr(args, "seed_context_frame_index_resolved", None)
    has_target_hint = bool(getattr(args, "seed_target_hint_present", False))
    crop_mode = bool(getattr(args, "seed_crop_transform", None))
    prompt = seed_prompt(request_width, request_height, args.target_description, context_frame_index, has_target_hint, crop_mode)
    payload = {
        "model": args.vlm_model or default_model,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "temperature": 0,
        "max_completion_tokens": 500,
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "auto-driving-step-calibration/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.vlm_timeout_s) as response:
            api_response = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{provider} request failed: HTTP {exc.code}\n{body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"{provider} request failed: {exc}") from exc

    content = api_response["choices"][0]["message"]["content"]
    data = extract_json_object(content)
    request_quad = normalize_quad(data)
    validate_quad(request_quad, request_shape)
    quad = map_seed_quad_to_original(request_quad, args)
    validate_quad(quad, image_shape)
    raw_response = {
        "model_response": data,
        "request_quad_uv": np.round(request_quad, 3).tolist(),
        "seed_image_transform": asdict(args.seed_crop_transform) if getattr(args, "seed_crop_transform", None) else None,
    }
    return SeedObservation(
        provider=provider,
        image_path=str(image_path),
        image_width_px=width,
        image_height_px=height,
        quad_uv=np.round(quad, 3).tolist(),
        confidence=data.get("confidence"),
        label=data.get("label"),
        notes=list(data.get("notes", [])),
        raw_response=raw_response,
    )


def seed_from_openai(image_path: Path, image_shape: tuple[int, ...], args: argparse.Namespace) -> SeedObservation:
    return seed_from_openai_compatible(
        provider="openai",
        api_url="https://api.openai.com/v1/chat/completions",
        api_key_env="OPENAI_API_KEY",
        default_model="gpt-4o",
        image_path=image_path,
        image_shape=image_shape,
        args=args,
    )


def seed_from_mulerouter(image_path: Path, image_shape: tuple[int, ...], args: argparse.Namespace) -> SeedObservation:
    return seed_from_openai_compatible(
        provider="mulerouter",
        api_url=args.vlm_base_url.rstrip("/") + "/chat/completions",
        api_key_env="MULEROUTER_API_KEY",
        default_model="qwen3-vl-plus",
        image_path=image_path,
        image_shape=image_shape,
        args=args,
    )


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def write_seed_crop(
    frame_bgr: np.ndarray,
    out_path: Path,
    args: argparse.Namespace,
    hint_quad: np.ndarray | None = None,
) -> SeedImageTransform:
    height, width = frame_bgr.shape[:2]
    if args.seed_crop_source == "hint":
        if hint_quad is None:
            raise SystemExit("--seed-crop-source hint requires --seed-target-hint-json")
        min_xy = hint_quad.min(axis=0)
        max_xy = hint_quad.max(axis=0)
        center = (min_xy + max_xy) / 2.0
        hint_size = float(np.max(max_xy - min_xy))
        crop_size = max(float(args.seed_crop_min_size_px), hint_size * (1.0 + 2.0 * args.seed_crop_pad_frac))
    elif args.seed_crop_source == "center":
        center = np.array([width / 2.0, height / 2.0], dtype=np.float32)
        crop_size = float(args.seed_crop_size_px)
    else:
        raise SystemExit(f"unknown seed crop source: {args.seed_crop_source}")

    crop_size = max(8.0, min(crop_size, float(width), float(height)))
    x0 = float(np.clip(center[0] - crop_size / 2.0, 0.0, width - crop_size))
    y0 = float(np.clip(center[1] - crop_size / 2.0, 0.0, height - crop_size))
    x1 = x0 + crop_size
    y1 = y0 + crop_size
    ix0, iy0 = int(math.floor(x0)), int(math.floor(y0))
    ix1, iy1 = int(math.ceil(x1)), int(math.ceil(y1))
    crop = frame_bgr[iy0:iy1, ix0:ix1]
    output_size = int(args.seed_crop_output_size_px)
    if output_size < 32:
        raise SystemExit("--seed-crop-output-size-px must be at least 32")
    upscaled = cv2.resize(crop, (output_size, output_size), interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(str(out_path), upscaled)
    scale_x = output_size / float(ix1 - ix0)
    scale_y = output_size / float(iy1 - iy0)
    return SeedImageTransform(
        mode=args.seed_crop_source,
        input_path=str(out_path),
        input_width_px=output_size,
        input_height_px=output_size,
        crop_xyxy_original=[float(ix0), float(iy0), float(ix1), float(iy1)],
        scale_x=float(scale_x),
        scale_y=float(scale_y),
    )


def map_seed_quad_to_original(quad: np.ndarray, args: argparse.Namespace) -> np.ndarray:
    transform = getattr(args, "seed_crop_transform", None)
    if transform is None:
        return np.asarray(quad, dtype=np.float32)
    if transform.crop_xyxy_original is None:
        return np.asarray(quad, dtype=np.float32)
    x0, y0, _x1, _y1 = transform.crop_xyxy_original
    q = np.asarray(quad, dtype=np.float32).copy()
    q[:, 0] = q[:, 0] / float(transform.scale_x) + float(x0)
    q[:, 1] = q[:, 1] / float(transform.scale_y) + float(y0)
    return q.astype(np.float32)


def write_seed_context_sheet(
    target_bgr: np.ndarray,
    context_bgr: np.ndarray,
    context_frame_index: int,
    out_path: Path,
    hint_quad: np.ndarray | None = None,
) -> None:
    height, width = target_bgr.shape[:2]
    if context_bgr.shape[:2] != (height, width):
        context_bgr = cv2.resize(context_bgr, (width, height), interpolation=cv2.INTER_AREA)
    sheet = np.zeros((height, width * 2, 3), dtype=np.uint8)
    sheet[:, :width] = target_bgr
    sheet[:, width:] = context_bgr
    cv2.line(sheet, (width, 0), (width, height - 1), (255, 255, 255), 2)
    draw_panel_label(sheet, "A frame 0 target coords", (8, 24))
    draw_panel_label(sheet, f"B frame {context_frame_index} evidence only", (width + 8, 24))
    if hint_quad is not None:
        q = np.rint(hint_quad).astype(np.int32)
        cv2.polylines(sheet, [q], True, (0, 255, 255), 2, cv2.LINE_AA)
        draw_panel_label(sheet, "yellow rough target hint", (8, height - 12))
    cv2.imwrite(str(out_path), sheet)


def draw_panel_label(img: np.ndarray, text: str, origin: tuple[int, int]) -> None:
    cv2.putText(img, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1, cv2.LINE_AA)


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise SystemExit(f"VLM response did not contain JSON object:\n{text}")
        data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise SystemExit("seed provider must return a JSON object")
    return data


def get_seed(image_path: Path, image_shape: tuple[int, ...], args: argparse.Namespace) -> SeedObservation:
    if args.seed_json:
        seed = load_seed_from_json(Path(args.seed_json))
        quad = normalize_quad(seed.quad_uv)
        validate_quad(quad, image_shape)
        height, width = image_shape[:2]
        seed.image_path = str(image_path)
        seed.image_width_px = width
        seed.image_height_px = height
        seed.quad_uv = np.round(quad, 3).tolist()
        return seed
    if args.seed_provider == "manual":
        if not args.seed_quad:
            raise SystemExit("--seed-quad is required when --seed-provider manual")
        return make_manual_seed(image_path, image_shape, args.seed_quad)
    if args.seed_provider == "command":
        return seed_from_command(image_path, image_shape, args)
    if args.seed_provider == "openai":
        return seed_from_openai(image_path, image_shape, args)
    if args.seed_provider == "mulerouter":
        return seed_from_mulerouter(image_path, image_shape, args)
    raise SystemExit(f"unknown seed provider: {args.seed_provider}")


def track_quad_lk_homography(
    frames_bgr: list[np.ndarray],
    seed_quad: np.ndarray,
    args: argparse.Namespace,
) -> tuple[list[np.ndarray], list[TrackStep]]:
    grays = [preprocess(img) for img in frames_bgr]
    quads = [np.asarray(seed_quad, dtype=np.float32)]
    track_steps: list[TrackStep] = []

    for i in range(len(grays) - 1):
        gray0 = grays[i]
        gray1 = grays[i + 1]
        q = quads[-1]

        mask = np.zeros_like(gray0)
        cv2.fillPoly(mask, [q.astype(np.int32)], 255)
        if args.track_mask_erode_px > 0:
            kernel_size = args.track_mask_erode_px * 2 + 1
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
            mask = cv2.erode(mask, kernel, iterations=1)

        pts0 = cv2.goodFeaturesToTrack(
            gray0,
            maxCorners=args.homography_max_corners,
            qualityLevel=args.homography_quality,
            minDistance=args.homography_min_distance,
            mask=mask,
            blockSize=5,
        )
        if pts0 is None or len(pts0) < 4:
            raise RuntimeError(f"Too few trackable points at frame {i}")

        pts1, status, err = cv2.calcOpticalFlowPyrLK(
            gray0,
            gray1,
            pts0,
            None,
            winSize=(21, 21),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
        )
        if pts1 is None or status is None or err is None:
            raise RuntimeError(f"LK tracking failed at frame {i}")
        pts0_back, status_back, _ = cv2.calcOpticalFlowPyrLK(
            gray1,
            gray0,
            pts1,
            None,
            winSize=(21, 21),
            maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
        )
        if pts0_back is None or status_back is None:
            raise RuntimeError(f"LK backtracking failed at frame {i}")
        status = status.reshape(-1).astype(bool)
        status_back = status_back.reshape(-1).astype(bool)
        err = err.reshape(-1)
        p0_all = pts0.reshape(-1, 2)
        p1_all = pts1.reshape(-1, 2)
        p0_back_all = pts0_back.reshape(-1, 2)
        fb_err = np.linalg.norm(p0_all - p0_back_all, axis=1)

        good = status & status_back & (err < args.lk_max_error) & (fb_err < args.lk_fb_max_error)
        p0 = p0_all[good]
        p1 = p1_all[good]
        if len(p0) < 4:
            raise RuntimeError(f"Too few good tracks at frame {i}")

        H, inliers = cv2.findHomography(p0, p1, cv2.RANSAC, args.homography_ransac_px)
        if H is None or inliers is None:
            raise RuntimeError(f"Homography failed at frame {i}")

        q_h = np.concatenate([q, np.ones((4, 1), dtype=np.float32)], axis=1) @ H.T
        q_next = (q_h[:, :2] / q_h[:, 2:]).astype(np.float32)
        quads.append(q_next)

        inlier_count = int(np.sum(inliers))
        track_steps.append(TrackStep(
            from_frame=i,
            to_frame=i + 1,
            candidate_points=int(len(pts0)),
            tracked_points=int(len(p0)),
            ransac_inliers=inlier_count,
            ransac_inlier_ratio=float(inlier_count / max(len(p0), 1)),
        ))

    return quads, track_steps


def fit_inverse_size(observed_px: np.ndarray, frames: np.ndarray, axis: str) -> SizeFit:
    if len(observed_px) < 3:
        raise ValueError(f"need at least 3 valid observations to fit {axis}")
    y = 1.0 / observed_px
    A = np.column_stack([np.ones_like(frames), frames])
    a, b = np.linalg.lstsq(A, y, rcond=None)[0]
    if b >= 0:
        raise ValueError(f"expected inverse {axis} to decrease with forward motion")

    size_px_steps = -1.0 / b
    z0_steps = a * size_px_steps
    z_i = z0_steps - frames
    recovered = observed_px * z_i
    mean_rec = float(np.mean(recovered))
    std_rec = float(np.std(recovered, ddof=1)) if len(recovered) > 1 else 0.0
    cv = float(std_rec / mean_rec) if mean_rec else float("nan")

    pred_px = 1.0 / (a + b * frames)
    rmse_px = float(np.sqrt(np.mean((observed_px - pred_px) ** 2)))
    residual_pct = ((recovered / mean_rec - 1.0) * 100.0).tolist()

    return SizeFit(
        axis=axis,
        z0_steps=float(z0_steps),
        size_px_steps=float(size_px_steps),
        mean_recovered_size_px_steps=mean_rec,
        std_recovered_size_px_steps=std_rec,
        coefficient_of_variation=cv,
        rmse_px=rmse_px,
        residual_pct_by_frame=residual_pct,
    )


def rolling_next_predictions(observed_px: np.ndarray) -> list[RollingPrediction]:
    out: list[RollingPrediction] = []
    for j in range(2, len(observed_px)):
        frames = np.arange(j, dtype=float)
        y = 1.0 / observed_px[:j]
        A = np.column_stack([np.ones_like(frames), frames])
        a, b = np.linalg.lstsq(A, y, rcond=None)[0]
        pred = float(1.0 / (a + b * j))
        obs = float(observed_px[j])
        out.append(RollingPrediction(
            frame=j,
            predicted_px=pred,
            observed_px=obs,
            error_pct=float((obs - pred) / pred * 100.0),
        ))
    return out


def prediction_stats(rows: list[RollingPrediction]) -> dict[str, float | None]:
    if not rows:
        return {
            "mean_abs_error_pct": None,
            "rmse_pct": None,
            "std_error_pct": None,
            "max_abs_error_pct": None,
        }
    errors = np.array([row.error_pct for row in rows], dtype=float)
    return {
        "mean_abs_error_pct": float(np.mean(np.abs(errors))),
        "rmse_pct": float(np.sqrt(np.mean(errors ** 2))),
        "std_error_pct": float(np.std(errors, ddof=1)) if len(errors) > 1 else 0.0,
        "max_abs_error_pct": float(np.max(np.abs(errors))),
    }


def edge_support_for_frame(frame_bgr: np.ndarray, quad: np.ndarray, frame_index: int, args: argparse.Namespace) -> FrameEdgeSupport:
    gray = preprocess(frame_bgr)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, args.edge_canny_low, args.edge_canny_high)
    grad_x = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)

    side_specs = [
        ("top", 0, 1),
        ("right", 1, 2),
        ("bottom", 3, 2),
        ("left", 0, 3),
    ]
    side_support = [
        edge_support_for_side(
            edges,
            grad_x,
            grad_y,
            np.asarray(quad, dtype=np.float32),
            frame_index,
            side,
            start,
            end,
            args,
        )
        for side, start, end in side_specs
    ]
    support_values = [item.support_fraction for item in side_support]
    distances = [
        item.mean_distance_px
        for item in side_support
        if item.mean_distance_px is not None
    ]
    mean_support = float(np.mean(support_values)) if support_values else 0.0
    min_support = float(np.min(support_values)) if support_values else 0.0
    return FrameEdgeSupport(
        frame=frame_index,
        side_support=side_support,
        mean_support_fraction=mean_support,
        min_support_fraction=min_support,
        mean_distance_px=float(np.mean(distances)) if distances else None,
        passed=mean_support >= args.edge_min_frame_support and min_support >= args.edge_min_side_support,
    )


def edge_lock_for_frame(frame_bgr: np.ndarray, quad: np.ndarray, frame_index: int, args: argparse.Namespace) -> FrameEdgeLock:
    gray = preprocess(frame_bgr)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blurred, args.edge_canny_low, args.edge_canny_high)
    grad_x = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)

    side_specs = [
        ("top", 0, 1),
        ("right", 1, 2),
        ("bottom", 3, 2),
        ("left", 0, 3),
    ]
    side_locks = [
        edge_lock_for_side(
            edges,
            grad_x,
            grad_y,
            np.asarray(quad, dtype=np.float32),
            frame_index,
            side,
            start,
            end,
            args,
        )
        for side, start, end in side_specs
    ]
    best_supports = [item.best_support_fraction for item in side_locks]
    offsets = [
        abs(item.best_offset_px)
        for item in side_locks
        if item.best_offset_px is not None
    ]
    ratios = [
        item.zero_to_best_ratio
        for item in side_locks
        if item.zero_to_best_ratio is not None
    ]
    return FrameEdgeLock(
        frame=frame_index,
        side_locks=side_locks,
        mean_best_support_fraction=float(np.mean(best_supports)) if best_supports else 0.0,
        max_abs_best_offset_px=float(np.max(offsets)) if offsets else None,
        min_zero_to_best_ratio=float(np.min(ratios)) if ratios else None,
        passed=all(item.passed for item in side_locks),
    )


def edge_support_for_side(
    edges: np.ndarray,
    grad_x: np.ndarray,
    grad_y: np.ndarray,
    quad: np.ndarray,
    frame_index: int,
    side: str,
    start_index: int,
    end_index: int,
    args: argparse.Namespace,
) -> SideEdgeSupport:
    p0 = quad[start_index].astype(np.float32)
    p1 = quad[end_index].astype(np.float32)
    vector = p1 - p0
    length = float(np.linalg.norm(vector))
    if length < 1e-6:
        return SideEdgeSupport(frame_index, side, 0, 0, 0.0, None, None, None, False)

    tangent = vector / length
    normal = np.array([-tangent[1], tangent[0]], dtype=np.float32)
    sample_count = max(8, min(args.edge_max_samples, int(length / max(args.edge_sample_spacing, 1.0)) + 1))
    offsets = np.arange(-args.edge_search_px, args.edge_search_px + 1, dtype=np.float32)

    distances: list[float] = []
    alignments: list[float] = []
    height, width = edges.shape

    for alpha in np.linspace(0.04, 0.96, sample_count):
        point = p0 + (p1 - p0) * float(alpha)
        best: tuple[float, float] | None = None
        for offset in offsets:
            candidate = point + normal * offset
            x = int(round(float(candidate[0])))
            y = int(round(float(candidate[1])))
            if x < 0 or x >= width or y < 0 or y >= height:
                continue
            if edges[y, x] == 0:
                continue
            gx = float(grad_x[y, x])
            gy = float(grad_y[y, x])
            magnitude = math.hypot(gx, gy)
            if magnitude < 1e-6:
                continue
            alignment = abs((gx * float(normal[0]) + gy * float(normal[1])) / magnitude)
            if alignment < args.edge_min_alignment:
                continue
            distance = abs(float(offset))
            if best is None or distance < best[0]:
                best = (distance, alignment)
        if best is not None:
            distances.append(best[0])
            alignments.append(best[1])

    hit_count = len(distances)
    support_fraction = float(hit_count / max(sample_count, 1))
    return SideEdgeSupport(
        frame=frame_index,
        side=side,
        sample_count=sample_count,
        hit_count=hit_count,
        support_fraction=support_fraction,
        mean_distance_px=float(np.mean(distances)) if distances else None,
        median_distance_px=float(np.median(distances)) if distances else None,
        mean_alignment=float(np.mean(alignments)) if alignments else None,
        passed=support_fraction >= args.edge_min_side_support,
    )


def edge_lock_for_side(
    edges: np.ndarray,
    grad_x: np.ndarray,
    grad_y: np.ndarray,
    quad: np.ndarray,
    frame_index: int,
    side: str,
    start_index: int,
    end_index: int,
    args: argparse.Namespace,
) -> SideEdgeLock:
    p0 = quad[start_index].astype(np.float32)
    p1 = quad[end_index].astype(np.float32)
    vector = p1 - p0
    length = float(np.linalg.norm(vector))
    if length < 1e-6:
        return SideEdgeLock(frame_index, side, 0, None, 0.0, 0.0, None, False)

    tangent = vector / length
    normal = np.array([-tangent[1], tangent[0]], dtype=np.float32)
    sample_count = max(8, min(args.edge_max_samples, int(length / max(args.edge_sample_spacing, 1.0)) + 1))
    offsets = np.arange(-args.edge_lock_search_px, args.edge_lock_search_px + 1, dtype=np.float32)
    hit_counts = np.zeros(len(offsets), dtype=np.int32)
    height, width = edges.shape

    for alpha in np.linspace(0.04, 0.96, sample_count):
        point = p0 + (p1 - p0) * float(alpha)
        for offset_index, offset in enumerate(offsets):
            candidate = point + normal * offset
            x = int(round(float(candidate[0])))
            y = int(round(float(candidate[1])))
            if aligned_edge_at(edges, grad_x, grad_y, x, y, normal, width, height, args) is not None:
                hit_counts[offset_index] += 1

    best_count = int(np.max(hit_counts)) if len(hit_counts) else 0
    if best_count <= 0:
        return SideEdgeLock(frame_index, side, sample_count, None, 0.0, 0.0, None, False)

    best_indexes = np.flatnonzero(hit_counts == best_count)
    best_index = int(best_indexes[np.argmin(np.abs(offsets[best_indexes]))])
    best_offset = float(offsets[best_index])
    zero_band = max(0.0, float(args.edge_lock_zero_band_px))
    zero_indexes = np.flatnonzero(np.abs(offsets) <= zero_band)
    zero_count = int(np.max(hit_counts[zero_indexes])) if len(zero_indexes) else int(hit_counts[best_index])

    best_support = float(best_count / max(sample_count, 1))
    zero_support = float(zero_count / max(sample_count, 1))
    ratio = float(zero_count / best_count) if best_count else None
    passed = (
        best_support >= args.edge_lock_min_support
        and abs(best_offset) <= args.edge_lock_max_offset_px
        and ratio is not None
        and ratio >= args.edge_lock_min_peak_ratio
    )
    return SideEdgeLock(
        frame=frame_index,
        side=side,
        sample_count=sample_count,
        best_offset_px=best_offset,
        best_support_fraction=best_support,
        zero_band_support_fraction=zero_support,
        zero_to_best_ratio=ratio,
        passed=bool(passed),
    )


def aligned_edge_at(
    edges: np.ndarray,
    grad_x: np.ndarray,
    grad_y: np.ndarray,
    x: int,
    y: int,
    normal: np.ndarray,
    width: int,
    height: int,
    args: argparse.Namespace,
) -> float | None:
    radius = max(0, int(args.edge_lock_pixel_radius))
    best_alignment: float | None = None
    for yy in range(y - radius, y + radius + 1):
        if yy < 0 or yy >= height:
            continue
        for xx in range(x - radius, x + radius + 1):
            if xx < 0 or xx >= width or edges[yy, xx] == 0:
                continue
            gx = float(grad_x[yy, xx])
            gy = float(grad_y[yy, xx])
            magnitude = math.hypot(gx, gy)
            if magnitude < 1e-6:
                continue
            alignment = abs((gx * float(normal[0]) + gy * float(normal[1])) / magnitude)
            if alignment < args.edge_min_alignment:
                continue
            if best_alignment is None or alignment > best_alignment:
                best_alignment = alignment
    return best_alignment


def summarize_edge_support(edge_support: list[FrameEdgeSupport]) -> dict[str, Any]:
    if not edge_support:
        return {
            "frame_count": 0,
            "passed_frame_count": 0,
            "passed_frames": [],
            "failed_frames": [],
            "mean_frame_support": None,
            "median_frame_support": None,
            "min_frame_support": None,
        }
    mean_supports = np.asarray([item.mean_support_fraction for item in edge_support], dtype=np.float64)
    return {
        "frame_count": len(edge_support),
        "passed_frame_count": sum(1 for item in edge_support if item.passed),
        "passed_frames": [item.frame for item in edge_support if item.passed],
        "failed_frames": [item.frame for item in edge_support if not item.passed],
        "mean_frame_support": float(np.mean(mean_supports)),
        "median_frame_support": float(np.median(mean_supports)),
        "min_frame_support": float(np.min(mean_supports)),
    }


def summarize_edge_locks(edge_locks: list[FrameEdgeLock]) -> dict[str, Any]:
    if not edge_locks:
        return {
            "frame_count": 0,
            "passed_frame_count": 0,
            "passed_frames": [],
            "failed_frames": [],
            "mean_best_support": None,
            "median_max_abs_best_offset_px": None,
            "min_zero_to_best_ratio": None,
        }
    best_supports = np.asarray([item.mean_best_support_fraction for item in edge_locks], dtype=np.float64)
    offsets = [
        item.max_abs_best_offset_px
        for item in edge_locks
        if item.max_abs_best_offset_px is not None
    ]
    ratios = [
        item.min_zero_to_best_ratio
        for item in edge_locks
        if item.min_zero_to_best_ratio is not None
    ]
    return {
        "frame_count": len(edge_locks),
        "passed_frame_count": sum(1 for item in edge_locks if item.passed),
        "passed_frames": [item.frame for item in edge_locks if item.passed],
        "failed_frames": [item.frame for item in edge_locks if not item.passed],
        "mean_best_support": float(np.mean(best_supports)),
        "median_max_abs_best_offset_px": float(np.median(offsets)) if offsets else None,
        "min_zero_to_best_ratio": float(np.min(ratios)) if ratios else None,
    }


def draw_edge_support_sheet(
    frames_bgr: list[np.ndarray],
    quads: list[np.ndarray],
    edge_support: list[FrameEdgeSupport],
    edge_locks: list[FrameEdgeLock],
    out_path: Path,
) -> None:
    from PIL import Image

    thumbs = []
    side_specs = [
        ("top", 0, 1),
        ("right", 1, 2),
        ("bottom", 3, 2),
        ("left", 0, 3),
    ]
    lock_by_frame = {item.frame: item for item in edge_locks}
    for img, quad, support in zip(frames_bgr, quads, edge_support):
        out = img.copy()
        gray = preprocess(img)
        edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 40, 120)
        edge_tint = np.zeros_like(out)
        edge_tint[edges > 0] = (255, 120, 0)
        out = cv2.addWeighted(out, 0.86, edge_tint, 0.28, 0)

        by_side = {item.side: item for item in support.side_support}
        q = np.asarray(quad, dtype=np.float32)
        for side, start, end in side_specs:
            side_support = by_side[side]
            color = edge_support_color(side_support.support_fraction)
            p0 = tuple(np.rint(q[start]).astype(int).tolist())
            p1 = tuple(np.rint(q[end]).astype(int).tolist())
            cv2.line(out, p0, p1, color, 2, cv2.LINE_AA)

        lock = lock_by_frame.get(support.frame)
        lock_text = ""
        if lock is not None:
            status = "ok" if lock.passed else "bad"
            offset = lock.max_abs_best_offset_px
            ratio = lock.min_zero_to_best_ratio
            offset_text = "na" if offset is None else f"{offset:.1f}"
            ratio_text = "na" if ratio is None else f"{ratio:.2f}"
            lock_text = f" lock={offset_text}px/{ratio_text} {status}"
        text = f"{support.frame} edge={support.mean_support_fraction:.2f} min={support.min_support_fraction:.2f}{lock_text}"
        cv2.putText(out, text, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, text, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 1, cv2.LINE_AA)
        rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
        thumbs.append(Image.fromarray(rgb).resize((320, 240)))

    columns = 3
    rows = int(math.ceil(len(thumbs) / columns))
    sheet = Image.new("RGB", (columns * 320, rows * 240), (220, 220, 220))
    for idx, im in enumerate(thumbs):
        sheet.paste(im, ((idx % columns) * 320, (idx // columns) * 240))
    sheet.save(out_path)


def edge_support_color(value: float) -> tuple[int, int, int]:
    if value >= 0.6:
        return (0, 230, 0)
    if value >= 0.35:
        return (0, 215, 255)
    return (0, 0, 255)


def draw_seed_overlay(frame_bgr: np.ndarray, seed: SeedObservation, out_path: Path) -> None:
    out = frame_bgr.copy()
    q = np.asarray(seed.quad_uv, dtype=np.float32)
    cv2.polylines(out, [np.rint(q).astype(np.int32)], True, (0, 255, 0), 2)
    for index, (x, y) in enumerate(q):
        cv2.circle(out, (int(round(x)), int(round(y))), 4, (0, 255, 255), -1, cv2.LINE_AA)
        cv2.putText(out, str(index), (int(x) + 5, int(y) - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.putText(out, f"seed: {seed.provider}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    cv2.imwrite(str(out_path), out)


def draw_overlay_sheet(frames_bgr: list[np.ndarray], quads: list[np.ndarray], out_path: Path) -> None:
    from PIL import Image

    thumbs = []
    for i, (img, q) in enumerate(zip(frames_bgr, quads)):
        out = img.copy()
        cv2.polylines(out, [np.rint(q).astype(np.int32)], True, (0, 255, 0), 2)
        cv2.putText(out, str(i), (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        rgb = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
        thumbs.append(Image.fromarray(rgb).resize((320, 240)))
    columns = 3
    rows = int(math.ceil(len(thumbs) / columns))
    sheet = Image.new("RGB", (columns * 320, rows * 240), (220, 220, 220))
    for idx, im in enumerate(thumbs):
        sheet.paste(im, ((idx % columns) * 320, (idx // columns) * 240))
    sheet.save(out_path)


def draw_fit_plot(
    frames_idx: np.ndarray,
    heights: np.ndarray,
    widths: np.ndarray,
    height_fit: SizeFit,
    width_fit: SizeFit,
    out_path: Path,
) -> None:
    if plt is None:
        return
    fig = plt.figure(figsize=(7, 5))
    x = np.asarray(frames_idx, dtype=float)
    for vals, fit, label in [(heights, height_fit, "height"), (widths, width_fit, "width")]:
        y = np.asarray(vals, dtype=float)
        pred = fit.size_px_steps / (fit.z0_steps - x)
        plt.plot(x, y, marker="o", linestyle="", label=f"observed {label}")
        plt.plot(x, pred, label=f"fit {label}")
    plt.xlabel("frame / forward pulse index")
    plt.ylabel("apparent size (px)")
    plt.title("Box-face size law: size ~= C / (Z0 - steps)")
    plt.legend()
    plt.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def write_csv(path: Path, rows: list[Any]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def run(args: argparse.Namespace) -> dict[str, Any]:
    image_dir = Path(args.image_dir)
    out_dir = Path(args.out_dir) if args.out_dir else image_dir / "step_calibration_box_consistency"
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = discover_frames(image_dir, args.pattern)
    if args.max_frames:
        paths = paths[:args.max_frames]
    frames_bgr = [cv2.imread(str(path)) for path in paths]
    if any(frame is None for frame in frames_bgr):
        unreadable = [str(path) for path, frame in zip(paths, frames_bgr) if frame is None]
        raise SystemExit("could not read frames: " + ", ".join(unreadable))

    hint_quad_for_seed = None
    if args.seed_target_hint_json:
        hint_seed = load_seed_from_json(Path(args.seed_target_hint_json))
        hint_quad_for_seed = normalize_quad(hint_seed.quad_uv)
        validate_quad(hint_quad_for_seed, frames_bgr[0].shape)
        args.seed_target_hint_present = True

    seed_crop_path: Path | None = None
    if args.seed_crop_source != "none":
        if args.seed_context_frame_index is not None:
            raise SystemExit("--seed-crop-source cannot currently be combined with --seed-context-frame-index")
        seed_crop_path = out_dir / "seed_crop_input.jpg"
        crop_transform = write_seed_crop(frames_bgr[0], seed_crop_path, args, hint_quad=hint_quad_for_seed)
        args.seed_crop_transform = crop_transform
        args.seed_request_image_path = str(seed_crop_path)
        args.seed_request_image_shape = (crop_transform.input_height_px, crop_transform.input_width_px, 3)

    seed_context_sheet_path: Path | None = None
    if args.seed_context_frame_index is not None:
        if args.seed_context_frame_index < 0 or args.seed_context_frame_index >= len(frames_bgr):
            raise SystemExit(
                f"--seed-context-frame-index must be within 0..{len(frames_bgr) - 1}, "
                f"got {args.seed_context_frame_index}"
            )
        seed_context_sheet_path = out_dir / "seed_context_sheet.jpg"
        write_seed_context_sheet(
            frames_bgr[0],
            frames_bgr[args.seed_context_frame_index],
            args.seed_context_frame_index,
            seed_context_sheet_path,
            hint_quad=hint_quad_for_seed,
        )
        args.seed_context_sheet_path = str(seed_context_sheet_path)
        args.seed_context_frame_index_resolved = args.seed_context_frame_index

    seed = get_seed(paths[0], frames_bgr[0].shape, args)
    seed_path = out_dir / "seed.json"
    seed_path.write_text(json.dumps(asdict(seed), indent=2), encoding="utf-8")
    draw_seed_overlay(frames_bgr[0], seed, out_dir / "seed_overlay.jpg")

    seed_quad = np.asarray(seed.quad_uv, dtype=np.float32)
    quads, track_steps = track_quad_lk_homography(frames_bgr, seed_quad, args)

    observations: list[FrameObservation] = []
    for idx, (img, q) in enumerate(zip(frames_bgr, quads)):
        height_px, width_px = quad_height_width(q)
        inside, margin = image_inside_stats(q, img.shape)
        observations.append(FrameObservation(
            frame=idx,
            quad_uv=np.round(q, 3).tolist(),
            height_px=height_px,
            width_px=width_px,
            fully_inside_image=inside,
            min_border_margin_px=margin,
        ))

    valid = np.array([o.fully_inside_image for o in observations], dtype=bool)
    frame_ids = np.array([o.frame for o in observations], dtype=float)
    heights = np.array([o.height_px for o in observations], dtype=float)
    widths = np.array([o.width_px for o in observations], dtype=float)

    valid_frame_ids = frame_ids[valid]
    valid_heights = heights[valid]
    valid_widths = widths[valid]

    height_fit = fit_inverse_size(valid_heights, valid_frame_ids, "height")
    width_fit = fit_inverse_size(valid_widths, valid_frame_ids, "width")
    height_roll = rolling_next_predictions(valid_heights)
    width_roll = rolling_next_predictions(valid_widths)
    edge_support = [
        edge_support_for_frame(frame, quad, index, args)
        for index, (frame, quad) in enumerate(zip(frames_bgr, quads))
    ]
    edge_locks = [
        edge_lock_for_frame(frame, quad, index, args)
        for index, (frame, quad) in enumerate(zip(frames_bgr, quads))
    ]
    edge_side_rows = [
        side
        for frame_support in edge_support
        for side in frame_support.side_support
    ]
    edge_lock_side_rows = [
        side
        for frame_lock in edge_locks
        for side in frame_lock.side_locks
    ]
    edge_summary = summarize_edge_support(edge_support)
    edge_lock_summary = summarize_edge_locks(edge_locks)

    write_csv(out_dir / "box_observations.csv", observations)
    write_csv(out_dir / "height_rolling_predictions.csv", height_roll)
    write_csv(out_dir / "width_rolling_predictions.csv", width_roll)
    write_csv(out_dir / "edge_side_support.csv", edge_side_rows)
    write_csv(out_dir / "edge_side_lock.csv", edge_lock_side_rows)
    draw_overlay_sheet(frames_bgr, quads, out_dir / "quad_tracking_overlay.jpg")
    draw_edge_support_sheet(frames_bgr, quads, edge_support, edge_locks, out_dir / "edge_support_overlay.jpg")
    draw_fit_plot(valid_frame_ids, valid_heights, valid_widths, height_fit, width_fit, out_dir / "height_width_fit.png")

    report = {
        "method": "seeded face tracking with Shi-Tomasi + PyrLK + RANSAC homography; inverse-size step fit",
        "seed": asdict(seed),
        "seed_image_transform": asdict(args.seed_crop_transform) if getattr(args, "seed_crop_transform", None) else None,
        "vlm_used": seed.provider in {"openai", "mulerouter", "command"},
        "manual_seed_used": seed.provider == "manual",
        "frames": [str(path) for path in paths],
        "valid_frames_for_direct_measurement": [int(o.frame) for o in observations if o.fully_inside_image],
        "invalid_or_extrapolated_frames": [int(o.frame) for o in observations if not o.fully_inside_image],
        "height_fit": asdict(height_fit),
        "width_fit": asdict(width_fit),
        "height_rolling_prediction_stats": prediction_stats(height_roll),
        "width_rolling_prediction_stats": prediction_stats(width_roll),
        "edge_support_summary": edge_summary,
        "edge_support": [asdict(frame_support) for frame_support in edge_support],
        "edge_lock_summary": edge_lock_summary,
        "edge_lock": [asdict(frame_lock) for frame_lock in edge_locks],
        "track_steps": [asdict(step) for step in track_steps],
        "observations": [asdict(obs) for obs in observations],
        "output_files": {
            "seed": str(seed_path),
            "seed_context_sheet": str(seed_context_sheet_path) if seed_context_sheet_path else None,
            "seed_crop_input": str(seed_crop_path) if seed_crop_path else None,
            "seed_overlay": str(out_dir / "seed_overlay.jpg"),
            "quad_tracking_overlay": str(out_dir / "quad_tracking_overlay.jpg"),
            "edge_support_overlay": str(out_dir / "edge_support_overlay.jpg"),
            "height_width_fit": str(out_dir / "height_width_fit.png"),
            "box_observations": str(out_dir / "box_observations.csv"),
            "edge_side_support": str(out_dir / "edge_side_support.csv"),
            "edge_side_lock": str(out_dir / "edge_side_lock.csv"),
            "height_rolling_predictions": str(out_dir / "height_rolling_predictions.csv"),
            "width_rolling_predictions": str(out_dir / "width_rolling_predictions.csv"),
            "report": str(out_dir / "report.json"),
        },
        "interpretation": {
            "z0_steps": "Estimated initial distance to the measured box plane in forward-pulse units.",
            "size_px_steps": "Pixel-size-times-step constant; this stays relative unless camera intrinsics/physical box size are added.",
            "coefficient_of_variation": "Std/mean of recovered constant size across valid frames. Lower means the pulse-step model is internally consistent.",
            "edge_support": "Local Canny/Sobel check for whether the propagated quad sides have nearby oriented image edges. It is diagnostic; it does not change the fitted dimensions unless used manually.",
            "edge_lock": "Data-derived stricter check that scans parallel candidate edges near each proposed side and fails sides whose strongest edge support is offset away from the proposed quad.",
            "seed_context_sheet": "Optional two-panel VLM input: frame 0 is the coordinate target and the later closer frame is evidence only.",
        },
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track a seeded box face and fit relative step consistency.")
    parser.add_argument("--image-dir", required=True)
    parser.add_argument("--pattern", default="frame_*.jpg")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--max-frames", type=int, default=None)

    parser.add_argument("--seed-provider", choices=["manual", "command", "openai", "mulerouter"], default="manual")
    parser.add_argument("--seed-quad", default=None,
                        help="JSON quad [[x_tl,y_tl],[x_tr,y_tr],[x_br,y_br],[x_bl,y_bl]].")
    parser.add_argument("--seed-json", default=None,
                        help="Use an existing seed.json and skip seed-provider execution.")
    parser.add_argument("--seed-context-frame-index", type=int, default=None,
                        help="Build a two-panel VLM seed image with frame 0 plus this later frame as context.")
    parser.add_argument("--seed-target-hint-json", default=None,
                        help="Draw this earlier seed.json as a rough target hint on the context sheet.")
    parser.add_argument("--seed-crop-source", choices=["none", "hint", "center"], default="none",
                        help="Send an upscaled crop to the VLM, then map returned crop coordinates back to frame 0.")
    parser.add_argument("--seed-crop-size-px", type=int, default=220,
                        help="Square crop size for --seed-crop-source center.")
    parser.add_argument("--seed-crop-min-size-px", type=int, default=180,
                        help="Minimum original-frame crop size for --seed-crop-source hint.")
    parser.add_argument("--seed-crop-pad-frac", type=float, default=1.25,
                        help="Padding around hint bbox, as a fraction of the largest hint dimension.")
    parser.add_argument("--seed-crop-output-size-px", type=int, default=768,
                        help="Upscaled square image size sent to the VLM.")
    parser.add_argument("--target-description", default="the most trackable visible cardboard box face")
    parser.add_argument("--seed-command", default=None,
                        help="External command that receives image_path width height target_description and prints seed JSON.")
    parser.add_argument("--vlm-model", default=os.environ.get("STEP_CALIBRATION_VLM_MODEL"))
    parser.add_argument(
        "--vlm-base-url",
        default=os.environ.get("MULEROUTER_BASE_URL", "https://api.mulerouter.ai/vendors/openai/v1"),
    )
    parser.add_argument("--vlm-timeout-s", type=float, default=60.0)
    parser.add_argument("--track-mask-erode-px", type=int, default=0)
    parser.add_argument("--homography-max-corners", type=int, default=200)
    parser.add_argument("--homography-quality", type=float, default=0.005)
    parser.add_argument("--homography-min-distance", type=float, default=3.0)
    parser.add_argument("--homography-ransac-px", type=float, default=2.0)
    parser.add_argument("--lk-max-error", type=float, default=30.0)
    parser.add_argument("--lk-fb-max-error", type=float, default=1.5)
    parser.add_argument("--edge-search-px", type=int, default=8)
    parser.add_argument("--edge-sample-spacing", type=float, default=4.0)
    parser.add_argument("--edge-max-samples", type=int, default=120)
    parser.add_argument("--edge-canny-low", type=int, default=35)
    parser.add_argument("--edge-canny-high", type=int, default=110)
    parser.add_argument("--edge-min-alignment", type=float, default=0.40)
    parser.add_argument("--edge-min-side-support", type=float, default=0.20)
    parser.add_argument("--edge-min-frame-support", type=float, default=0.35)
    parser.add_argument("--edge-lock-search-px", type=int, default=10)
    parser.add_argument("--edge-lock-zero-band-px", type=float, default=1.0)
    parser.add_argument("--edge-lock-pixel-radius", type=int, default=1)
    parser.add_argument("--edge-lock-max-offset-px", type=float, default=4.0)
    parser.add_argument("--edge-lock-min-support", type=float, default=0.30)
    parser.add_argument("--edge-lock-min-peak-ratio", type=float, default=0.45)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run(args)
    print(json.dumps({
        "out_dir": str(Path(report["output_files"]["report"]).parent),
        "seed_provider": report["seed"]["provider"],
        "seed_confidence": report["seed"]["confidence"],
        "valid_frames": report["valid_frames_for_direct_measurement"],
        "invalid_or_extrapolated_frames": report["invalid_or_extrapolated_frames"],
        "height_fit": report["height_fit"],
        "width_fit": report["width_fit"],
        "height_rolling_prediction_stats": report["height_rolling_prediction_stats"],
        "width_rolling_prediction_stats": report["width_rolling_prediction_stats"],
        "edge_support_summary": report["edge_support_summary"],
        "edge_lock_summary": report["edge_lock_summary"],
        "report": report["output_files"]["report"],
        "seed_overlay": report["output_files"]["seed_overlay"],
        "quad_tracking_overlay": report["output_files"]["quad_tracking_overlay"],
        "edge_support_overlay": report["output_files"]["edge_support_overlay"],
        "height_width_fit": report["output_files"]["height_width_fit"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
