"""Retain applied control receipts; arm by name with memory/control_runs.

Observe-only produces no applied runs, so this plugin appends nothing until
the host publishes applied control receipts.
"""

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.plugin import MemoryPluginContract
from autonomy.shared_memory import SharedMemory
from implementations.decision_cycle.shared.control_runs import (
    CONTROL_RUNS_KEY, STATE_KEY, control_runs_status, record_control_runs,
)


class ControlRuns:
    plugin_id = "control_runs"
    contract = MemoryPluginContract(
        description="Keep the last 40 runs of applied control receipts. Observe-only produces no applied runs.",
        assumptions=("The host publishes applied control receipts at action.control_applications before each cycle.",),
        belief_ids=(),
        limitations=("Lost application windows are counted as gaps; unknown windows retain prior runs.",),
    )

    def update(self, context: DecisionFrameContext) -> None:
        if context.shared_memory is None:
            raise ValueError("control_runs requires shared memory")
        record_control_runs(context.shared_memory)

    def status(self, shared_memory: SharedMemory | None) -> dict:
        return control_runs_status(shared_memory)

    def reset(self, shared_memory: SharedMemory | None) -> None:
        if shared_memory is not None:
            shared_memory.pop(STATE_KEY, None)
            shared_memory.pop(CONTROL_RUNS_KEY, None)
