from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def write_frames(root: Path, count: int = 3) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        image = np.full((240, 320, 3), 140, np.uint8)
        left = 60 + 15 * index
        cv2.rectangle(image, (left, 90), (left + 80, 200), (30, 30, 200), -1)
        cv2.imwrite(str(root / f"frame_{index}.png"), image)
    return root
