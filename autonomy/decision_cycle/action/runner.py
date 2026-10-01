"""Action step runner.

``ActionRunner`` asks the selected action plugin for the cycle's control. With
a plan it passes the plan and the drive mode; when the proposal step failed or
there is no plan, it passes the error reason instead, so the plugin fails
closed. A plugin that raises or returns something other than an
``ActionDecision`` is replaced by hold for that cycle with reason
``step_internal_error``. ``context.metadata["host_application"]`` is the
host-reported application envelope recorded in the authority.
"""

from __future__ import annotations

from typing import ClassVar

from autonomy.decision_cycle.action.hold import HoldAction
from autonomy.decision_cycle.action.plugin import ActionPlugin
from autonomy.decision_cycle.action.result import ActionResult
from autonomy.decision_cycle.action.values import ActionDecision, build_authority
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.plan.values import ActionPlan
from autonomy.decision_cycle.proposal.inputs import ComponentEnvelope
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
        mode = context.mode if isinstance(context.mode, str) else "unknown"
        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        host_application = metadata.get("host_application")
        if not isinstance(host_application, ComponentEnvelope):
            host_application = None
        if proposal is not None and proposal.status != "ok":
            error_reason: str | None = proposal.reason
        elif plan is None:
            error_reason = PLAN_MISSING_REASON
        else:
            error_reason = None
        with self._runtime_lock:
            self.refresh_selection(context.shared_memory)
            self.run_count += 1
            plugin = self._single()
            return self._decide(
                plugin,
                frame_id=context.frame_id,
                plan=plan if error_reason is None else None,
                mode=mode,
                error_reason=error_reason,
                host_application=host_application,
            )

    def _decide(
        self,
        plugin: ActionPlugin,
        *,
        frame_id: str,
        plan: ActionPlan | None,
        mode: str,
        error_reason: str | None,
        host_application: ComponentEnvelope | None,
    ) -> ActionResult:
        try:
            if error_reason is None:
                decision = plugin.decide(plan, mode=mode)
            else:
                decision = plugin.decide(None, mode=mode, error_reason=error_reason)
            if not isinstance(decision, ActionDecision):
                raise TypeError("action plugin must return an ActionDecision")
            proposed = _selected_command(plan)
            result = action_result(
                frame_id=frame_id,
                plugin_id=plugin.plugin_id,
                decision=decision,
                error_reason=error_reason,
                proposed=proposed,
                host_application=host_application,
                mode=mode,
            )
        except Exception as exc:  # noqa: BLE001 - the cycle still gets a held control
            self.failure_count += 1
            self.last_error = describe_exception(exc)
            return error_action_result(
                frame_id=frame_id,
                reason="step_internal_error",
                host_application=host_application,
                mode=mode,
            )
        self.last_error = None
        return result


def _selected_command(plan: ActionPlan | None) -> ProposedVehicleCommand | None:
    if plan is None:
        return None
    selected = plan.selected_candidate()
    if selected is None or selected.command is None:
        return None
    # The authority owns a detached copy of the selected command.
    return ProposedVehicleCommand.from_dict(selected.command.to_dict())


def action_result(
    *,
    frame_id: str,
    plugin_id: str,
    decision: ActionDecision,
    error_reason: str | None = None,
    proposed: ProposedVehicleCommand | None = None,
    host_application: ComponentEnvelope | None = None,
    mode: str = "unknown",
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
        host_application=host_application,
        drive_mode_gate=mode,
    )
    return ActionResult(
        frame_id=frame_id,
        status=status,
        reason=reason,
        authority=authority,
        control=decision.control,
    )


def error_action_result(
    *,
    frame_id: str,
    reason: str,
    host_application: ComponentEnvelope | None = None,
    mode: str = "unknown",
) -> ActionResult:
    """The fail-closed record when no action plugin could decide: hold."""

    hold = HoldAction()
    return action_result(
        frame_id=frame_id,
        plugin_id=hold.plugin_id,
        decision=hold.decide(None, mode=mode, error_reason=reason),
        error_reason=reason,
        host_application=host_application,
        mode=mode,
    )
