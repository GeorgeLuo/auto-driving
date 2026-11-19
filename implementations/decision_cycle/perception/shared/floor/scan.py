"""Bottom-up column scans for floor interruption boundaries."""

from __future__ import annotations

import numpy as np


def first_column_hits(
    bottom_floor: np.ndarray,
    bottom_valid: np.ndarray,
    floor_minimum: int,
    run_minimum: int,
    *,
    reset_at_gaps: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """First bottom-up run of valid occupied pixels after enough floor support."""

    height = bottom_floor.shape[0]
    if height < run_minimum:
        empty = np.empty(0, dtype=np.intp)
        return empty, empty, np.empty(0, dtype=np.int32)
    floor_prefix = np.cumsum(bottom_floor & bottom_valid, axis=0, dtype=np.int32)
    if reset_at_gaps:
        rows = np.arange(height, dtype=np.int32)[:, None]
        last_gap = np.maximum.accumulate(np.where(bottom_valid, -1, rows), axis=0)
        previous_floor = np.take_along_axis(floor_prefix, last_gap.clip(min=0), axis=0)
        floor_prefix -= np.where(last_gap == -1, 0, previous_floor)
    occupied = bottom_valid & ~bottom_floor
    occupied_prefix = np.empty((height + 1, occupied.shape[1]), dtype=np.int32)
    occupied_prefix[0] = 0
    np.cumsum(occupied, axis=0, dtype=np.int32, out=occupied_prefix[1:])
    eligible = occupied_prefix[run_minimum:] - occupied_prefix[:-run_minimum] == run_minimum
    eligible &= floor_prefix[: height - run_minimum + 1] >= floor_minimum
    columns = np.flatnonzero(eligible.any(axis=0))
    if columns.size == 0:
        empty = np.empty(0, dtype=np.intp)
        return empty, empty, np.empty(0, dtype=np.int32)
    starts = np.argmax(eligible[:, columns], axis=0)
    return columns, starts, floor_prefix[starts, columns]
