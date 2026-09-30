"""Memory publication in the host-owned shared memory.

The memory framework publishes each plugin's accepted retained evidence at
``SNAPSHOT_KEY``; plugins read their previous value there. A memory
plugin may also publish a current-cycle ``Observation`` at ``OBSERVATION_KEY``;
the cycle uses it in place of the current observation for subsequent steps
when it describes the same observation and memory did not fail.
"""

from __future__ import annotations

from autonomy.decision_cycle.memory.snapshots.values import MemorySnapshot
from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory

SNAPSHOT_KEY = "decision.snapshot"
OBSERVATION_KEY = "decision.observation"


def publish_snapshot(shared_memory: SharedMemory, snapshot: MemorySnapshot | None) -> None:
    """Publish ``snapshot`` as the retained evidence, including ``None``."""

    shared_memory[SNAPSHOT_KEY] = snapshot


def withdraw_publication(shared_memory: SharedMemory) -> None:
    """Remove the published snapshot and replacement observation."""

    shared_memory.pop(SNAPSHOT_KEY, None)
    shared_memory.pop(OBSERVATION_KEY, None)


def observation_after_memory(
    shared_memory: SharedMemory | None,
    observation: Observation | None,
    memory: MemorySnapshot | None,
) -> Observation | None:
    """Return the published replacement observation, or ``observation``.

    The replacement applies only when it is an ``Observation`` with the current
    observation's ID and memory returned a snapshot without error health.
    """

    if shared_memory is None:
        return observation
    replacement = shared_memory.get(OBSERVATION_KEY)
    if (
        isinstance(replacement, Observation)
        and observation is not None
        and replacement.observation_id == observation.observation_id
        and memory is not None
        and memory.health != "error"
    ):
        return replacement
    return observation
