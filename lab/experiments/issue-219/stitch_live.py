"""Render readable per-detector and aggregate CV-vs-Jev comparison panels."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont


def _pixel_bbox(box: list[float] | tuple[float, ...], size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    return (
        int(round(float(box[0]) * max(width - 1, 1))),
        int(round(float(box[1]) * max(height - 1, 1))),
        int(round(float(box[2]) * max(width - 1, 1))),
        int(round(float(box[3]) * max(height - 1, 1))),
    )


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def _source_color(source: str) -> tuple[int, int, int]:
    return {
        "classical_regions": (45, 220, 105),
        "edge_rectangles": (225, 90, 245),
        "hsv_components": (255, 170, 35),
        "canny_morph": (65, 190, 255),
        "quad_rectangularity": (105, 225, 85),
        "partial_contour": (255, 155, 45),
        "photometric_windows": (190, 115, 255),
        "hough_fragments": (255, 220, 55),
        "floor_continuity": (255, 125, 45),
        "floor_plane": (255, 215, 45),
        "motion_tracks": (55, 180, 255),
    }.get(source, (235, 235, 235))


def _short_source(source: str) -> str:
    return {
        "classical_regions": "classical",
        "edge_rectangles": "edge",
        "hsv_components": "HSV",
        "canny_morph": "Canny",
        "quad_rectangularity": "quad",
        "partial_contour": "partial",
        "photometric_windows": "photo",
        "hough_fragments": "Hough",
        "floor_continuity": "floor",
        "floor_plane": "plane",
        "motion_tracks": "motion",
    }.get(source, source)


def _draw_box(
    draw: ImageDraw.ImageDraw,
    box: dict,
    size: tuple[int, int],
    *,
    offset: tuple[int, int] = (0, 0),
    color: tuple[int, int, int],
    label: str,
    width: int,
    label_font: ImageFont.ImageFont,
) -> None:
    raw_box = _pixel_bbox(box["bbox_xyxy_norm"], size)
    pixel_box = tuple(
        coordinate + delta
        for coordinate, delta in zip(raw_box, (offset[0], offset[1], offset[0], offset[1]))
    )
    draw.rectangle(pixel_box, outline=color, width=width)
    text_width, text_height = _text_size(draw, label, label_font)
    padding_x = max(7, width)
    padding_y = max(4, width // 2)
    label_width = text_width + (padding_x * 2)
    label_height = text_height + (padding_y * 2)
    x1, y1, x2, y2 = pixel_box
    x = max(0, min(x1, size[0] - label_width))
    y = y1 - label_height - 2
    if y < 0:
        y = min(y2 + 2, max(0, size[1] - label_height))
    draw.rounded_rectangle(
        (x, y, x + label_width, y + label_height),
        radius=max(3, width),
        fill=(8, 10, 13),
        outline=color,
        width=max(1, width // 2),
    )
    draw.text((x + padding_x, y + padding_y - 1), label, fill=color, font=label_font)


def _panel(
    image: Image.Image,
    title: str,
    boxes: list[dict],
    *,
    scale: float,
    title_color: tuple[int, int, int],
    label_for: Callable[[dict], str],
    color_for: Callable[[dict], tuple[int, int, int]],
    legend: str | None = None,
) -> Image.Image:
    image_width = max(1, int(round(image.width * scale)))
    image_height = max(1, int(round(image.height * scale)))
    header_height = max(72, int(round(58 * scale)))
    resized = image.resize((image_width, image_height), Image.Resampling.LANCZOS)
    output = Image.new("RGB", (image_width, image_height + header_height), (20, 24, 29))
    output.paste(resized, (0, header_height))
    draw = ImageDraw.Draw(output)
    title_font = _font(max(24, int(round(22 * scale))), bold=True)
    label_font = _font(max(20, int(round(17 * scale))), bold=True)
    draw.text((18, 12), title, fill=title_color, font=title_font)
    if legend:
        legend_font = _font(max(16, int(round(13 * scale))))
        legend_width, _ = _text_size(draw, legend, legend_font)
        draw.text(
            (max(18, output.width - legend_width - 18), 16),
            legend,
            fill=(205, 212, 220),
            font=legend_font,
        )
    for box in boxes:
        _draw_box(
            draw,
            box,
            (image_width, image_height),
            offset=(0, header_height),
            color=color_for(box),
            label=label_for(box),
            width=max(4, int(round(3 * scale))),
            label_font=label_font,
        )
    return output


def _grid(panels: list[Image.Image], *, columns: int = 3, gap: int = 18) -> Image.Image:
    if not panels:
        raise ValueError("at least one panel is required")
    columns = max(1, min(columns, len(panels)))
    rows = math.ceil(len(panels) / columns)
    panel_width = max(panel.width for panel in panels)
    panel_height = max(panel.height for panel in panels)
    canvas = Image.new(
        "RGB",
        (columns * panel_width + (columns - 1) * gap, rows * panel_height + (rows - 1) * gap),
        (8, 11, 15),
    )
    for index, panel in enumerate(panels):
        x = (index % columns) * (panel_width + gap)
        y = (index // columns) * (panel_height + gap)
        canvas.paste(panel, (x, y))
    return canvas


def _jev_boxes(findings: dict) -> list[dict]:
    boxes: list[dict] = []
    for item in findings.get("jev", {}).get("selected", []):
        hypothesis = item.get("hypothesis")
        if not hypothesis:
            continue
        boxes.append(
            {
                "bbox_xyxy_norm": hypothesis["bbox_xyxy_norm"],
                "probability": item.get("probability", 0.0),
                "kind": hypothesis.get("kind", "aggregate"),
                "source_names": hypothesis.get("source_names", []),
            }
        )
    return boxes


def _render_strategy_grid(
    findings: dict,
    image: Image.Image,
    *,
    scale: float,
    columns: int = 3,
) -> Image.Image:
    raw_boxes = findings.get("raw_boxes", [])
    detectors = list(findings.get("detectors") or [])
    if not detectors:
        detectors = sorted({item.get("source", "unknown") for item in raw_boxes})

    panels: list[Image.Image] = []
    panels.append(
        _panel(
            image,
            "INPUT FRAME",
            [],
            scale=scale,
            title_color=(255, 255, 255),
            label_for=lambda _: "",
            color_for=lambda _: (255, 255, 255),
        )
    )
    for detector in detectors:
        selected = [item for item in raw_boxes if item.get("source") == detector]
        panels.append(
            _panel(
                image,
                f"{detector}  —  {len(selected)} boxes",
                selected,
                scale=scale,
                title_color=_source_color(detector),
                label_for=lambda item, detector=detector: f"{_short_source(detector)}  {float(item.get('confidence', 0.0)):.2f}",
                color_for=lambda item, detector=detector: _source_color(detector),
            )
        )

    panels.append(
        _panel(
            image,
            f"ALL CV CUES  —  {len(raw_boxes)} raw boxes",
            raw_boxes,
            scale=scale,
            title_color=(235, 240, 245),
            label_for=lambda item: f"{_short_source(str(item.get('source', 'cv')))}  {float(item.get('confidence', 0.0)):.2f}",
            color_for=lambda item: _source_color(str(item.get("source", "cv"))),
            legend="classical / Canny / quad / partial / photo / Hough",
        )
    )

    jev_boxes = _jev_boxes(findings)
    panels.append(
        _panel(
            image,
            f"JEV AGGREGATE  —  {len(jev_boxes)} boundaries",
            jev_boxes,
            scale=scale,
            title_color=(255, 70, 195),
            label_for=lambda item: f"Jev  {float(item.get('probability', 0.0)):.2f}",
            color_for=lambda item: (255, 70, 195),
        )
    )
    return _grid(panels, columns=columns, gap=max(18, int(round(12 * scale))))


def _render_pair(
    findings: dict,
    image: Image.Image,
    *,
    scale: float,
    label: str,
) -> Image.Image:
    raw_boxes = findings.get("raw_boxes", [])
    jev_boxes = _jev_boxes(findings)
    panels = [
        _panel(
            image,
            f"ALL CV CUES  —  {len(raw_boxes)} raw boxes",
            raw_boxes,
            scale=scale,
            title_color=(235, 240, 245),
            label_for=lambda item: f"{_short_source(str(item.get('source', 'cv')))}  {float(item.get('confidence', 0.0)):.2f}",
            color_for=lambda item: _source_color(str(item.get("source", "cv"))),
            legend="raw heterogeneous detector evidence",
        ),
        _panel(
            image,
            f"JEV AGGREGATE  —  {len(jev_boxes)} boundaries",
            jev_boxes,
            scale=scale,
            title_color=(255, 70, 195),
            label_for=lambda item: f"Jev  {float(item.get('probability', 0.0)):.2f}",
            color_for=lambda item: (255, 70, 195),
            legend="selected fused boundaries",
        ),
    ]
    pair = _grid(panels, columns=2, gap=max(24, int(round(18 * scale))))
    header_height = max(92, int(round(72 * scale)))
    output = Image.new("RGB", (pair.width, pair.height + header_height), (8, 11, 15))
    output.paste(pair, (0, header_height))
    draw = ImageDraw.Draw(output)
    title_font = _font(max(30, int(round(28 * scale))), bold=True)
    detail_font = _font(max(18, int(round(16 * scale))))
    draw.text((24, 16), label, fill=(255, 255, 255), font=title_font)
    detail = "CV-only evidence  →  Jev aggregate"
    detail_width, _ = _text_size(draw, detail, detail_font)
    draw.text(
        (max(24, output.width - detail_width - 24), 25),
        detail,
        fill=(205, 212, 220),
        font=detail_font,
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--columns", type=int, default=3)
    parser.add_argument("--pair-only", action="store_true")
    parser.add_argument("--label", type=str, default="")
    args = parser.parse_args()
    if args.scale <= 0:
        raise SystemExit("--scale must be positive")
    if args.columns <= 0:
        raise SystemExit("--columns must be positive")
    run_dir = args.run_dir.resolve()
    output_dir = (args.output or run_dir / "stitched").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    panels: list[tuple[str, Image.Image]] = []
    for findings_path in sorted(run_dir.glob("results/frame_*/composite-box-fusion-v0/findings.json")):
        findings = json.loads(findings_path.read_text(encoding="utf-8"))
        source_path = findings.get("image", {}).get("source_path")
        if not source_path or not Path(source_path).is_file():
            continue
        image = Image.open(source_path).convert("RGB")
        if args.pair_only:
            detailed = _render_pair(
                findings,
                image,
                scale=args.scale,
                label=args.label or Path(source_path).name,
            )
        else:
            detailed = _render_strategy_grid(
                findings,
                image,
                scale=args.scale,
                columns=args.columns,
            )
        frame_id = findings_path.parents[1].name
        detailed_name = (
            f"{frame_id}_cv_vs_jev_pair.png"
            if args.pair_only
            else f"{frame_id}_cv_strategies_vs_jev.png"
        )
        detailed_path = output_dir / detailed_name
        detailed.save(detailed_path)
        detailed.save(
            output_dir / f"{frame_id}_cv_strategies_vs_jev.jpg",
            quality=88,
            optimize=True,
            progressive=True,
        )
        panels.append((Path(source_path).name, detailed))

    if not panels:
        raise SystemExit(f"no findings under {run_dir}")

    if len(panels) == 1:
        contact = panels[0][1]
    else:
        tile_width = max(panel.width for _, panel in panels)
        tile_height = max(panel.height for _, panel in panels) + 44
        contact = Image.new(
            "RGB",
            (tile_width, len(panels) * tile_height),
            (8, 11, 15),
        )
        for index, (name, panel) in enumerate(panels):
            contact.paste(panel, (0, index * tile_height + 34))
            ImageDraw.Draw(contact).text(
                (12, index * tile_height + 7),
                name,
                fill=(255, 255, 255),
                font=_font(24, bold=True),
            )
    output = output_dir / (
        "cv_vs_jev_pair.png" if args.pair_only else "contact_sheet_cv_vs_jev.png"
    )
    contact.save(output)
    jpeg_output = output_dir / "contact_sheet_cv_vs_jev.jpg"
    contact.save(jpeg_output, quality=88, optimize=True, progressive=True)
    print(output_dir)
    print(output)
    print(jpeg_output)


if __name__ == "__main__":
    main()
