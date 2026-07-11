from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def write_diff_artifact(before_path: Path, after_path: Path, diff_path: Path) -> str:
    before = Image.open(before_path).convert("L")
    after = Image.open(after_path).convert("L")
    if before.size != after.size:
        after = after.resize(before.size)
    diff = ImageChops.difference(before, after)
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff.save(diff_path)
    return str(diff_path)


def write_contact_sheet(path: Path, results: list[dict[str, Any]]) -> None:
    if not results:
        return

    tile_size = (240, 180)
    columns = 3
    rows = len(results)
    sheet = Image.new("RGB", (tile_size[0] * columns, tile_size[1] * rows), (18, 18, 18))

    for row, result in enumerate(results):
        instruction = result["instruction"]
        label = instruction["label"]
        before_path = Path(result["before_capture"]["path"])
        after_path = Path(result["after_capture"]["path"])
        diff_path = Path(result["diff_path"])
        status = "PASS" if result["passed"] else "FAIL"
        comparison = result["comparison"]

        tiles = [
            (_fit_tile(Image.open(before_path), tile_size), f"{row:02d} {label} before"),
            (_fit_tile(Image.open(after_path), tile_size), f"{row:02d} {label} after"),
            (
                _fit_tile(Image.open(diff_path), tile_size),
                f"{status} mean={comparison['mean_abs_diff_norm']:.5f} pix={comparison['changed_pixel_ratio']:.5f}",
            ),
        ]
        for column, (tile, text) in enumerate(tiles):
            _draw_label(tile, text)
            sheet.paste(tile, (column * tile_size[0], row * tile_size[1]))

    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path)


def _fit_tile(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    tile = Image.new("RGB", size, (28, 28, 28))
    copy = image.convert("RGB")
    copy.thumbnail((size[0], size[1] - 24))
    tile.paste(copy, ((size[0] - copy.width) // 2, 24 + (size[1] - 24 - copy.height) // 2))
    return tile


def _draw_label(tile: Image.Image, text: str) -> None:
    draw = ImageDraw.Draw(tile)
    draw.rectangle((0, 0, tile.width, 22), fill=(0, 0, 0))
    draw.text((6, 5), text[:80], fill=(255, 255, 255))
