from __future__ import annotations

import importlib
import threading
import time
import traceback
from typing import Any, Callable

from .engine import AutonomyControl, AutonomySnapshot


DEFAULT_ENGINE_SPEC = "autonomy.runtime.engine:IdleAutonomyEngine"


def timestamp_ms() -> int:
    return int(time.time() * 1000)


class EngineLoadError(RuntimeError):
    pass


class AutonomyManager:
    """Thread-safe holder for the currently loaded autonomy engine."""

    def __init__(
        self,
        *,
        default_engine_spec: str = DEFAULT_ENGINE_SPEC,
        default_engine_config: dict[str, Any] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self.default_engine_spec = default_engine_spec
        self.engine_spec = default_engine_spec
        self.engine_config = default_engine_config or {}
        self.engine_schema: dict[str, Any] | None = None
        self.engine: Any = None
        self.loaded_at_ms: int | None = None
        self.last_step_at_ms: int | None = None
        self.step_count = 0
        self.error_count = 0
        self.last_error: str | None = None
        self.last_control = AutonomyControl(reason="engine-not-loaded")
        self._status_providers: dict[str, Callable[[], dict[str, Any]]] = {}
        self.load_engine(default_engine_spec, self.engine_config)

    def register_status_provider(
        self,
        component_id: str,
        provider: Callable[[], dict[str, Any]],
    ) -> None:
        if not component_id or not callable(provider):
            raise ValueError("runtime status providers require an id and callable")
        with self._lock:
            self._status_providers[component_id] = provider

    def load_engine(
        self,
        engine_spec: str,
        engine_config: dict[str, Any] | None = None,
        *,
        reload_module: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            try:
                engine = self._instantiate_engine(
                    engine_spec,
                    engine_config or {},
                    reload_module=reload_module,
                )
                engine.reset()
                engine_schema = engine.describe_schema()
                if not isinstance(engine_schema, dict):
                    raise TypeError("autonomy engine describe_schema must return a dictionary")
            except Exception as exc:
                self.error_count += 1
                self.last_error = "".join(
                    traceback.format_exception_only(type(exc), exc),
                ).strip()
                raise EngineLoadError(self.last_error) from exc

            self.engine = engine
            self.engine_spec = engine_spec
            self.engine_config = engine_config or {}
            self.engine_schema = engine_schema
            self.loaded_at_ms = timestamp_ms()
            self.last_error = None
            self.last_control = AutonomyControl(reason="engine-loaded")
            return self.status()

    def reload_engine(self) -> dict[str, Any]:
        return self.load_engine(
            self.engine_spec,
            self.engine_config,
            reload_module=True,
        )

    def step(self, snapshot: AutonomySnapshot) -> AutonomyControl:
        with self._lock:
            engine = self.engine
            engine_spec = self.engine_spec

        if engine is None:
            return AutonomyControl(reason="engine-not-loaded")

        try:
            control = engine.step(snapshot)
            if not isinstance(control, AutonomyControl):
                raise TypeError("autonomy engine step must return AutonomyControl")
        except Exception as exc:
            with self._lock:
                self.error_count += 1
                self.last_error = "".join(
                    traceback.format_exception_only(type(exc), exc),
                ).strip()
                self.last_control = AutonomyControl(
                    reason="engine-error",
                    metadata={"engine": engine_spec, "error": self.last_error},
                )
                return self.last_control

        with self._lock:
            self.step_count += 1
            self.last_step_at_ms = timestamp_ms()
            self.last_error = None
            self.last_control = control
            return control

    def status(self) -> dict[str, Any]:
        with self._lock:
            payload = {
                "engine": self.engine_spec,
                "engine_config": self.engine_config,
                "engine_schema": self.engine_schema,
                "loaded_at_ms": self.loaded_at_ms,
                "last_step_at_ms": self.last_step_at_ms,
                "step_count": self.step_count,
                "error_count": self.error_count,
                "last_error": self.last_error,
                "last_control": self.last_control.to_dict(),
            }
            providers = dict(self._status_providers)
        components: dict[str, Any] = {}
        for component_id, provider in providers.items():
            try:
                components[component_id] = provider()
            except Exception as exc:
                components[component_id] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        payload["components"] = components
        return payload

    def _instantiate_engine(
        self,
        engine_spec: str,
        engine_config: dict[str, Any],
        *,
        reload_module: bool,
    ) -> Any:
        module_name, separator, class_name = engine_spec.partition(":")
        if not separator or not module_name or not class_name:
            raise ValueError("engine spec must be 'module.path:ClassName'")

        importlib.invalidate_caches()
        module = importlib.import_module(module_name)
        if reload_module:
            module = importlib.reload(module)
        engine_cls = getattr(module, class_name)
        return engine_cls(**engine_config)
