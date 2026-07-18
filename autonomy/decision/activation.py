"""Memory activation documents and framework-owned stage execution."""

from __future__ import annotations

import importlib
import json
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cycle import DecisionFrameContext
from .memory import (
    MemoryBounds,
    MemorySnapshot,
    empty_memory_snapshot,
    error_memory_snapshot,
)
from .observation import Observation
from .plugin import MemoryImplementation


MEMORY_ACTIVATION_SCHEMA = "automa_memory_activation_v0"
DEFAULT_MAX_RECORDS = 32
DEFAULT_MAX_AGE_MS = 10_000
DEFAULT_EVICTION_POLICY = "oldest_first"


@dataclass(frozen=True)
class MemoryActivation:
    implementation_id: str
    implementation_spec: str
    implementation_config: dict[str, Any]
    bounds: MemoryBounds
    source_path: Path
    payload: dict[str, Any]


def read_memory_activation(path: Path) -> MemoryActivation:
    if not path.exists():
        raise FileNotFoundError(f"memory activation is missing: {path}")
    payload = json_load_object(path)
    if payload.get("schema") != MEMORY_ACTIVATION_SCHEMA:
        raise ValueError(
            f"memory activation has unsupported schema {payload.get('schema')!r}: {path}"
        )
    memory = payload.get("memory")
    if not isinstance(memory, dict):
        raise ValueError(f"memory activation has no memory section: {path}")

    implementation_id = memory.get("implementation_id")
    implementation_spec = memory.get("implementation_spec")
    implementation_config = memory.get("implementation_config")
    if not isinstance(implementation_id, str) or not implementation_id.strip():
        raise ValueError(f"memory activation has no implementation_id: {path}")
    if not isinstance(implementation_spec, str) or not implementation_spec.strip():
        raise ValueError(f"memory activation has no implementation_spec: {path}")
    if not isinstance(implementation_config, dict):
        raise ValueError(f"memory activation has invalid implementation_config: {path}")

    config = deepcopy(implementation_config)
    bounds = bounds_from_config(config)
    return MemoryActivation(
        implementation_id=implementation_id.strip(),
        implementation_spec=implementation_spec.strip(),
        implementation_config=config,
        bounds=bounds,
        source_path=path,
        payload=payload,
    )


def bounds_from_config(config: dict[str, Any]) -> MemoryBounds:
    max_records = config.get("max_records", DEFAULT_MAX_RECORDS)
    max_age_ms = config.get("max_age_ms", DEFAULT_MAX_AGE_MS)
    eviction_policy = config.get("eviction_policy", DEFAULT_EVICTION_POLICY)
    return MemoryBounds(
        max_records=int(max_records),
        max_age_ms=int(max_age_ms) if max_age_ms is not None else None,
        eviction_policy=str(eviction_policy or DEFAULT_EVICTION_POLICY),
    )


def load_memory_implementation(
    activation: MemoryActivation,
    *,
    reload_module: bool = False,
) -> MemoryImplementation:
    return instantiate_memory_implementation(
        activation.implementation_spec,
        activation.implementation_config,
        expected_implementation_id=activation.implementation_id,
        reload_module=reload_module,
    )


def load_memory_stage_if_present(path: Path) -> ActivatedMemoryStage | None:
    """Load an activated memory stage when the activation document exists.

    Missing paths return None so Chase and Donkey hosts can share optional
    wiring without requiring memory before package activation exists.
    """

    if not path.exists():
        return None
    return ActivatedMemoryStage(read_memory_activation(path))


def instantiate_memory_implementation(
    implementation_spec: str,
    implementation_config: dict[str, Any],
    *,
    expected_implementation_id: str | None = None,
    reload_module: bool = False,
) -> MemoryImplementation:
    module_name, separator, class_name = implementation_spec.partition(":")
    if not separator or not module_name or not class_name:
        raise ValueError("memory implementation spec must be 'module.path:ClassName'")
    importlib.invalidate_caches()
    module = importlib.import_module(module_name)
    if reload_module:
        module = importlib.reload(module)
    implementation_cls = getattr(module, class_name)
    implementation = implementation_cls(**deepcopy(implementation_config))
    if not isinstance(implementation, MemoryImplementation):
        raise TypeError(
            "configured memory implementation does not satisfy MemoryImplementation: "
            f"{implementation_spec}"
        )
    if not isinstance(implementation.implementation_id, str) or not implementation.implementation_id.strip():
        raise TypeError(
            f"memory implementation must declare a non-empty implementation_id: {implementation_spec}"
        )
    if (
        expected_implementation_id is not None
        and implementation.implementation_id != expected_implementation_id
    ):
        raise ValueError(
            "memory implementation_id mismatch: activation declares "
            f"{expected_implementation_id!r} but loaded {implementation.implementation_id!r}"
        )
    return implementation


class ActivatedMemoryStage:
    """Decision-cycle memory stage backed by one activated implementation.

    The framework owns load, reset, timing, status, and failure isolation.
    Implementations only express update/reset/snapshot policy. Source
    observations are treated as read-only inputs; failures produce an error
    snapshot with no retained claims and do not raise into the cycle.
    """

    def __init__(self, activation: MemoryActivation) -> None:
        self.activation = activation
        self.implementation = load_memory_implementation(activation)
        self.last_snapshot: MemorySnapshot | None = None
        self.last_duration_ms: float | None = None
        self.last_error: str | None = None
        self.update_count = 0
        self.reset_count = 0
        self.failure_count = 0
        self.last_snapshot = self.reset()

    def __call__(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> MemorySnapshot:
        return self.update(context, observation)

    def update(
        self,
        context: DecisionFrameContext,
        observation: Observation | None,
    ) -> MemorySnapshot:
        started = time.perf_counter()
        try:
            # Observations are frozen; deepcopy defensive metadata only if needed
            # by refusing mutation contracts: never pass writable shared state.
            snapshot = self.implementation.update(context, observation)
            snapshot = self._accept_snapshot(snapshot, operation="update")
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - stage isolation boundary
            self.failure_count += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            snapshot = self._error_snapshot(self.last_error)
        self.last_duration_ms = (time.perf_counter() - started) * 1000.0
        self.update_count += 1
        self.last_snapshot = snapshot
        return snapshot

    def reset(self) -> MemorySnapshot:
        started = time.perf_counter()
        try:
            snapshot = self.implementation.reset()
            snapshot = self._accept_snapshot(snapshot, operation="reset")
            if snapshot.health not in {"empty", "unavailable"}:
                raise ValueError(
                    "memory reset must return empty or unavailable health; "
                    f"got {snapshot.health!r}"
                )
            if snapshot.records:
                raise ValueError("memory reset must not retain records")
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - stage isolation boundary
            self.failure_count += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            snapshot = empty_memory_snapshot(
                memory_id=f"memory-reset-failed-{self.reset_count + 1}",
                epoch_id=f"epoch-failed-{self.reset_count + 1}",
                bounds=self.activation.bounds,
                created_at_ms=_timestamp_ms(),
                implementation_id=self.activation.implementation_id,
                summary=(f"memory_reset_failed={self.last_error}",),
                metadata={"reset_error": self.last_error},
            )
        self.last_duration_ms = (time.perf_counter() - started) * 1000.0
        self.reset_count += 1
        self.last_snapshot = snapshot
        return snapshot

    def snapshot(self) -> MemorySnapshot:
        try:
            current = self.implementation.snapshot()
            return self._accept_snapshot(current, operation="snapshot")
        except Exception as exc:  # noqa: BLE001 - stage isolation boundary
            self.failure_count += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            return self._error_snapshot(self.last_error)

    def status(self) -> dict[str, Any]:
        last = self.last_snapshot
        return {
            "implementation_id": self.activation.implementation_id,
            "implementation_spec": self.activation.implementation_spec,
            "activation": str(self.activation.source_path),
            "bounds": self.activation.bounds.to_dict(),
            "update_count": self.update_count,
            "reset_count": self.reset_count,
            "failure_count": self.failure_count,
            "last_duration_ms": self.last_duration_ms,
            "last_error": self.last_error,
            "last_health": last.health if last is not None else None,
            "last_epoch_id": last.epoch_id if last is not None else None,
            "last_record_count": last.record_count if last is not None else None,
        }

    def _accept_snapshot(
        self,
        snapshot: MemorySnapshot,
        *,
        operation: str,
    ) -> MemorySnapshot:
        if not isinstance(snapshot, MemorySnapshot):
            raise TypeError(
                f"memory {operation} must return MemorySnapshot; "
                f"got {type(snapshot).__name__}"
            )
        if snapshot.implementation_id not in (
            None,
            self.activation.implementation_id,
        ):
            raise ValueError(
                "memory snapshot implementation_id "
                f"{snapshot.implementation_id!r} does not match activation "
                f"{self.activation.implementation_id!r}"
            )
        configured = self.activation.bounds
        if snapshot.bounds.max_records > configured.max_records:
            raise ValueError(
                "memory snapshot max_records "
                f"{snapshot.bounds.max_records} exceeds activation max_records "
                f"{configured.max_records}"
            )
        if snapshot.record_count > configured.max_records:
            raise ValueError(
                f"memory snapshot retains {snapshot.record_count} records but "
                f"activation max_records is {configured.max_records}"
            )
        if (
            configured.max_age_ms is not None
            and snapshot.bounds.max_age_ms is not None
            and snapshot.bounds.max_age_ms > configured.max_age_ms
        ):
            raise ValueError(
                "memory snapshot max_age_ms "
                f"{snapshot.bounds.max_age_ms} exceeds activation max_age_ms "
                f"{configured.max_age_ms}"
            )
        return snapshot

    def _error_snapshot(self, error: str) -> MemorySnapshot:
        previous = self.last_snapshot
        epoch_id = previous.epoch_id if previous is not None else "epoch-error"
        return error_memory_snapshot(
            memory_id=f"memory-error-{self.failure_count}",
            epoch_id=epoch_id,
            bounds=self.activation.bounds,
            created_at_ms=_timestamp_ms(),
            error=error,
            implementation_id=self.activation.implementation_id,
            metadata={
                "failure_count": self.failure_count,
                "previous_health": previous.health if previous is not None else None,
            },
        )


def json_load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"memory activation must be a JSON object: {path}")
    return payload


def _timestamp_ms() -> int:
    return int(time.time() * 1000)
