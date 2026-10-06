"""Memory plugin protocol.

Concrete plugins live under implementations/. Like perception plugins, a
memory plugin keeps the history later frames need in the host map,
``context.shared_memory``, under keys it owns, and chooses where to publish
anything other plugins read. ``update`` runs once per cycle; ``reset`` starts a
new epoch and writes the plugin's fresh state to the map. ``status`` is an
optional JSON summary of that state for diagnostics. Framework code owns
selection, timing, and failure isolation.

A memory plugin reads the host map and the ``observation``; it does not read
the feed. The context it receives has no ``sensor_frame``. Anything memory needs
from the feed reaches it through the observation or the map, written by a
perception plugin.

The CLI and the viewers it serves read ``LEDGER_SUMMARY_KEYS`` from each
plugin's status. The report preserves that status without adding missing keys;
the framework does not reject the plugin. Live CLI ledger projections return
null for missing keys. Inspect and workbench frame rows default a missing
``record_count`` to 0, preserve an explicit null, and omit ``bounds``.

How several plugins share one map, including the one ``EVIDENCE_KEY`` and
last-write-wins, is ``composition_declaration`` in ``interface``. What a
failure does is ``FAILURE_POLICY`` there: an update failure stops the cycle,
and a reset failure is recorded. ``MemoryRunner`` reads those values.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory


@runtime_checkable
class MemoryPlugin(Protocol):
    """Loadable memory plugin.

    The runner that applies a selection of these is a ``MemoryBackend``
    (``interface``). A plugin does not describe the step's schema.
    """

    plugin_id: str

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> None:
        """Remember one observation in ``context.shared_memory``; raise on failure."""

    def reset(self, shared_memory: SharedMemory) -> None:
        """Begin a new epoch: replace this plugin's keys with fresh state."""


def plugin_status(plugin: Any, shared_memory: SharedMemory | None) -> dict[str, Any] | None:
    """Return the plugin's optional ``status(shared_memory)`` summary, if it has one."""

    status = getattr(plugin, "status", None)
    if not callable(status):
        return None
    return status(shared_memory)
