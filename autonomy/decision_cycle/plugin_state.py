"""A plugin call must leave the plugin instance as it found it.

History that later frames need lives in shared memory (``autonomy/shared_memory.py``);
an instance holds configuration and resources set when it was constructed.
Each step runner calls its plugins inside ``unchanged_instance``, which fails
the call with ``RetainedStateError`` when the instance's attributes differ
after it.

Attributes are compared by value through plain containers (dict, list, tuple,
set, deque) and scalars, and by identity for any other object. Dict key order
is not a difference. An instance with no ``__dict__`` fails the call. A
resource such as a thread pool or a loaded model may change inside itself, but
an attribute rebound, added, removed, or a container on the instance changed,
is reported.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Hashable, Iterator
from contextlib import contextmanager

SCALARS = (str, bytes, int, float, complex, bool, type(None))
SEQUENCES = (list, tuple, deque)
SETS = (set, frozenset)
CONTAINERS = (dict, *SEQUENCES, *SETS)


class RetainedStateError(RuntimeError):
    """A plugin call changed its instance; the state belongs in shared memory."""


@contextmanager
def unchanged_instance(plugin: object) -> Iterator[None]:
    """Fail the block with ``RetainedStateError`` when it changes ``plugin``'s attributes.

    A block that raises is left to raise as it did.
    """

    pinned: list[object] = []
    before = _attributes(plugin, pinned)
    yield
    after = _attributes(plugin, None)
    pinned.clear()
    changed = [name for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
    if changed:
        raise RetainedStateError(
            f"plugin {_plugin_name(plugin)!r} changed "
            f"{', '.join(changed)} on its instance; keep state between cycles in shared memory"
        )


def _attributes(plugin: object, pinned: list[object] | None) -> dict[str, Hashable]:
    attributes = getattr(plugin, "__dict__", None)
    if attributes is None:
        raise RetainedStateError(
            f"plugin {_plugin_name(plugin)!r} has no instance __dict__; its attributes cannot be checked"
        )
    return {name: _value(value, (), pinned) for name, value in attributes.items()}


def _plugin_name(plugin: object) -> object:
    return getattr(plugin, "plugin_id", type(plugin).__name__)


def _value(value: object, path: tuple[int, ...], pinned: list[object] | None) -> Hashable:
    """A comparable form of ``value``: contents for plain containers and scalars, identity otherwise."""

    if isinstance(value, SCALARS):
        return type(value), repr(value)  # repr: NaN equals itself
    if not isinstance(value, CONTAINERS):
        # Hold the object through the second read so a replacement cannot reuse its id.
        if pinned is not None:
            pinned.append(value)
        return "object", id(value)
    if id(value) in path:
        return "object", id(value)
    inner = (*path, id(value))
    if isinstance(value, dict):
        pairs = ((_value(key, inner, pinned), _value(item, inner, pinned)) for key, item in value.items())
        return type(value), frozenset(pairs)
    if isinstance(value, SETS):
        return type(value), frozenset(_value(item, inner, pinned) for item in value)
    return type(value), tuple(_value(item, inner, pinned) for item in value)
