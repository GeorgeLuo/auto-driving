"""Strict canonical JSON encoding and immutable JSON storage.

``canonical_json_utf8`` encodes a value with sorted keys and compact
separators. ``canonical_json_size_bytes`` is the UTF-8 length of that
encoding, also available as ``canonical_json_bytes``. ``ensure_strict_json_value``
round-trips a value through the same encoding. ``deep_freeze`` stores JSON
objects as ``FrozenJsonObject`` and JSON arrays as tuples.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import Any

_CANONICAL_JSON_SEPARATORS = (",", ":")


def _canonical_json_text(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=_CANONICAL_JSON_SEPARATORS,
        allow_nan=False,
    )


def _strict_json_error(exc: TypeError | ValueError) -> ValueError:
    return ValueError(
        f"value is not strictly JSON-serializable: {type(exc).__name__}: {exc}"
    )


def canonical_json_utf8(value: Any) -> bytes:
    """UTF-8 bytes of strict canonical JSON (sorted keys, compact separators).

    Rejects non-JSON types and non-finite numbers. Digests and equality use
    these bytes. ``canonical_json_size_bytes`` is their length.
    """

    try:
        return _canonical_json_text(value).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _strict_json_error(exc) from exc


def canonical_json_size_bytes(value: Any) -> int:
    """UTF-8 byte length of strict canonical JSON.

    Rejects non-JSON types and non-finite numbers. Callers must not retain values
    that cannot be measured with this path. Equal lengths are not equal content.
    """

    return len(canonical_json_utf8(value))


canonical_json_bytes = canonical_json_size_bytes


def ensure_strict_json_value(value: Any) -> Any:
    """Round-trip through strict JSON or raise ValueError."""

    try:
        return json.loads(_canonical_json_text(value))
    except (TypeError, ValueError) as exc:
        raise _strict_json_error(exc) from exc


class FrozenJsonObject(Mapping[str, Any]):
    """Immutable JSON object storage that preserves object identity and deepcopies.

    Empty ``{}`` freezes to an empty FrozenJsonObject (not an empty sequence).
    Nested arrays freeze as tuples. Internal storage is a sealed MappingProxyType;
    item assignment and attribute rebinding are rejected.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, object]) -> None:
        # Seal immediately: no mutable dict remains for ordinary mutation paths.
        object.__setattr__(self, "_data", MappingProxyType(dict(data)))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("FrozenJsonObject is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("FrozenJsonObject is immutable")

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
        raise TypeError("unhashable type: 'FrozenJsonObject'")

    def __repr__(self) -> str:
        return f"FrozenJsonObject({dict(self._data)!r})"

    def __deepcopy__(self, memo: dict[int, object]) -> FrozenJsonObject:
        cloned = FrozenJsonObject(
            {key: deepcopy(value, memo) for key, value in self._data.items()}
        )
        memo[id(self)] = cloned
        return cloned

    def to_plain(self) -> dict[str, Any]:
        return {key: frozen_mapping_to_dict(value) for key, value in self._data.items()}


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


def _require_string_object_keys(value: object, *, field_name: str) -> None:
    """Reject non-string mapping keys on the original input (before JSON dumps)."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(
                    f"{field_name} JSON object keys must be strings; "
                    f"got {type(key).__name__}"
                )
            _require_string_object_keys(item, field_name=f"{field_name}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _require_string_object_keys(item, field_name=f"{field_name}[{index}]")


def deep_freeze(value: object, *, field_name: str = "value") -> object:
    """Validate strict JSON, then freeze objects as FrozenJsonObject and arrays as tuples.

    Preserves object vs array identity exactly (empty {} stays mapping; empty []
    stays empty sequence). Rejects sets, non-string keys, and non-JSON types.
    """

    _require_string_object_keys(value, field_name=field_name)
    try:
        plain = ensure_strict_json_value(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is not strict JSON: {exc}") from exc
    return _freeze_plain_json(plain, field_name=field_name)


def _freeze_plain_json(value: object, *, field_name: str) -> object:
    if isinstance(value, dict):
        frozen_items: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
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


def frozen_mapping_to_dict(value: object) -> object:
    """Convert frozen JSON storage back to plain dict/list for serialization."""

    if isinstance(value, FrozenJsonObject):
        return value.to_plain()
    if isinstance(value, Mapping) and not isinstance(value, dict):
        return {key: frozen_mapping_to_dict(item) for key, item in value.items()}
    if isinstance(value, dict):
        return {key: frozen_mapping_to_dict(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [frozen_mapping_to_dict(item) for item in value]
    if isinstance(value, list):
        return [frozen_mapping_to_dict(item) for item in value]
    return value


__all__ = [
    "FrozenJsonObject",
    "canonical_json_bytes",
    "canonical_json_size_bytes",
    "canonical_json_utf8",
    "deep_freeze",
    "ensure_strict_json_value",
    "frozen_mapping_to_dict",
]
