"""Shadow-proposals engine runner (M006-01..04)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence

from autonomy.decision.action_plan import ActionPlan, select_action_plan
from autonomy.decision.action_proposal import ActionProposal, synthetic_error_proposal
from autonomy.decision.decision_data import (
    ComponentEnvelope,
    DecisionDataSource,
    build_decision_data_source,
    default_capabilities,
    ready_envelope,
    unavailable_envelope,
)
from autonomy.decision.memory import MemorySnapshot
from autonomy.decision.observation import Observation
from autonomy.decision.shadow_authority import (
    ShadowAuthorityResult,
    ShadowDecisionCycleResult,
    authorized_idle_control,
    build_authority,
)
from autonomy.decision.shadow_ids import (
    ShadowCycleInputError,
    proposal_id_for,
    require_ascii_id,
    require_safe_int,
)
from autonomy.runtime.engine import AutonomyControl

ENGINE_ID = "shadow-proposals"
DEFAULT_ENABLED_PLUGINS = ("avoid_recent_obstruction",)
DEFAULT_ACCEPTED_KINDS = ("floor_boundary", "obstacle", "obstruction_evidence")
DEFAULT_RETAINED_MAX_AGE_MS = 1000
DEFAULT_STEER_MAGNITUDE = 0.35


class ProposalPlugin(Protocol):
    def __call__(self, source: DecisionDataSource) -> ActionProposal: ...


@dataclass(frozen=True)
class ShadowProposalsConfig:
    enabled_plugins: tuple[str, ...] = DEFAULT_ENABLED_PLUGINS
    accepted_kinds: tuple[str, ...] = DEFAULT_ACCEPTED_KINDS
    retained_max_age_ms: int = DEFAULT_RETAINED_MAX_AGE_MS
    steer_magnitude: float = DEFAULT_STEER_MAGNITUDE
    known_plugins: frozenset[str] = field(
        default_factory=lambda: frozenset(DEFAULT_ENABLED_PLUGINS)
    )

    def __post_init__(self) -> None:
        plugins = tuple(self.enabled_plugins)
        if not 1 <= len(plugins) <= 4:
            raise ValueError("enabled_plugins must contain 1..4 entries")
        if len(plugins) != len(set(plugins)):
            raise ValueError("enabled_plugins must be unique")
        for plugin_id in plugins:
            require_ascii_id(plugin_id, field_name="plugin_id")
            if plugin_id not in self.known_plugins:
                raise ValueError(f"unknown plugin_id {plugin_id!r}")
        kinds = tuple(self.accepted_kinds)
        if not kinds or len(kinds) > 8:
            raise ValueError("accepted_kinds must contain 1..8 entries")
        if len(kinds) != len(set(kinds)):
            raise ValueError("accepted_kinds must be unique")
        for kind in kinds:
            require_ascii_id(kind, field_name="accepted_kind")
        if type(self.retained_max_age_ms) is not int:
            raise ValueError("retained_max_age_ms must be a non-bool int")
        age = self.retained_max_age_ms
        if not 1 <= age <= 60_000:
            raise ValueError("retained_max_age_ms must be in 1..60000")
        object.__setattr__(self, "retained_max_age_ms", age)
        try:
            magnitude = float(self.steer_magnitude)
        except (TypeError, ValueError) as exc:
            raise ValueError("steer_magnitude must be numeric") from exc
        if not (0.0 < magnitude <= 1.0):
            raise ValueError("steer_magnitude must satisfy 0 < value <= 1")
        object.__setattr__(self, "steer_magnitude", magnitude)
        object.__setattr__(self, "enabled_plugins", plugins)
        object.__setattr__(self, "accepted_kinds", kinds)


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
    return returned


@dataclass
class ShadowProposalsEngine:
    """Minimal shadow-proposals engine: proposals may be nonzero; applied is idle."""

    config: ShadowProposalsConfig
    plugins: dict[str, Callable[[DecisionDataSource], ActionProposal]]

    @classmethod
    def create(
        cls,
        *,
        config: ShadowProposalsConfig,
        plugins: dict[str, Callable[[DecisionDataSource], ActionProposal]],
    ) -> "ShadowProposalsEngine":
        """Build an engine with caller-provided plugins (implementations own wiring)."""

        return cls(config=config, plugins=plugins)

    def run_cycle(
        self,
        *,
        frame_id: str,
        frame_index: int,
        timestamp_ms: int,
        observation: Observation | None = None,
        memory: MemorySnapshot | None = None,
        host_application: ComponentEnvelope | None = None,
        prior_host_applied_command: ComponentEnvelope | None = None,
        drive_mode_gate: str = "unknown",
        capabilities: ComponentEnvelope | None = None,
    ) -> tuple[ShadowDecisionCycleResult, AutonomyControl]:
        # Entry precondition — invalid identity never yields a cycle result.
        try:
            frame_id = require_ascii_id(frame_id, field_name="frame_id")
            frame_index = require_safe_int(frame_index, field_name="frame_index")
            timestamp_ms = require_safe_int(timestamp_ms, field_name="timestamp_ms")
        except ValueError as exc:
            raise ShadowCycleInputError(str(exc)) from exc

        try:
            source = build_decision_data_source(
                frame_id=frame_id,
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                observation=observation,
                memory=memory,
                capabilities=capabilities
                or ready_envelope(default_capabilities(), updated_at_ms=timestamp_ms),
                prior_host_applied_command=prior_host_applied_command,
            )
        except Exception:
            authority = build_authority(
                frame_id=frame_id,
                cycle_status="engine_error",
                cycle_reason="decision_data_source_invalid",
                proposed=None,
                host_application=host_application,
                drive_mode_gate=drive_mode_gate,
            )
            result = ShadowDecisionCycleResult(
                frame_id=frame_id,
                status="engine_error",
                reason="decision_data_source_invalid",
                source=None,
                plan=None,
                authority=authority,
            )
            return result, authorized_idle_control()

        candidates: list[ActionProposal] = []
        for plugin_id in sorted(self.config.enabled_plugins):
            plugin = self.plugins.get(plugin_id)
            if plugin is None:
                candidates.append(
                    synthetic_error_proposal(
                        plugin_id=plugin_id,
                        frame_id=frame_id,
                        reason="plugin_invalid_return",
                    )
                )
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
            except Exception:
                authority = build_authority(
                    frame_id=frame_id,
                    cycle_status="engine_error",
                    cycle_reason="synthetic_error_proposal_failed",
                    proposed=None,
                    host_application=host_application,
                    drive_mode_gate=drive_mode_gate,
                )
                result = ShadowDecisionCycleResult(
                    frame_id=frame_id,
                    status="engine_error",
                    reason="synthetic_error_proposal_failed",
                    source=source,
                    plan=None,
                    authority=authority,
                )
                return result, authorized_idle_control()

        try:
            if len(candidates) != len(self.config.enabled_plugins):
                raise ValueError("candidate count mismatch")
            plan = select_action_plan(
                frame_id=frame_id,
                timestamp_ms=timestamp_ms,
                candidates=candidates,
            )
        except Exception:
            authority = build_authority(
                frame_id=frame_id,
                cycle_status="engine_error",
                cycle_reason="action_plan_invariant_violated",
                proposed=None,
                host_application=host_application,
                drive_mode_gate=drive_mode_gate,
            )
            result = ShadowDecisionCycleResult(
                frame_id=frame_id,
                status="engine_error",
                reason="action_plan_invariant_violated",
                source=source,
                plan=None,
                authority=authority,
            )
            return result, authorized_idle_control()

        selected = plan.selected_candidate()
        proposed = selected.command if selected is not None else None
        authority = build_authority(
            frame_id=frame_id,
            cycle_status="ok",
            cycle_reason="",
            proposed=proposed,
            host_application=host_application,
            drive_mode_gate=drive_mode_gate,
        )
        result = ShadowDecisionCycleResult(
            frame_id=frame_id,
            status="ok",
            reason="",
            source=source,
            plan=plan,
            authority=authority,
        )
        return result, authorized_idle_control()
