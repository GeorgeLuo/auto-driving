"""Host one decision cycle on a vehicle loop.

``AutonomyCycleHost`` owns the host map every step's plugins share, runs the
cycle once per frame, applies its output through ControlExecution, and keeps
the last result. A target-less host only computes decisions. ``status`` reports
each fixed runner's status plus the applied pipeline identity. ``watch_selection``
records the activation files a running host follows. ``run`` adopts a
successfully built replacement pipeline before binding the frame's map. Arm
and disarm submit a desired selection. A runner's plugins do not change in
place. Staged files follow plugin-ID changes when their specs and configs
still match the loaded activation.
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
    STEPS,
    StepActivation,
    activation_for_definitions,
    activation_generation_id,
    executable_document,
    selected_definitions,
    read_step_activation,
    require_step,
    stamp_source,
    step_activation_from_payload,
)
from autonomy.decision_cycle.cycle import (
    DecisionCycle,
    DecisionCycleResult,
    DecisionFrameContext,
    DecisionSteps,
    timestamp_ms,
)
from autonomy.decision_cycle.steps import (
    STEP_RUNNERS, decision_steps, load_decision_steps, applied_step_activations,
)
from autonomy.decision_cycle.action.publication import write_host_inputs
from autonomy.runtime.control import AutonomyControl
from autonomy.runtime.execution import ControlExecution, ControlTarget
from autonomy.runtime.session import RunConfiguration
from autonomy.runtime.recording import INSTALLED_RELEASE, RunRecording
from autonomy.plugins import LocalPluginCatalog, PluginDefinition, PluginManagementError
from autonomy.runtime.plugin_catalog import PluginCatalogAPI, describe_plugin
from autonomy.shared_memory import SharedMemory

IDLE_REASON = "cycle-idle"


# A status read waits this long for a running cycle, then answers with the
# step reports from the last refresh, so a slow plugin never hides the host.
STATUS_WAIT_S = 0.05


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
        self._session_view: dict[str, Any] = {}
        self.configuration = RunConfiguration(mode="manual", num_decisions=None)
        self.run_state = "pre_start"
        self.run_decisions = 0
        self.recording: RunRecording | None = None
        self._run_generation = 0
        self.cycle_count = 0
        self.error_count = 0
        self.last_error: str | None = None
        self._status_providers: dict[str, Callable[[], dict[str, Any]]] = {}
        self._host_name: str | None = None
        self._watched: dict[str, tuple[Path, StepActivation]] = {}
        self._last_staged_ids: dict[str, tuple[str, ...]] = {}
        self._applied_activations: dict[str, StepActivation | None] = {}
        self._pending_steps: DecisionSteps | None = None
        self._pending_activations: dict[str, StepActivation | None] | None = None
        self._fixed_steps: set[str] = set()
        self._selection_error: str | None = None
        self._arm_request_id = 0
        self._applied_decision: dict[str, Any] | None = None
        self._cycle_in_progress: dict[str, Any] | None = None
        self._pipeline_report: dict[str, Any] = {}
        self._step_reports_wanted = False
        self.plugin_catalog = PluginCatalogAPI(
            self.catalog, arm=self.arm, disarm=self.disarm, selection_status=self.selection_status,
            applied_decision=self.applied_decision,
        )
        # A host whose cycles run back to back may never free the lock for a
        # status read, so its steps and arming are reported from the start.
        self._applied_activations = self._runner_activations()
        self._record_applied_decision()
        self._refresh_step_reports()
        self.selection_status()

    @classmethod
    def from_runtime(cls, runtime_root: Path, *, target: ControlTarget | None = None) -> "AutonomyCycleHost":
        """Load every step from ``runtime_root/<step>/active.json``."""

        return cls(steps=load_decision_steps(runtime_root), target=target)

    @property
    def steps(self) -> DecisionSteps:
        return self.cycle.steps

    def step(self, step: str) -> Any:
        return getattr(self.cycle.steps, require_step(step))

    def _attach_catalog(self, runner: Any) -> None:
        activation = getattr(runner, "activation", None)
        if not isinstance(activation, StepActivation):
            return
        known = {definition.plugin_id: definition for definition in selected_definitions(activation)}
        for plugin_id, spec in activation.plugin_specs.items():
            known.setdefault(plugin_id, PluginDefinition(
                activation.step, plugin_id, spec, config=activation.plugin_configs.get(plugin_id, {}),
            ))
        self.catalog.include(known.values())

    def watch_selection(self, step: str, path: Path, loaded: StepActivation) -> None:
        """Follow ``path`` for selection changes to the runner loaded from ``loaded``."""

        with self._lock:
            self._watched[require_step(step)] = (Path(path), loaded)
            self._last_staged_ids[step] = tuple(loaded.plugins)

    def follow_activations(self, activations: Mapping[str, StepActivation], runtime_root: Path) -> None:
        """Watch staged selections and seed identity from the runners actually loaded."""

        from autonomy.decision_cycle.activation import step_activation_path

        for step in STEPS:
            if step in activations:
                self.watch_selection(step, step_activation_path(runtime_root, step), activations[step])
        self._applied_activations = self._runner_activations()
        self._record_applied_decision()
        self._refresh_step_reports()

    def note_host_name(self, host_name: str) -> None:
        """Stamp ``host_name`` on the pipeline before its generation is published."""

        if not isinstance(host_name, str) or not host_name:
            raise ValueError("host_name must be a non-empty string")
        with self._lock:
            self._host_name = host_name
            self._applied_activations = {
                step: self._stamp(item) if item is not None else None
                for step, item in self._applied_activations.items()
            }
            pending = self._pending_activations
            self._record_applied_decision()
            if pending is not None:
                self._stage_pipeline(pending)
            self._refresh_step_reports()

    def sync_selection(self) -> dict[str, StepActivation]:
        """Stage a file whose plugin IDs changed and whose specs and configs did not.

        An unchanged file does not replace a later arm or disarm. A file whose
        specs or configs differ is ignored until the process restarts.
        """

        with self._lock:
            requested: dict[str, StepActivation] = {}
            updates: dict[str, StepActivation] = {}
            for step, (path, loaded) in self._watched.items():
                live = sync_live_selection(
                    path, loaded, previous_ids=self._last_staged_ids.get(step),
                )
                if live is None:
                    continue
                try:
                    staged = replace(loaded, plugins=tuple(live.plugins))
                except (TypeError, ValueError, PluginManagementError) as exc:
                    self._selection_error = f"{step}: {type(exc).__name__}: {exc}"
                    continue
                updates[step] = staged
                requested[step] = staged
                self._last_staged_ids[step] = tuple(staged.plugins)
            if not updates:
                return requested
            self._stage_pipeline(self._with_updates(updates))
            return requested

    def arm(self, selections: Mapping[str, Any]) -> dict[str, Any]:
        """Stage the selection named plugins resolve to. Untouched plugins keep their definitions.

        A step that runs exactly one plugin swaps it for the named definitions.
        Construction answers the request now. The running pipeline changes at
        the next frame, and only when construction succeeded.
        """

        if not isinstance(selections, Mapping) or not selections:
            raise ValueError("arming requires a selection map of steps to ordered plugin IDs")
        with self._lock:
            _, available = self.catalog.read()
            known = {(item.step, item.plugin_id): item for item in available}
            updates: dict[str, StepActivation] = {}
            for step, ids in selections.items():
                require_step(step)
                if not isinstance(ids, (list, tuple)) or not all(isinstance(item, str) for item in ids):
                    raise ValueError(f"{step} selection must be an ordered list of plugin IDs")
                base = self._base_activations().get(step)
                current = _definitions_by_id(base)
                if STEP_RUNNERS[step].single_plugin:
                    chosen = [_require_catalog(known, step, plugin_id) for plugin_id in ids]
                else:
                    chosen = []
                    named = list(ids)
                    for plugin_id, definition in current.items():
                        if plugin_id in named:
                            chosen.append(_require_catalog(known, step, plugin_id))
                        else:
                            chosen.append(definition)
                    for plugin_id in named:
                        if plugin_id not in current:
                            chosen.append(_require_catalog(known, step, plugin_id))
                updates[step] = activation_for_definitions(
                    step, tuple(chosen),
                    metadata=dict(base.metadata) if base is not None else {},
                    source_path=base.source_path if base is not None else None,
                )
            self._stage_pipeline(self._with_updates(updates))
            return self._selection_result()

    def disarm(self, selections: Mapping[str, Any], *, all_plugins: bool = False) -> dict[str, Any]:
        """Stage the selection with named plugins removed. An absent step stays absent."""

        if not isinstance(all_plugins, bool):
            raise ValueError("disarming all must be a boolean")
        if not isinstance(selections, Mapping) or not selections:
            raise ValueError("disarming requires a selection map of steps to ordered plugin IDs")
        with self._lock:
            _, available = self.catalog.read()
            known = {(item.step, item.plugin_id) for item in available}
            updates: dict[str, StepActivation] = {}
            for step, ids in selections.items():
                require_step(step)
                if not isinstance(ids, (list, tuple)) or not all(isinstance(item, str) for item in ids):
                    raise ValueError(f"{step} selection must be an ordered list of plugin IDs")
                if all_plugins and ids:
                    raise ValueError("disarming all cannot include plugin IDs")
                for plugin_id in ids:
                    if (step, plugin_id) not in known:
                        raise ValueError(f"unknown plugin id {plugin_id!r} for step {step!r}")
                base = self._base_activations().get(step)
                if base is None:
                    continue
                current = _definitions_by_id(base)
                chosen = () if all_plugins else tuple(
                    definition for plugin_id, definition in current.items() if plugin_id not in set(ids)
                )
                updates[step] = activation_for_definitions(
                    step, chosen, metadata=dict(base.metadata), source_path=base.source_path,
                )
            self._stage_pipeline(self._with_updates(updates))
            return self._selection_result()

    def _base_activations(self) -> dict[str, StepActivation | None]:
        if self._pending_activations is not None:
            return self._pending_activations
        return self._applied_activations

    def _with_updates(self, updates: Mapping[str, StepActivation | None]) -> dict[str, StepActivation | None]:
        desired = dict(self._base_activations())
        desired.update(updates)
        return {step: desired.get(step) for step in STEPS}

    def _stage_pipeline(self, desired: Mapping[str, StepActivation | None]) -> None:
        """Build ``desired`` beside the applied pipeline. A failure leaves both in place."""

        self._arm_request_id += 1
        desired = {step: desired.get(step) for step in STEPS}
        if _same_pipeline(desired, self._applied_activations):
            self._pending_steps = None
            self._pending_activations = None
            self._selection_error = None
            return
        if self._pending_activations is not None and _same_pipeline(desired, self._pending_activations):
            self._selection_error = None
            return
        if self._fixed_steps:
            self._selection_error = "pipeline has a step that cannot be reconstructed"
            return
        stamped = {
            step: self._stamp(item) if item is not None else None
            for step, item in desired.items()
        }
        try:
            built = decision_steps(stamped)
        except Exception as exc:  # noqa: BLE001 - construction must not touch the applied pipeline
            self._selection_error = f"{type(exc).__name__}: {exc}"
            return
        self._pending_steps = built
        self._pending_activations = stamped
        self._selection_error = None

    def _adopt_pending(self) -> None:
        """Install a built pipeline, an empty map, and its generation together."""

        pending = self._pending_steps
        activations = self._pending_activations
        if pending is None or activations is None:
            return
        self.cycle.steps = pending
        for step in STEPS:
            runner = self.step(step)
            if runner is not None:
                self._attach_catalog(runner)
        self.shared_memory = {}
        self.last_result = None
        self._applied_activations = activations
        self._pending_steps = None
        self._pending_activations = None
        self._record_applied_decision()
        self._refresh_step_reports()

    def _selection_result(self) -> dict[str, Any]:
        state = self.selection_status()
        result = {"ok": self._selection_error is None, "status": state["status"], "arming": state}
        if self._selection_error is not None:
            result["error"] = self._selection_error
        return result

    def _selection_status(self) -> dict[str, Any]:
        requested = self._base_activations()
        return {
            "status": "failed" if self._selection_error else "requested" if self._pending_steps is not None else "applied",
            "request_id": self._arm_request_id, "error": self._selection_error,
            "generation_id": None if self._applied_decision is None else self._applied_decision["generation_id"],
            "requested": {step: _describe(requested.get(step)) for step in STEPS},
            "applied": {step: _describe(self._applied_activations.get(step)) for step in STEPS},
        }

    def selection_status(self) -> dict[str, Any]:
        if not self._lock.acquire(timeout=STATUS_WAIT_S):
            return deepcopy(self._pipeline_report.get("arming"))
        try:
            selection = self._selection_status()
            self._pipeline_report = {**self._pipeline_report, "arming": selection}
            return deepcopy(selection)
        finally:
            self._lock.release()

    def applied_decision(self) -> dict[str, Any] | None:
        """The executable identity published with the installed runner reports."""
        return deepcopy(self._pipeline_report.get("applied_decision"))

    def _record_applied_decision(self) -> None:
        """Derive the pipeline generation from the applied activations."""
        steps = {
            step: None if item is None else item.to_payload()
            for step, item in self._applied_activations.items()
        }
        generation_id = activation_generation_id(steps, prefix="decision")
        self._applied_decision = {"generation_id": generation_id, "steps": steps}

    def _stamp(self, activation: StepActivation) -> StepActivation:
        if self._host_name is not None:
            activation = replace(activation, metadata={**activation.metadata, "host_name": self._host_name})
        return stamp_source(activation, host_name=self._host_name, release=INSTALLED_RELEASE)

    def _runner_activations(self) -> dict[str, StepActivation | None]:
        raw = applied_step_activations(self.steps)
        found: dict[str, StepActivation | None] = {}
        fixed: set[str] = set()
        for step in STEPS:
            runner = self.step(step)
            payload = raw.get(step)
            if not isinstance(payload, dict):
                if runner is not None:
                    fixed.add(step)
                found[step] = None
                continue
            activation = step_activation_from_payload(payload, step=step)
            source = getattr(runner, "activation", None)
            if source is None:
                fixed.add(step)
            if isinstance(source, StepActivation) and source.source_path is not None:
                activation = replace(activation, source_path=source.source_path)
            found[step] = self._stamp(activation)
        self._fixed_steps = fixed
        return found

    def run(
        self, context: DecisionFrameContext, *, mode: str | None = None,
        user_steering: float | None = None, user_throttle: float | None = None,
    ) -> DecisionCycleResult:
        with self._session_lock:
            run_generation = self._run_generation
        with self._lock:
            ticket = self.execution.ticket() if self.execution is not None else None
            if ticket is not None:
                mode = ticket.mode
            self._cycle_in_progress = {"frame_id": context.frame_id, "started_at_ms": timestamp_ms()}
            try:
                self.sync_selection()
                self._adopt_pending()
                context = replace(context, shared_memory=self.shared_memory)
                write_host_inputs(
                    context.shared_memory, mode=mode, user_steering=user_steering,
                    user_throttle=user_throttle,
                    control_applications=ticket.control_applications if ticket is not None else None,
                )
                result = self.cycle.run(context)
                result = replace(result, context=self._record_frame_context(result.context))
                if self.execution is not None:
                    result = replace(result, application=self.execution.apply(result, ticket))
                with self._session_lock:
                    if run_generation == self._run_generation:
                        self.last_error = None
                    if self.run_state == "running" and run_generation == self._run_generation:
                        if self.recording is not None:
                            self.recording.append(
                                result, ticket.control_applications if ticket is not None else None,
                            )
                        self.run_decisions += 1
                        if (
                            self.configuration.num_decisions is not None
                            and self.run_decisions >= self.configuration.num_decisions
                        ):
                            self.stop(reason="completed")
                    self._refresh_session_view()
                self.cycle_count += 1
                self.last_result = result
            except Exception as exc:
                self._cycle_in_progress = None
                self._record_frame_context(context)
                self.error_count += 1
                self.last_error = "".join(
                    traceback.format_exception_only(type(exc), exc)
                ).strip()
                try:
                    if self.execution is not None:
                        self.execution.cycle_failed(ticket)
                finally:
                    with self._session_lock:
                        if run_generation == self._run_generation:
                            self.stop(reason="error", error=self.last_error)
                raise
            finally:
                if self._step_reports_wanted:
                    self._refresh_step_reports()
            self._cycle_in_progress = None
            return result

    def _record_frame_context(self, context: DecisionFrameContext) -> DecisionFrameContext:
        """Retain the applied selections even when a frame produces no cycle result."""
        activations = {
            step: None if item is None else item.to_payload()
            for step, item in self._applied_activations.items()
        }
        applied = self._applied_decision
        self.last_context = replace(context, metadata={
            **context.metadata,
            "step_activations": activations,
            "generation_id": None if applied is None else applied["generation_id"],
        })
        return self.last_context

    def start(self, configuration: RunConfiguration, *,
              recording: RunRecording | None = None) -> dict[str, Any]:
        with self._session_lock:
            self.stop()
            self.last_error = None
            self.configuration = configuration
            self.recording = recording
            self.run_decisions = 0
            self.run_state = "starting"
            self._refresh_session_view()
            try:
                self.set_mode(configuration.mode)
            except Exception as exc:
                self.run_state = "error"
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._refresh_session_view()
                raise
            self._run_generation += 1
            self.run_state = "running"
            return self.session_status()

    def stop(self, *, reason: str = "stopped", error: str | None = None) -> dict[str, Any]:
        with self._session_lock:
            self._run_generation += 1
            self.run_state = "stopping"
            self._refresh_session_view()
            if error is not None:
                self.last_error = error
            try:
                if self.execution is not None and not self.execution.status()["closed"]:
                    self.set_mode("manual")
            except Exception as exc:
                self.run_state = "error"
                # The error that stopped the run stays its cause.
                failure = f"stop failed: {type(exc).__name__}: {exc}"
                self.last_error = failure if error is None else f"{error}; {failure}"
                self._refresh_session_view()
                raise
            self.run_state = reason
            return self.session_status()

    def _refresh_session_view(self) -> None:
        self._session_view = {
            "status": self.run_state, "configuration": self.configuration.to_dict(),
            "processed_decisions": self.run_decisions,
            "recording": self.recording.status() if self.recording is not None else None,
        }

    def session_status(self) -> dict[str, Any]:
        if self._session_lock.acquire(blocking=False):
            try:
                self._refresh_session_view()
            finally:
                self._session_lock.release()
        return {**deepcopy(self._session_view),
                "execution": self.execution.status() if self.execution is not None else None}

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
            self._refresh_session_view()
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
        cycle = self._cycle_in_progress
        components: dict[str, Any] = {}
        # Providers report from their own state and never wait on a cycle.
        for component_id, provider in list(self._status_providers.items()):
            try:
                components[component_id] = provider()
            except Exception as exc:  # noqa: BLE001 - status must not fail the host
                components[component_id] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        pipeline = self._report_steps()
        return {
            "session": self.session_status(),
            "execution": self.execution.status() if self.execution is not None else None,
            **pipeline,
            "components": components,
            "cycle_in_progress": None if cycle is None else {
                **cycle, "elapsed_ms": timestamp_ms() - cycle["started_at_ms"],
            },
            "cycle_count": self.cycle_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
        }

    def _report_steps(self) -> dict[str, Any]:
        """Each step's report; during a running cycle, the last refreshed reports.

        A read the running cycle turns away asks it to refresh them as it ends.
        """

        if not self._lock.acquire(timeout=STATUS_WAIT_S):
            self._step_reports_wanted = True
            return deepcopy(self._pipeline_report)
        try:
            return deepcopy(self._refresh_step_reports())
        finally:
            self._lock.release()

    def _refresh_step_reports(self) -> dict[str, Any]:
        """Collect each step's report; the caller holds the cycle lock or owns the host."""

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
        last_result = self.last_result
        self._pipeline_report = {
            "steps": steps, "applied_decision": deepcopy(self._applied_decision),
            "arming": self._selection_status(),
            "last_control": None if last_result is None else last_result.control.to_dict(),
            "last_cycle": None if last_result is None else last_result.to_dict(),
        }
        self._step_reports_wanted = False
        return self._pipeline_report


def sync_live_selection(
    activation_path: Path, loaded: StepActivation, *,
    previous_ids: tuple[str, ...] | None = None,
) -> StepActivation | None:
    """The staged activation when only its plugin IDs changed.

    Specs and configs must still match ``loaded``.
    """

    try:
        live = read_step_activation(activation_path, loaded.step)
    except (OSError, ValueError, TypeError, PluginManagementError):
        # An incomplete or stale activation must not replace the current set.
        return None
    if live.plugin_specs != loaded.plugin_specs or live.plugin_configs != loaded.plugin_configs:
        return None
    if tuple(live.plugins) == tuple(loaded.plugins if previous_ids is None else previous_ids):
        return None
    return live


def _definitions_by_id(activation: StepActivation | None) -> dict[str, PluginDefinition]:
    if activation is None:
        return {}
    return {definition.plugin_id: definition for definition in selected_definitions(activation)}


def _require_catalog(known: Mapping[tuple[str, str], PluginDefinition], step: str, plugin_id: str) -> PluginDefinition:
    definition = known.get((step, plugin_id))
    if definition is None:
        raise ValueError(f"unknown plugin id {plugin_id!r} for step {step!r}")
    return definition


def _describe(activation: StepActivation | None) -> list[dict[str, Any]]:
    if activation is None:
        return []
    return [describe_plugin(definition) for definition in selected_definitions(activation)]


def _same_pipeline(
    left: Mapping[str, StepActivation | None],
    right: Mapping[str, StepActivation | None],
) -> bool:
    return all(
        executable_document(left.get(step)) == executable_document(right.get(step))
        for step in STEPS
    )
