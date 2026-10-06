"""Plan step runner.

The runner plans over the proposal step's candidates. It returns ``None``, and
the action step then fails closed, when the proposal step failed or the plan
plugin raised or returned a plan that does not match this frame, its
candidates, or its own plugin ID. Without a proposal step the plan has no
candidates.
"""

from __future__ import annotations

from typing import ClassVar

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.plugin import PlanPlugin
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.runner import StepRunner, describe_exception
from autonomy.plugins import PluginDefinition


class PlanRunner(StepRunner[PlanPlugin]):
    step: ClassVar[str] = "plan"
    single_plugin: ClassVar[bool] = True

    def validate_plugin(self, plugin: PlanPlugin, definition: PluginDefinition) -> None:
        if not callable(getattr(plugin, "plan", None)):
            raise TypeError(f"plan plugin {definition.entrypoint} must implement plan()")

    def __call__(
        self, context: DecisionFrameContext, proposal: ProposalResult | None
    ) -> ActionPlan | None:
        if proposal is not None and proposal.status != "ok":
            return None
        candidates = proposal.candidates if proposal is not None else ()
        with self._runtime_lock:
            self.refresh_selection(context.shared_memory)
            self.run_count += 1
            plugin = self._single()
            try:
                plan = plugin.plan(
                    tuple(candidates),
                    frame_id=context.frame_id,
                    timestamp_ms=context.timestamp_ms,
                )
                _check_plan(plan, plugin.plugin_id, context.frame_id, candidates)
            except Exception as exc:  # noqa: BLE001 - a failed plan fails the action closed
                self.failure_count += 1
                self.last_error = describe_exception(exc)
                return None
            self.last_error = None
            return plan


def _check_plan(plan: object, plugin_id: str, frame_id: str, candidates) -> None:
    if not isinstance(plan, ActionPlan):
        raise TypeError("plan plugin must return an ActionPlan")
    if plan.frame_id != frame_id:
        raise ValueError("plan frame_id must match the cycle frame")
    if plan.selector_id != plugin_id:
        raise ValueError(f"plan selector_id {plan.selector_id!r} must be the plugin ID {plugin_id!r}")
    if sorted(c.proposal_id for c in plan.candidates) != sorted(
        c.proposal_id for c in candidates
    ):
        raise ValueError("plan candidates must be the proposal step's candidates")
