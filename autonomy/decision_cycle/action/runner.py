"""Action step runner.

``ActionRunner`` asks the selected action plugin for the cycle's control. With
a plan it passes the context and plan; when the proposal step failed or
there is no plan, it passes the error reason instead, so the plugin fails
closed. A plugin that raises or returns something other than an
``ActionDecision`` is replaced by hold for that cycle with reason
``step_internal_error``. The authority records the action decision; host
applications are published separately in shared memory.
"""

from __future__ import annotations

from typing import ClassVar

from autonomy.decision_cycle.action.hold import HoldAction
from autonomy.decision_cycle.action.plugin import ActionPlugin
from autonomy.decision_cycle.action.publication import read_mode
from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.action.values import ActionDecision, build_authority
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.result import ProposalResult
from autonomy.decision_cycle.proposal.values import ProposedVehicleCommand
from autonomy.decision_cycle.runner import StepRunner, describe_exception
from autonomy.plugins import PluginDefinition

PLAN_MISSING_REASON = "action_plan_invariant_violated"


class ActionRunner(StepRunner[ActionPlugin]):
    step: ClassVar[str] = "action"
    single_plugin: ClassVar[bool] = True

    def validate_plugin(self, plugin: ActionPlugin, definition: PluginDefinition) -> None:
        if not callable(getattr(plugin, "decide", None)):
            raise TypeError(f"action plugin {definition.entrypoint} must implement decide()")

    def __call__(
        self,
        context: DecisionFrameContext,
        proposal: ProposalResult | None,
        plan: ActionPlan | None,
    ) -> ActionResult:
        if proposal is not None and proposal.status != "ok":
            error_reason: str | None = proposal.reason
        elif plan is None:
            error_reason = PLAN_MISSING_REASON
        else:
            error_reason = None
        with self._runtime_lock, self._timed_single() as plugin:
            self.run_count += 1
            return self._decide(
                plugin,
                context=context,
                plan=plan if error_reason is None else None,
                error_reason=error_reason,
            )

    def _decide(
        self,
        plugin: ActionPlugin,
        *,
        context: DecisionFrameContext,
        plan: ActionPlan | None,
        error_reason: str | None,
    ) -> ActionResult:
        try:
            if error_reason is None:
                decision = plugin.decide(context, plan)
            else:
                decision = plugin.decide(context, None, error_reason=error_reason)
            if not isinstance(decision, ActionDecision):
                raise TypeError("action plugin must return an ActionDecision")
            proposed = _planned_command(plan)
            result = action_result(
                frame_id=context.frame_id,
                plugin_id=plugin.plugin_id,
                decision=decision,
                error_reason=error_reason,
                proposed=proposed,
                mode=read_mode(context.shared_memory),
            )
        except Exception as exc:  # noqa: BLE001 - the cycle still gets a held control
            self.failure_count += 1
            self.last_error = describe_exception(exc)
            return error_action_result(context, reason="step_internal_error")
        self.last_error = None
        return result


def _planned_command(plan: ActionPlan | None) -> ProposedVehicleCommand | None:
    if plan is None or plan.command is None:
        return None
    return plan.command


def action_result(
    *,
    frame_id: str,
    plugin_id: str,
    decision: ActionDecision,
    error_reason: str | None = None,
    proposed: ProposedVehicleCommand | None = None,
    mode: str | None = None,
) -> ActionResult:
    status = "ok" if error_reason is None else "error"
    reason = "" if error_reason is None else error_reason
    authority = build_authority(
        frame_id=frame_id,
        gate_id=plugin_id,
        decision=decision,
        cycle_status=status,
        cycle_reason=reason,
        proposed=proposed if error_reason is None else None,
        drive_mode_gate=mode,
    )
    return ActionResult(
        frame_id=frame_id,
        status=status,
        reason=reason,
        authority=authority,
        control=decision.control,
    )


def error_action_result(context: DecisionFrameContext, *, reason: str) -> ActionResult:
    """The fail-closed record when no action plugin could decide: hold."""

    hold = HoldAction()
    return action_result(
        frame_id=context.frame_id,
        plugin_id=hold.plugin_id,
        decision=hold.decide(context, None, error_reason=reason),
        error_reason=reason,
        mode=read_mode(context.shared_memory),
    )
