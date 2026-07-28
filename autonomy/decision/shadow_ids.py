"""ASCII identity grammar for shadow action proposals (M006)."""

from __future__ import annotations

import re

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
MAX_ID_LEN = 64
MAX_SAFE_INT = 9_007_199_254_740_991  # 2**53 - 1


class ShadowCycleInputError(ValueError):
    """Raised when cycle identity is invalid before a cycle result is promised."""


def require_ascii_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field_name} must match ^[A-Za-z0-9._:-]{{1,64}}$; got {value!r}"
        )
    return value


def require_safe_int(value: object, *, field_name: str) -> int:
    # Reject bool (subclass of int), floats, and numeric strings.
    if type(value) is not int:
        raise ValueError(f"{field_name} must be a non-bool int; got {type(value).__name__}")
    if value < 0 or value > MAX_SAFE_INT:
        raise ValueError(f"{field_name} must be in 0..{MAX_SAFE_INT}")
    return value


def deep_freeze(value: object) -> object:
    """Recursively freeze mappings/sequences into immutable containers."""

    if isinstance(value, dict):
        return tuple(
            sorted(
                ((str(key), deep_freeze(item)) for key, item in value.items()),
                key=lambda pair: pair[0],
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((deep_freeze(item) for item in value), key=repr))
    return value


def frozen_mapping_to_dict(value: object) -> object:
    """Convert deep_freeze mapping tuples back to plain JSON-friendly data."""

    if isinstance(value, tuple) and value and isinstance(value[0], tuple) and len(value[0]) == 2:
        # Heuristic: mapping stored as sorted (key, value) pairs.
        try:
            return {
                key: frozen_mapping_to_dict(item)
                for key, item in value  # type: ignore[misc]
            }
        except (TypeError, ValueError):
            return tuple(frozen_mapping_to_dict(item) for item in value)
    if isinstance(value, tuple):
        return [frozen_mapping_to_dict(item) for item in value]
    return value


def proposal_id_for(plugin_id: str, frame_id: str) -> str:
    return f"{plugin_id}:{frame_id}"


def plan_id_for(frame_id: str) -> str:
    return f"action-plan:{frame_id}"


def require_code_point_len(value: str, *, field_name: str, max_len: int) -> str:
    if len(value) > max_len:
        raise ValueError(f"{field_name} exceeds {max_len} code points")
    return value
