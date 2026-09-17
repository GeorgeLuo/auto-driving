#!/usr/bin/env python3
"""Render the recorded selector comparison as readable image evidence."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from evaluate_marginal_value import _labels_by_frame, _read_json, _selectors  # noqa: E402


REFERENCE_COLOR = (255, 225, 45)
METHOD_COLORS = {
    "covering_union": (65, 160, 255),
    "robust_extent": (255, 145, 35),
    "handwritten_selector": (205, 110, 255),
    "jev": (255, 55, 180),
}
SOURCE_COLORS = {
    "classical_regions": (45, 220, 105),
    "canny_morph": (65, 190, 255),
    "quad_rectangularity": (105, 225, 85),
    "partial_contour": (255, 155, 45),
    "photometric_windows": (190, 115, 255),
    "hough_fragments": (255, 220, 55),
}
SHORT_SOURCE = {
    "classical_regions": "classical",
    "canny_morph": "Canny",
    "quad_rectangularity": "quad",
    "partial_contour": "partial",
    "photometric_windows": "photo",
    "hough_fragments": "Hough",
}


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _pixel_box(box: dict[str, Any], size: tuple[int, int]) -> tuple[int, int, int, int]:
    values = box["bbox_xyxy_norm"]
    width, height = size
    return tuple(
        int(round(float(value) * (axis - 1)))
        for value, axis in zip(values, (width, height, width, height))
    )


def _draw_dashed_rectangle(
    draw: ImageDraw.ImageDraw,
    rectangle: tuple[int, int, int, int],
    color: tuple[int, int, int],
    width: int,
    dash: int = 16,
) -> None:
    x1, y1, x2, y2 = rectangle
    for start in range(x1, x2, dash * 2):
        draw.line((start, y1, min(start + dash, x2), y1), fill=color, width=width)
        draw.line((start, y2, min(start + dash, x2), y2), fill=color, width=width)
    for start in range(y1, y2, dash * 2):
        draw.line((x1, start, x1, min(start + dash, y2)), fill=color, width=width)
        draw.line((x2, start, x2, min(start + dash, y2)), fill=color, width=width)


def _label_box(
    draw: ImageDraw.ImageDraw,
    rectangle: tuple[int, int, int, int],
    text: str,
    color: tuple[int, int, int],
    font: ImageFont.ImageFont,
    canvas_size: tuple[int, int] | None = None,
) -> None:
    x1, y1, x2, _ = rectangle
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_width = right - left
    text_height = bottom - top
    canvas_width = canvas_size[0] if canvas_size else x1 + text_width + 12
    x = max(4, min(x1, canvas_width - text_width - 12))
    y = max(4, y1 - text_height - 12)
    draw.rounded_rectangle(
        (x, y, x + text_width + 10, y + text_height + 8),
        radius=4,
        fill=(8, 10, 14),
        outline=color,
        width=2,
    )
    draw.text((x + 5, y + 3), text, fill=color, font=font)


def _draw_reference(
    draw: ImageDraw.ImageDraw,
    box: dict[str, Any],
    size: tuple[int, int],
    font: ImageFont.ImageFont,
    index: int,
) -> None:
    rectangle = _pixel_box(box, size)
    _draw_dashed_rectangle(draw, rectangle, REFERENCE_COLOR, max(3, size[0] // 360), dash=18)
    _label_box(draw, rectangle, f"GT{index}", REFERENCE_COLOR, font, size)


def _draw_prediction(
    draw: ImageDraw.ImageDraw,
    box: dict[str, Any],
    size: tuple[int, int],
    color: tuple[int, int, int],
    font: ImageFont.ImageFont,
    index: int,
    *,
    label: bool = True,
) -> None:
    rectangle = _pixel_box(box, size)
    draw.rectangle(rectangle, outline=color, width=max(3, size[0] // 360))
    if label:
        kind = str(box.get("kind", "box"))
        probability = box.get("probability")
        confidence = box.get("confidence")
        score = probability if probability is not None else confidence
        score_text = f" {float(score):.2f}" if score is not None else ""
        _label_box(draw, rectangle, f"{index} {kind}{score_text}", color, font, size)


def _raw_panel_boxes(findings: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in findings.get("raw_boxes", []) if isinstance(item, dict)]


def _draw_panel(
    image: Image.Image,
    title: str,
    reference: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    width: int,
    prediction_color: tuple[int, int, int] | None,
    source_colored: bool = False,
    label_predictions: bool = True,
) -> Image.Image:
    panel_height = round(width * image.height / image.width)
    header_height = 92
    resized = image.resize((width, panel_height), Image.Resampling.LANCZOS)
    output = Image.new("RGB", (width, panel_height + header_height), (9, 12, 17))
    output.paste(resized, (0, header_height))
    draw = ImageDraw.Draw(output)
    title_font = _font(30, bold=True)
    label_font = _font(21, bold=True)
    title_color = prediction_color or (235, 240, 245)
    draw.text((18, 17), title, fill=title_color, font=title_font)
    for index, box in enumerate(reference, start=1):
        rectangle = _pixel_box(box, (width, panel_height))
        rectangle = tuple(value + (header_height if position in (1, 3) else 0) for position, value in enumerate(rectangle))
        _draw_dashed_rectangle(draw, rectangle, REFERENCE_COLOR, max(3, width // 360), dash=18)
        _label_box(
            draw,
            rectangle,
            f"GT{index}",
            REFERENCE_COLOR,
            label_font,
            (width, panel_height + header_height),
        )
    for index, box in enumerate(predictions, start=1):
        source = str(box.get("source", "cv"))
        color = SOURCE_COLORS.get(source, (235, 235, 235)) if source_colored else prediction_color
        if color is None:
            color = (235, 235, 235)
        rectangle = _pixel_box(box, (width, panel_height))
        rectangle = tuple(value + (header_height if position in (1, 3) else 0) for position, value in enumerate(rectangle))
        draw.rectangle(rectangle, outline=color, width=max(2, width // 450))
        if label_predictions:
            kind = SHORT_SOURCE.get(source, source) if source_colored else str(box.get("kind", "box"))
            score = box.get("probability") if box.get("probability") is not None else box.get("confidence")
            score_text = f" {float(score):.2f}" if score is not None else ""
            _label_box(
                draw,
                rectangle,
                f"{index} {kind}{score_text}",
                color,
                label_font,
                (width, panel_height + header_height),
            )
    if source_colored:
        legend = "  ".join(
            f"{SHORT_SOURCE.get(source, source)}"
            for source in ("canny_morph", "classical_regions", "partial_contour", "photometric_windows", "quad_rectangularity", "hough_fragments")
        )
        draw.text((18, 57), legend, fill=(205, 212, 220), font=_font(17))
    else:
        draw.text((18, 57), "yellow dashed = approximate visible-object envelope", fill=(205, 212, 220), font=_font(17))
    return output


def _metrics(report: dict[str, Any], method: str, frame: str) -> dict[str, Any]:
    for item in report.get("methods", []):
        if item.get("method") != method:
            continue
        for frame_item in item.get("per_frame", []):
            if frame_item.get("frame") == frame:
                return frame_item
    return {}


def _metric_title(label: str, metric: dict[str, Any]) -> str:
    return (
        f"{label}  |  {metric.get('matched_count', 0)}/{metric.get('ground_truth_count', 0)} matched  "
        f"| {metric.get('unmatched_prediction_count', 0)} unmatched"
    )


def render_frame(
    image: Image.Image,
    frame: str,
    findings: dict[str, Any],
    labels: list[dict[str, Any]],
    report: dict[str, Any],
    width: int,
) -> Image.Image:
    selectors = _selectors(findings)
    raw = _raw_panel_boxes(findings)
    panels = [
        _draw_panel(
            image,
            "REFERENCE ENVELOPES",
            labels,
            [],
            width=width,
            prediction_color=REFERENCE_COLOR,
            label_predictions=True,
        ),
        _draw_panel(
            image,
            f"ALL CV CUES  |  {len(raw)} raw boxes",
            labels,
            raw,
            width=width,
            prediction_color=None,
            source_colored=True,
            label_predictions=False,
        ),
    ]
    for method, label in (
        ("covering_union", "COVERING UNION"),
        ("robust_extent", "ROBUST EXTENT"),
        ("handwritten_selector", "HANDWRITTEN HEURISTIC"),
        ("jev", "JEV SELECTION"),
    ):
        metric = _metrics(report, method, frame)
        panel_label = _metric_title(label, metric)
        panels.append(
            _draw_panel(
                image,
                panel_label,
                labels,
                selectors[method],
                width=width,
                prediction_color=METHOD_COLORS[method],
                label_predictions=True,
            )
        )
    columns = 3
    gap = 18
    panel_width = max(panel.width for panel in panels)
    panel_height = max(panel.height for panel in panels)
    rows = math.ceil(len(panels) / columns)
    canvas = Image.new(
        "RGB",
        (columns * panel_width + gap * (columns - 1), rows * panel_height + gap * (rows - 1) + 58),
        (5, 7, 10),
    )
    draw = ImageDraw.Draw(canvas)
    draw.text((18, 13), f"{frame}  —  selector comparison", fill=(255, 255, 255), font=_font(31, bold=True))
    draw.text((18, 44), "Yellow dashed outlines are approximate manual reference envelopes; colored outlines are selector outputs.", fill=(205, 212, 220), font=_font(17))
    top = 58
    for index, panel in enumerate(panels):
        x = (index % columns) * (panel_width + gap)
        y = top + (index // columns) * (panel_height + gap)
        canvas.paste(panel, (x, y))
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-dir", type=Path, required=True)
    parser.add_argument("--findings-dir", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1000)
    args = parser.parse_args()
    if args.width < 600:
        parser.error("--width must be at least 600")
    label_map = _labels_by_frame(_read_json(args.labels))
    report = _read_json(args.report)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    for findings_path in sorted(args.findings_dir.glob("IMG_*.json")):
        frame = f"{findings_path.stem}.JPG".upper()
        image_path = args.image_dir / frame
        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        findings = _read_json(findings_path)
        image = Image.open(image_path).convert("RGB")
        rendered = render_frame(image, frame, findings, label_map[frame], report, args.width)
        output_path = args.output_dir / f"{findings_path.stem}_selector_comparison.jpg"
        rendered.save(output_path, quality=92, optimize=True, progressive=True)
        outputs.append(str(output_path))
    if not outputs:
        raise ValueError(f"no findings in {args.findings_dir}")
    print(json.dumps({"outputs": outputs}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
