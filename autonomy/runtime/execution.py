"""Vehicle-independent authority and delivery of decision output.

A target only acquires its transport, writes normalized control, and releases
it. All mode, freshness, invalidation, and stop policy lives here. A successful
write acknowledges delivery to the target's command boundary, not measured
motion. The receipt identifies that boundary on both physical and simulated
vehicles.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from autonomy.runtime.control import AutonomyControl

CONTROL_MAX_AGE_S = 2.0
MODES = frozenset({"manual", "observe_only", "autonomy"})


def require_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown execution mode {mode!r}; expected {sorted(MODES)}")
    return mode


class ControlTarget(Protocol):
    """Transport operations; implementations must not add decision policy."""

    def acquire(self) -> None: ...
    def write(self, control: AutonomyControl) -> dict[str, Any]: ...
    def release(self) -> None: ...


@dataclass(frozen=True)
class ExecutionTicket:
    generation: int
    mode: str


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
        self._closed = False
        self._wake = threading.Event()
        self._watchdog: threading.Thread | None = None
        self.last_application = ControlApplication(
            None, "manual", False, "not-started", AutonomyControl(reason="not-started")
        )

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def ticket(self) -> ExecutionTicket:
        with self._lock:
            return ExecutionTicket(self._generation, self._mode)

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
            if self._owned:
                try:
                    self._write_idle("mode-changed")
                    self.target.release()
                    self._owned = False
                except Exception as exc:
                    self._fail(exc)
                    raise
            if mode == "autonomy":
                # Mark ownership before acquisition: partial acquisition must
                # still attempt a stop when a transport raises.
                self._owned = True
                try:
                    self.target.acquire()
                    self._write_idle("awaiting-fresh-decision")
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
            self.last_application = ControlApplication(
                None, mode, False, "awaiting-fresh-decision" if mode == "autonomy" else mode,
                AutonomyControl(reason=mode),
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
                return application
            control = result.control
            if result.action is None or result.action.status != "ok":
                control = AutonomyControl(reason="decision-unavailable")
            try:
                receipt = self.target.write(control)
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
                int(time.time() * 1000), receipt,
            )
            return self.last_application

    def cycle_failed(self, ticket: ExecutionTicket) -> None:
        with self._lock:
            if ticket.generation == self._generation and self._owned:
                self._write_idle("cycle-error")

    def _write_idle(self, reason: str) -> None:
        self._deadline = None
        control = AutonomyControl(reason=reason)
        self._output = control
        receipt = self.target.write(control)
        self.last_application = ControlApplication(
            None, self._mode, True, reason, control, int(time.time() * 1000), receipt
        )

    def _fail(self, exc: Exception, *, frame_id: str | None = None) -> None:
        self._generation += 1
        self._mode = "manual"
        self._deadline = None
        self._output = AutonomyControl(reason="delivery-error")
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
        drivetrain loop) read the same delivered command here. Manual input
        belongs to the operator while automation has no authority.
        """
        with self._lock:
            if self._mode == "autonomy" and not self._closed:
                return self._output
            return manual or AutonomyControl(reason=self._mode)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self.set_mode("manual")
            finally:
                self._closed = True
                self._generation += 1
                self._wake.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self._mode,
                "generation": self._generation,
                "closed": self._closed,
                "max_control_age_s": CONTROL_MAX_AGE_S,
                "application": self.last_application.to_dict(),
            }
