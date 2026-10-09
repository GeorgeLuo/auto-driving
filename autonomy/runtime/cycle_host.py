"""Host one decision cycle on a vehicle loop.

``AutonomyCycleHost`` owns the host map every step's plugins share, runs the
cycle once per frame, applies its output through ControlExecution, and keeps
the last result. A target-less host only computes decisions. ``set_step`` swaps
one step's runner between frames, and ``status`` reports each step runner's
status plus the applied decision identity. ``watch_selection`` records the
activation files a running host follows. ``run`` synchronizes those selections
before the cycle and records the applied decision identity afterward. Changed
specs or configs need a restart.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from autonomy.decision_cycle.activation import (
    DECISION_STEPS,
    STEPS,
    StepActivation,
    activation_generation_id,
    read_step_activation,
    require_step,
)
from autonomy.decision_cycle.cycle import (
    DecisionCycle,
    DecisionCycleResult,
    DecisionFrameContext,
    DecisionSteps,
)
from autonomy.decision_cycle.steps import decision_steps, load_decision_steps, snapshot_step_activations
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlExecution, ControlTarget
from autonomy.runtime.session import RunConfiguration
from autonomy.runtime.recording import RunRecording
from autonomy.plugins import LocalPluginCatalog
from autonomy.shared_memory import SharedMemory

IDLE_REASON = "cycle-idle"
# Steps whose restaged plugin selection a running host applies between frames.
# Other steps, and changed specs or configs, take effect on a restart.
LIVE_SELECTION_STEPS = ("perception", "memory", "proposal")


class AutonomyCycleHost:
    """Run the decision cycle around the host map."""

    def __init__(self, *, steps: DecisionSteps | None = None, target: ControlTarget | None = None) -> None:
        self._lock = threading.RLock()
        self.execution = ControlExecution(target) if target is not None else None
        self.cycle = DecisionCycle(steps or decision_steps(), idle_reason=IDLE_REASON)
        self.catalog = LocalPluginCatalog()
        for step in STEPS:
            self._attach_catalog(self.step(step))
        self.shared_memory: SharedMemory = {}
        self.last_result: DecisionCycleResult | None = None
        self.last_context: DecisionFrameContext | None = None
        self._session_lock = threading.RLock()
        self.configuration = RunConfiguration(mode="manual")
        self.run_state = "stopped"
        self.run_decisions = 0
        self.recording: RunRecording | None = None
        self._run_generation = 0
        self.cycle_count = 0
        self.error_count = 0
        self.last_error: str | None = None
        self._status_providers: dict[str, Callable[[], dict[str, Any]]] = {}
        self._watched: dict[str, tuple[Path, StepActivation]] = {}
        self._pending_decision: dict[str, StepActivation] = {}
        self._applied_decision: dict[str, Any] | None = None

    @classmethod
    def from_runtime(cls, runtime_root: Path, *, target: ControlTarget | None = None) -> "AutonomyCycleHost":
        """Load every step from ``runtime_root/<step>/active.json``."""

        return cls(steps=load_decision_steps(runtime_root), target=target)

    @property
    def steps(self) -> DecisionSteps:
        return self.cycle.steps

    def step(self, step: str) -> Any:
        return getattr(self.cycle.steps, require_step(step))

    def set_step(self, step: str, runner: Any) -> None:
        """Replace one step's runner; the next frame uses it."""

        with self._lock:
            self._attach_catalog(runner)
            self.cycle.steps = replace(self.cycle.steps, **{require_step(step): runner})

    def _attach_catalog(self, runner: Any) -> None:
        manager = getattr(runner, "plugin_manager", None)
        if manager is not None:
            self.catalog.include(manager.available)
            manager.resolver = self.catalog

    def watch_selection(self, step: str, path: Path, loaded: StepActivation) -> None:
        """Follow ``path`` for selection changes to the runner loaded from ``loaded``."""

        with self._lock:
            self._watched[require_step(step)] = (Path(path), loaded)

    def follow_activations(self, activations: Mapping[str, StepActivation], runtime_root: Path) -> None:
        """Watch staged selections and seed identity from the runners actually loaded."""

        from autonomy.decision_cycle.activation import step_activation_path

        for step in LIVE_SELECTION_STEPS:
            if step in activations:
                self.watch_selection(step, step_activation_path(runtime_root, step), activations[step])
        loaded = snapshot_step_activations(self.steps)
        decision = {step: loaded[step] for step in DECISION_STEPS}
        self.use_applied_decision({
            "generation_id": activation_generation_id(decision, prefix="decision"),
            "steps": decision,
        })

    def sync_selection(self) -> dict[str, StepActivation]:
        """Select each watched step's restaged plugin IDs before the next frame.

        Returns the activation each changed step now requests. The runner
        applies the selection during its next call.
        """

        with self._lock:
            requested = {}
            for step, (path, loaded) in self._watched.items():
                live = sync_live_selection(self.step(step), path, loaded)
                if live is not None:
                    requested[step] = live
                    if step in DECISION_STEPS:
                        self._pending_decision[step] = live
            return requested

    def use_applied_decision(self, identity: Mapping[str, Any]) -> None:
        """Seed the decision identity published until a restaged selection applies."""

        generation_id = identity.get("generation_id") if isinstance(identity, Mapping) else None
        steps = identity.get("steps") if isinstance(identity, Mapping) else None
        if not isinstance(generation_id, str) or not isinstance(steps, Mapping):
            raise ValueError("applied decision identity needs a generation_id and steps")
        with self._lock:
            self._applied_decision = {
                "generation_id": generation_id,
                "steps": deepcopy(dict(steps)),
            }

    def applied_decision(self) -> dict[str, Any] | None:
        """The proposal, plan, and action identity this host is running."""

        with self._lock:
            if self._applied_decision is None:
                return None
            return deepcopy(self._applied_decision)

    def _record_applied_decision(self) -> None:
        """Retain pending activations until their decision steps apply them."""

        applied = self._applied_decision
        if applied is None or not self._pending_decision:
            return
        steps = dict(applied["steps"])
        adopted = []
        for step, live in self._pending_decision.items():
            runner = self.step(step)
            applied_ids = tuple(getattr(runner, "plugin_ids", ()) or ())
            if applied_ids != tuple(live.plugins):
                continue
            steps[step] = live.to_payload()
            adopted.append(step)
        if not adopted:
            return
        try:
            generation_id = activation_generation_id(steps, prefix="decision")
        except (TypeError, ValueError):
            return
        self._applied_decision = {"generation_id": generation_id, "steps": steps}
        for step in adopted:
            del self._pending_decision[step]

    def run(self, context: DecisionFrameContext) -> DecisionCycleResult:
        with self._session_lock:
            run_generation = self._run_generation
        ticket = self.execution.ticket() if self.execution is not None else None
        if ticket is not None:
            context = replace(context, mode=ticket.mode)
        with self._lock:
            if context.shared_memory is None:
                context = replace(context, shared_memory=self.shared_memory)
            else:
                self.shared_memory = context.shared_memory
            try:
                self.sync_selection()
                result = self.cycle.run(context)
                result = replace(result, context=self._record_frame_context(result.context))
                if self.execution is not None:
                    result = replace(result, application=self.execution.apply(result, ticket))
                with self._session_lock:
                    if self.run_state == "running" and run_generation == self._run_generation:
                        if self.recording is not None:
                            self.recording.append(result)
                        self.run_decisions += 1
                        if (
                            self.configuration.num_decisions
                            and self.run_decisions >= self.configuration.num_decisions
                        ):
                            self.stop(reason="completed")
                self.cycle_count += 1
                self.last_error = None
                self.last_result = result
                self._record_applied_decision()
            except Exception as exc:
                self._record_frame_context(context)
                try:
                    if self.execution is not None:
                        self.execution.cycle_failed(ticket)
                finally:
                    with self._session_lock:
                        if run_generation == self._run_generation:
                            self.stop(reason="error")
                self.error_count += 1
                self.last_error = "".join(
                    traceback.format_exception_only(type(exc), exc)
                ).strip()
                raise
            return result

    def _record_frame_context(self, context: DecisionFrameContext) -> DecisionFrameContext:
        """Retain the applied selections even when a frame produces no cycle result."""
        self.last_context = replace(context, metadata={
            **context.metadata, "step_activations": snapshot_step_activations(self.steps),
        })
        return self.last_context

    def start(self, configuration: RunConfiguration | None = None, *,
              recording: RunRecording | None = None) -> dict[str, Any]:
        configuration = configuration or RunConfiguration()
        with self._session_lock:
            self.stop()
            self.configuration = configuration
            self.recording = recording
            self.run_decisions = 0
            self.set_mode(configuration.mode)
            self._run_generation += 1
            self.run_state = "running"
            return self.session_status()

    def stop(self, *, reason: str = "stopped") -> dict[str, Any]:
        with self._session_lock:
            self._run_generation += 1
            self.run_state = reason
            if self.execution is not None and not self.execution.status()["closed"]:
                self.set_mode("manual")
            return self.session_status()

    def session_status(self) -> dict[str, Any]:
        with self._session_lock:
            return {
                "status": self.run_state,
                "configuration": self.configuration.to_dict(),
                "processed_decisions": self.run_decisions,
                "recording": self.recording.status() if self.recording is not None else None,
                "execution": self.execution.status() if self.execution is not None else None,
            }

    def set_mode(self, mode: str) -> dict[str, Any]:
        if self.execution is None:
            raise RuntimeError("host has no control target")
        return self.execution.set_mode(mode)

    def close(self) -> None:
        """End the run and release control without raising on a failed stop."""
        with self._session_lock:
            if self.execution is not None and self.execution.status()["closed"]:
                return
            self._run_generation += 1
            self.run_state = "stopped"
            if self.execution is not None:
                self.execution.close()

    def register_status_provider(
        self, component_id: str, provider: Callable[[], dict[str, Any]]
    ) -> None:
        """Report a host component (not a step) under ``status()["components"]``."""

        if not component_id or not callable(provider):
            raise ValueError("runtime status providers require an id and callable")
        with self._lock:
            self._status_providers[component_id] = provider

    @property
    def last_control(self) -> AutonomyControl | None:
        result = self.last_result
        return result.control if result is not None else None

    def status(self) -> dict[str, Any]:
        with self._lock:
            steps: dict[str, Any] = {}
            for step in STEPS:
                runner = getattr(self.cycle.steps, step)
                report = getattr(runner, "status", None)
                if runner is None:
                    steps[step] = None
                elif callable(report):
                    try:
                        steps[step] = report()
                    except Exception as exc:  # noqa: BLE001 - status must not fail the host
                        steps[step] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                else:
                    steps[step] = {"runner": type(runner).__name__}
            components: dict[str, Any] = {}
            for component_id, provider in self._status_providers.items():
                try:
                    components[component_id] = provider()
                except Exception as exc:  # noqa: BLE001 - status must not fail the host
                    components[component_id] = {
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            return {
                "session": self.session_status(),
                "execution": self.execution.status() if self.execution is not None else None,
                "steps": steps,
                "applied_decision": self.applied_decision(),
                "components": components,
                "cycle_count": self.cycle_count,
                "error_count": self.error_count,
                "last_error": self.last_error,
                "last_control": (
                    self.last_control.to_dict() if self.last_control is not None else None
                ),
                "last_cycle": self.last_result.to_dict() if self.last_result is not None else None,
            }

    def reset_memory(self) -> dict[str, Any] | None:
        """Reset the memory step when present.

        Clears the host map and keeps only the fresh state memory plugins
        wrote while resetting (such as a new epoch). Returns the memory report
        after the reset, or ``None`` when no memory step is configured.
        """

        with self._lock:
            memory = self.cycle.steps.memory
            if memory is None:
                self.shared_memory.clear()
                return None
            if not callable(getattr(memory, "reset", None)) or not callable(
                getattr(memory, "report", None)
            ):
                raise TypeError("configured memory step does not support reset")
            fresh = memory.reset(self.shared_memory)
            self.shared_memory.clear()
            self.shared_memory.update(fresh)
            return memory.report()


def sync_live_selection(
    runner: Any, activation_path: Path, loaded: StepActivation
) -> StepActivation | None:
    """Select the staged plugin IDs when only the selection changed since loading.

    Returns the live activation when it changed the runner's selection.
    """

    try:
        live = read_step_activation(activation_path, loaded.step)
    except (OSError, ValueError, TypeError):
        # An incomplete or stale activation must not replace the current set.
        return None
    if live.plugin_specs != loaded.plugin_specs or live.plugin_configs != loaded.plugin_configs:
        return None
    manager = getattr(runner, "plugin_manager", None)
    if manager is None or tuple(live.plugins) == tuple(manager.selected_ids):
        return None
    try:
        manager.select(live.plugins)
    except Exception:  # noqa: BLE001 - a bad selection keeps the applied plugins
        return None
    return live
