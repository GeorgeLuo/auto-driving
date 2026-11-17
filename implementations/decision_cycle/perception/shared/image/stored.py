"""An image array stored in shared memory as JSON."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any

import numpy as np

from autonomy.fields import FieldError, require_keys, require_safe_int, require_text


@dataclass(eq=False)
class StoredImage:
    """Pixels a shared-memory document can round-trip.

    ``from_dict`` returns a writable copy. The buffer behind a decoded
    document is not the array a later frame may mutate.
    """

    pixels: np.ndarray

    def to_dict(self) -> dict[str, Any]:
        array = self.pixels
        return {
            "dtype": array.dtype.str,
            "shape": [int(dim) for dim in array.shape],
            "base64": base64.b64encode(array.tobytes()).decode("ascii"),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StoredImage:
        document = require_keys(data, ("dtype", "shape", "base64"))
        dtype_name = require_text(document["dtype"], field_name="dtype")
        try:
            dtype = np.dtype(dtype_name)
        except (TypeError, ValueError) as exc:
            raise FieldError("dtype", str(exc)) from exc
        if dtype.hasobject or dtype.itemsize == 0 or dtype.str != dtype_name:
            raise FieldError("dtype", "must be a canonical, fixed-size, non-object dtype")
        dimensions = document["shape"]
        if not isinstance(dimensions, (list, tuple)):
            raise FieldError("shape", "must be an array")
        shape = tuple(require_safe_int(dim, field_name=f"shape.{index}") for index, dim in enumerate(dimensions))
        encoded = document["base64"]
        if not isinstance(encoded, str):
            raise FieldError("base64", "must be a string")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise FieldError("base64", str(exc)) from exc
        if base64.b64encode(raw).decode("ascii") != encoded:
            raise FieldError("base64", "must be canonical base64")
        expected = dtype.itemsize
        for dim in shape:
            expected *= dim
        if len(raw) != expected:
            raise FieldError("base64", "byte length must match dtype and shape")
        try:
            pixels = np.array(np.frombuffer(raw, dtype=dtype).reshape(shape), copy=True)
        except (ValueError, OverflowError) as exc:
            raise FieldError("shape", str(exc)) from exc
        return cls(pixels)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, StoredImage):
            return NotImplemented
        return (self.pixels.dtype == other.pixels.dtype and self.pixels.shape == other.pixels.shape
                and self.pixels.tobytes() == other.pixels.tobytes())
