"""The optional score a decision record may carry.

A record that omits its score holds ``None``. A supplied score is a finite
number in ``[0, 1]``: evidence records clamp it into range, and decision
records (proposals and plans) reject a score outside it. ``confidence_rank``
orders scored records first, highest first, and unscored records last.
``confidence_text`` is how rendered lines show a score.
"""

from __future__ import annotations

import math
from typing import Any


def optional_confidence(value: Any, *, field_name: str, clamp: bool) -> float | None:
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(confidence):
        raise ValueError(f"{field_name} must be finite")
    if clamp:
        return max(0.0, min(1.0, confidence))
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError(f"{field_name} must be in [0, 1]")
    return confidence


def confidence_rank(value: float | None) -> tuple[bool, float]:
    """Sort key: scored before unscored, then highest score first."""

    return (value is None, -(value or 0.0))


def confidence_text(value: float | None) -> str:
    return "none" if value is None else f"{value:.3f}"
