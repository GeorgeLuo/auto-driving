"""Host-owned shared memory for sequential steps and plugins.

A host supplies one mutable mapping for a run; absent access is None. Parameters
and fields that carry this mapping are named shared_memory. Producers own their
keys and include timing in values where needed. Writes are visible to subsequent
consumers in execution order. Hosts own reset and synchronization; this mapping
imposes no retention policy or domain-specific schema.

Plugins must be replaceable between frames without changing results. All
history affecting later frames belongs here, including previous image buffers,
identity allocation, counters, and smoothing state. Configuration and reusable
model resources may remain on instances. Plugins own namespacing, bounds,
discontinuity handling, and when a completed update is committed. Hosts pass
one map in execution order and clear/replace it at run boundaries; they do not
interpret plugin histories.

A value is JSON data or a record. JSON data is a dict with string keys, a list
or a tuple, a string, a number, a boolean, or None. A record has ``to_dict()``
and a ``from_dict`` classmethod. Either kind may be nested in the other. An
array is wrapped in a record. A tuple decodes as a list, so readers accept
either. ``shared_memory_document`` writes ``{"schema": "shared_memory_v1",
"values": {...}}``. A record is ``{"$record": "module.path:QualName", "data":
...}``. ``shared_memory_from_document`` reads that document into a fresh dict
on every call. Loading a record by its ``module:Name`` spec keeps this module
free of implementation imports.

A plugin's own state lives at ``<step>.<plugin_id>.<name>``. A key a step
publishes for others, such as ``memory.evidence``, is rewritten from its
producer's state on every update. A value meant for a consumer is therefore
set through the producer's state key.

A host may start a run from a shared-memory file, decoding a fresh map per
run. Resets and restages clear affected plugins' seeded keys as they clear
history. Set a value a step publishes through its producer's state key.

Steps reset plugins through ``reset_plugin``. A plugin's reset may omit the
map and may leave fresh state behind, such as ``bounded_evidence``'s new
epoch. ``MemoryRunner.reset`` returns that fresh state, and
``CycleHost.reset_memory`` keeps it after clearing the map. A perception
plugin's ``memory_required`` flag means that plugin needs this map to perceive.

A memory plugin's belief at ``memory.beliefs.<belief_id>`` is both that
plugin's state and its publication. Only the plugin whose contract declares the
belief writes it, and the memory runner removes it when that plugin resets.
"""

from __future__ import annotations

import importlib
import json
import math
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

SHARED_MEMORY_SCHEMA = "shared_memory_v1"
_RECORD_KEYS = frozenset({"$record", "data"})

SharedMemory = MutableMapping[str, Any]


class SharedMemoryValueError(ValueError):
    """A shared-memory document cannot encode or decode a value."""

    def __init__(self, path: str, detail: str) -> None:
        self.path = path
        super().__init__(f"{path}: {detail}")


def read_shared_memory_file(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decode a file into a fresh map; return the map and the document it holds."""

    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedMemoryValueError(str(path), f"not UTF-8 JSON: {exc}") from exc
    try:
        return shared_memory_from_document(document), document
    except SharedMemoryValueError as exc:
        raise SharedMemoryValueError(str(path), str(exc)) from exc


def shared_memory_document(shared_memory: Mapping[str, Any]) -> dict[str, Any]:
    """Encode ``shared_memory`` as a ``shared_memory_v1`` document."""

    if not isinstance(shared_memory, Mapping):
        raise SharedMemoryValueError("values", "shared memory must be an object")
    values: dict[str, Any] = {}
    for key, value in shared_memory.items():
        if type(key) is not str:
            raise SharedMemoryValueError("values", f"dict key {key!r} is not a string")
        values[key] = _encode(value, f"values[{key!r}]")
    return {"schema": SHARED_MEMORY_SCHEMA, "values": values}


def shared_memory_from_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Decode a ``shared_memory_v1`` document into a fresh map."""

    if not isinstance(document, Mapping):
        raise SharedMemoryValueError("document", "shared memory document must be an object")
    if document.get("schema") != SHARED_MEMORY_SCHEMA:
        raise SharedMemoryValueError(
            "schema", f"schema must be {SHARED_MEMORY_SCHEMA!r}"
        )
    values = document.get("values")
    if not isinstance(values, Mapping):
        raise SharedMemoryValueError("values", "values must be an object")
    decoded: dict[str, Any] = {}
    for key, value in values.items():
        if type(key) is not str:
            raise SharedMemoryValueError("values", f"dict key {key!r} is not a string")
        decoded[key] = _decode(value, f"values[{key!r}]")
    return decoded


def _encode(value: Any, path: str) -> Any:
    if _json_scalar(value):
        return value
    if _is_record(value):
        spec = _record_spec(type(value), path)
        try:
            data = value.to_dict()
        except SharedMemoryValueError:
            raise
        except Exception as exc:
            raise SharedMemoryValueError(
                path, f"{spec} to_dict failed ({type(exc).__name__}: {exc})"
            ) from exc
        return {"$record": spec, "data": _encode(data, f"{path}.data")}
    if isinstance(value, (list, tuple)):
        return [_encode(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        if "$record" in value:
            raise SharedMemoryValueError(path, "dict key '$record' is reserved for records")
        encoded: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise SharedMemoryValueError(path, f"dict key {key!r} is not a string")
            encoded[key] = _encode(item, f"{path}[{key!r}]")
        return encoded
    raise SharedMemoryValueError(
        path, f"{type(value).__name__} is not JSON data or a record"
    )


def _decode(value: Any, path: str) -> Any:
    if _json_scalar(value):
        return value
    if isinstance(value, list):
        return [_decode(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        if "$record" in value:
            return _decode_record(value, path)
        decoded: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise SharedMemoryValueError(path, f"dict key {key!r} is not a string")
            decoded[key] = _decode(item, f"{path}[{key!r}]")
        return decoded
    raise SharedMemoryValueError(
        path, f"{type(value).__name__} is not JSON data or a record"
    )


def _decode_record(value: dict[str, Any], path: str) -> Any:
    if set(value) != _RECORD_KEYS:
        raise SharedMemoryValueError(path, "record must be exactly {$record, data}")
    spec = value["$record"]
    if type(spec) is not str:
        raise SharedMemoryValueError(path, "record $record must be 'module.path:QualName'")
    cls = _load_record_class(spec, path)
    from_dict = getattr(cls, "from_dict", None)
    if not callable(from_dict):
        raise SharedMemoryValueError(path, f"{spec} has no from_dict")
    data = _decode(value["data"], f"{path}.data")
    try:
        return from_dict(data)
    except SharedMemoryValueError:
        raise
    except Exception as exc:
        raise SharedMemoryValueError(
            path, f"{spec} from_dict failed ({type(exc).__name__}: {exc})"
        ) from exc


def _load_record_class(spec: str, path: str) -> type:
    module_name, separator, qualname = spec.partition(":")
    if (
        not separator
        or not module_name
        or not qualname
        or module_name.endswith(".py")
        or "/" in module_name
        or "\\" in module_name
    ):
        raise SharedMemoryValueError(
            path, f"record spec must be 'module.path:QualName', got {spec!r}"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise SharedMemoryValueError(path, f"unknown module {module_name!r}") from exc
    except Exception as exc:
        raise SharedMemoryValueError(
            path, f"{module_name} import failed ({type(exc).__name__}: {exc})"
        ) from exc
    current: Any = module
    for part in qualname.split("."):
        if not part.isidentifier():
            raise SharedMemoryValueError(path, f"unknown class {spec!r}")
        try:
            current = getattr(current, part)
        except AttributeError as exc:
            raise SharedMemoryValueError(path, f"unknown class {spec!r}") from exc
    if not isinstance(current, type):
        raise SharedMemoryValueError(path, f"unknown class {spec!r}")
    return current


def _record_spec(cls: type, path: str) -> str:
    module_name = cls.__module__
    qualname = cls.__qualname__
    if not module_name or module_name == "builtins" or not qualname or "<locals>" in qualname:
        raise SharedMemoryValueError(
            path, f"{cls.__name__} has no importable record spec"
        )
    return f"{module_name}:{qualname}"


def _is_record(value: Any) -> bool:
    if isinstance(value, (str, dict, list, tuple)):
        return False
    to_dict = getattr(value, "to_dict", None)
    from_dict = getattr(type(value), "from_dict", None)
    return callable(to_dict) and callable(from_dict)


def _json_scalar(value: Any) -> bool:
    if value is None or type(value) is str or type(value) is bool or type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)


__all__ = [
    "SHARED_MEMORY_SCHEMA",
    "SharedMemory",
    "SharedMemoryValueError",
    "read_shared_memory_file",
    "shared_memory_document",
    "shared_memory_from_document",
]
