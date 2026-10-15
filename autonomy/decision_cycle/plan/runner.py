"""Plan step runner.

The runner plans over the proposal step's candidates. It returns ``None``, and
the action step then fails closed, when the proposal step failed or the plan
plugin raised or returned a plan that does not match this frame, its
candidates, or its own plugin ID. Without a proposal step the plan has no
candidates. A failed proposal and a plugin failure follow ``FAILURE_POLICY``
in ``interface``; ``describe_schema`` reports the step's contract.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, ClassVar

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.interface import (
    FAILURE_POLICY,
    PLAN_SCHEMA,
    composition_declaration,
)
from autonomy.decision_cycle.plan.plugin import PlanPlugin
from autonomy.decision_cycle.plan.values import ACTION_PLAN_SCHEMA, ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.runner import StepRunner, describe_exception
from autonomy.plugins import PluginDefinition


class PlanRunner(StepRunner[PlanPlugin]):
    step: ClassVar[str] = "plan"
    single_plugin: ClassVar[bool] = True
    schema_name = PLAN_SCHEMA
    failure_policy = FAILURE_POLICY

    def validate_plugin(self, plugin: PlanPlugin, definition: PluginDefinition) -> None:
        if not callable(getattr(plugin, "plan", None)):
            raise TypeError(f"plan plugin {definition.entrypoint} must implement plan()")

    def _describe_schema(self) -> dict[str, Any]:
        failed_proposal = (
            "a failed proposal skips the plugin and the step returns no plan"
            if FAILURE_POLICY.missing_input == "skip_plugin"
            else "a failed proposal is still passed to the plugin"
        )
        return {
            **super()._describe_schema(),
            "inputs": [
                {
                    "name": "candidates",
                    "required": False,
                    "source": "the proposal step's candidates",
                    "missing_behavior": (
                        f"{failed_proposal}; an absent proposal step calls the "
                        "plugin with no candidates"
                    ),
                },
                {
                    "name": "source",
                    "required": False,
                    "source": "a detached copy of the proposal step's DecisionDataSource",
                    "missing_behavior": "an absent proposal step passes source=None",
                },
                {
                    "name": "shared_memory",
                    "required": True,
                    "source": "the host shared-memory map",
                    "missing_behavior": "an absent host map passes an empty map",
                },
            ],
            "output": {
                "schema": ACTION_PLAN_SCHEMA,
                "records": [
                    {
                        "record": "ActionPlan",
                        "meaning": "one plan; its selector_id is the plugin ID",
                    }
                ],
                "invalid_plan_behavior": (
                    "a plugin error, or a plan whose frame_id, candidates, or "
                    "selector_id does not match, returns no plan and the action "
                    "step holds"
                ),
            },
            "composition": composition_declaration(),
        }

    def __call__(
        self, context: DecisionFrameContext, proposal: ProposalResult | None
    ) -> ActionPlan | None:
        with self._runtime_lock:
            self._clear_plugin_records()
            # A failed proposal skips the plugin; its record stays null.
            if (
                proposal is not None
                and proposal.status != "ok"
                and FAILURE_POLICY.missing_input == "skip_plugin"
            ):
                return None
            candidates = proposal.candidates if proposal is not None else ()
            source = deepcopy(proposal.source) if proposal is not None else None
            shared_memory = context.shared_memory if context.shared_memory is not None else {}
            self.run_count += 1
            with self._timed_single() as plugin:
                try:
                    plan = plugin.plan(
                        tuple(candidates),
                        source,
                        shared_memory,
                        frame_id=context.frame_id,
                        timestamp_ms=context.timestamp_ms,
                    )
                    _check_plan(plan, plugin.plugin_id, context.frame_id, candidates)
                except Exception as exc:  # noqa: BLE001 - a failed plan fails the action closed
                    self.failure_count += 1
                    self.last_error = describe_exception(exc)
                    if FAILURE_POLICY.update != "isolate_plugin":
                        raise
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
