"""Memory plugin protocol.

Concrete plugins live under implementations/. Like perception plugins, a
memory plugin keeps the history later frames need in the host map,
``context.shared_memory``, under keys it owns, and chooses where to publish
anything other plugins read. ``update`` runs once per cycle. ``reset`` is
optional and may omit the map; when present it starts a new epoch and writes
the plugin's fresh state to the map. The runner calls it through
``reset_plugin``. ``status`` is an optional JSON summary of that state for
diagnostics. Framework code owns selection, timing, and failure isolation.

Every plugin exposes a ``MemoryPluginContract``: what it does and assumes, the
beliefs it publishes, and its limits. A ``Belief`` (``beliefs``) is a learned
value and the checks of it; the plugin decides what counts as support and keeps
it at ``belief_key`` (``publication``). The runner removes declared beliefs
before the plugin resets; plugin state lives only in shared memory.

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
last-write-wins per evidence or belief key, is ``composition_declaration`` in
``interface``. What a failure does is ``FAILURE_POLICY`` there: an update
failure stops the cycle, and a reset failure is recorded. ``MemoryRunner``
reads those values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol, runtime_checkable

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.evidence import require_identifier
from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory


@dataclass(frozen=True)
class MemoryPluginContract:
    """What one memory plugin does and assumes, the beliefs it publishes, and its limits.

    ``belief_ids`` names each ``Belief`` the plugin publishes; the last write
    to each key wins. A plugin that does not check what it learns
    declares ``belief_ids=()`` and publishes no beliefs, so readers get None,
    never a score. A published belief's confidence is None until its first check.
    """

    description: str = ""
    assumptions: tuple[str, ...] = ()
    belief_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.belief_ids, str):
            raise TypeError("belief_ids must be a tuple of belief ids, not one string")
        object.__setattr__(self, "belief_ids", tuple(self.belief_ids))
        for belief_id in self.belief_ids:
            require_identifier(belief_id, field_name="belief_ids")
        if len(set(self.belief_ids)) != len(self.belief_ids):
            raise ValueError("belief ids must be unique")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: list(value) if isinstance(value, tuple) else value for key, value in data.items()}


@runtime_checkable
class MemoryPlugin(Protocol):
    """Loadable memory plugin.

    The runner that applies a selection of these is a ``MemoryBackend``
    (``interface``). A plugin does not describe the step's schema. ``reset``
    is optional and may omit the map.
    """

    plugin_id: str
    contract: MemoryPluginContract

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> None:
        """Remember one observation in ``context.shared_memory``; raise on failure."""


def plugin_status(plugin: Any, shared_memory: SharedMemory | None) -> dict[str, Any] | None:
    """Return the plugin's optional ``status(shared_memory)`` summary, if it has one."""

    status = getattr(plugin, "status", None)
    if not callable(status):
        return None
    return status(shared_memory)
