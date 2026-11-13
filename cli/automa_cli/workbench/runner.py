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
from typing import Callable, NoReturn

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS, StepActivation
from autonomy.decision_cycle.cycle import CYCLE_RECORD_KEYS, cycle_result_of
from autonomy.decision_cycle.perception.interface import PerceptionText
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
    replace_replay_pipeline,
    resolve_replay_activations,
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
from .page_payloads import frame_summary


def _safe_status(value: object) -> str:
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


def _plugin_fields_rejected(
    action: str,
    step: str | None,
    active_plugin_ids: list[str] | tuple[str, ...] | None,
) -> bool:
    return (
        (step is not None or active_plugin_ids is not None)
        and action != "select_plugins"
    )


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
        step_factories: Mapping[str, Callable[[], object]] | None = None,
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
        self._steps: dict[str, object] = {}
        self._shared_memory: dict[str, object] = {}
        self._history: dict[str, dict[str, object]] = {}
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

    def state(self) -> dict[str, object]:
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

    def _plugin_configuration(self) -> dict[str, object]:
        """Each selectable step's catalog and active plugins, as the page shows them."""

        configuration: dict[str, object] = {}
        for step in WORKBENCH_SELECTABLE_STEPS:
            active = self._activations[step].plugins
            configuration[f"{step}_plugin_catalog"] = self._catalogs[step].to_dict(
                active_ids=active
            )
            configuration[f"active_{step}_plugin_ids"] = list(active)
        return configuration

    def _selection_presets(self) -> dict[str, object]:
        """Each selectable step's preset, under ``<step>_preset``."""

        return {f"{step}_preset": self._selection_preset(step) for step in WORKBENCH_SELECTABLE_STEPS}

    def _step_reports(self) -> dict[str, object]:
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
    ) -> dict[str, object]:
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

    def wait(self, timeout: float | None = None) -> dict[str, object]:
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
    ) -> dict[str, object]:
        action = str(action or "").strip()
        if action not in WORKBENCH_ACTIONS:
            raise ReplayActionError(
                f"unknown workbench action {action!r}",
                status_code=400,
                boundary="action",
            )
        if action in {"pause", "resume", "set_cadence", "set_loop"}:
            return self._dispatch_worker_flags(
                action, run_id=run_id, cadence_ms=cadence_ms, pace=pace, loop=loop,
            )
        if action == "seek":
            return self._dispatch_seek(run_id, step, active_plugin_ids, position)
        return self._dispatch_held_action(
            action,
            run_id=run_id,
            source_dir=source_dir,
            cadence_ms=cadence_ms,
            pace=pace,
            step=step,
            active_plugin_ids=active_plugin_ids,
            loop=loop,
        )

    def _dispatch_worker_flags(
        self,
        action: str,
        *,
        run_id: str | None,
        cadence_ms: int | None,
        pace: str | None,
        loop: bool | None,
    ) -> dict[str, object]:
        # Pause, resume, cadence, and loop only flip worker flags. A frame
        # already executing still finishes and publishes; the page does not
        # wait it out.
        with self._condition:
            self._check_run_id_locked(action, run_id)
            if action == "set_cadence":
                return self._set_cadence_locked(cadence_ms, pace)
            if action == "set_loop":
                return self._set_loop(loop)
            if action == "pause":
                return self._pause()
            return self._resume()

    def _set_cadence_locked(self, cadence_ms: int | None, pace: str | None) -> dict[str, object]:
        if cadence_ms is None and pace is None:
            raise ReplayActionError(
                "set_cadence requires cadence_ms or pace",
                status_code=400,
                boundary="input",
            )
        value = self._validate_cadence(cadence_ms) if cadence_ms is not None else None
        selected_pace = self._validate_pace(pace) if pace is not None else self._pace
        if self._state["phase"] not in {"running", "paused"}:
            raise ReplayActionError(
                "cadence can only change while replay is running or paused",
                boundary="lifecycle",
            )
        if value is not None:
            self._cadence_ms = value
        self._pace = selected_pace
        self._record_action_locked(
            "set_cadence", cadence_ms=self._cadence_ms, pace=self._pace,
        )
        self._condition.notify_all()
        return self.state()

    def _dispatch_seek(
        self,
        run_id: str | None,
        step: str | None,
        active_plugin_ids: list[str] | tuple[str, ...] | None,
        position: int | None,
    ) -> dict[str, object]:
        # A newer seek invalidates an older catch-up without waiting for it.
        # The work itself still takes the frame lock one frame at a time.
        with self._condition:
            self._check_run_id_locked("seek", run_id)
            if step is not None or active_plugin_ids is not None:
                raise ReplayActionError(
                    "step and active_plugin_ids are only accepted by select_plugins",
                    status_code=400,
                    boundary="input",
                )
            self._seek_generation += 1
            seek_generation = self._seek_generation
        return self._seek(position, seek_generation=seek_generation)

    def _dispatch_held_action(
        self,
        action: str,
        *,
        run_id: str | None,
        source_dir: str | os.PathLike[str] | None,
        cadence_ms: int | None,
        pace: str | None,
        step: str | None,
        active_plugin_ids: list[str] | tuple[str, ...] | None,
        loop: bool | None,
    ) -> dict[str, object]:
        # Plugin changes keep the action lock, which is the frame boundary.
        if action == "reset":
            with self._condition:
                self._check_run_id_locked(action, run_id)
                self._seek_generation += 1
        with self._action_lock:
            with self._lock:
                self._check_run_id_locked(action, run_id)
            if _plugin_fields_rejected(action, step, active_plugin_ids):
                raise ReplayActionError(
                    "step and active_plugin_ids are only accepted by select_plugins",
                    status_code=400,
                    boundary="input",
                )
            if action == "select_plugins":
                return self._select_plugins(step, active_plugin_ids)
            if action == "validate":
                return self._validate_action(source_dir)
            if action == "start":
                return self._start_action(
                    source_dir, cadence_ms=cadence_ms, pace=pace, loop=loop,
                )
            if action == "step":
                return self._step()
            if action == "reset":
                return self._reset()
        raise AssertionError(f"unhandled action {action}")

    def _validate_action(
        self, source_dir: str | os.PathLike[str] | None,
    ) -> dict[str, object]:
        with self._lock:
            if self._state["phase"] in {"running", "paused"}:
                raise ReplayActionError(
                    "validate is unavailable while replay is active",
                    boundary="lifecycle",
                )
        try:
            self.validate_source(source_dir)
        except PluginCatalogError as exc:
            self._reject_validation(exc, "plugin_catalog")
        except SourceValidationError as exc:
            self._reject_validation(exc, "source")
        with self._lock:
            self._state["failure"] = None
            self._state["failure_boundary"] = None
            self._record_action_locked("validate")
        return self.state()

    def _reject_validation(self, exc: Exception, boundary: str) -> NoReturn:
        with self._lock:
            self._state["failure"] = {"message": str(exc), "boundary": boundary}
            self._state["failure_boundary"] = boundary
            self._record_action_locked("validate")
        raise ReplayActionError(
            str(exc), status_code=422, boundary=boundary, state=self.state(),
        ) from exc

    def _start_action(
        self,
        source_dir: str | os.PathLike[str] | None,
        *,
        cadence_ms: int | None,
        pace: str | None,
        loop: bool | None,
    ) -> dict[str, object]:
        try:
            return self.start(source_dir, cadence_ms=cadence_ms, pace=pace, loop=loop)
        except SourceValidationError as exc:
            raise ReplayActionError(
                str(exc), status_code=422, boundary="source", state=self.state(),
            ) from exc

    def _select_plugins(
        self,
        step: str | None,
        active_plugin_ids: list[str] | tuple[str, ...] | None,
    ) -> dict[str, object]:
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
            refresh = self._install_selection_locked(
                step, catalog, normalized, active_phase,
            )
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
                self._history.pop(frame.frame_id, None)
                self._state["position"] = frame.position
        if refresh:
            self._process_one(run_id, generation, frame, allow_paused=True)
        return self.state()

    def _install_selection_locked(
        self,
        step: str,
        catalog: PluginCatalog,
        normalized: tuple[str, ...],
        active_phase: bool,
    ) -> bool:
        """Install a changed selection. Return whether the paused frame is republished."""

        if normalized == self._activations[step].plugins:
            return False
        activation = self._merged_selection(catalog, normalized)
        refresh = False
        try:
            if active_phase:
                runners, activations, changed = replace_replay_pipeline(
                    self._steps, self._replay_activations, {step: activation},
                    overrides=set(self._replay_activations) - {step},
                    build=self._runner_for,
                )
                if changed:
                    self._steps = runners
                    self._replay_activations = activations
                    self._shared_memory = {}
                    refresh = (
                        self._state["phase"] == "paused"
                        and self._state["latest_processed_position"] is not None
                    )
            self._activations[step] = activation
        except Exception as exc:
            message = str(exc)
            self._state["failure"] = {"message": message, "boundary": "plugin_catalog"}
            self._state["failure_boundary"] = "plugin_catalog"
            raise ReplayActionError(
                message, status_code=422, boundary="plugin_catalog", state=self.state(),
            ) from exc
        return refresh

    def _viewing_latest(self) -> bool:
        frame = self._state.get("current_frame")
        return frame is None or frame["position"] == self._state["latest_processed_position"]

    def _merged_selection(self, catalog: object, normalized: tuple[str, ...]) -> StepActivation:
        """Catalog definitions for new ids; retained ids keep their specs and configs."""

        activation = catalog.activation(normalized)
        previous = self._activations[catalog.step]
        retained = set(previous.plugins) & set(normalized)
        metadata = {**previous.metadata, "preset": activation.metadata.get("preset")}
        revisions = metadata.get("plugin_revisions")
        if isinstance(revisions, dict):
            kept = {
                key: value for key, value in revisions.items()
                if key in retained and isinstance(value, str) and value
            }
            if kept:
                metadata["plugin_revisions"] = kept
            else:
                metadata.pop("plugin_revisions", None)
        return replace(
            activation,
            plugin_specs={**activation.plugin_specs, **{
                key: previous.plugin_specs[key] for key in retained if key in previous.plugin_specs
            }},
            plugin_configs={**activation.plugin_configs, **{
                key: dict(previous.plugin_configs.get(key, {})) for key in retained
            }},
            metadata=metadata,
        )

    def _runner_for(self, step: str, activation: StepActivation) -> object:
        """A fresh runner for ``step``. Test factories stay in their bundle-free hook."""

        factory = self.step_factories.get(step)
        if factory is not None:
            return factory()
        return recorded_runner(activation)

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
        self._state["timing"] = None
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

    def _build_steps(self, first_frame: ReplayFrame | None = None) -> dict[str, object]:
        """Restore the first frame's selections before constructing any selectable step."""
        recorded = recorded_selections(first_frame.metadata) if first_frame else {}
        defaults = {step: default_selection(step) for step in STEPS}
        defaults.update(self._default_activations)
        activations = resolve_replay_activations(
            recorded, defaults=defaults,
            overrides={step: self._activations[step] for step in self._selection_overrides},
        )
        steps = {
            step: self._runner_for(step, activation) if activation is not None else None
            for step, activation in activations.items()
        }
        self._replay_activations = activations
        for step, activation in activations.items():
            self._store_recorded_activation(step, activation)
        return steps

    def _set_loop(self, loop: bool | None) -> dict[str, object]:
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

    def _pause(self) -> dict[str, object]:
        with self._condition:
            if self._state["phase"] != "running":
                raise ReplayActionError(
                    "pause requires a running replay", boundary="lifecycle"
                )
            self._state["phase"] = "paused"
            self._record_action_locked("pause")
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _resume(self) -> dict[str, object]:
        with self._condition:
            if self._state["phase"] != "paused":
                raise ReplayActionError(
                    "resume requires a paused replay", boundary="lifecycle"
                )
            self._state["phase"] = "running"
            self._record_action_locked("resume")
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _step(self) -> dict[str, object]:
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

    def _seek(self, position: int | None, *, seek_generation: int) -> dict[str, object]:
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
        cached: dict[str, object],
    ) -> None:
        self._state["current_frame"] = frame.to_dict()
        self._set_displayed_locked(cached["cycle"], cached["perception"])
        # The live pipeline is updated in place. A cached frame keeps its own.
        if "pipeline" in cached:
            self._state["machine_detail"]["pipeline"] = copy.deepcopy(cached["pipeline"])
        completed = frame.position + 1
        total = len(self._image_source.frames) if self._image_source is not None else completed
        self._state["progress"]["completed"] = completed
        self._state["progress"]["percent"] = (
            round((completed / total) * 100.0, 2) if total else 100.0
        )
        self._state["position"] = completed
        self._state["summary"] = cached["summary"]
        self._state["timing"] = cached["timing"]
        if completed >= total:
            self._wrap_or_complete_locked()

    def _reset(self) -> dict[str, object]:
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
                if not self._loop_still_running_locked(run_id, generation):
                    return
                if self._state["phase"] == "paused":
                    self._condition.wait()
                    continue
                image_source = self._image_source
                if image_source is None:
                    self._complete_locked()
                    return
                position = int(self._state["position"])
                frame = (
                    image_source.frames[position]
                    if position < len(image_source.frames)
                    else None
                )
            if frame is None:
                if not self._continue_after_last_frame(generation, image_source):
                    return
                continue
            processing_started = time.monotonic()
            if not self._process_one(run_id, generation, frame):
                continue
            self._wait_for_frame_gap(
                run_id, generation, frame, time.monotonic() - processing_started,
            )

    def _loop_still_running_locked(self, run_id: str, generation: int) -> bool:
        return (
            generation == self._generation
            and self._state["run_id"] == run_id
            and self._state["phase"] in {"running", "paused"}
        )

    def _continue_after_last_frame(self, generation: int, image_source: ImageSource) -> bool:
        """Return whether the worker should take another frame after the source ends."""

        with self._action_lock, self._condition:
            if generation != self._generation or self._state["phase"] != "running":
                return True
            if self._state["position"] < len(image_source.frames):
                return True
            if self._loop:
                self._restart_capture_locked()
                return True
            self._complete_locked()
            return False

    def _wait_for_frame_gap(
        self,
        run_id: str,
        generation: int,
        frame: ReplayFrame,
        processing_elapsed: float,
    ) -> None:
        with self._condition:
            if not self._loop_still_running_locked(run_id, generation):
                return
            if self._state["phase"] != "running":
                return
            delay = self._frame_gap_seconds_locked(frame, processing_elapsed)
            if delay > 0:
                self._condition.wait(timeout=delay)

    def _frame_gap_seconds_locked(self, frame: ReplayFrame, processing_elapsed: float) -> float:
        if self._pace != "realtime":
            return self._cadence_ms / 1000.0
        next_position = int(self._state["position"])
        source = self._image_source
        if source is None or next_position >= len(source.frames):
            return 0.0
        next_frame = source.frames[next_position]
        return max(
            0.0,
            (next_frame.timestamp_ms - frame.timestamp_ms) / 1000.0 - processing_elapsed,
        )

    def _process_one(
        self,
        run_id: str,
        generation: int,
        frame: ReplayFrame,
        *,
        allow_paused: bool = False,
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
                    try:
                        runners, activations, changed = replace_replay_pipeline(
                            steps, self._replay_activations, recorded_selections(frame.metadata),
                            overrides=self._selection_overrides, build=self._runner_for,
                        )
                    except Exception as exc:
                        self._set_failure_locked(
                            boundary="pipeline", message=f"Frame {frame.frame_id}: {type(exc).__name__}: {exc}",
                            recovery_action="start", cleanup=False,
                        )
                        self._condition.notify_all()
                        return False
                    if changed:
                        steps = runners
                        self._accept_changed_pipeline_locked(runners, activations)
                    self._apply_plugin_configuration_locked()
                outcome = run_frame(frame, steps=steps, shared_memory=self._shared_memory)
                result = outcome.result
                with self._condition:
                    self._publish_frame_locked(frame, outcome)
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
                    partial = cycle_result_of(exc)
                    cycle = (
                        partial.export_cycle()
                        if partial is not None
                        else {key: None for key in CYCLE_RECORD_KEYS}
                    )
                    self._set_displayed_locked(cycle, cycle.get("perception"))
                    self._state["timing"] = None if partial is None else copy.deepcopy(partial.timing)
                    self._set_failure_locked(
                        boundary=getattr(exc, "boundary", "pipeline"),
                        message=f"{type(exc).__name__}: {exc}",
                        recovery_action="start",
                    )
                    self._condition.notify_all()
                return False

    def _accept_changed_pipeline_locked(
        self,
        runners: dict[str, object],
        activations: Mapping[str, StepActivation | None],
    ) -> None:
        self._steps = runners
        self._replay_activations = activations
        self._shared_memory = {}
        for name, activation in activations.items():
            if name not in self._selection_overrides:
                self._store_recorded_activation(name, activation)

    def _publish_frame_locked(self, frame: ReplayFrame, outcome: FrameOutcome) -> None:
        """Publish a processed frame and cache its presentation under the condition lock."""

        result = outcome.result
        cycle = result.export_cycle()
        perception = cycle.get("perception")
        self._state["current_frame"] = frame.to_dict()
        self._set_displayed_locked(cycle, perception)
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
            memory=cycle["memory"],
            duration_ms=result.duration_ms,
            frames_completed=self._state["progress"]["completed"],
            frames_total=self._state["progress"].get("total", 0),
        )
        pipeline = self._state["machine_detail"]["pipeline"]
        pipeline.update(self._step_reports())
        timing = copy.deepcopy(result.timing)
        self._state["timing"] = timing
        # History owns this frame. The cycle, perception, summary, and timing
        # are those objects. The pipeline is a copy because the live one is
        # updated in place for the next frame.
        self._history[frame.frame_id] = {
            "cycle": cycle,
            "perception": perception,
            "pipeline": copy.deepcopy(pipeline),
            "summary": self._state["summary"],
            "timing": timing,
        }
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
        cleanup: bool = True,
    ) -> None:
        self._state["phase"] = "failed"
        self._state["failure"] = {
            "message": str(message)[:1000],
            "boundary": str(boundary),
        }
        self._state["failure_boundary"] = str(boundary)
        self._state["recovery_action"] = recovery_action
        if cleanup:
            self._state["cleanup"] = self._cleanup_locked()
        self._state["controls"] = self._controls()

    def _cleanup_locked(self) -> dict[str, object]:
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

    def _fresh_run_state(self, run_id: str) -> dict[str, object]:
        state = self._initial_state()
        state["run_id"] = run_id
        return state

    def _initial_state(self) -> dict[str, object]:
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
            "cycle": {key: None for key in CYCLE_RECORD_KEYS},
            "perception": None,
            "steps_revision": 0,
            "timing": None,
            "failure": None,
            "failure_boundary": None,
            "recovery_action": "start",
            "cleanup": None,
            "controls": self._controls(phase="idle"),
        }

    def _controls(self, *, phase: str | None = None) -> dict[str, object]:
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

    def _machine_detail(self) -> dict[str, object]:
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

    def _set_displayed_locked(self, cycle: dict[str, object], perception: object) -> None:
        """Replace the displayed cycle and perception; the revision tells the page they changed.

        The page shows the perception ``text`` rendered from the stored evidence.
        """

        self._state["cycle"] = cycle
        self._state["perception"] = (
            {**perception, "text": PerceptionText.from_dict(perception).text}
            if isinstance(perception, dict) else perception
        )
        self._state["steps_revision"] = int(self._state.get("steps_revision", 0)) + 1

    def _record_action_locked(self, action: str, **fields: object) -> None:
        item = {"action": action, **fields, "at_ms": timestamp_ms()}
        detail = self._state.setdefault("machine_detail", self._machine_detail())
        detail["last_transition"] = item
        self._state["controls"] = self._controls()

    def _decision_step_plugins(self) -> dict[str, list[str]]:
        return {
            step: list(getattr(self._steps.get(step), "plugin_ids", ()) or ())
            for step in DECISION_STEPS
        }

    def _decision_configuration(self) -> dict[str, object]:
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
    def _validate_pace(value: object) -> str:
        if not isinstance(value, str) or value.strip().lower() not in WORKBENCH_PACES:
            raise ReplayActionError(
                "pace must be one of: fixed, realtime",
                status_code=400,
                boundary="input",
            )
        return value.strip().lower()


def _reset_status(runner: object, shared_memory: dict[str, object]) -> str:
    """Reset a step's runner for cleanup; report ``reset``, ``not_created`` or the error."""

    if runner is None:
        return "not_created"
    try:
        runner.reset(shared_memory)
    except Exception as exc:  # noqa: BLE001 - cleanup boundary
        return f"error: {type(exc).__name__}: {exc}"
    return "reset"


__all__ = ["ImageReplayRunner"]
