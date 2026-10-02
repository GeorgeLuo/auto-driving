"""What a memory plugin may publish for later steps.

A memory plugin may publish an ``Observation`` at ``OBSERVATION_KEY``; the
cycle uses it in place of the current observation for subsequent steps when it
describes the same observation. A failed memory update stops the cycle before
the replacement is read.

A memory plugin may also publish the ``RetainedEvidence`` records it holds, as
a tuple at ``EVIDENCE_KEY``. Later steps read them there whichever plugin kept
them.

Each key holds one value, the last one written. With several plugins that
publish evidence, later steps see only the last plugin's records; the others
keep theirs in their own ledgers, which appear in the memory report. A
replacement observation that lacks the current observation's ID is ignored
silently, and it stays in the map until a plugin replaces or removes it.
"""

from __future__ import annotations

from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory

OBSERVATION_KEY = "decision.observation"
EVIDENCE_KEY = "retained_evidence"


def withdraw_publication(shared_memory: SharedMemory) -> None:
    """Remove the replacement observation."""

    shared_memory.pop(OBSERVATION_KEY, None)


def observation_after_memory(
    shared_memory: SharedMemory | None,
    observation: Observation | None,
) -> Observation | None:
    """Return the published replacement observation, or ``observation``.

    The replacement applies only when it is an ``Observation`` with the current
    observation's ID.
    """

    if shared_memory is None:
        return observation
    replacement = shared_memory.get(OBSERVATION_KEY)
    if (
        isinstance(replacement, Observation)
        and observation is not None
        and replacement.observation_id == observation.observation_id
    ):
        return replacement
    return observation
