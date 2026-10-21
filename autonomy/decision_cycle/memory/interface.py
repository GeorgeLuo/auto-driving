"""The memory step's contract, report, and the ledger summary tooling reads.

``MemoryBackend`` is the runner boundary, as ``PerceptionBackend`` is for
perception. ``FAILURE_POLICY`` and ``composition_declaration`` are the values
``MemoryRunner`` and ``describe_schema`` read. The evidence slot stays one
value per key, last write wins for evidence and beliefs.

``MemoryReport`` is the diagnostics record for one run, with one
``MemoryPluginReport`` per applied plugin in selection order, and
``evidence_publisher``: the id of the plugin whose value ``EVIDENCE_KEY``
holds. Decisions read plugin-published keys in the host map, not this report.
``LEDGER_SUMMARY_KEYS`` are the four fields the CLI and viewers read from each
plugin's retained-evidence ledger summary. The framework does not require a
plugin to publish them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol, runtime_checkable

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.publication import BELIEF_KEY_PREFIX, EVIDENCE_KEY
from autonomy.decision_cycle.runner import FailurePolicy
from autonomy.shared_memory import SharedMemory

MEMORY_SCHEMA = "memory_schema_v1"
MEMORY_REPORT_SCHEMA = "memory_report_v1"

# A memory plugin that raises stops the cycle, a reset failure is recorded,
# and plugins are still called when read_observation returns None.
FAILURE_POLICY = FailurePolicy(
    update="stop_cycle",
    reset="record",
    missing_input="invoke",
)


def composition_declaration() -> dict[str, str]:
    """How several memory plugins share one host map."""

    return {
        "order": "selection_order",
        "evidence_key": EVIDENCE_KEY,
        "evidence": "one_value_last_write_wins",
        "belief_key_prefix": BELIEF_KEY_PREFIX,
        "beliefs": "one_value_per_key_last_write_wins",
    }


@runtime_checkable
class MemoryBackend(Protocol):
    """Run memory on the cycle context and its host map, returning a report."""

    plugin_id: str

    def reset(self, shared_memory: SharedMemory | None = None) -> dict[str, Any]:
        """Reset each plugin. Return the keys written during the reset."""

    def describe_schema(self) -> dict[str, Any]:
        ...

    def update(
        self,
        context: DecisionFrameContext,
    ) -> dict[str, Any]:
        """Run one cycle. Return the memory report."""


EPOCH_ID = "epoch_id"
HEALTH = "health"
BOUNDS = "bounds"
RECORD_COUNT = "record_count"
LEDGER_SUMMARY_KEYS = (EPOCH_ID, HEALTH, BOUNDS, RECORD_COUNT)

LEDGER_HEALTH_EMPTY = "empty"
LEDGER_HEALTH_HEALTHY = "healthy"
LEDGER_HEALTH_VALUES: frozenset[str] = frozenset(
    (LEDGER_HEALTH_EMPTY, LEDGER_HEALTH_HEALTHY)
)


@dataclass(frozen=True)
class MemoryPluginReport:
    """One applied plugin in the memory report.

    ``state`` is that plugin's own status summary of JSON data, including
    None when the plugin offers none. Invalid status data becomes a visible
    ``status_error`` on the plugin. ``to_dict`` serializes that summary with ``asdict``.
    """

    plugin_id: str
    state: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryPluginReport":
        """Read an object status summary; a non-object state is treated as absent."""

        state = data.get("state")
        return cls(
            plugin_id=str(data.get("plugin_id") or "unknown"),
            state=dict(state) if isinstance(state, dict) else None,
        )


@dataclass(frozen=True)
class MemoryReport:
    """Diagnostics for one memory step run.

    ``plugins`` has one entry per applied plugin, in selection order; readers
    find a plugin by its ``plugin_id``. ``evidence_publisher`` is the id of the
    applied plugin whose value ``EVIDENCE_KEY`` holds, or None when the key is
    absent or holds no applied plugin's value. The runner sets it (see
    ``MemoryRunner``). ``beliefs`` maps each published belief key suffix to its
    summary, or is None when the report's source does not know them, as for
    a decision publication.
    """

    schema: str
    plugins: tuple[MemoryPluginReport, ...] = ()
    evidence_publisher: str | None = None
    beliefs: dict[str, dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "plugins": [plugin.to_dict() for plugin in self.plugins],
            "evidence_publisher": self.evidence_publisher,
            "beliefs": (
                None if self.beliefs is None
                else {belief_id: dict(belief) for belief_id, belief in self.beliefs.items()}
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryReport":
        publisher = data.get("evidence_publisher")
        beliefs = data.get("beliefs")
        return cls(
            schema=str(data.get("schema") or MEMORY_REPORT_SCHEMA),
            plugins=tuple(
                MemoryPluginReport.from_dict(item)
                for item in data.get("plugins") or ()
                if isinstance(item, dict)
            ),
            evidence_publisher=publisher if isinstance(publisher, str) and publisher else None,
            beliefs=(
                {belief_id: dict(value) for belief_id, value in beliefs.items()}
                if isinstance(beliefs, dict) else None
            ),
        )
