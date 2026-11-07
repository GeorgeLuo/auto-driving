"""Read a collection manifest and the sources it names, without importing them."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from autonomy.plugin_collections import is_module_path

from ...shared.plugin_catalog import step_plugin


def read_manifest(path: Path) -> dict[str, Any]:
    """The ``upload_collection`` request for the manifest at ``path``.

    Each plugin is a ``step/plugin`` name, a ``module:Class`` entrypoint and an
    optional config. The modules are every file under ``source_directory``,
    relative to the manifest and by default its directory, that Python can import
    by name; the host checks the entrypoints against them.
    """

    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    plugins = _field(manifest, "plugins", "manifest")
    if not isinstance(plugins, list) or not plugins:
        raise ValueError("manifest plugins must be a non-empty list")
    definitions = []
    for index, plugin in enumerate(plugins):
        where = f"manifest plugins[{index}]"
        if not isinstance(plugin, dict):
            raise ValueError(f"{where} must be an object")
        name = _field(plugin, "name", where)
        try:
            step, plugin_id = step_plugin(name)
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from exc
        definitions.append({"step": step, "plugin_id": plugin_id, "entrypoint": _field(plugin, "entrypoint", where),
                            **({"config": plugin["config"]} if "config" in plugin else {})})
    root = (path.parent / manifest.get("source_directory", ".")).resolve()
    if not root.is_dir():
        raise ValueError(f"collection source directory does not exist: {root}")
    return {"operation": "upload_collection", "name": _field(manifest, "name", "manifest"),
            "plugins": definitions, "sources": _sources(root)}


def _field(entry: dict[str, Any], key: str, where: str) -> Any:
    if key not in entry:
        raise ValueError(f"{where}: missing field: {key}")
    return entry[key]


def _sources(root: Path) -> dict[str, str]:
    """Each module under ``root`` by its relative path; scripts such as ``run-sim.py`` and folders such as ``.venv`` are skipped."""

    sources = {}
    for directory, folders, files in os.walk(root):
        folders[:] = sorted(folder for folder in folders if folder.isidentifier())
        for file in sorted(files):
            source = Path(directory, file)
            name = source.relative_to(root).as_posix()
            if not is_module_path(name):
                continue
            if source.is_symlink():
                raise ValueError(f"collection source is a link: {source}")
            sources[name] = source.read_text(encoding="utf-8")
    return sources
