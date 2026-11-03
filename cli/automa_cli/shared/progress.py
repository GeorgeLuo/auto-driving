"""Facts emitted while packaging a release or running a deployment step."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProgressEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


ProgressSink = Callable[[ProgressEvent], None]


def report(emit: ProgressSink | None, kind: str, **data: Any) -> None:
    if emit is not None:
        emit(ProgressEvent(kind, data))
