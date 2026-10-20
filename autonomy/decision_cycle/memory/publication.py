"""What a memory plugin may publish for later steps.

A memory plugin may publish the ``RetainedEvidence`` records it holds, as a
tuple at ``EVIDENCE_KEY``. Later steps read them there whichever plugin kept
them.

The key holds one value, the last one written. With several plugins that
publish evidence, later steps see only the last plugin's records; the others
keep theirs in their own ledgers, which appear in the memory report. The
report's ``evidence_publisher`` names the plugin whose records the key holds.
Beliefs occupy a separate map, merged by belief id. Publishing or dropping one
belief preserves every other id; two writers of the same id use selection order.
"""
from __future__ import annotations

from collections.abc import Iterable

from autonomy.decision_cycle.memory.beliefs.values import Belief
from autonomy.shared_memory import SharedMemory

EVIDENCE_KEY = "memory.evidence"

BELIEFS_KEY = "memory.beliefs"


def publish_belief(shared_memory: SharedMemory, belief: Belief) -> None:
    """Publish a detached belief, preserving other belief ids."""
    shared_memory[BELIEFS_KEY] = {**shared_memory.get(BELIEFS_KEY, {}), belief.belief_id: belief.to_dict()}


def read_belief(shared_memory: SharedMemory | None, belief_id: str) -> Belief | None:
    data = None if shared_memory is None else shared_memory.get(BELIEFS_KEY, {}).get(belief_id)
    return Belief.from_dict(data) if data is not None else None


def drop_beliefs(shared_memory: SharedMemory | None, belief_ids: Iterable[str]) -> None:
    if shared_memory is None or BELIEFS_KEY not in shared_memory:
        return
    removed = set(belief_ids)
    current = shared_memory[BELIEFS_KEY]
    if removed.intersection(current):
        shared_memory[BELIEFS_KEY] = {key: value for key, value in current.items() if key not in removed}
