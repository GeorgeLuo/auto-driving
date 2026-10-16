"""An image array stored in shared memory as JSON."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(eq=False)
class StoredImage:
    """Pixels a shared-memory document can round-trip.

    ``from_dict`` returns a writable copy. The buffer behind a decoded
    document is not the array a later frame may mutate.
    """

    pixels: np.ndarray

    def to_dict(self) -> dict[str, Any]:
        array = np.ascontiguousarray(self.pixels)
        return {
            "dtype": array.dtype.str,
            "shape": [int(dim) for dim in array.shape],
            "base64": base64.b64encode(array.tobytes()).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoredImage:
        if not isinstance(data, dict):
            raise ValueError("stored image data must be an object")
        try:
            dtype = np.dtype(data["dtype"])
            shape = tuple(int(dim) for dim in data["shape"])
            raw = base64.b64decode(data["base64"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"stored image data is invalid: {exc}") from exc
        if dtype.hasobject or any(dim < 0 for dim in shape):
            raise ValueError("stored image dtype or shape is invalid")
        expected = int(dtype.itemsize)
        for dim in shape:
            expected *= int(dim)
        if len(raw) != expected:
            raise ValueError("stored image byte length does not match dtype and shape")
        pixels = np.array(np.frombuffer(raw, dtype=dtype).reshape(shape), copy=True)
        return cls(pixels)
