"""Execution of one applied memory plugin.

``MemoryPluginRuntime`` loads the plugin and runs its update and reset calls
with timing and status. The plugin writes its own keys in the host map. Update
failures propagate so the cycle stops; reset failures are recorded and leave
the host to clear the map.
"""

from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.activation import instantiate_memory_implementation
from autonomy.decision_cycle.memory.plugin import plugin_status
from autonomy.decision_cycle.observation.values import Observation
from autonomy.plugins import PluginDefinition
from autonomy.shared_memory import SharedMemory

# Cap for status and worker-facing diagnostic strings.
DEFAULT_MAX_DIAGNOSTIC_CHARS = 1_024


class MemoryPluginRuntime:
    """Load, time, and report one applied memory plugin."""

    def __init__(self, definition: PluginDefinition, *, source_path: Path) -> None:
        self.definition = definition
        self.plugin_id = definition.plugin_id
        self.source_path = source_path
        self.implementation = instantiate_memory_implementation(
            definition.entrypoint, deepcopy(dict(definition.config))
        )
        self.implementation_id = self.implementation.implementation_id
        self.last_duration_ms: float | None = None
        self.last_error: str | None = None
        self.update_count = 0
        self.reset_count = 0
        self.failure_count = 0
        # The map the plugin last worked in, for status reads between cycles.
        self._shared_memory: SharedMemory | None = None

    def __call__(self, context: DecisionFrameContext, observation: Observation | None) -> None:
        self.update(context, observation)

    def update(self, context: DecisionFrameContext, observation: Observation | None) -> None:
        started = time.perf_counter()
        if context.shared_memory is not None:
            self._shared_memory = context.shared_memory
        try:
            self.implementation.update(context, observation)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - step isolation boundary
            self.failure_count += 1
            self.last_error = _truncate_text(format_exception_safely(exc), DEFAULT_MAX_DIAGNOSTIC_CHARS)
            raise
        finally:
            self.last_duration_ms = (time.perf_counter() - started) * 1000.0
            self.update_count += 1

    def reset(self, shared_memory: SharedMemory | None = None) -> None:
        started = time.perf_counter()
        if shared_memory is not None:
            self._shared_memory = shared_memory
        try:
            if self._shared_memory is None:
                raise ValueError("memory reset requires a shared-memory map")
            self.implementation.reset(self._shared_memory)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - step isolation boundary
            self.failure_count += 1
            self.last_error = _truncate_text(format_exception_safely(exc), DEFAULT_MAX_DIAGNOSTIC_CHARS)
        self.last_duration_ms = (time.perf_counter() - started) * 1000.0
        self.reset_count += 1

    def plugin_status(self) -> dict[str, Any] | None:
        """The plugin's own summary of its state, or None when it offers none."""

        try:
            return plugin_status(self.implementation, self._shared_memory)
        except Exception as exc:  # noqa: BLE001 - status must not fail the caller
            return {"status_error": _truncate_text(format_exception_safely(exc), DEFAULT_MAX_DIAGNOSTIC_CHARS)}

    def status(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "implementation_id": self.implementation_id,
            "implementation_spec": self.definition.entrypoint,
            "activation": str(self.source_path),
            "update_count": self.update_count,
            "reset_count": self.reset_count,
            "failure_count": self.failure_count,
            "last_duration_ms": self.last_duration_ms,
            "last_error": self.last_error,
            "state": self.plugin_status(),
        }


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
