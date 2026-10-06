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

The CLI and the viewers it serves read four fields from each plugin's
retained-evidence ledger summary. Their names are ``LEDGER_SUMMARY_KEYS`` in
``interface``: ``epoch_id``, ``health`` (``empty`` or ``healthy``,
``LEDGER_HEALTH_VALUES``), ``bounds`` and ``record_count``. The framework does
not check for them. Live CLI probes return None for missing fields. Inspect
frame rows default a missing ``record_count`` to 0, preserve an explicit None,
and omit ``bounds``. The CLI's reset check reads ``health`` and
``record_count`` from every plugin's summary: a reset emptied the memory when
each plugin reports ``empty`` health or no records.

The report keeps every applied plugin's status, keyed by ``plugin_id``, and
names the evidence publisher: the plugin whose value ``EVIDENCE_KEY`` holds
(see ``MemoryRunner``). Every reader, from inspect and the live probes to the
workbench, shows each plugin and the publisher.

Several memory plugins may be selected together. They run in selection order on
one host map, and each receives the same ``observation``. The framework does not merge or arbitrate between
them; resolving what a later plugin overwrites is the implementer's job. These
cause unexpected behavior:

- A key two plugins write holds the later plugin's value. That includes a
  private key that lacks the plugin's ``plugin_id`` prefix.
- ``EVIDENCE_KEY`` (see ``publication``) holds one value. A later plugin that
  writes it replaces the earlier plugin's value.
- A plugin that raises stops the plugins after it and the cycle.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory


@runtime_checkable
class MemoryPlugin(Protocol):
    """Loadable memory plugin."""

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
