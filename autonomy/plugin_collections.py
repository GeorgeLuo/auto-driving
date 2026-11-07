"""Self-contained source packages for uploaded plugin collections.

A collection's modules travel as one uploaded source revision: a generated
module that embeds every file and holds one factory per plugin. Recordings keep
that revision, so a replay loads the same package without the directory it came
from. Collection modules import each other relatively and import the framework
as installed.
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
from typing import Any, Mapping, Sequence

_PACKAGES: dict[str, tempfile.TemporaryDirectory] = {}
_LOCK = threading.RLock()


def is_module_path(name: str) -> bool:
    """Whether ``name`` is a relative ``.py`` path Python can import by name, such as ``chase/drive.py``."""

    path = PurePosixPath(name)
    return (not path.is_absolute() and str(path) == name and path.suffix == ".py"
            and all(part.isidentifier() for part in path.with_suffix("").parts))


def entrypoint_file(entrypoint: str, sources: Mapping[str, str]) -> str:
    """The source path that defines ``entrypoint``'s module: ``a/b.py``, or ``a/b/__init__.py``."""

    module, separator, attribute = str(entrypoint).partition(":")
    if (not isinstance(entrypoint, str) or not separator or not attribute.isidentifier()
            or not all(part.isidentifier() for part in module.split("."))):
        raise ValueError(f"collection entrypoint must be module:Class, got {entrypoint!r}")
    base = module.replace(".", "/")
    for name in (f"{base}.py", f"{base}/__init__.py"):
        if name in sources:
            return name
    raise ValueError(f"collection entrypoint has no source: {entrypoint}")


def collection_source(sources: Mapping[str, str], entrypoints: Sequence[str]) -> tuple[bytes, list[tuple[str, str]]]:
    """One upload holding ``sources``, and for each entrypoint its factory there and its module's file.

    Sources must compile; nothing is imported.
    """

    if not isinstance(sources, Mapping) or not sources:
        raise ValueError("collection sources must be a non-empty map of Python paths to source")
    for name, source in sources.items():
        if not isinstance(name, str) or not isinstance(source, str) or not is_module_path(name):
            raise ValueError(f"invalid collection source path: {name}")
        try:
            compile(source, name, "exec")
        except SyntaxError as exc:
            raise ValueError(f"{name}:{exc.lineno}: {exc.msg}") from exc
    ordered = dict(sorted(sources.items()))
    namespace = "_automa_collection_" + hashlib.sha256(json.dumps(ordered).encode()).hexdigest()
    lines = ["from autonomy.plugin_collections import package_attribute",
             f"_NAMESPACE = {namespace!r}", f"_SOURCES = {ordered!r}"]
    plugins = []
    for index, entrypoint in enumerate(entrypoints):
        file = entrypoint_file(entrypoint, sources)
        factory = f"{entrypoint.partition(':')[2]}_{index}"
        lines.extend([f"def {factory}(**config):",
                      f"    return package_attribute(_NAMESPACE, _SOURCES, {entrypoint!r})(**config)"])
        plugins.append((factory, file))
    return ("\n".join(lines) + "\n").encode(), plugins


def package_attribute(namespace: str, sources: Mapping[str, str], entrypoint: str) -> Any:
    """``entrypoint``'s class in the package ``sources`` make, loaded once per process as ``namespace``."""

    with _LOCK:
        if namespace not in sys.modules:
            _load_package(namespace, sources)
        module, _, attribute = entrypoint.partition(":")
        return getattr(importlib.import_module(f"{namespace}.{module}"), attribute)


def _load_package(namespace: str, sources: Mapping[str, str]) -> None:
    directory = tempfile.TemporaryDirectory(prefix="automa-collection-")
    root = Path(directory.name)
    for name, source in sources.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    if not (root / "__init__.py").exists():
        (root / "__init__.py").write_text("", encoding="utf-8")
    spec = importlib.util.spec_from_file_location(namespace, root / "__init__.py",
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
