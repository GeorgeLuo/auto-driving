"""Shadow-proposals engine runner (M006-01..04)."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from autonomy.decision.action_plan import select_action_plan
from autonomy.decision.action_proposal import (
    MAX_PROPOSAL_BYTES,
    ActionProposal,
    ProposedVehicleCommand,
    synthetic_error_proposal,
)
from autonomy.decision.decision_data import (
    ComponentEnvelope,
    DecisionDataSource,
    build_decision_data_source,
    default_capabilities,
    ready_envelope,
)
from autonomy.decision.memory import MemorySnapshot, canonical_json_bytes
from autonomy.decision.observation import Observation
from autonomy.decision.shadow_authority import (
    ShadowDecisionCycleResult,
    authorized_idle_control,
    build_authority,
)
from autonomy.decision.shadow_ids import (
    ActionProposalMatrixError,
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
    """Activation config. Catalog membership is owned by the engine factory / plugins map."""

    enabled_plugins: tuple[str, ...] = DEFAULT_ENABLED_PLUGINS
    accepted_kinds: tuple[str, ...] = DEFAULT_ACCEPTED_KINDS
    retained_max_age_ms: int = DEFAULT_RETAINED_MAX_AGE_MS
    steer_magnitude: float = DEFAULT_STEER_MAGNITUDE

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
        if type(self.accepted_kinds) not in (list, tuple):
            raise ValueError("accepted_kinds must be a list or tuple of kind ids")
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
        if not math.isfinite(magnitude) or not (0.0 < magnitude <= 1.0):
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
    # Re-validate lifecycle matrix + byte bounds and admit a reconstructed copy
    # so post-construction mutation of nested storage cannot inflate candidates.
    try:
        plain = returned.to_dict()
        size = canonical_json_bytes(plain)
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
class ShadowProposalsEngine:
    """Minimal shadow-proposals engine: proposals may be nonzero; applied is idle."""

    config: ShadowProposalsConfig
    plugins: dict[str, Callable[[DecisionDataSource], ActionProposal]]

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
        observation: Observation | dict[str, Any] | None = None,
        observation_error: str | None = None,
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

        gate = drive_mode_gate if isinstance(drive_mode_gate, str) else "unknown"
        # Host-report boundary owned after valid entry (never raise TypeError out).
        if host_application is not None and not isinstance(
            host_application, ComponentEnvelope
        ):
            return _engine_error(
                frame_id=frame_id,
                reason="engine_internal_error",
                source=None,
                drive_mode_gate=gate,
            )

        def fail(reason: str, *, source: DecisionDataSource | None) -> tuple[
            ShadowDecisionCycleResult, AutonomyControl
        ]:
            return _engine_error(
                frame_id=frame_id,
                reason=reason,
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
                # Absent + no error ⇒ stage not configured for this unit.
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
            authority = build_authority(
                frame_id=frame_id,
                cycle_status="ok",
                cycle_reason="",
                proposed=proposed,
                host_application=host_application,
                drive_mode_gate=gate,
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
        except Exception:
            return fail("engine_internal_error", source=source)


def _engine_error(
    *,
    frame_id: str,
    reason: str,
    source: DecisionDataSource | None,
    host_application: ComponentEnvelope | None = None,
    drive_mode_gate: str = "unknown",
) -> tuple[ShadowDecisionCycleResult, AutonomyControl]:
    authority = build_authority(
        frame_id=frame_id,
        cycle_status="engine_error",
        cycle_reason=reason,
        proposed=None,
        host_application=host_application
        if isinstance(host_application, ComponentEnvelope)
        else None,
        drive_mode_gate=drive_mode_gate,
    )
    result = ShadowDecisionCycleResult(
        frame_id=frame_id,
        status="engine_error",
        reason=reason,
        source=source,
        plan=None,
        authority=authority,
    )
    return result, authorized_idle_control()