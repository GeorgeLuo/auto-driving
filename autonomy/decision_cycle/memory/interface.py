"""The memory step's report and the ledger summary tooling reads.

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
from typing import Any

MEMORY_REPORT_SCHEMA = "memory_report_v1"

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

    ``state`` is that plugin's own status summary. The framework passes
    through whatever ``status`` returned, including None when the plugin
    offers none. Serialization returns a detached copy of nested state.
    Readers can edit the copy without changing the plugin, and later plugin
    updates leave earlier reports intact.
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
    ``MemoryRunner``).
    """

    schema: str
    plugins: tuple[MemoryPluginReport, ...] = ()
    evidence_publisher: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "plugins": [plugin.to_dict() for plugin in self.plugins],
            "evidence_publisher": self.evidence_publisher,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryReport":
        publisher = data.get("evidence_publisher")
        return cls(
            schema=str(data.get("schema") or MEMORY_REPORT_SCHEMA),
            plugins=tuple(
                MemoryPluginReport.from_dict(item)
                for item in data.get("plugins") or ()
                if isinstance(item, dict)
            ),
            evidence_publisher=publisher if isinstance(publisher, str) and publisher else None,
        )
