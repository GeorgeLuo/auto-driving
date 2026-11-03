"""Authoritative replay runner for the decision playback workbench."""

from __future__ import annotations

import copy
import os
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS, StepActivation
from autonomy.decision_cycle.memory.runner import MemoryRunner
from autonomy.plugins import LocalPluginCatalog
from autonomy.runtime.plugin_catalog import PluginCatalogAPI
from autonomy.runtime.recording import recorded_selections
from implementations.decision_cycle.catalog import CUSTOM_PRESET

from ..frontend.workbench_contract import (
    WORKBENCH_ACTIONS,
    WORKBENCH_DEFAULT_CADENCE_MS,
    WORKBENCH_DEFAULT_LOOP,
    WORKBENCH_DEFAULT_PACE,
    WORKBENCH_PACES,
    WORKBENCH_SEQUENCE_ID,
    WORKBENCH_STATE_SCHEMA,
    ReplayActionError,
)
from ..replay.frames import run_frame
from ..replay.models import (
    WORKBENCH_ADAPTER,
    FrameOutcome,
    ImageSource,
    ReplayFrame,
    SourceValidationError,
)
from ..replay.selections import (
    default_selection,
    recorded_runner,
    resolve_replay_activations,
    restage_replay_steps,
)
from ..replay.source import (
    WORKBENCH_DEFAULT_MAX_FRAMES,
    WORKBENCH_DEFAULT_MAX_IMAGE_BYTES,
    content_type_for_path,
    normalize_image_directory,
)
from ..shared.step_hosting import plugin_report
from ..shared.timestamps import timestamp_ms
from .plugins import (
    WORKBENCH_SELECTABLE_STEPS,
    PluginCatalog,
    PluginCatalogError,
    packaged_plugin_catalog,
)
from .presentation import (
    displayed_steps,
    empty_steps,
    frame_detail,
    frame_summary,
    timeline_item,
)


def _safe_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _state_action_set(phase: str) -> list[str]:
    if phase == "idle":
        return ["validate", "select_plugins", "start", "reset", "set_loop"]
    if phase == "running":
        return ["pause", "seek", "reset", "set_cadence", "set_loop", "select_plugins"]
    if phase == "paused":
        return ["resume", "step", "seek", "reset", "set_cadence", "set_loop", "select_plugins"]
    if phase in {"completed", "failed"}:
        return ["validate", "select_plugins", "start", "reset", "set_loop"]
    return []


class ImageReplayRunner:
    """Shared replay owner used by both the CLI and loopback API."""

    def __init__(
        self,
        source_dir: str | os.PathLike[str] | None = None,
        *,
        source_root: Path | None = None,
        activations: dict[str, StepActivation] | None = None,
        cadence_ms: int = WORKBENCH_DEFAULT_CADENCE_MS,
        pace: str = WORKBENCH_DEFAULT_PACE,
        loop: bool = WORKBENCH_DEFAULT_LOOP,
        max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
        max_image_bytes: int = WORKBENCH_DEFAULT_MAX_IMAGE_BYTES,
        step_factories: Mapping[str, Callable[[], Any]] | None = None,
    ) -> None:
        self.source_root = (
            Path(source_root).expanduser().resolve() if source_root else None
        )
        self.source_dir = os.fspath(source_dir) if source_dir is not None else None
        self.max_frames = int(max_frames)
        self.max_image_bytes = int(max_image_bytes)
        # Test hooks: a selectable step's runner, in place of its catalog build.
        self.step_factories = dict(step_factories or {})
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._action_lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._image_source: ImageSource | None = None
        # The replay's step runners by step; empty until a replay starts.
        self._steps: dict[str, Any] = {}
        self._shared_memory: dict[str, Any] = {}
        self._history: dict[str, dict[str, Any]] = {}
        self._frame_checkpoint: dict[str, Any] | None = None
        self._seek_generation = 0
        self._generation = 0
        self._cadence_ms = self._validate_cadence(cadence_ms)
        self._pace = self._validate_pace(pace)
        self._loop = bool(loop)
        self._server_identity = f"workbench-{uuid.uuid4().hex[:12]}"
        self._catalogs: dict[str, PluginCatalog] = {
            step: packaged_plugin_catalog(step) for step in WORKBENCH_SELECTABLE_STEPS
        }
        self.catalog = LocalPluginCatalog(
            definition for step, catalog in self._catalogs.items()
            for definition in catalog.resolver.list(step)
        )
        self._catalogs = {
            step: PluginCatalog(step, self.catalog, catalog.defaults)
            for step, catalog in self._catalogs.items()
        }
        self.plugin_catalog = PluginCatalogAPI(self.catalog)
        self._activations = self._initial_activations(activations)
        self._default_activations = dict(self._activations)
        self._selection_overrides = set(activations or {}) | set(self.step_factories)
        self._state = self._initial_state()

    @property
    def server_identity(self) -> str:
        return self._server_identity

    def state(self) -> dict[str, Any]:
        with self._lock:
            for step in WORKBENCH_SELECTABLE_STEPS:
                self._state[f"{step}_plugin_catalog"] = self._catalogs[step].to_dict(
                    active_ids=self._state[f"active_{step}_plugin_ids"],
                )
            return copy.deepcopy(self._state)

    @staticmethod
    def _initial_activations(
        activations: dict[str, StepActivation] | None,
    ) -> dict[str, StepActivation]:
        """Each selectable step's activation; an unlisted step uses its default selection."""

        given = dict(activations or {})
        selected: dict[str, StepActivation] = {}
        for step in WORKBENCH_SELECTABLE_STEPS:
            activation = given[step] if step in given else default_selection(step)
            if activation.step != step:
                raise ValueError(f"activation for {activation.step!r} given as {step!r}")
            selected[step] = activation
        return selected

    def _selection_preset(self, step: str) -> str:
        """The preset the step's selection came from or equals, else ``custom``."""

        return str(self._activations[step].metadata.get("preset", CUSTOM_PRESET))

    def _plugin_configuration(self) -> dict[str, Any]:
        """Each selectable step's catalog and active plugins, as the page shows them."""

        configuration: dict[str, Any] = {}
        for step in WORKBENCH_SELECTABLE_STEPS:
            active = self._activations[step].plugins
            configuration[f"{step}_plugin_catalog"] = self._catalogs[step].to_dict(
                active_ids=active
            )
            configuration[f"active_{step}_plugin_ids"] = list(active)
        return configuration

    def _selection_presets(self) -> dict[str, Any]:
        """Each selectable step's preset, under ``<step>_preset``."""

        return {f"{step}_preset": self._selection_preset(step) for step in WORKBENCH_SELECTABLE_STEPS}

    def _step_reports(self) -> dict[str, Any]:
        """What the built steps report.

        Each selectable step's plugin report, under ``<step>_plugin_report``,
        and the plugins each decision step runs.
        """

        return {
            **{
                f"{step}_plugin_report": plugin_report(self._steps.get(step))
                for step in WORKBENCH_SELECTABLE_STEPS
            },
            "decision_steps": self._decision_step_plugins(),
            "decision_config": self._decision_configuration(),
        }

    def _apply_plugin_configuration_locked(self) -> None:
        self._state.update(self._plugin_configuration())
        self._state["selection_presets"] = self._selection_presets()
        self._state["machine_detail"]["pipeline"].update(self._selection_presets())

    def frame_bytes(
        self,
        frame_id: str | None = None,
        *,
        run_id: str,
        position: int | None = None,
    ) -> tuple[bytes, str] | None:
        with self._lock:
            self._require_run_id_locked(run_id)
            frame = (
                self._frame_for_position_locked(position)
                if position is not None
                else self._frame_for_id_locked(frame_id)
            )
            if frame is None or frame.image_path is None:
                return None
            path = frame.image_path
        try:
            return path.read_bytes(), frame.content_type or content_type_for_path(path)
        except OSError:
            return None

    def _frame_for_id_locked(self, frame_id: str | None) -> ReplayFrame | None:
        if self._image_source is None:
            return None
        selected_id = frame_id
        if selected_id is None:
            current = self._state.get("current_frame")
            selected_id = current.get("frame_id") if isinstance(current, dict) else None
        if selected_id is None:
            return None
        return next(
            (frame for frame in self._image_source.frames if frame.frame_id == selected_id),
            None,
        )

    def _frame_for_position_locked(self, position: int) -> ReplayFrame | None:
        if self._image_source is None:
            return None
        if position < 0 or position >= len(self._image_source.frames):
            return None
        return self._image_source.frames[position]

    def validate_source(
        self, source_dir: str | os.PathLike[str] | None = None
    ) -> ImageSource:
        raw_source = self.source_dir if source_dir is None else os.fspath(source_dir)
        if raw_source is None:
            raise SourceValidationError("start requires source_dir")
        with self._lock:
            if self._state["phase"] in {"running", "paused"}:
                raise SourceValidationError(
                    "source cannot be changed while replay is active"
                )
        image_source = normalize_image_directory(
            raw_source,
            source_root=self.source_root,
            max_frames=self.max_frames,
            max_image_bytes=self.max_image_bytes,
        )
        with self._lock:
            self._image_source = image_source
            self._history.clear()
            self.source_dir = str(image_source.source_path)
            self._state = self._initial_state()
            self._apply_plugin_configuration_locked()
            self._state["source"] = image_source.to_dict()
            self._state["source_identity"] = image_source.source_id
            self._state["adapter"] = image_source.adapter
            self._state["progress"] = {
                "completed": 0,
                "total": len(image_source.frames),
                "percent": 0.0,
            }
            self._state["summary"] = frame_summary(
                frames_completed=0,
                frames_total=len(image_source.frames),
            )
        return image_source

    def start(
        self,
        source_dir: str | os.PathLike[str] | None = None,
        *,
        cadence_ms: int | None = None,
        pace: str | None = None,
        loop: bool | None = None,
    ) -> dict[str, Any]:
        with self._action_lock:
            with self._condition:
                phase = self._state["phase"]
                if phase in {"running", "paused"}:
                    raise ReplayActionError(
                        f"cannot start while replay is {phase}",
                        boundary="lifecycle",
                    )
                if cadence_ms is not None:
                    self._cadence_ms = self._validate_cadence(cadence_ms)
                if pace is not None:
                    self._pace = self._validate_pace(pace)
                if loop is not None:
                    self._loop = bool(loop)
                raw_source = (
                    self.source_dir if source_dir is None else os.fspath(source_dir)
                )
                if raw_source is None:
                    raise ReplayActionError(
                        "start requires source_dir",
                        status_code=400,
                        boundary="source",
                    )
                run_id = f"run-{uuid.uuid4().hex}"
                self._generation += 1
                generation = self._generation
                if self._steps:
                    self._cleanup_locked()
                self._image_source = None
                self._history.clear()
                self._shared_memory = {}
                self._state = self._fresh_run_state(run_id)
                self._apply_plugin_configuration_locked()
                self._state["source"] = {"path": str(raw_source)}
                try:
                    image_source = normalize_image_directory(
                        raw_source,
                        source_root=self.source_root,
                        max_frames=self.max_frames,
                        max_image_bytes=self.max_image_bytes,
                    )
                    steps = self._build_steps(image_source.frames[0])
                except Exception as exc:  # noqa: BLE001 - startup isolation boundary
                    self._set_failure_locked(
                        boundary=getattr(exc, "boundary", "startup"),
                        message=str(exc),
                        recovery_action="start",
                    )
                    self._condition.notify_all()
                    return copy.deepcopy(self._state)
                self._image_source = image_source
                self.source_dir = str(image_source.source_path)
                self._steps = steps
                self._state["source"] = image_source.to_dict()
                self._state["source_identity"] = image_source.source_id
                self._state["adapter"] = image_source.adapter
                self._state["progress"]["total"] = len(image_source.frames)
                self._state["machine_detail"] = self._machine_detail()
                self._apply_plugin_configuration_locked()
                self._state["phase"] = "running"
                self._record_action_locked("start", run_id=run_id)
                self._worker = threading.Thread(
                    target=self._run_loop,
                    args=(run_id, generation),
                    name=f"automa-workbench-{run_id[-12:]}",
                    daemon=True,
                )
                self._worker.start()
                self._condition.notify_all()
                return copy.deepcopy(self._state)

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=timeout)
        return self.state()

    def close(self) -> None:
        """Stop replay work and discard all server-lifetime state."""

        with self._action_lock:
            with self._condition:
                worker = self._worker
                self._generation += 1
                self._cleanup_locked()
                self._history.clear()
                self._image_source = None
                self.source_dir = None
                self._worker = None
                self._state = self._initial_state()
                self._apply_plugin_configuration_locked()
                self._condition.notify_all()
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)

    def dispatch(
        self,
        action: str,
        *,
        run_id: str | None = None,
        source_dir: str | os.PathLike[str] | None = None,
        cadence_ms: int | None = None,
        pace: str | None = None,
        step: str | None = None,
        active_plugin_ids: list[str] | tuple[str, ...] | None = None,
        position: int | None = None,
        loop: bool | None = None,
    ) -> dict[str, Any]:
        action = str(action or "").strip()
        if action not in WORKBENCH_ACTIONS:
            raise ReplayActionError(
                f"unknown workbench action {action!r}",
                status_code=400,
                boundary="action",
            )
        # Pause, resume, cadence, and loop only flip worker flags. A frame
        # already executing still finishes and publishes; the page does not
        # wait it out. Plugin changes keep the action lock, which is the
        # frame boundary.
        if action in {"pause", "resume", "set_cadence", "set_loop"}:
            with self._condition:
                self._check_run_id_locked(action, run_id)
                if action == "set_cadence":
                    if cadence_ms is None and pace is None:
                        raise ReplayActionError(
                            "set_cadence requires cadence_ms or pace",
                            status_code=400,
                            boundary="input",
                        )
                    value = (
                        self._validate_cadence(cadence_ms)
                        if cadence_ms is not None
                        else None
                    )
                    selected_pace = (
                        self._validate_pace(pace) if pace is not None else self._pace
                    )
                    if self._state["phase"] not in {"running", "paused"}:
                        raise ReplayActionError(
                            "cadence can only change while replay is running or paused",
                            boundary="lifecycle",
                        )
                    if value is not None:
                        self._cadence_ms = value
                    self._pace = selected_pace
                    self._record_action_locked(
                        action,
                        cadence_ms=self._cadence_ms,
                        pace=self._pace,
                    )
                    self._condition.notify_all()
                    return self.state()
                if action == "set_loop":
                    return self._set_loop(loop)
                if action == "pause":
                    return self._pause()
                return self._resume()
        # A newer seek invalidates an older catch-up without waiting for it.
        # The work itself still takes the frame lock one frame at a time.
        if action == "seek":
            with self._condition:
                self._check_run_id_locked(action, run_id)
                if step is not None or active_plugin_ids is not None:
                    raise ReplayActionError(
                        "step and active_plugin_ids are only accepted by select_plugins",
                        status_code=400, boundary="input",
                    )
                self._seek_generation += 1
                seek_generation = self._seek_generation
            return self._seek(position, seek_generation=seek_generation)
        if action == "reset":
            with self._condition:
                self._check_run_id_locked(action, run_id)
                self._seek_generation += 1
        with self._action_lock:
            with self._lock:
                self._check_run_id_locked(action, run_id)
            if (step is not None or active_plugin_ids is not None) and action != "select_plugins":
                raise ReplayActionError(
                    "step and active_plugin_ids are only accepted by select_plugins",
                    status_code=400,
                    boundary="input",
                )
            if action == "select_plugins":
                return self._select_plugins(step, active_plugin_ids)
            if action == "validate":
                with self._lock:
                    if self._state["phase"] in {"running", "paused"}:
                        raise ReplayActionError(
                            "validate is unavailable while replay is active",
                            boundary="lifecycle",
                        )
                try:
                    self.validate_source(source_dir)
                except PluginCatalogError as exc:
                    with self._lock:
                        self._state["failure"] = {
                            "message": str(exc),
                            "boundary": "plugin_catalog",
                        }
                        self._state["failure_boundary"] = "plugin_catalog"
                        self._record_action_locked(action)
                    raise ReplayActionError(
                        str(exc),
                        status_code=422,
                        boundary="plugin_catalog",
                        state=self.state(),
                    ) from exc
                except SourceValidationError as exc:
                    with self._lock:
                        self._state["failure"] = {
                            "message": str(exc),
                            "boundary": "source",
                        }
                        self._state["failure_boundary"] = "source"
                        self._record_action_locked(action)
                    raise ReplayActionError(
                        str(exc),
                        status_code=422,
                        boundary="source",
                        state=self.state(),
                    ) from exc
                with self._lock:
                    self._state["failure"] = None
                    self._state["failure_boundary"] = None
                    self._record_action_locked(action)
                return self.state()
            if action == "start":
                try:
                    return self.start(source_dir, cadence_ms=cadence_ms, pace=pace, loop=loop)
                except SourceValidationError as exc:
                    raise ReplayActionError(
                        str(exc),
                        status_code=422,
                        boundary="source",
                        state=self.state(),
                    ) from exc
            if action == "step":
                return self._step()
            if action == "reset":
                return self._reset()
        raise AssertionError(f"unhandled action {action}")

    def _select_plugins(
        self,
        step: str | None,
        active_plugin_ids: list[str] | tuple[str, ...] | None,
    ) -> dict[str, Any]:
        if step not in WORKBENCH_SELECTABLE_STEPS:
            raise ReplayActionError(
                f"select_plugins requires step, one of: {', '.join(WORKBENCH_SELECTABLE_STEPS)}",
                status_code=400,
                boundary="input",
            )
        if active_plugin_ids is None:
            raise ReplayActionError(
                "select_plugins requires active_plugin_ids",
                status_code=400,
                boundary="input",
            )
        catalog = self._catalogs[step]
        # The action lock is the frame boundary: a plugin already executing
        # finishes, and publishes, before this selection changes anything.
        with self._action_lock, self._lock:
            active_phase = self._state["phase"] in {"running", "paused"}
            try:
                normalized = catalog.normalize_selection(active_plugin_ids)
            except PluginCatalogError as exc:
                self._state["failure"] = {"message": str(exc), "boundary": "plugin_catalog"}
                self._state["failure_boundary"] = "plugin_catalog"
                raise ReplayActionError(
                    str(exc),
                    status_code=422,
                    boundary="plugin_catalog",
                    state=self.state(),
                ) from exc

            if active_phase and not self._viewing_latest():
                raise ReplayActionError(
                    "Return to the latest processed frame before changing plugins.",
                    boundary="lifecycle", state=self.state(),
                )
            refresh = False
            if normalized != self._activations[step].plugins:
                activation = catalog.activation(normalized)
                previous = self._activations[step]
                # Keep the definitions/configs of retained plugins, including
                # recorded selections and uploaded source revisions.
                retained = set(previous.plugins) & set(normalized)
                activation = replace(
                    activation,
                    plugin_specs={**activation.plugin_specs, **{
                        key: previous.plugin_specs[key] for key in retained
                        if key in previous.plugin_specs
                    }},
                    plugin_configs={**activation.plugin_configs, **{
                        key: previous.plugin_configs.get(key, {}) for key in retained
                    }},
                )
                try:
                    if active_phase:
                        self._apply_selection_locked(step, activation)
                        refresh = self._state["phase"] == "paused" and self._frame_checkpoint is not None
                    self._activations[step] = activation
                except Exception as exc:
                    message = str(exc)
                    self._state["failure"] = {"message": message, "boundary": "plugin_catalog"}
                    self._state["failure_boundary"] = "plugin_catalog"
                    raise ReplayActionError(
                        message, status_code=422, boundary="plugin_catalog", state=self.state(),
                    ) from exc
            self._selection_overrides.add(step)
            self._apply_plugin_configuration_locked()
            self._state["failure"] = None
            self._state["failure_boundary"] = None
            self._record_action_locked("select_plugins")
            self._condition.notify_all()
            run_id = str(self._state["run_id"])
            generation = self._generation
            if refresh:
                frame = self._image_source.frames[self._state["latest_processed_position"]]
                self._restore_frame_checkpoint()
                self._history.pop(frame.frame_id)
                self._state["position"] = frame.position
        if refresh:
            self._process_one(run_id, generation, frame, allow_paused=True, refresh=True)
        return self.state()

    def _viewing_latest(self) -> bool:
        frame = self._state.get("current_frame")
        return frame is None or frame["position"] == self._state["latest_processed_position"]

    def _apply_selection_locked(self, step: str, activation: StepActivation) -> None:
        """Commit a live selection without resetting retained plugin state."""
        runner = self._steps.get(step)
        checkpoint = copy.deepcopy(self._frame_checkpoint)
        # Staged runners forward this protocol inside their bundle context.
        if getattr(runner, "plugin_manager", None) is not None:
            manager = runner.plugin_manager
            previous = manager.selected
            retained = {definition.plugin_id: definition for definition, _ in runner.applied}
            definitions = activation.plugin_manager().selected
            definitions = tuple(retained.get(item.plugin_id, item) for item in definitions)
            try:
                manager.select(definitions)
                runner.prepare_selection()
                # Also clear removed state from the pre-frame checkpoint so a
                # later paused refresh cannot resurrect a deselected plugin.
                if checkpoint is not None:
                    for definition, plugin in runner.applied:
                        if definition not in definitions:
                            runner._reset_plugin(plugin, checkpoint["memory"])
                runner.commit_selection(self._shared_memory)
                runner.adopt_activation(activation)
            except Exception:
                runner.discard_selection()
                manager.select(previous)
                raise
        else:
            # A recording can have no runner for a selectable step.
            replacement = self._catalogs[step].build(activation)
            if runner is not None:
                if checkpoint is not None:
                    runner.reset(checkpoint["memory"])
                runner.reset(self._shared_memory)
            self._steps[step] = replacement
        self._frame_checkpoint = checkpoint
        self._replay_activations[step] = activation

    def _restore_frame_checkpoint(self) -> None:
        checkpoint = self._frame_checkpoint
        self._shared_memory = copy.deepcopy(checkpoint["memory"])
        memory = self._steps.get("memory")
        if isinstance(memory, MemoryRunner):
            memory.restore_shared_memory(self._shared_memory, publisher=checkpoint["publisher"])

    def _restart_capture_locked(self) -> None:
        """Start another pass over the capture with fresh pipelines.

        Each loop reprocesses every frame with the current selection instead
        of replaying the previous pass.
        """

        self._rebuild_pipelines_locked()
        self._record_action_locked("loop")

    def _rebuild_pipelines_locked(self) -> None:
        """Fresh pipelines under the current selections, at the first frame.

        The new pipelines are built before the old ones are reset, so a
        selection that fails to build leaves the replay as it was.
        """

        steps = self._build_steps(self._image_source.frames[0] if self._image_source else None)
        self._cleanup_locked()
        self._steps = steps
        self._shared_memory = {}
        self._history.clear()
        self._state["timeline"] = []
        self._state["latest_processed_position"] = None
        self._state["current_frame"] = None
        self._set_position_locked(0)

    def _set_position_locked(self, position: int) -> None:
        self._state["position"] = position
        total = len(self._image_source.frames) if self._image_source else 0
        self._state["progress"]["completed"] = position
        self._state["progress"]["percent"] = (
            round((position / total) * 100.0, 2) if total else 0.0
        )

    def _store_recorded_activation(self, step: str, activation: StepActivation | None) -> None:
        """Show a selectable step's recorded plugins in its panel, none if it ran none.

        Whether the step runs comes from the recording, not from this activation.
        """

        if step in WORKBENCH_SELECTABLE_STEPS:
            self._activations[step] = activation or self._catalogs[step].activation([])

    def _build_steps(self, first_frame: ReplayFrame | None = None) -> dict[str, Any]:
        """Restore the first frame's selections before constructing any selectable step."""
        recorded = recorded_selections(first_frame.metadata) if first_frame else {}
        defaults = {step: default_selection(step) for step in STEPS}
        defaults.update(self._default_activations)
        activations = resolve_replay_activations(
            recorded, defaults=defaults,
            overrides={step: self._activations[step] for step in self._selection_overrides},
        )
        steps = {}
        for step, activation in activations.items():
            factory = self.step_factories.get(step)
            if factory is not None:
                steps[step] = factory()
            elif step in WORKBENCH_SELECTABLE_STEPS and (
                step not in recorded or step in self._selection_overrides
            ):
                # A local selection may carry staging metadata without its bundle.
                steps[step] = self._catalogs[step].build(activation)
            else:
                steps[step] = recorded_runner(activation) if activation is not None else None
        self._replay_activations = activations
        for step, activation in activations.items():
            self._store_recorded_activation(step, activation)
        return steps

    def _set_loop(self, loop: bool | None) -> dict[str, Any]:
        if not isinstance(loop, bool):
            raise ReplayActionError(
                "set_loop requires a boolean loop",
                status_code=400,
                boundary="input",
            )
        with self._condition:
            self._loop = loop
            self._record_action_locked("set_loop", loop=loop)
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _wrap_or_complete_locked(self) -> None:
        image_source = self._image_source
        if image_source is None or int(self._state["position"]) < len(image_source.frames):
            return
        if self._loop:
            # Keep the final frame editable until the next pass begins.
            return
        self._complete_locked()

    def _pause(self) -> dict[str, Any]:
        with self._condition:
            if self._state["phase"] != "running":
                raise ReplayActionError(
                    "pause requires a running replay", boundary="lifecycle"
                )
            self._state["phase"] = "paused"
            self._record_action_locked("pause")
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _resume(self) -> dict[str, Any]:
        with self._condition:
            if self._state["phase"] != "paused":
                raise ReplayActionError(
                    "resume requires a paused replay", boundary="lifecycle"
                )
            self._state["phase"] = "running"
            self._record_action_locked("resume")
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _step(self) -> dict[str, Any]:
        with self._condition:
            if self._state["phase"] != "paused" or self._image_source is None:
                raise ReplayActionError(
                    "step requires a paused replay", boundary="lifecycle"
                )
            run_id = str(self._state["run_id"])
            generation = self._generation
            if self._state["position"] >= len(self._image_source.frames):
                if self._loop:
                    self._restart_capture_locked()
                else:
                    self._complete_locked()
                    return copy.deepcopy(self._state)
            frame = self._image_source.frames[self._state["position"]]
        self._process_one(run_id, generation, frame, allow_paused=True)
        with self._lock:
            self._record_action_locked("step")
            return copy.deepcopy(self._state)

    def _seek(self, position: int | None, *, seek_generation: int) -> dict[str, Any]:
        if isinstance(position, bool) or not isinstance(position, int):
            raise ReplayActionError("seek requires an integer position", status_code=400, boundary="input")
        with self._action_lock, self._condition:
            if seek_generation != self._seek_generation:
                return self.state()
            if self._state["phase"] not in {"running", "paused"} or self._image_source is None:
                raise ReplayActionError("seek requires a running or paused replay", boundary="lifecycle")
            if position < 0 or position >= len(self._image_source.frames):
                raise ReplayActionError("seek position is outside the loaded source", status_code=400,
                                        boundary="input", state=copy.deepcopy(self._state))
            self._state["phase"] = "paused"
            self._condition.notify_all()
            run_id = str(self._state["run_id"])
            generation = self._generation
            source = self._image_source

        while True:
            with self._action_lock:
                with self._condition:
                    if (seek_generation != self._seek_generation or generation != self._generation
                            or self._state["phase"] not in {"running", "paused"}):
                        return self.state()
                    frame = source.frames[position]
                    cached = self._history.get(frame.frame_id)
                    if cached is not None:
                        self._apply_cached_frame_locked(frame, cached)
                        self._record_action_locked("seek", position=position)
                        return copy.deepcopy(self._state)
                    next_position = len(self._history)
                    self._set_position_locked(next_position)
                    next_frame = source.frames[next_position]
                # Release the condition while executing a frame; its reentrant
                # action lock is released between frames for other requests.
                if not self._process_one(run_id, generation, next_frame, allow_paused=True):
                    return self.state()

    def _apply_cached_frame_locked(
        self,
        frame: ReplayFrame,
        cached: dict[str, Any],
    ) -> None:
        self._state["current_frame"] = frame.to_dict()
        self._set_steps_locked(copy.deepcopy(cached["steps"]))
        # Execution selections stay live while cached results show their own pipeline.
        if "pipeline" in cached:
            self._state["machine_detail"]["pipeline"] = copy.deepcopy(cached["pipeline"])
        completed = frame.position + 1
        total = len(self._image_source.frames) if self._image_source is not None else completed
        self._state["progress"]["completed"] = completed
        self._state["progress"]["percent"] = (
            round((completed / total) * 100.0, 2) if total else 100.0
        )
        self._state["position"] = completed
        self._state["summary"] = copy.deepcopy(cached.get("summary")) or frame_summary(
            frames_completed=completed, frames_total=total,
            duration_ms=cached.get("duration_ms"),
        )
        if completed >= total:
            self._wrap_or_complete_locked()

    def _reset(self) -> dict[str, Any]:
        with self._condition:
            self._generation += 1
            self._seek_generation += 1
            self._cleanup_locked()
            self._shared_memory = {}
            image_source = self._image_source
            source_dir = self.source_dir
            source_state = copy.deepcopy(self._state.get("source"))
            source_identity = self._state.get("source_identity")
            adapter = self._state.get("adapter")
            self._state = self._initial_state()
            self._apply_plugin_configuration_locked()
            self._history.clear()
            self._image_source = image_source
            if image_source is not None:
                self.source_dir = source_dir
                self._state["source"] = (
                    source_state if source_state is not None else image_source.to_dict()
                )
                self._state["source_identity"] = source_identity or image_source.source_id
                self._state["adapter"] = adapter or image_source.adapter
                self._state["progress"] = {
                    "completed": 0,
                    "total": len(image_source.frames),
                    "percent": 0.0,
                }
                self._state["summary"] = frame_summary(
                    frames_completed=0,
                    frames_total=len(image_source.frames),
                )
            self._record_action_locked("reset")
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _run_loop(self, run_id: str, generation: int) -> None:
        while True:
            with self._condition:
                if (
                    generation != self._generation
                    or self._state["run_id"] != run_id
                    or self._state["phase"] not in {"running", "paused"}
                ):
                    return
                if self._state["phase"] == "paused":
                    self._condition.wait()
                    continue
                image_source = self._image_source
                position = int(self._state["position"])
                if image_source is None:
                    self._complete_locked()
                    return
                frame = image_source.frames[position] if position < len(image_source.frames) else None
            if frame is None:
                with self._action_lock, self._condition:
                    if generation != self._generation or self._state["phase"] != "running":
                        continue
                    if self._state["position"] < len(image_source.frames):
                        continue
                    if self._loop:
                        self._restart_capture_locked()
                        continue
                    self._complete_locked()
                    return
            processing_started = time.monotonic()
            if not self._process_one(run_id, generation, frame):
                continue
            processing_elapsed = time.monotonic() - processing_started
            with self._condition:
                if (
                    generation != self._generation
                    or self._state["run_id"] != run_id
                    or self._state["phase"] != "running"
                ):
                    continue
                if self._pace == "realtime":
                    next_position = int(self._state["position"])
                    next_frame = (
                        self._image_source.frames[next_position]
                        if self._image_source is not None
                        and next_position < len(self._image_source.frames)
                        else None
                    )
                    delay = (
                        max(
                            0.0,
                            (next_frame.timestamp_ms - frame.timestamp_ms) / 1000.0
                            - processing_elapsed,
                        )
                        if next_frame is not None
                        else 0.0
                    )
                else:
                    delay = self._cadence_ms / 1000.0
                if delay > 0:
                    self._condition.wait(timeout=delay)

    def _process_one(
        self,
        run_id: str,
        generation: int,
        frame: ReplayFrame,
        *,
        allow_paused: bool = False,
        refresh: bool = False,
    ) -> bool:
        with self._action_lock:
            with self._condition:
                if (
                    generation != self._generation
                    or self._state["run_id"] != run_id
                    or self._state["phase"]
                    not in ({"running", "paused"} if allow_paused else {"running"})
                    or frame.position != int(self._state["position"])
                ):
                    return False
                cached = self._history.get(frame.frame_id)
                if cached is not None:
                    self._apply_cached_frame_locked(frame, cached)
                    self._condition.notify_all()
                    return True
                if frame.position > len(self._history):
                    raise RuntimeError("replay frame would skip uncached source frames")
                steps = self._steps
            try:
                with self._condition:
                    changed = restage_replay_steps(
                        steps, self._replay_activations, recorded_selections(frame.metadata),
                        overrides=self._selection_overrides, shared_memory=self._shared_memory,
                    )
                    for step in changed:
                        self._store_recorded_activation(step, self._replay_activations[step])
                    self._apply_plugin_configuration_locked()
                    if not refresh:
                        memory = steps.get("memory")
                        self._frame_checkpoint = {
                            "memory": copy.deepcopy(self._shared_memory),
                            "publisher": memory.evidence_publisher if isinstance(memory, MemoryRunner) else None,
                            "previous_memory": copy.deepcopy(self._state["steps"]["memory"]),
                        }
                outcome = run_frame(frame, steps=steps, shared_memory=self._shared_memory)
                result = outcome.result
                presented_steps = displayed_steps(outcome)
                with self._condition:
                    self._publish_frame_locked(frame, outcome, presented_steps)
                    if result.perception is not None and _safe_status(
                        result.perception.status
                    ) in {
                        "error",
                        "unavailable",
                    }:
                        self._set_failure_locked(
                            boundary="perception",
                            message=f"perception status is {result.perception.status}",
                            recovery_action="start",
                        )
                    else:
                        self._wrap_or_complete_locked()
                    self._condition.notify_all()
                return True
            except Exception as exc:  # noqa: BLE001 - per-frame isolation boundary
                with self._condition:
                    self._state["current_frame"] = frame.to_dict()
                    self._set_steps_locked(empty_steps())
                    self._set_failure_locked(
                        boundary=getattr(exc, "boundary", "pipeline"),
                        message=f"{type(exc).__name__}: {exc}",
                        recovery_action="start",
                    )
                    self._condition.notify_all()
                return False

    def _publish_frame_locked(
        self, frame: ReplayFrame, outcome: FrameOutcome, steps: dict[str, Any],
    ) -> None:
        """Publish a processed frame and cache its presentation under the condition lock."""

        result = outcome.result
        previous_memory = self._frame_checkpoint["previous_memory"]
        self._state["current_frame"] = frame.to_dict()
        self._set_steps_locked(steps)
        self._state["progress"]["completed"] = frame.position + 1
        self._state["progress"]["percent"] = (
            round(
                ((frame.position + 1) / len(self._image_source.frames)) * 100.0,
                2,
            )
            if self._image_source
            else 100.0
        )
        self._state["summary"] = frame_summary(
            perception=result.perception,
            observation=result.observation,
            memory=steps["memory"],
            duration_ms=result.duration_ms,
            frames_completed=self._state["progress"]["completed"],
            frames_total=self._state["progress"].get("total", 0),
        )
        detail = frame_detail(
            frame=frame,
            outcome=outcome,
            steps=steps,
            previous_memory=previous_memory,
        )
        detail.update({
            f"{step}_plugin_report": outcome.plugin_reports.get(step)
            for step in WORKBENCH_SELECTABLE_STEPS
        })
        pipeline = self._state.get("machine_detail", {}).get("pipeline", {})
        pipeline.update(self._step_reports())
        detail["configuration"] = self._plugin_configuration()
        detail["pipeline"] = copy.deepcopy(pipeline)
        detail["summary"] = copy.deepcopy(self._state["summary"])
        self._history[frame.frame_id] = detail
        self._upsert_timeline_locked(detail)
        self._state["position"] = frame.position + 1
        self._state["latest_processed_position"] = frame.position
        self._state["controls"] = self._controls()

    def _complete_locked(self) -> None:
        if self._state["phase"] in {"running", "paused"}:
            self._state["phase"] = "completed"
            self._state["progress"]["completed"] = self._state["progress"]["total"]
            self._state["progress"]["percent"] = 100.0
            self._state["cleanup"] = self._cleanup_locked()
            self._record_action_locked("complete")
            self._condition.notify_all()

    def _set_failure_locked(
        self,
        *,
        boundary: str,
        message: str,
        recovery_action: str,
    ) -> None:
        self._state["phase"] = "failed"
        self._state["failure"] = {
            "message": str(message)[:1000],
            "boundary": str(boundary),
        }
        self._state["failure_boundary"] = str(boundary)
        self._state["recovery_action"] = recovery_action
        self._state["cleanup"] = self._cleanup_locked()
        self._state["controls"] = self._controls()

    def _cleanup_locked(self) -> dict[str, Any]:
        """Reset every step's runner and drop every runner.

        The cleanup record names each step's outcome: ``reset``,
        ``not_created``, or the reset error.
        """

        cleanup = {
            "completed_at_ms": timestamp_ms(),
            **{
                step: _reset_status(self._steps.get(step), self._shared_memory)
                for step in STEPS
            },
            "source_read_only": True,
            "worker_started": False,
            "simulator_used": False,
            "movement_control": False,
            "metrics_used": False,
            "recording_enabled": False,
        }
        self._steps = {}
        self._frame_checkpoint = None
        return cleanup

    def _check_run_id_locked(self, action: str, run_id: str | None) -> None:
        if action == "start":
            if run_id is not None and run_id != self._state.get("run_id"):
                raise ReplayActionError(
                    "run_id is stale for this workbench server",
                    status_code=409,
                    boundary="stale_run",
                    state=copy.deepcopy(self._state),
                )
            return
        current = self._state.get("run_id")
        if (
            action
            in {
                "validate",
                "select_plugins",
                "reset",
                "set_loop",
            }
            and current is None
            and run_id is None
        ):
            return
        if not run_id:
            raise ReplayActionError(
                f"{action} requires run_id",
                status_code=400,
                boundary="input",
                state=copy.deepcopy(self._state),
            )
        if run_id != current:
            raise ReplayActionError(
                "run_id is stale for this workbench server",
                status_code=409,
                boundary="stale_run",
                state=copy.deepcopy(self._state),
            )

    def _require_run_id_locked(self, run_id: str) -> None:
        if run_id != self._state.get("run_id"):
            raise ReplayActionError(
                "run_id is stale for this workbench server",
                status_code=409,
                boundary="stale_run",
                state=copy.deepcopy(self._state),
            )

    def _fresh_run_state(self, run_id: str) -> dict[str, Any]:
        state = self._initial_state()
        state["run_id"] = run_id
        return state

    def _initial_state(self) -> dict[str, Any]:
        return {
            "schema": WORKBENCH_STATE_SCHEMA,
            "server_identity": self._server_identity,
            "sequence_id": WORKBENCH_SEQUENCE_ID,
            "run_id": None,
            "phase": "idle",
            "source": None,
            "source_identity": None,
            "adapter": WORKBENCH_ADAPTER,
            **self._plugin_configuration(),
            "current_frame": None,
            "position": 0,
            "latest_processed_position": None,
            "selection_presets": self._selection_presets(),
            "progress": {"completed": 0, "total": 0, "percent": 0.0},
            "summary": frame_summary(frames_completed=0, frames_total=0),
            "machine_detail": self._machine_detail(),
            "steps": empty_steps(),
            "steps_revision": 0,
            "timeline": [],
            "failure": None,
            "failure_boundary": None,
            "recovery_action": "start",
            "cleanup": None,
            "controls": self._controls(phase="idle"),
        }

    def _controls(self, *, phase: str | None = None) -> dict[str, Any]:
        current_state = getattr(self, "_state", {})
        current_phase = phase or str(current_state.get("phase", "idle"))
        allowed = _state_action_set(current_phase)
        if current_phase in {"running", "paused"} and not self._viewing_latest():
            allowed.remove("select_plugins")
        return {
            "cadence_ms": self._cadence_ms,
            "pace": self._pace,
            "loop": bool(getattr(self, "_loop", WORKBENCH_DEFAULT_LOOP)),
            "allowed_actions": allowed,
        }

    def _machine_detail(self) -> dict[str, Any]:
        return {
            "pipeline": {
                **self._selection_presets(),
                **self._step_reports(),
                "observation_adapter": "autonomy.decision_cycle.observation.perception_summary.observation_from_perception",
                "decision_cycle": "autonomy.decision_cycle.cycle.DecisionCycle",
            },
            "source_contract": {
                "sequence_id": WORKBENCH_SEQUENCE_ID,
                "adapter": WORKBENCH_ADAPTER,
                "ordered": True,
                "absence_supported": True,
                "max_frames": self.max_frames,
                "max_image_bytes": self.max_image_bytes,
            },
            "side_effects": {
                "observation_only": True,
                "source_read_only": True,
                "worker": False,
                "simulator": False,
                "movement_control": False,
                "metrics": False,
                "recording": False,
            },
            "last_transition": None,
        }

    def _set_steps_locked(self, steps: dict[str, Any]) -> None:
        """Replace the displayed frame's step payloads; the revision tells the page they changed."""

        self._state["steps"] = steps
        self._state["steps_revision"] = int(self._state.get("steps_revision", 0)) + 1

    def _record_action_locked(self, action: str, **fields: Any) -> None:
        item = {"action": action, **fields, "at_ms": timestamp_ms()}
        detail = self._state.setdefault("machine_detail", self._machine_detail())
        detail["last_transition"] = item
        self._state["controls"] = self._controls()

    def _upsert_timeline_locked(self, detail: dict[str, Any]) -> None:
        item = timeline_item(detail)
        frame_id = item["frame"]["frame_id"]
        timeline = self._state["timeline"]
        for index, existing in enumerate(timeline):
            existing_frame = existing.get("frame") if isinstance(existing, dict) else None
            if isinstance(existing_frame, dict) and existing_frame.get("frame_id") == frame_id:
                timeline[index] = item
                return
        timeline.append(item)

    def _decision_step_plugins(self) -> dict[str, list[str]]:
        return {
            step: list(getattr(self._steps.get(step), "plugin_ids", ()) or ())
            for step in DECISION_STEPS
        }

    def _decision_configuration(self) -> dict[str, Any]:
        """The proposal selection, plus the plan and action plugins it runs through."""

        activation = getattr(self._steps.get("proposal"), "activation", None)
        document = activation.to_payload() if activation is not None else {}
        plugins = self._decision_step_plugins()
        return {
            "plugins": document.get("plugins", []),
            "plugin_specs": document.get("plugin_specs", {}),
            "plugin_configs": document.get("plugin_configs", {}),
            "plan": plugins["plan"],
            "action": plugins["action"],
            "gate_id": plugins["action"][0] if plugins["action"] else None,
            "host_delivery": "absent",
        }

    @staticmethod
    def _validate_cadence(value: int) -> int:
        try:
            cadence = int(value)
        except (TypeError, ValueError) as exc:
            raise ReplayActionError(
                "cadence_ms must be a nonnegative integer",
                status_code=400,
                boundary="input",
            ) from exc
        if cadence < 0 or cadence > 60_000:
            raise ReplayActionError(
                "cadence_ms must be between 0 and 60000",
                status_code=400,
                boundary="input",
            )
        return cadence

    @staticmethod
    def _validate_pace(value: str) -> str:
        if not isinstance(value, str) or value.strip().lower() not in WORKBENCH_PACES:
            raise ReplayActionError(
                "pace must be one of: fixed, realtime",
                status_code=400,
                boundary="input",
            )
        return value.strip().lower()


def _reset_status(runner: Any, shared_memory: dict[str, Any]) -> str:
    """Reset a step's runner for cleanup; report ``reset``, ``not_created`` or the error."""

    if runner is None:
        return "not_created"
    try:
        runner.reset(shared_memory)
    except Exception as exc:  # noqa: BLE001 - cleanup boundary
        return f"error: {type(exc).__name__}: {exc}"
    return "reset"


__all__ = ["ImageReplayRunner"]
