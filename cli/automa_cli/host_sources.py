"""Uploaded plan sources a runtime host ran, fetched so the CLI can check its plans.

The CLI checks each published plan by running the staged plan plugin itself.
An uploaded plugin is staged as ``<sha256>.py:Class``; when this process does
not have that file, it reads it from the host's catalog and keeps it only if
its bytes hash to the name.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import os
import shutil
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from autonomy.plugins import PluginManagementError, add_uploaded_source_root, uploaded_source
from autonomy.runtime.recording import uploaded_sources

from .plugin_catalog import PluginCatalogClient

_LOCK = threading.Lock()
_ROOT: Path | None = None


class UploadedSourceError(ValueError):
    """An uploaded source the staged plan names could not be provided here."""

    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name


def host_source_loader(base_url: str, *, timeout_s: float) -> Callable[[str], bytes]:
    """Read an uploaded ``<sha256>.py`` from the catalog of the host at ``base_url``."""

    client = PluginCatalogClient(base_url, timeout_s=timeout_s)

    def load(name: str) -> bytes:
        result = client({"operation": "source", "name": name})
        if not result.get("ok") or result.get("name") != name:
            raise ValueError(result.get("error") or "the catalog returned no source")
        return base64.b64decode(result["source_base64"], validate=True)

    return load


def provide_plan_sources(plan: Any, load: Callable[[str], bytes]) -> None:
    """Make each uploaded source the plan activation names importable here.

    A malformed activation is left to the publication checks that follow.
    """

    specs = plan.get("plugin_specs") if isinstance(plan, Mapping) else None
    if not isinstance(specs, Mapping) or not all(isinstance(spec, str) for spec in specs.values()):
        return
    for name in sorted(uploaded_sources({"plan": plan})):
        try:
            uploaded_source(name)
            continue
        except PluginManagementError:
            pass
        try:
            source = load(name)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            raise UploadedSourceError(
                name, f"uploaded plan source {name} is not available here and the host did not serve it: {exc}",
            ) from exc
        if f"{hashlib.sha256(source).hexdigest()}.py" != name:
            raise UploadedSourceError(name, f"the host served uploaded plan source {name} with other content")
        _store(name, source)


def _store(name: str, source: bytes) -> None:
    global _ROOT
    with _LOCK:
        if _ROOT is None:
            _ROOT = Path(tempfile.mkdtemp(prefix="automa-host-sources-"))
            atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)
            add_uploaded_source_root(_ROOT)
        path = _ROOT / name
        if not path.is_file():
            descriptor, partial = tempfile.mkstemp(suffix=".partial", dir=_ROOT)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(source)
            os.replace(partial, path)
