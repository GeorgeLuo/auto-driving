"""Facts emitted while packaging a release or running a deployment step.

``ProgressKind`` lists every event a host operation emits. An event of any
other kind fails when it is made, and the deployment renderer formats each
kind.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

ProgressKind = Literal[
    "step", "command", "output",
    "selected", "scope", "remote_home", "core_synced", "readiness_failed", "ready",
    "archive", "tree_hash", "archive_hash", "extracted",
    "vendor_ready", "vendor_registered", "vendor_patched", "vendor_replacing", "vendor_prepared",
]
PROGRESS_KINDS: tuple[str, ...] = get_args(ProgressKind)


@dataclass(frozen=True)
class ProgressEvent:
    kind: ProgressKind
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in PROGRESS_KINDS:
            raise ValueError(f"Unknown progress kind: {self.kind!r}")


ProgressSink = Callable[[ProgressEvent], None]


def report(emit: ProgressSink | None, kind: ProgressKind, **data: Any) -> None:
    """Emit one event; its kind is checked even when nothing observes it."""

    event = ProgressEvent(kind, data)
    if emit is not None:
        emit(event)
