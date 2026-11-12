"""A plugin call must leave the plugin instance as it found it.

History that later frames need lives in shared memory (``autonomy/shared_memory.py``);
an instance holds configuration and resources set when it was constructed.
Each step runner calls its plugins inside ``unchanged_instance``, which fails
the call with ``RetainedStateError`` when the instance's attributes differ
after it.

Attributes are compared by value through plain containers (dict, list, tuple,
set, deque) and scalars, and by identity for any other object. So a resource
such as a thread pool or a loaded model may change inside itself, but an
attribute rebound, added, removed, or a container on the instance changed, is
reported.
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

    before = _attributes(plugin)
    yield
    after = _attributes(plugin)
    changed = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    if changed:
        raise RetainedStateError(
            f"plugin {getattr(plugin, 'plugin_id', type(plugin).__name__)!r} changed "
            f"{', '.join(changed)} on its instance; keep state between cycles in shared memory"
        )


def _attributes(plugin: object) -> dict[str, Hashable]:
    return {name: _value(value, ()) for name, value in getattr(plugin, "__dict__", {}).items()}


def _value(value: object, path: tuple[int, ...]) -> Hashable:
    """A comparable form of ``value``: contents for plain containers and scalars, identity otherwise."""

    if isinstance(value, SCALARS):
        return type(value), repr(value)  # repr: NaN equals itself
    revisited = id(value) in path
    if revisited or not isinstance(value, CONTAINERS):
        return "object", id(value)
    inner = (*path, id(value))
    if isinstance(value, dict):
        return type(value), tuple((_value(key, inner), _value(item, inner)) for key, item in value.items())
    if isinstance(value, SETS):
        return type(value), frozenset(_value(item, inner) for item in value)
    return type(value), tuple(_value(item, inner) for item in value)
