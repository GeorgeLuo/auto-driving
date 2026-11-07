"""Self-contained source packages for uploaded plugin collections.

Collection packages use relative imports internally and installed imports for
framework dependencies. The generated source is an ordinary uploaded revision,
so the existing recording transport carries all its dependencies unchanged.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
import tempfile
import threading
from typing import Any, Mapping

_PACKAGES: dict[str, tempfile.TemporaryDirectory] = {}
_LOCK = threading.RLock()


def collection_source(sources: Mapping[str, str], entrypoints: list[str]) -> bytes:
    """Validate package sources without importing them and build one upload."""
    if not isinstance(sources, Mapping) or not sources:
        raise ValueError("collection sources must be a non-empty map of Python paths to source")
    for name, source in sources.items():
        path = PurePosixPath(name)
        if (not isinstance(source, str) or path.is_absolute() or '..' in path.parts
                or str(path) != name or path.suffix != '.py'
                or not all(part.isidentifier() for part in path.with_suffix('').parts)):
            raise ValueError(f"invalid collection source path: {name}")
        try:
            compile(source, name, 'exec')
        except SyntaxError as exc:
            raise ValueError(f"{name}:{exc.lineno}: {exc.msg}") from exc
    lines = ['from autonomy.plugin_collections import construct_package_plugin',
             f'_sources = {dict(sorted(sources.items()))!r}']
    for index, entrypoint in enumerate(entrypoints):
        if not isinstance(entrypoint, str):
            raise ValueError("collection entrypoint must be module:Class")
        module, separator, attribute = entrypoint.partition(':')
        if (not separator or not attribute.isidentifier() or not module
                or not all(part.isidentifier() for part in module.split('.'))):
            raise ValueError(f"invalid collection entrypoint: {entrypoint}")
        file = module.replace('.', '/')
        if file + '.py' not in sources and file + '/__init__.py' not in sources:
            raise ValueError(f"collection entrypoint has no source: {entrypoint}")
        lines.extend([f'def plugin_{index}(**config):',
                      f'    return construct_package_plugin(_sources, {entrypoint!r}, config)'])
    return ('\n'.join(lines) + '\n').encode()


def construct_package_plugin(sources: dict[str, str], entrypoint: str, config: dict[str, Any]) -> Any:
    """Load a revision under its own namespace; siblings resolve through relative imports."""
    digest = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    namespace = '_automa_collection_' + digest
    with _LOCK:
        if namespace not in sys.modules:
            directory = tempfile.TemporaryDirectory(prefix='automa-collection-')
            root = Path(directory.name)
            for name, source in sources.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
            if not (root / '__init__.py').exists():
                (root / '__init__.py').write_text('', encoding='utf-8')
            spec = importlib.util.spec_from_file_location(namespace, root / '__init__.py',
                                                        submodule_search_locations=[str(root)])
            package = importlib.util.module_from_spec(spec)
            sys.modules[namespace] = package
            _PACKAGES[namespace] = directory
            try:
                spec.loader.exec_module(package)
            except BaseException:
                sys.modules.pop(namespace, None)
                _PACKAGES.pop(namespace).cleanup()
                raise
        module, _, attribute = entrypoint.partition(':')
        factory = getattr(importlib.import_module(namespace + '.' + module), attribute)
        return factory(**config)
