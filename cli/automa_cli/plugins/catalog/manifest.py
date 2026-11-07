"""Read an external collection without importing its plugin code."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autonomy.plugin_collections import collection_source


def read_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict):
        raise ValueError('manifest must be a JSON object')
    name, plugins = manifest.get('name'), manifest.get('plugins')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('manifest name must be a non-empty string')
    if not isinstance(plugins, list) or not plugins:
        raise ValueError('manifest plugins must be a non-empty list')
    root = (path.parent / manifest.get('source_directory', '.')).resolve()
    if not root.is_dir():
        raise ValueError(f'collection source directory does not exist: {root}')
    sources = {}
    for source in sorted(root.rglob('*.py')):
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError(f'collection source escapes its directory: {source}')
        sources[source.relative_to(root).as_posix()] = source.read_text(encoding='utf-8')
    for plugin in plugins:
        if not isinstance(plugin, dict):
            raise ValueError('manifest plugins must contain objects')
        file = (path.parent / plugin['file']).resolve()
        if not file.is_relative_to(root) or file.relative_to(root).as_posix() not in sources:
            raise ValueError(f'plugin file is not in the source directory: {file}')
        if not isinstance(plugin['entrypoint'], str):
            raise ValueError('collection entrypoint must be module:Class')
        module = plugin['entrypoint'].partition(':')[0].replace('.', '/')
        if file.relative_to(root).as_posix() not in (module + '.py', module + '/__init__.py'):
            raise ValueError(f"plugin file does not match entrypoint: {plugin['entrypoint']}")
    collection_source(sources, [plugin['entrypoint'] for plugin in plugins])
    return {'operation': 'upload_collection', 'name': name, 'plugins': plugins, 'sources': sources}
