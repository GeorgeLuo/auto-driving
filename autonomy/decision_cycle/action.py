"""Action composition for one cycle.

``ActionComposition`` builds the proposal input, invokes the configured
proposal plugins, admits their candidates, selects a plan, and asks its gate
for the control to apply. The gate's authority record states whether that
control is the selected command. The proposal protocol, configuration,
admission, and invocation stay in this module until the proposal step has its
own plugin files.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from autonomy.decision_cycle.planning.selector import select_action_plan
from autonomy.decision_cycle.proposal.values import (
    MAX_PROPOSAL_BYTES,
    ActionProposal,
    ProposedVehicleCommand,
    synthetic_error_proposal,
)
from autonomy.decision_cycle.proposal.inputs import (
    ComponentEnvelope,
    DecisionDataSource,
    build_decision_data_source,
    default_capabilities,
    omit_forbidden_channel_keys,
    ready_envelope,
)
from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.snapshots.values import MemorySnapshot
from autonomy.serialization import canonical_json_size_bytes
from autonomy.decision_cycle.observation.values import Observation
from autonomy.decision_cycle.action_gate.hold import HoldGate
from autonomy.decision_cycle.action_gate.values import ActionGate, build_authority
from autonomy.decision_cycle.result import ActionResult
from autonomy.decision_cycle.action_identifiers import (
    ActionInputError,
    ActionProposalMatrixError,
    proposal_id_for,
    require_ascii_id,
    require_safe_int,
)


class ProposalPlugin(Protocol):
    def __call__(self, source: DecisionDataSource) -> ActionProposal: ...


@dataclass(frozen=True)
class ProposalConfig:
    """Activation config. Catalog membership is owned by the engine factory / plugins map."""

    enabled_plugins: tuple[str, ...]

    def __post_init__(self) -> None:
        # Require real sequences of ids — reject str (char-iter) and other coercible shapes.
        if type(self.enabled_plugins) not in (list, tuple):
            raise ValueError("enabled_plugins must be a list or tuple of plugin ids")
        plugins = tuple(self.enabled_plugins)
        if not 1 <= len(plugins) <= 4:
            raise ValueError("enabled_plugins must contain 1..4 entries")
        if len(plugins) != len(set(plugins)):
            raise ValueError("enabled_plugins must be unique")
        for plugin_id in plugins:
            require_ascii_id(plugin_id, field_name="plugin_id")
        object.__setattr__(self, "enabled_plugins", plugins)


def _admit_candidate(
    *,
    returned: object,
    invoked_plugin_id: str,
    frame_id: str,
    raised: BaseException | None = None,
) -> ActionProposal:
    if raised is not None:
        return synthetic_error_proposal(
            plugin_id=invoked_plugin_id,
            frame_id=frame_id,
            reason="plugin_exception",
        )
    if not isinstance(returned, ActionProposal):
        return synthetic_error_proposal(
            plugin_id=invoked_plugin_id,
            frame_id=frame_id,
            reason="plugin_invalid_return",
        )
    expected_id = proposal_id_for(invoked_plugin_id, frame_id)
    if (
        returned.plugin_id != invoked_plugin_id
        or returned.proposal_id != expected_id
    ):
        return synthetic_error_proposal(
            plugin_id=invoked_plugin_id,
            frame_id=frame_id,
            reason="plugin_invalid_return",
        )
    # Re-validate lifecycle matrix + byte bounds and admit a reconstructed copy
    # so post-construction mutation of nested storage cannot inflate candidates.
    try:
        plain = returned.to_dict()
        size = canonical_json_size_bytes(plain)
        if size > MAX_PROPOSAL_BYTES:
            raise ActionProposalMatrixError(
                f"admitted proposal serializes to {size} bytes; max {MAX_PROPOSAL_BYTES}"
            )
        validated = ActionProposal.from_dict(plain)
    except ActionProposalMatrixError:
        raise
    except Exception as exc:
        raise ActionProposalMatrixError(
            f"candidate fails lifecycle/bounds matrix: {exc}"
        ) from exc
    if (
        validated.plugin_id != invoked_plugin_id
        or validated.proposal_id != expected_id
        or validated.frame_id != frame_id
    ):
        return synthetic_error_proposal(
            plugin_id=invoked_plugin_id,
            frame_id=frame_id,
            reason="plugin_invalid_return",
        )
    return validated




@dataclass
class ActionComposition:
    """Propose, plan, and gate one cycle's action."""

    config: ProposalConfig
    plugins: dict[str, Callable[[DecisionDataSource], ActionProposal]]
    gate: ActionGate = field(default_factory=HoldGate)
    # Activation document supplied by the implementation. The runner does not read it.
    reported_config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Activation membership is the plugins map (catalog), not a self-declared set.
        if not isinstance(self.plugins, dict):
            raise TypeError("plugins must be a dict of plugin_id -> callable")
        for plugin_id in self.config.enabled_plugins:
            if plugin_id not in self.plugins:
                raise ValueError(f"unknown plugin_id {plugin_id!r}")

    @classmethod
    def create(
        cls,
        *,
        config: ProposalConfig,
        plugins: dict[str, Callable[[DecisionDataSource], ActionProposal]],
        gate: ActionGate | None = None,
    ) -> "ActionComposition":
        """Build a composition with caller-provided plugins (implementations own wiring)."""

        return cls(config=config, plugins=plugins, gate=gate or HoldGate())

    def act(
        self,
        context: DecisionFrameContext,
        perception: Any,
        observation: Observation | dict[str, Any] | None,
        memory: MemorySnapshot | None,
    ) -> ActionResult:
        """Run the composition as the cycle's action step.

        ``context.metadata`` may carry ``observation_error`` and host-reported
        envelopes (``host_application``, ``prior_host_applied_command``,
        ``capabilities``). ``context.mode`` is the drive mode given to the gate.
        """

        if isinstance(observation, Observation):
            observation = Observation.from_dict(
                omit_forbidden_channel_keys(observation.to_dict())
            )
        elif isinstance(observation, dict):
            observation = omit_forbidden_channel_keys(observation)
        else:
            observation = None
        metadata = context.metadata if isinstance(context.metadata, dict) else {}
        observation_error = metadata.get("observation_error")

        def envelope(key: str) -> ComponentEnvelope | None:
            value = metadata.get(key)
            return value if isinstance(value, ComponentEnvelope) else None

        return self.run(
            frame_id=context.frame_id,
            frame_index=context.frame_index,
            timestamp_ms=context.timestamp_ms,
            observation=observation,
            observation_error=observation_error if type(observation_error) is str else None,
            # Retained evidence from shared_memory["decision.snapshot"].
            memory=memory if isinstance(memory, MemorySnapshot) else None,
            host_application=envelope("host_application"),
            prior_host_applied_command=envelope("prior_host_applied_command"),
            drive_mode_gate=context.mode if isinstance(context.mode, str) else "unknown",
            capabilities=envelope("capabilities"),
        )

    def run(
        self,
        *,
        frame_id: str,
        frame_index: int,
        timestamp_ms: int,
        observation: Observation | dict[str, Any] | None = None,
        observation_error: str | None = None,
        # Retained evidence from shared_memory["decision.snapshot"].
        memory: MemorySnapshot | None = None,
        host_application: ComponentEnvelope | None = None,
        prior_host_applied_command: ComponentEnvelope | None = None,
        drive_mode_gate: str = "unknown",
        capabilities: ComponentEnvelope | None = None,
    ) -> ActionResult:
        # Entry precondition — invalid identity never yields a cycle result.
        try:
            frame_id = require_ascii_id(frame_id, field_name="frame_id")
            frame_index = require_safe_int(frame_index, field_name="frame_index")
            timestamp_ms = require_safe_int(timestamp_ms, field_name="timestamp_ms")
        except ValueError as exc:
            raise ActionInputError(str(exc)) from exc

        gate = drive_mode_gate if isinstance(drive_mode_gate, str) else "unknown"
        # Host-report boundary owned after valid entry (never raise TypeError out).
        if host_application is not None and not isinstance(
            host_application, ComponentEnvelope
        ):
            return error_result(
                frame_id=frame_id,
                reason="engine_internal_error",
                gate=self.gate,
                source=None,
                drive_mode_gate=gate,
            )

        def fail(reason: str, *, source: DecisionDataSource | None) -> ActionResult:
            return error_result(
                frame_id=frame_id,
                reason=reason,
                gate=self.gate,
                source=source,
                host_application=host_application,
                drive_mode_gate=gate,
            )

        # Observation: None → unconfigured; dict/Observation → ready; error → error.
        if observation is not None and not isinstance(
            observation, (Observation, dict)
        ):
            return fail("decision_data_source_invalid", source=None)
        if observation_error is not None and type(observation_error) is not str:
            return fail("decision_data_source_invalid", source=None)

        try:
            source = build_decision_data_source(
                frame_id=frame_id,
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                observation=observation,
                observation_error=observation_error,
                # Absent + no error ⇒ step not configured for this unit.
                observation_configured=False,
                memory=memory,
                capabilities=capabilities
                or ready_envelope(default_capabilities(), updated_at_ms=timestamp_ms),
                prior_host_applied_command=prior_host_applied_command,
            )
        except Exception:
            return fail("decision_data_source_invalid", source=None)

        candidates: list[ActionProposal] = []
        try:
            for plugin_id in sorted(self.config.enabled_plugins):
                plugin = self.plugins.get(plugin_id)
                if plugin is None:
                    try:
                        candidates.append(
                            synthetic_error_proposal(
                                plugin_id=plugin_id,
                                frame_id=frame_id,
                                reason="plugin_invalid_return",
                            )
                        )
                    except Exception:
                        return fail("synthetic_error_proposal_failed", source=source)
                    continue
                raised: BaseException | None = None
                returned: object = None
                try:
                    # Isolate nested state so one plugin cannot mutate another's view.
                    returned = plugin(deepcopy(source))
                except BaseException as exc:  # noqa: BLE001 - fail closed per proposal
                    raised = exc
                try:
                    candidates.append(
                        _admit_candidate(
                            returned=returned,
                            invoked_plugin_id=plugin_id,
                            frame_id=frame_id,
                            raised=raised,
                        )
                    )
                except ActionProposalMatrixError:
                    return fail("action_proposal_matrix_violated", source=source)
                except Exception:
                    return fail("synthetic_error_proposal_failed", source=source)

            if len(candidates) != len(self.config.enabled_plugins):
                return fail("action_plan_invariant_violated", source=source)
            try:
                plan = select_action_plan(
                    frame_id=frame_id,
                    timestamp_ms=timestamp_ms,
                    candidates=candidates,
                )
            except Exception:
                return fail("action_plan_invariant_violated", source=source)
            selected = plan.selected_candidate()
            # Authority owns a detached copy of the selected command (not an alias).
            proposed: ProposedVehicleCommand | None = None
            if selected is not None and selected.command is not None:
                proposed = ProposedVehicleCommand.from_dict(selected.command.to_dict())
            decision = self.gate.decide(plan, mode=gate)
            authority = build_authority(
                frame_id=frame_id,
                gate_id=self.gate.gate_id,
                decision=decision,
                cycle_status="ok",
                cycle_reason="",
                proposed=proposed,
                host_application=host_application,
                drive_mode_gate=gate,
            )
            return ActionResult(
                frame_id=frame_id,
                status="ok",
                reason="",
                source=source,
                plan=plan,
                authority=authority,
                control=decision.control,
            )
        except Exception:
            return fail("engine_internal_error", source=source)


def error_result(
    *,
    frame_id: str,
    reason: str,
    gate: ActionGate | None = None,
    source: DecisionDataSource | None = None,
    host_application: ComponentEnvelope | None = None,
    drive_mode_gate: str = "unknown",
) -> ActionResult:
    """Return the fail-closed result for ``reason``; the gate still chooses the control."""

    gate = gate or HoldGate()
    decision = gate.decide(None, mode=drive_mode_gate, error_reason=reason)
    authority = build_authority(
        frame_id=frame_id,
        gate_id=gate.gate_id,
        decision=decision,
        cycle_status="engine_error",
        cycle_reason=reason,
        proposed=None,
        host_application=host_application
        if isinstance(host_application, ComponentEnvelope)
        else None,
        drive_mode_gate=drive_mode_gate,
    )
    return ActionResult(
        frame_id=frame_id,
        status="engine_error",
        reason=reason,
        source=source,
        plan=None,
        authority=authority,
        control=decision.control,
    )
