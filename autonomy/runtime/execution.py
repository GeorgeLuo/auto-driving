"""Vehicle-independent authority and delivery of decision output.

A target acquires its transport, accepts normalized control, and releases it.
All mode, freshness, invalidation, and stop policy lives here. Applications
record successful target writes, with their execution mode, on the runtime
wall clock. Push-target receipts identify their transport boundary. On Donkey,
``delivered_at_ms`` marks the in-process output mailbox update, not the next
20 Hz DriveMode tick. ``HostTelemetryStore`` logs what reached the drivetrain,
including manual driving. Application times can step backwards on clock sync;
consumers must treat non-autonomous spans and clock discontinuities as unknown.
"""
from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from autonomy.runtime.control import AutonomyControl

CONTROL_MAX_AGE_S = 2.0
CONTROL_APPLICATION_LIMIT = 32
MODES = frozenset({"manual", "observe_only", "autonomy"})


def require_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown execution mode {mode!r}; expected {sorted(MODES)}")
    return mode


class ControlTarget(Protocol):
    """Transport operations; implementations must not add decision policy.

    ``acquire`` returns the vehicle's preparation receipt. ``None`` means
    acquisition changed nothing. Callers that do not need the receipt ignore it.
    """

    def acquire(self) -> dict[str, Any] | None: ...
    def write(self, control: AutonomyControl) -> dict[str, Any]: ...
    def release(self) -> None: ...


@dataclass(frozen=True)
class ExecutionTicket:
    generation: int
    mode: str
    control_applications: tuple[ControlApplication, ...] = field(default=(), repr=False, compare=False)


@dataclass(frozen=True)
class ControlApplication:
    frame_id: str | None
    mode: str
    applied: bool
    reason: str
    control: AutonomyControl
    delivered_at_ms: int | None = None
    receipt: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ControlApplication:
        """Decode a recorded application without silently coercing bad input."""
        if not isinstance(data, dict) or not isinstance(data.get("control"), dict):
            raise ValueError("control application must contain a control object")
        require_mode(data.get("mode"))
        if type(data.get("applied")) is not bool:
            raise ValueError("control application applied must be a boolean")
        if not isinstance(data.get("reason"), str) or not data["reason"]:
            raise ValueError("control application reason must be a non-empty string")
        frame_id = data.get("frame_id")
        if frame_id is not None and (not isinstance(frame_id, str) or not frame_id):
            raise ValueError("control application frame_id must be a string or null")
        at_ms = data.get("delivered_at_ms")
        if at_ms is not None and (type(at_ms) is not int or at_ms < 0):
            raise ValueError("delivered_at_ms must be a nonnegative integer or null")
        if data["applied"] and at_ms is None:
            raise ValueError("an applied control requires delivered_at_ms")
        control = data["control"]
        for key in ("steering", "throttle"):
            value = control.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or not -1 <= value <= 1:
                raise ValueError(f"control {key} must be a finite number between -1 and 1")
        confidence = control.get("confidence")
        if confidence is not None and (
            type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1
        ):
            raise ValueError("control confidence must be between 0 and 1 or null")
        if not isinstance(control.get("reason", "idle"), str) or not isinstance(control.get("metadata", {}), dict):
            raise ValueError("control reason and metadata must be a string and object")
        if data.get("receipt") is not None and not isinstance(data["receipt"], dict):
            raise ValueError("control application receipt must be an object or null")
        if data.get("error") is not None and not isinstance(data["error"], str):
            raise ValueError("control application error must be a string or null")
        json.dumps(data, allow_nan=False)
        return cls(
            frame_id=frame_id, mode=data["mode"], applied=data["applied"], reason=data["reason"],
            control=AutonomyControl(**deepcopy(control)), delivered_at_ms=at_ms,
            receipt=deepcopy(data.get("receipt")), error=data.get("error"),
        )


class ControlExecution:
    """One authority owner, shared by every host of the decision cycle.

    The execution lock is independent of the plugin/cycle lock. Stopping or
    changing mode invalidates in-flight work without waiting for plugins.
    The watchdog also stops a held command if capture or a plugin stalls.
    """

    def __init__(self, target: ControlTarget) -> None:
        self.target = target
        self._lock = threading.RLock()
        self._mode = "manual"
        self._generation = 0
        self._entered_at_ms = 0
        self._deadline: float | None = None
        self._owned = False
        self._output = AutonomyControl(reason="not-started")
        self._applications: deque[ControlApplication] = deque(maxlen=CONTROL_APPLICATION_LIMIT)
        self._closed = False
        self._wake = threading.Event()
        self._watchdog: threading.Thread | None = None
        self.last_application = ControlApplication(
            None, "manual", False, "not-started", AutonomyControl(reason="not-started")
        )
        self._status: dict[str, Any] = {}
        self._publish_status()

    @property
    def mode(self) -> str:
        return self.status()["mode"]

    def ticket(self) -> ExecutionTicket:
        with self._lock:
            return ExecutionTicket(self._generation, self._mode, tuple(self._applications))

    def set_mode(self, mode: str) -> dict[str, Any]:
        require_mode(mode)
        with self._lock:
            if self._closed:
                raise RuntimeError("execution is closed")
            if mode == self._mode and not (mode == "manual" and self._owned):
                return self.status()
            self._generation += 1
            self._deadline = None
            self._mode = "manual"
            self._publish_status()
            delivered = self._owned
            if self._owned:
                try:
                    self._write_idle("mode-changed", mode=mode)
                    self.target.release()
                    self._owned = False
                except Exception as exc:
                    self._fail(exc)
                    raise
            if mode == "autonomy":
                # Mark ownership before acquisition: partial acquisition must
                # still attempt a stop when a transport raises.
                self._owned = True
                delivered = True
                try:
                    self.target.acquire()
                    self._write_idle("awaiting-fresh-decision", mode=mode)
                except Exception as exc:
                    self._fail(exc)
                    raise
                if self._watchdog is None:
                    self._watchdog = threading.Thread(
                        target=self._watch, name="autonomy-control-watchdog", daemon=True
                    )
                    self._watchdog.start()
            self._mode = mode
            self._entered_at_ms = int(time.time() * 1000)
            if not delivered:
                self.last_application = ControlApplication(
                    None, mode, False, mode, AutonomyControl(reason=mode)
                )
            return self.status()

    def apply(self, result: Any, ticket: ExecutionTicket) -> ControlApplication:
        with self._lock:
            reason = None
            if self._closed or ticket.generation != self._generation:
                reason = "execution-changed"
            elif self._mode != "autonomy":
                reason = self._mode
            elif result.context.timestamp_ms < self._entered_at_ms:
                reason = "frame-precedes-authority"
            elif int(time.time() * 1000) - result.context.timestamp_ms > CONTROL_MAX_AGE_S * 1000:
                reason = "stale-decision"
            if reason is not None:
                application = ControlApplication(
                    result.context.frame_id, self._mode, False, reason, result.control
                )
                if reason in {"stale-decision", "frame-precedes-authority"}:
                    self._write_idle(reason)
                if ticket.generation == self._generation:
                    self.last_application = application
                self._publish_status()
                return application
            control = result.control
            if result.action is None or result.action.status != "ok":
                control = AutonomyControl(reason="decision-unavailable")
            try:
                receipt = self.target.write(control)
                delivered_at_ms = int(time.time() * 1000)
                self._output = control
            except Exception as exc:
                self._fail(exc, frame_id=result.context.frame_id)
                raise
            remaining_s = CONTROL_MAX_AGE_S - max(
                0.0, (time.time() * 1000 - result.context.timestamp_ms) / 1000
            )
            self._deadline = time.monotonic() + max(0.0, remaining_s)
            self.last_application = ControlApplication(
                result.context.frame_id, self._mode, True, control.reason, control,
                delivered_at_ms, receipt,
            )
            self._applications.append(self.last_application)
            self._publish_status()
            return self.last_application

    def cycle_failed(self, ticket: ExecutionTicket) -> None:
        with self._lock:
            if ticket.generation == self._generation and self._owned:
                self._write_idle("cycle-error")

    def _write_idle(self, reason: str, *, mode: str | None = None) -> None:
        self._deadline = None
        control = AutonomyControl(reason=reason)
        self._output = control
        self._publish_status()
        receipt = self.target.write(control)
        self.last_application = ControlApplication(
            None, mode or self._mode, True, reason, control, int(time.time() * 1000), receipt
        )
        self._applications.append(self.last_application)
        self._publish_status()

    def _fail(self, exc: Exception, *, frame_id: str | None = None) -> None:
        self._generation += 1
        self._mode = "manual"
        self._deadline = None
        self._output = AutonomyControl(reason="delivery-error")
        self._publish_status()
        error = f"{type(exc).__name__}: {exc}"
        try:
            if self._owned:
                self._write_idle("delivery-error")
                self.target.release()
                self._owned = False
        except Exception as stop_error:
            error += f"; stop failed: {type(stop_error).__name__}: {stop_error}"
        self.last_application = ControlApplication(
            frame_id, self._mode, False, "delivery-error",
            AutonomyControl(reason="delivery-error"), error=error,
        )
        self._publish_status()

    def _watch(self) -> None:
        while not self._wake.wait(0.05):
            with self._lock:
                if self._deadline is not None and time.monotonic() >= self._deadline:
                    try:
                        self._write_idle("decision-expired")
                    except Exception as exc:
                        self._fail(exc)

    def output(self, manual: AutonomyControl | None = None) -> AutonomyControl:
        """Select the host's final output without provider-specific mode policy.

        Push transports receive writes immediately. Pull transports (Donkey's
        drivetrain loop) read the output mailbox here on their next tick. Manual input
        belongs to the operator while automation has no authority.
        """
        with self._lock:
            if self._mode == "autonomy" and not self._closed:
                return self._output
            return manual or AutonomyControl(reason=self._mode)

    def close(self) -> None:
        """Stop and release the target; a failed stop is recorded, not raised."""
        with self._lock:
            if self._closed:
                return
            try:
                self.set_mode("manual")
            except Exception:
                pass  # _fail recorded the error in last_application
            finally:
                self._closed = True
                self._generation += 1
                self._wake.set()
                self._publish_status()

    def _publish_status(self) -> None:
        self._status = {
            "mode": self._mode, "generation": self._generation,
            "closed": self._closed, "max_control_age_s": CONTROL_MAX_AGE_S,
            "application": self.last_application.to_dict(),
        }

    def status(self) -> dict[str, Any]:
        # Transport writes/acquisition can hold the authority lock. Inspection
        # uses the last complete status and never waits on those round trips.
        if self._lock.acquire(blocking=False):
            try:
                self._publish_status()
            finally:
                self._lock.release()
        return deepcopy(self._status)
