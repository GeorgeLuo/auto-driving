"""Controlled capture time and perception completion for real runtime loops."""
from __future__ import annotations

import threading

from autonomy.decision_cycle.activation import step_activation
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.perception.interface import PERCEPTION_TEXT_SCHEMA, PerceptionText


class CaptureClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class GatedPerception:
    """Hold selected perception calls while the real host and capture worker run."""

    plugins = ()

    def __init__(self, num_decisions: int, *, blocked: tuple[int, ...] = (0,)) -> None:
        self.contexts: list[DecisionFrameContext] = []
        self.started = [threading.Event() for _ in range(num_decisions)]
        self.release = [threading.Event() for _ in range(num_decisions)]
        for index, event in enumerate(self.release):
            if index not in blocked:
                event.set()
        # Recording names the selection this stand-in applied.
        self.plugin_ids = ("test.gated-perception",)
        self.activation = step_activation(
            "perception",
            list(self.plugin_ids),
            {"test.gated-perception": "tests.integration.automation_pipeline.cadence_fixtures:GatedPerception"},
        )

    def __call__(self, context: DecisionFrameContext) -> PerceptionText:
        index = len(self.contexts)
        self.contexts.append(context)
        self.started[index].set()
        if not self.release[index].wait(timeout=5.0):
            raise TimeoutError("perception was not released by the capture scenario")
        return PerceptionText(
            schema=PERCEPTION_TEXT_SCHEMA,
            plugin_id="test.gated-perception",
            status="empty",
            lines=(f"schema={PERCEPTION_TEXT_SCHEMA}", "plugin=test.gated-perception"),
            signals=(),
            things=(),
        )

    def wait_started(self, index: int) -> None:
        if not self.started[index].wait(timeout=2.0):
            raise TimeoutError(f"decision {index} did not start")

    def release_all(self) -> None:
        for event in self.release:
            event.set()

    def reset(self, shared_memory=None) -> None:
        del shared_memory

    def status(self) -> dict:
        return {"step": "perception", "decisions": len(self.contexts)}
