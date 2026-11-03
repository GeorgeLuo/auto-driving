"""Results of listing, changing, and saving a plugin catalog."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Recovery:
    """Which follow-up to offer after a catalog change.

    ``kind`` is ``config``, ``file``, ``endpoint``, ``rejected``, or ``list``.
    The renderer turns it into the command text; ``endpoint`` is omitted for a
    named host, whose error already says why it could not be reached.
    """

    kind: str
    file: Path | None = None
    url: str | None = None
    host_name: str | None = None
    step: str | None = None
    long: bool = False


@dataclass(frozen=True)
class ListingResult:
    """A packaged or host catalog listing.

    ``payload`` is the public JSON document. Text ordering and the exit status
    are derived from it: a successful read still exits unsuccessfully when the
    host reports ``arming_error``.
    """

    payload: dict[str, Any]

    @property
    def exit_code(self) -> int:
        return 0 if self.payload["ok"] and not self.payload.get("arming_error") else 2


@dataclass(frozen=True)
class MutationResult:
    """An upload, arm, or disarm.

    ``response`` is the catalog body's answer, or the error object the request
    raised. An upload that then fails to arm keeps the upload body under
    ``response["upload"]`` and takes the arm's success flag. ``url`` is the
    catalog endpoint that was called; ``recovery`` still names the caller's
    original ``--url`` or ``--host-name``.
    """

    operation: str
    response: dict[str, Any]
    url: str | None
    host_name: str | None
    step: str | None
    plugin_id: str | None
    recovery: Recovery

    @property
    def exit_code(self) -> int:
        return 0 if self.response.get("ok") else 2


@dataclass(frozen=True)
class SavedStep:
    activation: Path
    preset: str | None
    plugins: tuple[str, ...]
    saved: bool


@dataclass(frozen=True)
class SaveResult:
    """A save of armed plugins into the host's staged activations.

    ``steps`` is set only when every requested step was saved. A refusal leaves
    it empty and sets ``error``; nothing is written in that case.
    """

    host_name: str
    step: str | None
    url: str | None
    ok: bool
    error: str | None = None
    steps: dict[str, SavedStep] | None = None
    runtime_dir: Path | None = None
    provider: str | None = None

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 2
