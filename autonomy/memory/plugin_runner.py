"""Memory plugin selection and execution at the decision-cycle boundary."""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any

from autonomy.decision.cycle import DecisionFrameContext, MemoryUpdateError
from autonomy.decision.memory import (
    DEFAULT_MAX_DIAGNOSTIC_CHARS,
    MemorySnapshot,
    detach_memory_snapshot,
    empty_memory_snapshot,
    error_memory_snapshot,
    serialized_mapping_bytes,
    serialized_memory_snapshot_bytes,
)
from autonomy.decision.observation import Observation
from autonomy.decision.plugin import MemoryImplementation
from autonomy.memory import SharedMemory
from autonomy.plugins import PluginDefinition, PluginManager, PluginSelectionRuntime

from .activation import (
    FRAMEWORK_FALLBACK_IMPLEMENTATION_ID,
    MemoryActivation,
    bounds_from_config,
    build_minimal_framework_fallback,
    framework_error_identity,
    framework_fallback_timestamp_ms,
    framework_reset_identity,
    load_memory_implementation,
    memory_manager_from_activation,
)


class PluginMemoryRunner:
    """Run the manager's selected memory plugins once per decision cycle.

    Plugins run in selection order on the same host map. The last snapshot is
    the decision-facing value; the framework does not merge retention policies.
    """

    def __init__(
        self,
        activation: MemoryActivation | None = None,
        *,
        plugin_manager: PluginManager | None = None,
    ) -> None:
        if plugin_manager is None:
            if activation is None:
                raise ValueError("memory requires an activation or plugin_manager")
            plugin_manager = memory_manager_from_activation(activation)
        if not isinstance(plugin_manager, PluginManager):
            raise TypeError("plugin_manager must be a PluginManager")
        if plugin_manager.step != "memory":
            raise ValueError(f"plugin_manager is scoped to {plugin_manager.step!r}, not 'memory'")
        self.activation = activation
        self.plugin_manager = plugin_manager
        self._selection_runtime = PluginSelectionRuntime(plugin_manager)
        self._runtime_lock = RLock()
        self.plugin_ids: tuple[str, ...] = ()
        self.plugins: tuple[_MemoryPluginRuntime, ...] = ()
        self.last_snapshot: MemorySnapshot | None = None
        self.last_duration_ms: float | None = None
        self.last_error: str | None = None
        self.update_count = 0
        self.reset_count = 0
        self.failure_count = 0
        self._apply_selection()
        self.snapshot()

    @property
    def implementation(self) -> MemoryImplementation | None:
        """Compatibility access for callers inspecting a single applied plugin."""
        return self.plugins[0].implementation if len(self.plugins) == 1 else None

    def _load_plugin(self, definition: PluginDefinition) -> _MemoryPluginRuntime:
        config = deepcopy(dict(definition.config))
        return _MemoryPluginRuntime(MemoryActivation(
            implementation_id=definition.plugin_id,
            implementation_spec=definition.entrypoint,
            implementation_config=config,
            bounds=bounds_from_config(config),
            source_path=self.activation.source_path if self.activation else Path("memory-manager"),
            payload={},
        ))

    def _apply_selection(self, shared_memory: SharedMemory | None = None) -> None:
        applied = self._selection_runtime.apply(
            load=self._load_plugin,
            reset=lambda plugin: plugin.reset(shared_memory),
        )
        self.plugin_ids = tuple(definition.plugin_id for definition, _plugin in applied)
        self.plugins = tuple(plugin for _definition, plugin in applied)

    def __call__(
        self, context: DecisionFrameContext, observation: Observation | None,
    ) -> MemorySnapshot | None:
        return self.update(context, observation)

    def update(
        self, context: DecisionFrameContext, observation: Observation | None,
    ) -> MemorySnapshot | None:
        with self._runtime_lock:
            self._apply_selection(context.shared_memory)
            started = time.perf_counter()
            self.last_error = None
            snapshot = None
            try:
                for plugin in self.plugins:
                    snapshot = plugin.update(context, observation)
                    if context.shared_memory is not None:
                        context.shared_memory["decision.snapshot"] = snapshot
                if not self.plugins and context.shared_memory is not None:
                    context.shared_memory.pop("decision.snapshot", None)
                    context.shared_memory.pop("decision.observation", None)
            except Exception:
                self.failure_count += 1
                self.last_error = plugin.last_error
                raise
            finally:
                self.update_count += 1
                self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            return self._publish_snapshot(snapshot)

    def reset(self, shared_memory: SharedMemory | None = None) -> MemorySnapshot | None:
        with self._runtime_lock:
            started = time.perf_counter()
            self.last_error = None
            snapshot = None
            for plugin in self.plugins:
                failures = plugin.failure_count
                snapshot = plugin.reset(shared_memory)
                self.failure_count += plugin.failure_count - failures
                self.last_error = plugin.last_error or self.last_error
            if shared_memory is not None:
                if snapshot is None:
                    shared_memory.pop("decision.snapshot", None)
                else:
                    shared_memory["decision.snapshot"] = snapshot
            self.reset_count += 1
            self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            return self._publish_snapshot(snapshot)

    def snapshot(self) -> MemorySnapshot | None:
        with self._runtime_lock:
            # The final plugin owns the shared decision-facing snapshot.
            snapshot = None
            if self.plugins:
                plugin = self.plugins[-1]
                failures = plugin.failure_count
                snapshot = plugin.snapshot()
                self.failure_count += plugin.failure_count - failures
                self.last_error = plugin.last_error
            return self._publish_snapshot(snapshot)

    def _publish_snapshot(self, snapshot: MemorySnapshot | None) -> MemorySnapshot | None:
        self.last_snapshot = detach_memory_snapshot(snapshot) if snapshot is not None else None
        return detach_memory_snapshot(snapshot) if snapshot is not None else None

    def status(self) -> dict[str, Any]:
        with self._runtime_lock:
            last = self.last_snapshot
            final = self.plugins[-1].activation if self.plugins else None
            return {
                "implementation_id": final.implementation_id if final else None,
                "implementation_spec": final.implementation_spec if final else None,
                "activation": str(self.activation.source_path) if self.activation else None,
                "bounds": final.bounds.to_dict() if final else None,
                "available_plugins": sorted(self.plugin_manager.available_ids),
                "selected_plugin_ids": list(self.plugin_manager.selected_ids),
                "plugin_ids": list(self.plugin_ids),
                "plugins": [plugin.status() for plugin in self.plugins],
                "update_count": self.update_count,
                "reset_count": self.reset_count,
                "failure_count": self.failure_count,
                "last_duration_ms": self.last_duration_ms,
                "last_error": self.last_error,
                "last_health": last.health if last else None,
                "last_epoch_id": last.epoch_id if last else None,
                "last_record_count": last.record_count if last else None,
            }


class _MemoryPluginRuntime:
    """Memory-specific execution and snapshot validation for one applied plugin.

    The framework owns load, reset, timing, status, and validation.
    Implementations only express update/reset/snapshot policy. Source
    observations are treated as read-only inputs; update failures stop the cycle.
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
        # The host map is not available during construction; inspect the
        # implementation's initial snapshot without counting a real reset.
        self.last_snapshot = self.snapshot()

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
            # Observation evidence is separate from the host-owned shared map
            # available through context.shared_memory.
            snapshot = self.implementation.update(context, observation)
            owned = self._accept_snapshot(snapshot, operation="update")
            if owned.health == "error":
                raise MemoryUpdateError(owned.error or "memory step returned an error snapshot")
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - step isolation boundary
            self.failure_count += 1
            self.last_error = self._bound_diagnostic(format_exception_safely(exc))
            raise
        finally:
            self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            self.update_count += 1
        return self._publish_snapshot(owned)

    def reset(self, shared_memory: SharedMemory | None = None) -> MemorySnapshot:
        started = time.perf_counter()
        try:
            snapshot = (
                self.implementation.reset(shared_memory)
                if shared_memory is not None
                else self.implementation.reset()
            )
            owned = self._accept_snapshot(snapshot, operation="reset")
            if owned.health not in {"empty", "unavailable"}:
                raise ValueError(
                    "memory reset must return empty or unavailable health; "
                    f"got {owned.health!r}"
                )
            if owned.records:
                raise ValueError("memory reset must not retain records")
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - step isolation boundary
            self.failure_count += 1
            self.last_error = self._bound_diagnostic(format_exception_safely(exc))
            memory_id, epoch_id = framework_reset_identity(self.reset_count + 1)
            owned = self._bounded_fallback_snapshot(
                memory_id=memory_id,
                epoch_id=epoch_id,
                health="empty",
                error=self.last_error,
                summary_prefix="memory_reset_failed",
                metadata_key="reset_error",
            )
        self.last_duration_ms = (time.perf_counter() - started) * 1000.0
        self.reset_count += 1
        if shared_memory is not None:
            shared_memory["decision.snapshot"] = owned
        return self._publish_snapshot(owned)

    def snapshot(self) -> MemorySnapshot:
        try:
            current = self.implementation.snapshot()
            owned = self._accept_snapshot(current, operation="snapshot")
        except Exception as exc:  # noqa: BLE001 - step isolation boundary
            self.failure_count += 1
            self.last_error = self._bound_diagnostic(format_exception_safely(exc))
            owned = self._error_snapshot(self.last_error)
        return self._publish_snapshot(owned)

    def status(self) -> dict[str, Any]:
        last = self.last_snapshot
        # Keep this step generic: do not promote implementation-specific
        # telemetry keys (for example capacity eviction counters) into status.
        # Callers that need snapshot metadata read the published MemorySnapshot.
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
        declared = snapshot.bounds
        if declared.max_records > configured.max_records:
            raise ValueError(
                "memory snapshot max_records "
                f"{declared.max_records} exceeds activation max_records "
                f"{configured.max_records}"
            )
        if snapshot.record_count > configured.max_records:
            raise ValueError(
                f"memory snapshot retains {snapshot.record_count} records but "
                f"activation max_records is {configured.max_records}"
            )
        # Policy labels are not orderable; require an exact match rather than
        # silently relabeling implementation behavior on normalize.
        if declared.eviction_policy != configured.eviction_policy:
            raise ValueError(
                "memory snapshot eviction_policy "
                f"{declared.eviction_policy!r} does not match activation "
                f"eviction_policy {configured.eviction_policy!r}"
            )
        # Reject removed or weakened age/size bounds. None is weaker than a
        # finite activation ceiling; a larger window/limit is also weaker.
        for field_name in ("max_age_ms", "max_property_bytes", "max_serialized_bytes"):
            configured_limit = getattr(configured, field_name)
            if configured_limit is None:
                continue
            declared_limit = getattr(declared, field_name)
            if declared_limit is None:
                raise ValueError(
                    f"memory snapshot removed {field_name} while activation requires "
                    f"{field_name}={configured_limit}"
                )
            if declared_limit > configured_limit:
                raise ValueError(
                    f"memory snapshot {field_name} {declared_limit} exceeds activation "
                    f"{field_name} {configured_limit}"
                )

        # Enforce the tighter of activation and declared property ceilings.
        property_limit = configured.max_property_bytes
        if declared.max_property_bytes is not None:
            property_limit = (
                declared.max_property_bytes
                if property_limit is None
                else min(property_limit, declared.max_property_bytes)
            )
        if property_limit is not None:
            for record in snapshot.records:
                size = serialized_mapping_bytes(record.properties)
                if size > property_limit:
                    raise ValueError(
                        "memory record "
                        f"{record.record_id!r} properties are {size} bytes; "
                        f"allowed max_property_bytes is {property_limit}"
                    )

        # Detach the implementation value and enforce its own declared ceiling
        # first (self-consistency of the advertised policy).
        detached = detach_memory_snapshot(snapshot)
        declared_size = serialized_memory_snapshot_bytes(detached)
        if (
            declared.max_serialized_bytes is not None
            and declared_size > declared.max_serialized_bytes
        ):
            raise ValueError(
                f"memory snapshot serializes to {declared_size} bytes but declares "
                f"max_serialized_bytes={declared.max_serialized_bytes}"
            )

        # Normalize to activation bounds, then measure the final returned value.
        if detached.bounds == configured:
            normalized = detached
        else:
            normalized = detach_memory_snapshot(replace(detached, bounds=configured))
        final_size = serialized_memory_snapshot_bytes(normalized)
        if (
            configured.max_serialized_bytes is not None
            and final_size > configured.max_serialized_bytes
        ):
            raise ValueError(
                f"normalized memory snapshot serializes to {final_size} bytes; "
                f"activation max_serialized_bytes is {configured.max_serialized_bytes}"
            )
        return normalized

    def _publish_snapshot(self, owned: MemorySnapshot) -> MemorySnapshot:
        """Store step-owned state and return a second detached caller copy."""

        self.last_snapshot = owned
        return detach_memory_snapshot(owned)

    def _bound_diagnostic(self, message: str) -> str:
        """Truncate diagnostics once for last_error, status, and fallbacks."""

        limit = self.activation.bounds.max_serialized_bytes
        # Keep status/worker-facing text modest even when snapshot ceiling is large.
        budget = DEFAULT_MAX_DIAGNOSTIC_CHARS
        if limit is not None:
            budget = max(64, min(budget, limit // 4))
        return _truncate_text(str(message), budget)

    def _error_snapshot(self, error: str) -> MemorySnapshot:
        previous = self.last_snapshot
        # Identity is framework-owned and fixed-width. Never reuse prior epoch_id /
        # memory_id values — a near-ceiling accepted snapshot can make those
        # fields too large for a failure fallback under the same byte limit.
        memory_id, epoch_id = framework_error_identity(self.failure_count)
        return self._bounded_fallback_snapshot(
            memory_id=memory_id,
            epoch_id=epoch_id,
            health="error",
            error=error,
            summary_prefix="memory_error",
            metadata_key="error",
            previous_health=previous.health if previous is not None else None,
        )

    def _bounded_fallback_snapshot(
        self,
        *,
        memory_id: str,
        epoch_id: str,
        health: str,
        error: str | None,
        summary_prefix: str,
        metadata_key: str,
        previous_health: str | None = None,
    ) -> MemorySnapshot:
        """Build a framework failure/reset-fallback snapshot under size limits.

        Identity and timestamp width match the shapes validated at activation.
        """

        configured = self.activation.bounds
        limit = configured.max_serialized_bytes
        created_at_ms = framework_fallback_timestamp_ms()
        safe_impl_id = FRAMEWORK_FALLBACK_IMPLEMENTATION_ID
        safe_previous_health = (
            previous_health
            if previous_health in {"empty", "healthy", "unavailable", "error"}
            else None
        )

        # Prefer the already-bounded diagnostic from the exception boundary.
        base_diagnostic = str(error or "unknown failure")
        diagnostic_budget = len(base_diagnostic) if base_diagnostic else 64
        if limit is not None:
            diagnostic_budget = max(16, min(diagnostic_budget, limit // 4, 4_096))

        budgets: list[int] = [diagnostic_budget]
        for candidate_budget in (256, 128, 64, 32, 16):
            if candidate_budget not in budgets and candidate_budget <= diagnostic_budget:
                budgets.append(candidate_budget)

        for budget in budgets:
            diagnostic = _truncate_text(base_diagnostic, budget)
            candidate = self._build_fallback_candidate(
                health=health,
                memory_id=memory_id,
                epoch_id=epoch_id,
                implementation_id=safe_impl_id,
                diagnostic=diagnostic,
                summary_prefix=summary_prefix,
                metadata_key=metadata_key,
                previous_health=safe_previous_health,
                include_metadata=True,
                created_at_ms=created_at_ms,
            )
            if limit is None or serialized_memory_snapshot_bytes(candidate) <= limit:
                return detach_memory_snapshot(candidate)

        # Last resort: same shared shape measured at activation time.
        candidate = build_minimal_framework_fallback(
            configured,
            health=health,
            memory_id=memory_id,
            epoch_id=epoch_id,
            created_at_ms=created_at_ms,
            summary_prefix=summary_prefix,
        )
        if limit is not None and serialized_memory_snapshot_bytes(candidate) > limit:
            # Unreachable when activation validated fallback capacity.
            raise ValueError(
                "framework could not construct a failure snapshot under "
                f"max_serialized_bytes={limit}"
            )
        return detach_memory_snapshot(candidate)

    def _build_fallback_candidate(
        self,
        *,
        health: str,
        memory_id: str,
        epoch_id: str,
        implementation_id: str,
        diagnostic: str,
        summary_prefix: str,
        metadata_key: str,
        previous_health: str | None,
        include_metadata: bool,
        created_at_ms: int,
    ) -> MemorySnapshot:
        configured = self.activation.bounds
        if health == "error":
            metadata: dict[str, Any] = {}
            if include_metadata:
                metadata = {
                    "failure_count": self.failure_count,
                    "previous_health": previous_health,
                }
            return error_memory_snapshot(
                memory_id=memory_id,
                epoch_id=epoch_id,
                bounds=configured,
                created_at_ms=created_at_ms,
                error=diagnostic,
                implementation_id=implementation_id,
                metadata=metadata,
            )
        metadata = {metadata_key: diagnostic} if include_metadata else {}
        return empty_memory_snapshot(
            memory_id=memory_id,
            epoch_id=epoch_id,
            bounds=configured,
            created_at_ms=created_at_ms,
            implementation_id=implementation_id,
            summary=(f"{summary_prefix}={diagnostic}",),
            metadata=metadata,
        )


def format_exception_safely(exc: BaseException) -> str:
    """Format an exception without letting ``__str__`` bypass isolation."""

    type_name = type(exc).__name__
    try:
        detail = str(exc)
    except Exception:  # noqa: BLE001 - secondary failure must not escape
        return f"{type_name}: <unprintable exception>"
    return f"{type_name}: {detail}"


def _truncate_text(value: str, max_chars: int) -> str:
    text = str(value)
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


def _timestamp_ms() -> int:
    return int(time.time() * 1000)
