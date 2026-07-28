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
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if normalized < 0 or normalized > MAX_SAFE_INT:
        raise ValueError(f"{field_name} must be in 0..{MAX_SAFE_INT}")
    return normalized


def proposal_id_for(plugin_id: str, frame_id: str) -> str:
    return f"{plugin_id}:{frame_id}"


def plan_id_for(frame_id: str) -> str:
    return f"action-plan:{frame_id}"


def require_code_point_len(value: str, *, field_name: str, max_len: int) -> str:
    if len(value) > max_len:
        raise ValueError(f"{field_name} exceeds {max_len} code points")
    return value
