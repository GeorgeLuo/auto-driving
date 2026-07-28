"""ASCII identity grammar for shadow action proposals (M006)."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping
from copy import deepcopy
from typing import Any

from autonomy.decision.memory import ensure_strict_json_value

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


class FrozenJsonObject(Mapping[str, Any]):
    """Immutable JSON object storage that preserves object identity and deepcopies.

    Empty ``{}`` freezes to an empty FrozenJsonObject (not an empty sequence).
    Nested arrays freeze as tuples. Not a ``dict`` subclass, so accidental
    mutation APIs are unavailable; consumers read via Mapping protocol.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, object]) -> None:
        object.__setattr__(self, "_data", dict(data))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self._data) == dict(other)
        return NotImplemented

    def __hash__(self) -> int:  # type: ignore[override]
        # Unhashable: nested values may be lists after thaw; keep unhashable.
        raise TypeError("unhashable type: 'FrozenJsonObject'")

    def __repr__(self) -> str:
        return f"FrozenJsonObject({self._data!r})"

    def __deepcopy__(self, memo: dict[int, object]) -> FrozenJsonObject:
        cloned = FrozenJsonObject(
            {key: deepcopy(value, memo) for key, value in self._data.items()}
        )
        memo[id(self)] = cloned
        return cloned

    def to_plain(self) -> dict[str, Any]:
        return {key: frozen_json_to_plain(value) for key, value in self._data.items()}


def _is_json_primitive(value: object) -> bool:
    if value is None or isinstance(value, str):
        return True
    if type(value) is bool:
        return True
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    return False


def deep_freeze_json(value: object, *, field_name: str = "value") -> object:
    """Validate strict JSON, then freeze objects as FrozenJsonObject and arrays as tuples.

    Preserves object vs array identity exactly (empty {} stays mapping; empty []
    stays empty sequence). Rejects sets, non-string keys, and non-JSON types.
    """

    try:
        plain = ensure_strict_json_value(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not strict JSON: {exc}") from exc
    return _freeze_plain_json(plain, field_name=field_name)


def _freeze_plain_json(value: object, *, field_name: str) -> object:
    if isinstance(value, dict):
        frozen_items: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} JSON object keys must be strings")
            frozen_items[key] = _freeze_plain_json(item, field_name=f"{field_name}.{key}")
        return FrozenJsonObject(frozen_items)
    if isinstance(value, list):
        return tuple(
            _freeze_plain_json(item, field_name=f"{field_name}[]") for item in value
        )
    if _is_json_primitive(value):
        return value
    raise ValueError(f"{field_name} is not a JSON value: {type(value).__name__}")


def frozen_json_to_plain(value: object) -> object:
    """Convert frozen JSON storage back to plain dict/list for serialization."""

    if isinstance(value, FrozenJsonObject):
        return value.to_plain()
    if isinstance(value, Mapping) and not isinstance(value, dict):
        return {key: frozen_json_to_plain(item) for key, item in value.items()}
    if isinstance(value, dict):
        return {key: frozen_json_to_plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [frozen_json_to_plain(item) for item in value]
    if isinstance(value, list):
        return [frozen_json_to_plain(item) for item in value]
    return value


# Back-compat aliases used by earlier modules.
def deep_freeze(value: object) -> object:
    return deep_freeze_json(value)


def frozen_mapping_to_dict(value: object) -> object:
    return frozen_json_to_plain(value)


def proposal_id_for(plugin_id: str, frame_id: str) -> str:
    return f"{plugin_id}:{frame_id}"


def plan_id_for(frame_id: str) -> str:
    return f"action-plan:{frame_id}"


def require_code_point_len(value: str, *, field_name: str, max_len: int) -> str:
    if len(value) > max_len:
        raise ValueError(f"{field_name} exceeds {max_len} code points")
    return value
