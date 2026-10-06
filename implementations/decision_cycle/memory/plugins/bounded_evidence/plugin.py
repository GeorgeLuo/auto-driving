"""Bounded recency ledger of observation evidence.

Retains attributed things and signals across cycles with finite capacity and
age. The reduction is ``shared.evidence_ledger.reduction``.

The plugin keeps its ledger at ``LEDGER_KEY`` in shared memory and publishes
the retained records at ``EVIDENCE_KEY`` for other plugins.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory
from implementations.decision_cycle.memory.shared.evidence_ledger.ledger import (
    EvidenceLedger,
    detach_ledger,
)
from implementations.decision_cycle.memory.shared.evidence_ledger.reduction import (
    BoundedEvidenceReducer,
    reduce_evidence,
)

LEDGER_KEY = "bounded_evidence.ledger"


class BoundedEvidenceLedger:
    """Memory plugin that keeps bounded retained evidence.

    The plugin keeps its ``EvidenceLedger`` at ``LEDGER_KEY`` and publishes the
    ledger's records at ``EVIDENCE_KEY``. Other keys in the map belong to other
    producers.
    """

    plugin_id = "bounded_evidence"

    def __init__(self, **config: Any) -> None:
        self.config = config
        reducer = BoundedEvidenceReducer(**config)
        self.bounds = reducer.bounds
        self._empty = reducer.ledger()

    def ledger(self, shared_memory: SharedMemory | None) -> EvidenceLedger:
        """The stored ledger, or the initial empty ledger before the first update."""

        current = shared_memory.get(LEDGER_KEY) if shared_memory is not None else None
        return detach_ledger(current if isinstance(current, EvidenceLedger) else self._empty)

    def status(self, shared_memory: SharedMemory | None) -> dict[str, Any]:
        return self.ledger(shared_memory).to_dict()

    def reset(self, shared_memory: SharedMemory) -> None:
        previous = self.ledger(shared_memory)
        next_epoch = _numbered_epoch(previous.epoch_id) + 1
        epoch = f"epoch-{next_epoch}"
        publish_ledger(
            shared_memory,
            replace(
                self._empty,
                memory_id=f"memory-reset-{next_epoch}",
                epoch_id=epoch,
                summary=(
                    "memory_empty=true",
                    f"epoch_id={epoch}",
                    "policy=bounded_evidence_recency",
                ),
            ),
        )

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> None:
        if context.shared_memory is None:
            raise ValueError("bounded evidence requires a shared-memory map")
        publish_ledger(
            context.shared_memory,
            reduce_evidence(
                self.ledger(context.shared_memory),
                context,
                observation,
                plugin_id=self.plugin_id,
                **self.config,
            ),
        )


def publish_ledger(shared_memory: SharedMemory, ledger: EvidenceLedger) -> None:
    """Store the ledger and publish its records for other plugins."""

    shared_memory[LEDGER_KEY] = ledger
    shared_memory[EVIDENCE_KEY] = ledger.records


def _numbered_epoch(epoch_id: str) -> int:
    number = epoch_id.removeprefix("epoch-") if epoch_id.startswith("epoch-") else ""
    return max(1, int(number)) if number.isdecimal() else 1
