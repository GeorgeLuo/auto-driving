"""Results of listing, changing, and saving a plugin catalog.

The service fills these from the host's answers and the staged bundle. The
renderer alone turns them into text and JSON, including each document's
``schema``. Plugin definitions stay in the ``describe_plugin`` form the host
sends: ``step``, ``id``, ``entrypoint``, ``config`` and ``metadata``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Definition = dict[str, Any]
RecoveryKind = Literal["config", "file", "endpoint", "rejected", "list"]


@dataclass(frozen=True)
class Recovery:
    """Which follow-up to offer after a catalog request.

    ``config``, ``file`` and ``rejected`` point at the upload's inputs;
    ``endpoint`` at a catalog that did not answer, and is omitted for a named
    host, whose error already says why; ``list`` at the listing that shows the
    change. The renderer turns it into the command text.
    """

    kind: RecoveryKind
    file: Path | None = None
    url: str | None = None
    host_name: str | None = None
    step: str | None = None
    long: bool = False


@dataclass(frozen=True)
class Arming:
    """A host's selection: what was last requested, and what its cycle has applied, by step."""

    request_id: int | None
    error: str | None
    requested: dict[str, list[Definition]]
    applied: dict[str, list[Definition]]


@dataclass(frozen=True)
class Receipt:
    """A host's answer to one catalog request, or the failure that stood in for it.

    ``body`` is the answer as the host sent it; receipts print it whole. The
    other fields are read from it on arrival, so a body that lacks what the
    receipt reports fails the request instead of the rendering. ``answered`` is
    false when no usable answer arrived.
    """

    body: dict[str, Any]
    answered: bool
    ok: bool
    status: str
    error: str | None = None
    revision: str | None = None
    catalog_version: int | None = None
    host_run_id: str | None = None
    arming: Arming | None = None


@dataclass(frozen=True)
class UploadResult:
    """An upload, and with ``--arm`` the arming that followed a successful one.

    ``url`` is the catalog endpoint that was called; ``recovery`` still names
    the caller's original ``--url`` or ``--host-name``. The command succeeds
    when the last request it made did.
    """

    host_name: str | None
    url: str | None
    step: str
    plugin_id: str
    upload: Receipt
    arm: Receipt | None
    recovery: Recovery

    @property
    def exit_code(self) -> int:
        return 0 if (self.arm or self.upload).ok else 2


@dataclass(frozen=True)
class SelectionResult:
    """An arm or disarm request; ``step`` is set when it armed a step's ``preset`` or disarm cleared one step."""

    operation: Literal["arm", "disarm"]
    host_name: str | None
    url: str | None
    step: str | None
    receipt: Receipt
    recovery: Recovery
    preset: str | None = None

    @property
    def exit_code(self) -> int:
        return 0 if self.receipt.ok else 2


@dataclass(frozen=True)
class CatalogListing:
    """Definitions a listing shows; ``version`` and ``host_run_id`` come only from a host."""

    source: str
    plugins: list[Definition]
    version: int | None = None
    host_run_id: str | None = None


@dataclass(frozen=True)
class ListingResult:
    """The packaged catalog with each step's defaults and presets, or what a host has armed.

    ``defaults`` is set only for the packaged catalog, with ``step_presets``:
    each step's presets as its catalog file lists them, the default marked.
    A host listing sets ``armed`` and, with ``--long``, ``catalog``. A
    successful read still exits unsuccessfully while the host reports
    ``arming_error``.
    """

    host_name: str | None
    url: str | None
    step: str | None
    ok: bool
    error: str | None = None
    defaults: dict[str, list[str]] | None = None
    step_presets: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    catalog: CatalogListing | None = None
    armed: list[Definition] | None = None
    arming_error: str | None = None

    @property
    def packaged(self) -> bool:
        return self.defaults is not None

    @property
    def exit_code(self) -> int:
        return 0 if self.ok and not self.arming_error else 2


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
