"""The current observation published for later steps.

After the observation step the cycle writes its result at ``OBSERVATION_KEY``,
and removes the key when the step returns None or the slot is empty. Later steps
read it with ``read_observation``; the cycle uses ``write_observation``.
"""
from __future__ import annotations

from autonomy.decision_cycle.observation.values import Observation
from autonomy.shared_memory import SharedMemory

OBSERVATION_KEY = "observation.current"


def write_observation(shared_memory: SharedMemory, observation: Observation | None) -> None:
    """Write the current observation for later steps; None removes the key."""
    if observation is None:
        shared_memory.pop(OBSERVATION_KEY, None)
    else:
        shared_memory[OBSERVATION_KEY] = observation


def read_observation(shared_memory: SharedMemory | None) -> Observation | None:
    """The current observation, or None when the key is absent or unknown."""
    value = None if shared_memory is None else shared_memory.get(OBSERVATION_KEY)
    return value if isinstance(value, Observation) else None
