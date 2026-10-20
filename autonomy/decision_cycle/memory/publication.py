"""What a memory plugin may publish for later steps.

A memory plugin may publish the ``RetainedEvidence`` records it holds, as a
tuple at ``EVIDENCE_KEY``. Later steps read them there whichever plugin kept
them.

The key holds one value, the last one written. With several plugins that
publish evidence, later steps see only the last plugin's records; the others
keep theirs in their own ledgers, which appear in the memory report. The
report's ``evidence_publisher`` names the plugin whose records the key holds.

A plugin publishes each ``Belief`` its contract declares at
``belief_key(belief_id)``, one key per belief, and keeps it there as its own
state: it reads the belief back on its next update and writes a new one when a
check or revision changes it. The last write to each belief key wins. ``read_belief``
returns the stored ``Belief``, or None when no plugin has published that
belief, never a score.
"""
from __future__ import annotations

from autonomy.decision_cycle.memory.beliefs import Belief
from autonomy.shared_memory import SharedMemory

EVIDENCE_KEY = "memory.evidence"

BELIEF_KEY_PREFIX = "memory.beliefs."


def belief_key(belief_id: str) -> str:
    return f"{BELIEF_KEY_PREFIX}{belief_id}"


def read_belief(shared_memory: SharedMemory | None, belief_id: str) -> Belief | None:
    """The published ``Belief``, or None when the key holds none."""

    value = None if shared_memory is None else shared_memory.get(belief_key(belief_id))
    return value if isinstance(value, Belief) else None
