"""The memory step's contract, report, and the ledger summary tooling reads.

``MemoryRunner`` is the runner boundary. ``FAILURE_POLICY`` is what it does
when a plugin fails. The evidence slot stays one value per key, last write
wins for evidence and beliefs.

``MemoryReport`` is the diagnostics record for one run, with one
``MemoryPluginReport`` per applied plugin in selection order, and
``evidence_publisher``: the id of the plugin whose value ``EVIDENCE_KEY``
holds. Decisions read plugin-published keys in the host map, not this report.
``LEDGER_SUMMARY_KEYS`` are the four fields the CLI and viewers read from each
plugin's retained-evidence ledger summary. The framework does not require a
plugin to publish them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from autonomy.decision_cycle.runner import FailurePolicy
from autonomy.fields import FieldError, require_ascii_id, require_choice, require_keys, require_nested, require_schema
from autonomy.serialization import deep_freeze, frozen_mapping_to_dict

MEMORY_REPORT_SCHEMA = "memory_report"

# A memory plugin that raises stops the cycle, a reset failure is recorded,
# and plugins are still called when read_observation returns None.
FAILURE_POLICY = FailurePolicy(
    update="stop_cycle",
    reset="record",
    missing_input="invoke",
)


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
    ``status_error`` on the plugin. The report freezes it so later plugin updates cannot change it.
    """

    plugin_id: str
    state: Any = None

    def __post_init__(self) -> None:
        require_ascii_id(self.plugin_id, field_name="plugin_id")
        try:
            object.__setattr__(self, "state", deep_freeze(self.state, field_name="state"))
        except ValueError as exc:
            raise FieldError("state", str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        return {"plugin_id": self.plugin_id, "state": frozen_mapping_to_dict(self.state)}

    @classmethod
    def from_dict(cls, payload: object) -> MemoryPluginReport:
        document = require_keys(payload, ("plugin_id", "state"))
        return cls(plugin_id=document["plugin_id"], state=document["state"])



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
    beliefs: Mapping[str, Mapping[str, object]] | None = None

    def __post_init__(self) -> None:
        require_choice(self.schema, (MEMORY_REPORT_SCHEMA,), field_name="schema")
        if not isinstance(self.plugins, (list, tuple)):  # pyright: ignore[reportUnnecessaryIsInstance] - validate runtime callers
            raise FieldError("plugins", "must be an array")
        if not all(isinstance(plugin, MemoryPluginReport) for plugin in self.plugins):  # pyright: ignore[reportUnnecessaryIsInstance] - validate runtime callers
            raise FieldError("plugins", "must contain MemoryPluginReport values")
        object.__setattr__(self, "plugins", tuple(self.plugins))
        if self.evidence_publisher is not None:
            require_ascii_id(self.evidence_publisher, field_name="evidence_publisher")
        if self.beliefs is not None:
            if not isinstance(self.beliefs, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance] - validate runtime callers
                raise FieldError("beliefs", "must be an object or null")
            for name, summary in self.beliefs.items():
                require_ascii_id(name, field_name=f"beliefs.{name}")
                if not isinstance(summary, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance] - validate runtime callers
                    raise FieldError(f"beliefs.{name}", "must be an object")
            try:
                object.__setattr__(self, "beliefs", deep_freeze(self.beliefs, field_name="beliefs"))
            except ValueError as exc:
                raise FieldError("beliefs", str(exc)) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "plugins": [plugin.to_dict() for plugin in self.plugins],
            "evidence_publisher": self.evidence_publisher,
            "beliefs": frozen_mapping_to_dict(self.beliefs),
        }

    @classmethod
    def from_dict(cls, payload: object) -> MemoryReport:
        document = require_schema(payload, MEMORY_REPORT_SCHEMA, ("schema", "plugins", "evidence_publisher", "beliefs"))
        plugins = document["plugins"]
        if not isinstance(plugins, (list, tuple)):
            raise FieldError("plugins", "must be an array")
        return cls(
            schema=document["schema"], evidence_publisher=document["evidence_publisher"], beliefs=document["beliefs"],
            plugins=tuple(require_nested(MemoryPluginReport.from_dict, plugin, field_name=f"plugins.{index}")
                          for index, plugin in enumerate(plugins)),
        )



def evidence_records(report: object) -> list[Mapping[str, object]] | tuple[Mapping[str, object], ...] | None:
    """The evidence publisher's retained records, or None when unavailable.

    Accept the typed cycle report or a JSON step status. Missing publishers
    and plugins yield None. A plugin's opaque state may be any JSON value;
    only an object with a records array supplies retained evidence.
    """

    if isinstance(report, MemoryReport):
        publisher = report.evidence_publisher
        plugins = ((plugin.plugin_id, plugin.state) for plugin in report.plugins)
    elif isinstance(report, dict):
        publisher = report.get("evidence_publisher")
        entries = report.get("plugins")
        if not isinstance(entries, list):
            return None
        plugins = ((entry.get("plugin_id"), entry.get("state")) for entry in entries if isinstance(entry, dict))
    else:
        return None
    if not isinstance(publisher, str) or not publisher:
        return None
    for plugin_id, state in plugins:
        if plugin_id != publisher or not isinstance(state, Mapping):
            continue
        records = state.get("records")
        return records if isinstance(records, (list, tuple)) else None
    return None
