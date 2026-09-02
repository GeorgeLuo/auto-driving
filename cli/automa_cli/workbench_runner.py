"""Authoritative replay runner for the perception-memory workbench."""

from __future__ import annotations

import copy
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from autonomy.decision import (
    ActivatedMemoryStage,
    DecisionCycle,
    DecisionFrameContext,
    DecisionStages,
    MemoryActivation,
    Observation,
    observation_from_perception,
)
from autonomy.decision.activation import bounds_from_config
from autonomy.perception import (
    PerceptionMapper,
    PerceptionText,
    build_perception_request,
)
from autonomy.perception.activation import instantiate_perception_mapper
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot
from implementations.memory.catalog import (
    DEFAULT_MEMORY_IMPLEMENTATION,
    build_memory_activation_payload,
)
from implementations.perception.catalog import (
    DEFAULT_PERCEPTION_ALGORITHM,
    PERCEPTION_ALGORITHMS,
)

from .workbench_contract import (
    ReplayActionError,
    WORKBENCH_ACTIONS,
    WORKBENCH_DEFAULT_CADENCE_MS,
    WORKBENCH_DEFAULT_PACE,
    WORKBENCH_PACES,
    WORKBENCH_SEQUENCE_ID,
    WORKBENCH_STATE_SCHEMA,
)
from .workbench_plugins import (
    PluginCatalog,
    PluginCatalogError,
    discover_plugin_catalog,
)
from .workbench_source import (
    ImageFeed,
    ReplayFrame,
    SourceValidationError,
    WORKBENCH_ADAPTER,
    WORKBENCH_DEFAULT_MAX_FRAMES,
    WORKBENCH_DEFAULT_MAX_IMAGE_BYTES,
    content_type_for_path,
    normalize_image_directory,
)


def _snapshot_for_frame(frame: ReplayFrame) -> SensorSnapshot | None:
    if frame.absent or frame.image_path is None:
        return None
    reading = SensorReading(
        sensor_id=FRONT_CAMERA_SENSOR_ID,
        sensor_kind="camera",
        captured_at_ms=frame.timestamp_ms,
        path=str(frame.image_path),
        metadata={
            "source_id": frame.source_id,
            "frame_id": frame.frame_id,
            "sequence_index": frame.position,
        },
    )
    return SensorSnapshot(
        read_id=frame.frame_id,
        readings={FRONT_CAMERA_SENSOR_ID: reading},
        started_at_ms=frame.timestamp_ms,
        completed_at_ms=frame.timestamp_ms,
        request={
            "source": "workbench.image_replay.v1",
            "requested_sensors": [FRONT_CAMERA_SENSOR_ID],
        },
        metadata={
            "source_id": frame.source_id,
            "sequence_index": frame.position,
            "absence": False,
        },
    )


def _default_mapper() -> PerceptionMapper:
    config = PERCEPTION_ALGORITHMS[DEFAULT_PERCEPTION_ALGORITHM]
    return instantiate_perception_mapper(
        str(config["mapper_spec"]),
        copy.deepcopy(dict(config["mapper_config"])),
    )


def _default_memory_stage() -> ActivatedMemoryStage:
    payload = build_memory_activation_payload(DEFAULT_MEMORY_IMPLEMENTATION)
    section = payload["memory"]
    config = copy.deepcopy(dict(section["implementation_config"]))
    activation = MemoryActivation(
        implementation_id=str(section["implementation_id"]),
        implementation_spec=str(section["implementation_spec"]),
        implementation_config=config,
        bounds=bounds_from_config(config),
        source_path=Path("workbench-fixed-memory"),
        payload=payload,
    )
    return ActivatedMemoryStage(activation)


def _safe_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _state_action_set(phase: str) -> list[str]:
    if phase == "idle":
        return ["validate", "refresh_plugins", "select_plugins", "start", "reset"]
    if phase == "running":
        return ["pause", "seek", "cancel", "reset", "set_cadence", "select_plugins"]
    if phase == "paused":
        return ["resume", "step", "seek", "cancel", "reset", "set_cadence", "select_plugins"]
    if phase in {"completed", "failed", "cancelled"}:
        return ["validate", "refresh_plugins", "select_plugins", "start", "reset"]
    return []


class ImageReplayRunner:
    """Shared replay owner used by both the CLI and loopback API."""

    def __init__(
        self,
        source_dir: str | os.PathLike[str] | None = None,
        *,
        source_root: Path | None = None,
        plugin_dir: str | os.PathLike[str] | None = None,
        active_plugin_ids: list[str] | tuple[str, ...] | None = None,
        cadence_ms: int = WORKBENCH_DEFAULT_CADENCE_MS,
        pace: str = WORKBENCH_DEFAULT_PACE,
        max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
        max_image_bytes: int = WORKBENCH_DEFAULT_MAX_IMAGE_BYTES,
        mapper_factory: Callable[[], PerceptionMapper] | None = None,
        memory_stage_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.source_root = (
            Path(source_root).expanduser().resolve() if source_root else None
        )
        self.source_dir = os.fspath(source_dir) if source_dir is not None else None
        self.plugin_dir = os.fspath(plugin_dir) if plugin_dir is not None else None
        self.max_frames = int(max_frames)
        self.max_image_bytes = int(max_image_bytes)
        self.mapper_factory = mapper_factory or _default_mapper
        self._mapper_factory_explicit = mapper_factory is not None
        self.memory_stage_factory = memory_stage_factory or _default_memory_stage
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._action_lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._feed: ImageFeed | None = None
        self._mapper: Any = None
        self._memory_stage: Any = None
        self._history: dict[str, dict[str, Any]] = {}
        self._generation = 0
        self._cadence_ms = self._validate_cadence(cadence_ms)
        self._pace = self._validate_pace(pace)
        self._server_identity = f"workbench-{uuid.uuid4().hex[:12]}"
        self._plugin_catalog: PluginCatalog = discover_plugin_catalog(plugin_dir)
        self.plugin_dir = (
            str(self._plugin_catalog.root)
            if self._plugin_catalog.explicit_root and self._plugin_catalog.root is not None
            else None
        )
        self._active_plugin_ids = self._initial_plugin_selection(active_plugin_ids)
        self._state = self._initial_state()

    @property
    def server_identity(self) -> str:
        return self._server_identity

    def state(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    @property
    def plugin_catalog(self) -> PluginCatalog:
        with self._lock:
            return self._plugin_catalog

    def _initial_plugin_selection(
        self,
        active_plugin_ids: list[str] | tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        if active_plugin_ids is None and not self._plugin_catalog.explicit_root:
            active_plugin_ids = ("frame", "floor_plane")
        if active_plugin_ids is None:
            return ()
        try:
            return self._plugin_catalog.normalize_selection(
                active_plugin_ids,
                require_explicit_selection=self._plugin_catalog.explicit_root,
            )
        except PluginCatalogError:
            # Keep invalid pending input visible in structured state; start and
            # selection actions will refuse it at the catalog boundary.
            return tuple(str(item) for item in active_plugin_ids)

    def _plugin_configuration(self) -> dict[str, Any]:
        return {
            "plugin_dir": self.plugin_dir,
            "catalog_digest": self._plugin_catalog.digest,
            "active_plugin_ids": list(self._active_plugin_ids),
            "plugin_order": list(self._active_plugin_ids),
        }

    def _apply_plugin_configuration_locked(self) -> None:
        configuration = self._plugin_configuration()
        self._state["plugin_dir"] = configuration["plugin_dir"]
        self._state["catalog_digest"] = configuration["catalog_digest"]
        self._state["active_plugin_ids"] = configuration["active_plugin_ids"]
        self._state["plugin_order"] = configuration["plugin_order"]
        self._state["plugin_catalog"] = self._plugin_catalog.to_dict(
            active_ids=self._active_plugin_ids
        )
        self._state["machine_detail"]["pipeline"]["active_plugin_ids"] = list(
            self._active_plugin_ids
        )
        self._state["machine_detail"]["pipeline"]["perception_algorithm"] = (
            DEFAULT_PERCEPTION_ALGORITHM
            if list(self._active_plugin_ids) == ["frame", "floor_plane"]
            else "manifest_plugin_selection"
        )
        self._state["machine_detail"]["pipeline"]["catalog_digest"] = (
            self._plugin_catalog.digest
        )

    def frame_detail(
        self,
        frame_id: str,
        *,
        run_id: str,
    ) -> dict[str, Any] | None:
        """Return one processed frame's server-owned detail for the active run."""

        with self._lock:
            self._require_run_id_locked(run_id)
            detail = self._history.get(frame_id)
            return copy.deepcopy(detail) if detail is not None else None

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
        if self._feed is None:
            return None
        selected_id = frame_id
        if selected_id is None:
            current = self._state.get("current_frame")
            selected_id = current.get("frame_id") if isinstance(current, dict) else None
        if selected_id is None:
            return None
        return next(
            (frame for frame in self._feed.frames if frame.frame_id == selected_id),
            None,
        )

    def _frame_for_position_locked(self, position: int) -> ReplayFrame | None:
        if self._feed is None:
            return None
        if position < 0 or position >= len(self._feed.frames):
            return None
        return self._feed.frames[position]

    def validate_source(
        self, source_dir: str | os.PathLike[str] | None = None
    ) -> ImageFeed:
        raw_source = self.source_dir if source_dir is None else os.fspath(source_dir)
        if raw_source is None:
            raise SourceValidationError("start requires source_dir")
        with self._lock:
            if self._state["phase"] in {"running", "paused"}:
                raise SourceValidationError(
                    "source cannot be changed while replay is active"
                )
        feed = normalize_image_directory(
            raw_source,
            source_root=self.source_root,
            max_frames=self.max_frames,
            max_image_bytes=self.max_image_bytes,
        )
        with self._lock:
            self._feed = feed
            self._history.clear()
            self.source_dir = str(feed.source_path)
            self._state = self._initial_state()
            self._apply_plugin_configuration_locked()
            self._state["source"] = feed.to_dict()
            self._state["source_identity"] = feed.source_id
            self._state["adapter"] = feed.adapter
            self._state["progress"] = {
                "completed": 0,
                "total": len(feed.frames),
                "percent": 0.0,
            }
            self._state["summary"] = self._summary(
                frames_completed=0,
                frames_total=len(feed.frames),
            )
        return feed

    def start(
        self,
        source_dir: str | os.PathLike[str] | None = None,
        *,
        cadence_ms: int | None = None,
        pace: str | None = None,
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
                if self._mapper is not None or self._memory_stage is not None:
                    self._cleanup_locked()
                self._feed = None
                self._mapper = None
                self._memory_stage = None
                self._history.clear()
                self._state = self._fresh_run_state(run_id)
                self._apply_plugin_configuration_locked()
                self._state["source"] = {"path": str(raw_source)}
                try:
                    selected_plugin_ids = self._plugin_catalog.normalize_selection(
                        self._active_plugin_ids,
                        require_explicit_selection=self._plugin_catalog.explicit_root,
                    )
                    feed = normalize_image_directory(
                        raw_source,
                        source_root=self.source_root,
                        max_frames=self.max_frames,
                        max_image_bytes=self.max_image_bytes,
                    )
                    mapper = self._build_mapper_for_selection(selected_plugin_ids)
                    memory_stage = self.memory_stage_factory()
                except Exception as exc:  # noqa: BLE001 - startup isolation boundary
                    self._set_failure_locked(
                        boundary=getattr(exc, "boundary", "startup"),
                        message=str(exc),
                        recovery_action="start",
                    )
                    self._condition.notify_all()
                    return copy.deepcopy(self._state)
                self._feed = feed
                self.source_dir = str(feed.source_path)
                self._mapper = mapper
                self._memory_stage = memory_stage
                self._state["active_plugin_ids"] = list(selected_plugin_ids)
                self._state["plugin_order"] = list(selected_plugin_ids)
                self._state["run_plugin_dir"] = self.plugin_dir
                self._state["run_catalog_digest"] = self._plugin_catalog.digest
                self._state["run_active_plugin_ids"] = list(selected_plugin_ids)
                self._state["run_plugin_order"] = list(selected_plugin_ids)
                self._state["source"] = feed.to_dict()
                self._state["source_identity"] = feed.source_id
                self._state["adapter"] = feed.adapter
                self._state["progress"]["total"] = len(feed.frames)
                self._state["machine_detail"] = self._machine_detail()
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
                self._feed = None
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
        plugin_dir: str | os.PathLike[str] | None = None,
        active_plugin_ids: list[str] | tuple[str, ...] | None = None,
        position: int | None = None,
    ) -> dict[str, Any]:
        action = str(action or "").strip()
        if action not in WORKBENCH_ACTIONS:
            raise ReplayActionError(
                f"unknown workbench action {action!r}",
                status_code=400,
                boundary="action",
            )
        with self._action_lock:
            with self._lock:
                self._check_run_id_locked(action, run_id)
            if action in {"refresh_plugins", "inspect_plugins"}:
                return self._refresh_plugins(plugin_dir)
            if action in {"select_plugins", "set_plugins"}:
                return self._select_plugins(active_plugin_ids)
            if action == "validate":
                with self._lock:
                    if self._state["phase"] in {"running", "paused"}:
                        raise ReplayActionError(
                            "validate is unavailable while replay is active",
                            boundary="lifecycle",
                        )
                try:
                    self._configure_plugins_if_requested(plugin_dir, active_plugin_ids)
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
                    self._configure_plugins_if_requested(plugin_dir, active_plugin_ids)
                except PluginCatalogError as exc:
                    raise ReplayActionError(
                        str(exc),
                        status_code=422,
                        boundary="plugin_catalog",
                        state=self.state(),
                    ) from exc
                return self.start(source_dir, cadence_ms=cadence_ms, pace=pace)
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
                with self._lock:
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
            if action == "pause":
                return self._pause()
            if action == "resume":
                return self._resume()
            if action == "step":
                return self._step()
            if action == "seek":
                return self._seek(position)
            if action == "cancel":
                return self._cancel()
            if action == "reset":
                return self._reset()
        raise AssertionError(f"unhandled action {action}")

    def _configure_plugins_if_requested(
        self,
        plugin_dir: str | os.PathLike[str] | None,
        active_plugin_ids: list[str] | tuple[str, ...] | None,
    ) -> None:
        if plugin_dir is None and active_plugin_ids is None:
            return
        with self._lock:
            if self._state["phase"] in {"running", "paused"}:
                raise ReplayActionError(
                    "plugin configuration cannot change while replay is active",
                    boundary="lifecycle",
                )
        catalog = (
            discover_plugin_catalog(plugin_dir)
            if plugin_dir is not None
            else self._plugin_catalog
        )
        with self._lock:
            same_root = (
                catalog.root_identity == self._plugin_catalog.root_identity
                and catalog.explicit_root == self._plugin_catalog.explicit_root
            )
            pending_ids = (
                tuple(active_plugin_ids)
                if active_plugin_ids is not None
                else self._active_plugin_ids
            )
            if active_plugin_ids is not None:
                normalized = catalog.normalize_selection(
                    pending_ids,
                    require_explicit_selection=catalog.explicit_root,
                )
            elif catalog.explicit_root and not same_root:
                normalized = ()
            else:
                try:
                    normalized = catalog.normalize_selection(
                        pending_ids,
                        require_explicit_selection=catalog.explicit_root,
                    )
                except PluginCatalogError:
                    # A refreshed catalog may have removed a previous id. Keep
                    # the stale ids visible so the next explicit selection can
                    # repair the configuration, but never run them.
                    normalized = tuple(pending_ids)
            self._plugin_catalog = catalog
            self.plugin_dir = (
                str(catalog.root) if catalog.explicit_root and catalog.root is not None else None
            )
            self._active_plugin_ids = tuple(normalized)
            self._apply_plugin_configuration_locked()
            if catalog.error:
                raise PluginCatalogError(catalog.error)

    def _refresh_plugins(
        self,
        plugin_dir: str | os.PathLike[str] | None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._state["phase"] in {"running", "paused"}:
                raise ReplayActionError(
                    "plugin catalog cannot refresh while replay is active",
                    boundary="lifecycle",
                )
        if plugin_dir is None:
            plugin_dir = self.plugin_dir
        try:
            self._configure_plugins_if_requested(plugin_dir, None)
        except PluginCatalogError as exc:
            with self._lock:
                self._state["failure"] = {"message": str(exc), "boundary": "plugin_catalog"}
                self._state["failure_boundary"] = "plugin_catalog"
            raise ReplayActionError(
                str(exc),
                status_code=422,
                boundary="plugin_catalog",
                state=self.state(),
            ) from exc
        with self._lock:
            self._record_action_locked("refresh_plugins")
            return copy.deepcopy(self._state)

    def _select_plugins(
        self,
        active_plugin_ids: list[str] | tuple[str, ...] | None,
    ) -> dict[str, Any]:
        if active_plugin_ids is None:
            raise ReplayActionError(
                "select_plugins requires active_plugin_ids",
                status_code=400,
                boundary="input",
            )
        with self._lock:
            active_phase = self._state["phase"] in {"running", "paused"}
            try:
                normalized = self._plugin_catalog.normalize_selection(
                    active_plugin_ids,
                    require_explicit_selection=self._plugin_catalog.explicit_root,
                )
            except PluginCatalogError as exc:
                self._state["failure"] = {"message": str(exc), "boundary": "plugin_catalog"}
                self._state["failure_boundary"] = "plugin_catalog"
                raise ReplayActionError(
                    str(exc),
                    status_code=422,
                    boundary="plugin_catalog",
                    state=self.state(),
                ) from exc

            # The action lock serializes this boundary with frame processing.
            # Build and reset before publishing the new mapper so a failed
            # instantiation leaves the effective selection and old mapper
            # untouched.
            if active_phase and (
                normalized != self._active_plugin_ids or self._mapper is None
            ):
                next_mapper = None
                previous_mapper = self._mapper
                try:
                    next_mapper = self._build_mapper_for_selection(normalized)
                    if previous_mapper is not None:
                        previous_mapper.reset()
                except Exception as exc:  # noqa: BLE001 - selection boundary
                    if next_mapper is not None:
                        try:
                            next_mapper.reset()
                        except Exception:
                            pass
                    message = str(exc)
                    self._state["failure"] = {
                        "message": message,
                        "boundary": "plugin_catalog",
                    }
                    self._state["failure_boundary"] = "plugin_catalog"
                    raise ReplayActionError(
                        message,
                        status_code=422,
                        boundary="plugin_catalog",
                        state=self.state(),
                    ) from exc
                self._mapper = next_mapper

            selection_changed = normalized != self._active_plugin_ids
            self._active_plugin_ids = normalized
            self._apply_plugin_configuration_locked()
            if active_phase:
                self._state["run_active_plugin_ids"] = list(normalized)
                self._state["run_plugin_order"] = list(normalized)
                pipeline = self._state.get("machine_detail", {}).get("pipeline", {})
                pipeline["run_active_plugin_ids"] = list(normalized)
            self._state["failure"] = None
            self._state["failure_boundary"] = None
            refresh_frame = None
            run_id = None
            generation = None
            if (
                selection_changed
                and self._state["phase"] == "paused"
                and self._feed is not None
            ):
                current = self._state.get("current_frame")
                frame_id = current.get("frame_id") if isinstance(current, dict) else None
                if frame_id:
                    refresh_frame = self._frame_for_id_locked(str(frame_id))
                    run_id = str(self._state["run_id"])
                    generation = self._generation
            self._record_action_locked("select_plugins")
            if refresh_frame is None:
                return copy.deepcopy(self._state)
        self._process_one(
            run_id,
            generation,
            refresh_frame,
            allow_paused=True,
            refresh_current=True,
        )
        with self._lock:
            return copy.deepcopy(self._state)

    def _build_mapper_for_selection(
        self,
        selected_plugin_ids: tuple[str, ...],
    ) -> Any:
        if self._mapper_factory_explicit:
            return self.mapper_factory()
        return self._plugin_catalog.build_mapper(selected_plugin_ids)

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
            if self._state["phase"] != "paused" or self._feed is None:
                raise ReplayActionError(
                    "step requires a paused replay", boundary="lifecycle"
                )
            run_id = str(self._state["run_id"])
            generation = self._generation
            if self._state["position"] >= len(self._feed.frames):
                self._complete_locked()
                return copy.deepcopy(self._state)
            frame = self._feed.frames[self._state["position"]]
        self._process_one(run_id, generation, frame, allow_paused=True)
        with self._lock:
            self._record_action_locked("step")
            return copy.deepcopy(self._state)

    def _seek(self, position: int | None) -> dict[str, Any]:
        if isinstance(position, bool) or not isinstance(position, int):
            raise ReplayActionError(
                "seek requires an integer position",
                status_code=400,
                boundary="input",
            )
        with self._condition:
            if self._state["phase"] not in {"running", "paused"} or self._feed is None:
                raise ReplayActionError(
                    "seek requires a running or paused replay",
                    boundary="lifecycle",
                )
            total = len(self._feed.frames)
            if position < 0 or position >= total:
                raise ReplayActionError(
                    "seek position is outside the loaded source",
                    status_code=400,
                    boundary="input",
                    state=copy.deepcopy(self._state),
                )
            if self._state["phase"] == "running":
                self._state["phase"] = "paused"
                self._condition.notify_all()
            run_id = str(self._state["run_id"])
            generation = self._generation
            frame = self._feed.frames[position]
            cached = self._history.get(frame.frame_id)
            if cached is not None:
                self._apply_cached_frame_locked(frame, cached)
                self._record_action_locked("seek", position=position)
                return copy.deepcopy(self._state)
        self._process_one(run_id, generation, frame, allow_paused=True)
        with self._lock:
            self._record_action_locked("seek", position=position)
            return copy.deepcopy(self._state)

    def _apply_cached_frame_locked(
        self,
        frame: ReplayFrame,
        cached: dict[str, Any],
    ) -> None:
        self._state["current_frame"] = frame.to_dict()
        self._state["perception"] = copy.deepcopy(cached.get("perception"))
        self._state["observation"] = copy.deepcopy(cached.get("observation"))
        self._state["memory"] = copy.deepcopy(cached.get("memory"))
        completed = frame.position + 1
        total = len(self._feed.frames) if self._feed is not None else completed
        self._state["progress"]["completed"] = completed
        self._state["progress"]["percent"] = (
            round((completed / total) * 100.0, 2) if total else 100.0
        )
        self._state["position"] = completed
        self._state["summary"] = self._summary(
            frames_completed=completed,
            frames_total=total,
            duration_ms=cached.get("duration_ms"),
        )
        if completed >= total:
            self._complete_locked()

    def _cancel(self) -> dict[str, Any]:
        with self._condition:
            if self._state["phase"] not in {"running", "paused"}:
                raise ReplayActionError(
                    "cancel requires a running or paused replay", boundary="lifecycle"
                )
            self._state["phase"] = "cancelled"
            self._state["recovery_action"] = "start"
            self._state["cleanup"] = self._cleanup_locked()
            self._record_action_locked("cancel")
            self._condition.notify_all()
            return copy.deepcopy(self._state)

    def _reset(self) -> dict[str, Any]:
        with self._condition:
            self._generation += 1
            if self._state["phase"] in {"running", "paused"}:
                self._state["phase"] = "cancelled"
            self._cleanup_locked()
            feed = self._feed
            source_dir = self.source_dir
            source_state = copy.deepcopy(self._state.get("source"))
            source_identity = self._state.get("source_identity")
            adapter = self._state.get("adapter")
            self._state = self._initial_state()
            self._apply_plugin_configuration_locked()
            self._history.clear()
            self._feed = feed
            if feed is not None:
                self.source_dir = source_dir
                self._state["source"] = (
                    source_state if source_state is not None else feed.to_dict()
                )
                self._state["source_identity"] = source_identity or feed.source_id
                self._state["adapter"] = adapter or feed.adapter
                self._state["progress"] = {
                    "completed": 0,
                    "total": len(feed.frames),
                    "percent": 0.0,
                }
                self._state["summary"] = self._summary(
                    frames_completed=0,
                    frames_total=len(feed.frames),
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
                feed = self._feed
                position = int(self._state["position"])
                if feed is None or position >= len(feed.frames):
                    self._complete_locked()
                    return
                frame = feed.frames[position]
            processing_started = time.monotonic()
            self._process_one(run_id, generation, frame)
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
                        self._feed.frames[next_position]
                        if self._feed is not None
                        and next_position < len(self._feed.frames)
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
        refresh_current: bool = False,
    ) -> None:
        with self._action_lock:
            with self._lock:
                if (
                    generation != self._generation
                    or self._state["run_id"] != run_id
                    or self._state["phase"]
                    not in ({"running", "paused"} if allow_paused else {"running"})
                ):
                    return
                mapper = self._mapper
                memory_stage = self._memory_stage
            try:
                snapshot = _snapshot_for_frame(frame)
                context = DecisionFrameContext(
                    frame_id=frame.frame_id,
                    frame_index=frame.frame_index,
                    timestamp_ms=frame.timestamp_ms,
                    sensor_snapshot=snapshot,
                    mode="workbench_replay",
                    metadata={
                        "source": WORKBENCH_SEQUENCE_ID,
                        "source_id": frame.source_id,
                        "sequence_index": frame.position,
                    },
                )

                def perceive(current: DecisionFrameContext) -> PerceptionText | None:
                    if frame.absent or current.sensor_snapshot is None:
                        return None
                    request = build_perception_request(
                        current.sensor_snapshot,
                        metadata={
                            "source": WORKBENCH_SEQUENCE_ID,
                            "source_id": frame.source_id,
                            "sequence_index": frame.position,
                        },
                    )
                    return mapper.perceive(request)

                def observe(
                    current: DecisionFrameContext,
                    perception: PerceptionText | None,
                ) -> Observation:
                    return observation_from_perception(
                        observation_id=f"{frame.source_id}:{frame.frame_id}",
                        sensor_snapshot=current.sensor_snapshot,
                        perception=perception,
                        metadata={
                            "source": WORKBENCH_SEQUENCE_ID,
                            "source_id": frame.source_id,
                            "sequence_index": frame.position,
                            "absence_reason": frame.absence_reason,
                        },
                        created_at_ms=frame.timestamp_ms,
                    )

                def remember(
                    current: DecisionFrameContext,
                    observation: Observation | None,
                ) -> Any:
                    return memory_stage(current, observation)

                result = DecisionCycle(
                    DecisionStages(
                        perceive=perceive,
                        observe=observe,
                        remember=remember,
                    ),
                    idle_reason="workbench-observation-only",
                ).run(context)
                perception_payload = (
                    result.perception.to_dict() if result.perception else None
                )
                observation_payload = (
                    result.observation.to_dict() if result.observation else None
                )
                memory_payload = result.memory.to_dict() if result.memory else None
                with self._condition:
                    previous_memory = self._state.get("memory")
                    self._state["current_frame"] = frame.to_dict()
                    self._state["perception"] = perception_payload
                    self._state["observation"] = observation_payload
                    self._state["memory"] = memory_payload
                    self._state["progress"]["completed"] = frame.position + 1
                    self._state["progress"]["percent"] = (
                        round(
                            ((frame.position + 1) / len(self._feed.frames)) * 100.0,
                            2,
                        )
                        if self._feed
                        else 100.0
                    )
                    self._state["summary"] = self._summary(
                        perception=result.perception,
                        observation=result.observation,
                        memory=result.memory,
                        duration_ms=result.duration_ms,
                    )
                    detail = self._frame_detail(
                        frame=frame,
                        result=result,
                        previous_memory=previous_memory,
                    )
                    self._history[frame.frame_id] = detail
                    if refresh_current:
                        self._upsert_timeline_locked(detail)
                    else:
                        self._state["timeline"].append(self._timeline_item(detail))
                        self._state["position"] = frame.position + 1
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
                    elif (
                        result.memory is not None
                        and _safe_status(result.memory.health) == "error"
                    ):
                        self._set_failure_locked(
                            boundary="memory",
                            message=result.memory.error
                            or "memory stage returned an error",
                            recovery_action="start",
                        )
                    elif (
                        not refresh_current
                        and self._state["position"] >= len(self._feed.frames)
                    ):
                        self._complete_locked()
                    self._condition.notify_all()
            except Exception as exc:  # noqa: BLE001 - per-frame isolation boundary
                with self._condition:
                    self._state["current_frame"] = frame.to_dict()
                    self._state["perception"] = None
                    self._state["observation"] = None
                    self._state["memory"] = None
                    self._set_failure_locked(
                        boundary=getattr(exc, "boundary", "pipeline"),
                        message=f"{type(exc).__name__}: {exc}",
                        recovery_action="start",
                    )
                    self._condition.notify_all()

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
        mapper_status = "not_created"
        memory_status = "not_created"
        if self._mapper is not None:
            try:
                self._mapper.reset()
                mapper_status = "reset"
            except Exception as exc:  # noqa: BLE001 - cleanup boundary
                mapper_status = f"error: {type(exc).__name__}: {exc}"
        if self._memory_stage is not None:
            try:
                self._memory_stage.reset()
                memory_status = "reset"
            except Exception as exc:  # noqa: BLE001 - cleanup boundary
                memory_status = f"error: {type(exc).__name__}: {exc}"
        cleanup = {
            "completed_at_ms": _now_ms(),
            "mapper": mapper_status,
            "memory": memory_status,
            "source_read_only": True,
            "worker_started": False,
            "simulator_used": False,
            "movement_control": False,
            "metrics_used": False,
            "recording_enabled": False,
        }
        self._mapper = None
        self._memory_stage = None
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
                "refresh_plugins",
                "inspect_plugins",
                "select_plugins",
                "set_plugins",
                "reset",
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
        plugin_configuration = (
            self._plugin_configuration()
            if hasattr(self, "_plugin_catalog") and hasattr(self, "_active_plugin_ids")
            else {
                "plugin_dir": None,
                "catalog_digest": None,
                "active_plugin_ids": [],
                "plugin_order": [],
            }
        )
        plugin_catalog = (
            self._plugin_catalog.to_dict(active_ids=self._active_plugin_ids)
            if hasattr(self, "_plugin_catalog") and hasattr(self, "_active_plugin_ids")
            else None
        )
        return {
            "schema": WORKBENCH_STATE_SCHEMA,
            "server_identity": self._server_identity,
            "sequence_id": WORKBENCH_SEQUENCE_ID,
            "run_id": None,
            "phase": "idle",
            "source": None,
            "source_identity": None,
            "adapter": WORKBENCH_ADAPTER,
            "plugin_dir": plugin_configuration["plugin_dir"],
            "catalog_digest": plugin_configuration["catalog_digest"],
            "active_plugin_ids": plugin_configuration["active_plugin_ids"],
            "plugin_order": plugin_configuration["plugin_order"],
            "plugin_catalog": plugin_catalog,
            "run_plugin_dir": None,
            "run_catalog_digest": None,
            "run_active_plugin_ids": [],
            "run_plugin_order": [],
            "current_frame": None,
            "position": 0,
            "progress": {"completed": 0, "total": 0, "percent": 0.0},
            "summary": self._summary(frames_completed=0, frames_total=0),
            "machine_detail": self._machine_detail(include_run=False),
            "perception": None,
            "observation": None,
            "memory": None,
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
        return {
            "cadence_ms": self._cadence_ms,
            "pace": self._pace,
            "allowed_actions": _state_action_set(current_phase),
        }

    def _summary(
        self,
        *,
        perception: PerceptionText | None = None,
        observation: Observation | None = None,
        memory: Any = None,
        duration_ms: int | float | None = None,
        frames_completed: int | None = None,
        frames_total: int | None = None,
    ) -> dict[str, Any]:
        progress = self._state.get("progress", {}) if hasattr(self, "_state") else {}
        summary = {
            "frames_completed": (
                int(progress.get("completed", 0))
                if frames_completed is None
                else int(frames_completed)
            ),
            "frames_total": (
                int(progress.get("total", 0))
                if frames_total is None
                else int(frames_total)
            ),
            "perception_status": perception.status if perception else None,
            "perception_things": len(perception.things) if perception else 0,
            "perception_signals": len(perception.signals) if perception else 0,
            "observation_available": observation is not None,
            "memory_health": memory.health if memory else None,
            "memory_records": memory.record_count if memory else 0,
            "last_duration_ms": round(float(duration_ms), 3)
            if duration_ms is not None
            else None,
        }
        return summary

    def _machine_detail(self, *, include_run: bool = True) -> dict[str, Any]:
        active_ids = list(getattr(self, "_active_plugin_ids", ()))
        current_state = getattr(self, "_state", {})
        run_active_ids = (
            current_state.get("run_active_plugin_ids") or active_ids
            if include_run
            else []
        )
        run_catalog_digest = (
            current_state.get("run_catalog_digest")
            or (self._plugin_catalog.digest if hasattr(self, "_plugin_catalog") else None)
            if include_run
            else None
        )
        return {
            "pipeline": {
                "perception_algorithm": (
                    DEFAULT_PERCEPTION_ALGORITHM
                    if active_ids == ["frame", "floor_plane"]
                    else "manifest_plugin_selection"
                ),
                "memory_implementation": DEFAULT_MEMORY_IMPLEMENTATION,
                "observation_adapter": "autonomy.decision.observation.observation_from_perception",
                "decision_cycle": "autonomy.decision.cycle.DecisionCycle",
                "active_plugin_ids": active_ids,
                "catalog_digest": (
                    self._plugin_catalog.digest
                    if hasattr(self, "_plugin_catalog")
                    else None
                ),
                "run_active_plugin_ids": list(run_active_ids),
                "run_catalog_digest": run_catalog_digest,
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

    def _record_action_locked(self, action: str, **fields: Any) -> None:
        item = {"action": action, **fields, "at_ms": _now_ms()}
        detail = self._state.setdefault("machine_detail", self._machine_detail())
        detail["last_transition"] = item
        self._state["controls"] = self._controls()

    def _frame_detail(
        self,
        *,
        frame: ReplayFrame,
        result: Any,
        previous_memory: dict[str, Any] | None,
    ) -> dict[str, Any]:
        memory = result.memory.to_dict() if result.memory else None
        previous_ids = {
            str(item.get("record_id"))
            for item in (previous_memory or {}).get("records", [])
            if isinstance(item, dict)
        }
        current_ids = {
            str(item.get("record_id"))
            for item in (memory or {}).get("records", [])
            if isinstance(item, dict)
        }
        return {
            "frame": frame.to_dict(include_path=False),
            "perception": result.perception.to_dict() if result.perception else None,
            "observation": result.observation.to_dict() if result.observation else None,
            "memory": memory,
            "perception_status": result.perception.status
            if result.perception
            else None,
            "memory_record_count": result.memory.record_count if result.memory else 0,
            "memory_effect": {
                "added": sorted(current_ids - previous_ids),
                "removed": sorted(previous_ids - current_ids),
                "retained": sorted(current_ids & previous_ids),
            },
            "duration_ms": result.duration_ms,
        }

    def _upsert_timeline_locked(self, detail: dict[str, Any]) -> None:
        item = self._timeline_item(detail)
        frame_id = item["frame"]["frame_id"]
        timeline = self._state["timeline"]
        for index, existing in enumerate(timeline):
            existing_frame = existing.get("frame") if isinstance(existing, dict) else None
            if isinstance(existing_frame, dict) and existing_frame.get("frame_id") == frame_id:
                timeline[index] = item
                return
        timeline.append(item)

    @staticmethod
    def _timeline_item(detail: dict[str, Any]) -> dict[str, Any]:
        frame = detail["frame"]
        return {
            "frame": {
                "frame_id": frame["frame_id"],
                "frame_index": frame["frame_index"],
                "position": frame["position"],
                "timestamp_ms": frame["timestamp_ms"],
                "absent": frame["absent"],
                "absence_reason": frame["absence_reason"],
            },
            "perception_status": detail["perception_status"],
            "memory_record_count": detail["memory_record_count"],
            "memory_effect": copy.deepcopy(detail["memory_effect"]),
            "duration_ms": detail["duration_ms"],
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


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = ["ImageReplayRunner"]
