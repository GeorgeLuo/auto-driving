"""Field rules for the documents Automa writes and reads back.

A document's ``from_dict`` accepts exactly what its ``to_dict`` writes. Every
key is present and ``None`` is written as ``null``. Identifiers are rejected,
never trimmed. Numbers are finite and never bools. Each rule raises
``FieldError`` with the field's path, so a reader can say which field is wrong.
``tests/deterministic/autonomy/test_documents.py`` holds every document to
these rules.

Messages from a simulator or another outside program follow that program's
shape. Parse them once into a typed value and serialize that value instead.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection, Mapping
from typing import TypeVar

ROOT = "$"
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
MAX_SAFE_INT = 9_007_199_254_740_991  # 2**53 - 1

T = TypeVar("T")


class FieldError(ValueError):
    """A field broke its rule. ``field_name`` is its dotted path, or ``$`` for the whole document."""

    def __init__(self, field_name: str, problem: str) -> None:
        super().__init__(f"{field_name} {problem}")
        self.field_name = field_name
        self.problem = problem


def require_keys(payload: object, keys: Collection[str], *, field_name: str = ROOT) -> Mapping[str, object]:
    """An object with exactly ``keys``. The error names the first missing or extra key."""

    if not isinstance(payload, Mapping):
        raise FieldError(field_name, "must be an object")
    for key in sorted(keys):
        if key not in payload:
            raise FieldError(_path(field_name, key), "is missing")
    for key in payload:
        if key not in keys:
            raise FieldError(_path(field_name, str(key)), f"is not one of {sorted(keys)}")
    return payload


def require_schema(payload: object, schema: str, keys: Collection[str]) -> Mapping[str, object]:
    """A ``schema`` document with exactly ``keys``. One from another version names its schema before its keys."""

    if isinstance(payload, Mapping) and "schema" in payload:
        require_choice(payload["schema"], (schema,), field_name="schema")
    return require_keys(payload, keys)


def require_nested(parse: Callable[[object], T], value: object, *, field_name: str) -> T:
    """``parse(value)``, with any field error placed under ``field_name``."""

    try:
        return parse(value)
    except FieldError as exc:
        inner = field_name if exc.field_name == ROOT else _path(field_name, exc.field_name)
        raise FieldError(inner, exc.problem) from None


def require_ascii_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise FieldError(field_name, "must be a string")
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise FieldError(field_name, f"must match {IDENTIFIER_PATTERN.pattern}; got {value!r}")
    return value


def require_safe_int(value: object, *, field_name: str) -> int:
    """A non-negative int that JSON readers keep exact; never a bool, float, or numeric string."""

    if type(value) is not int:
        raise FieldError(field_name, f"must be a non-bool int; got {type(value).__name__}")
    if value < 0 or value > MAX_SAFE_INT:
        raise FieldError(field_name, f"must be in 0..{MAX_SAFE_INT}")
    return value


def require_number(value: object, *, field_name: str, bounds: tuple[float, float] | None = None) -> float:
    """A finite int or float, never a bool, inside ``bounds`` when given."""

    low, high = (-math.inf, math.inf) if bounds is None else bounds
    within = "" if bounds is None else f" in [{low:g}, {high:g}]"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FieldError(field_name, f"must be a finite number{within}")
    try:
        number = float(value)
    except OverflowError:
        number = math.inf
    if not math.isfinite(number) or not low <= number <= high:
        raise FieldError(field_name, f"must be a finite number{within}")
    return number


def require_text(value: object, *, field_name: str) -> str:
    """A nonempty string, kept exactly as written."""

    if not isinstance(value, str) or not value:
        raise FieldError(field_name, "must be a nonempty string")
    return value


def require_choice(value: object, choices: Collection[str], *, field_name: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise FieldError(field_name, f"must be one of {sorted(choices)}; got {value!r}")
    return value


def _path(parent: str, key: str) -> str:
    return key if parent == ROOT else f"{parent}.{key}"
