"""Donkey part that feeds camera memory to the shared frame loop.

The loop (``autonomy.runtime.frame_loop``) owns capture cadence, the decision
worker, publications, and the applied identity. This part translates Donkey's
drive-mode vocabulary, hands the car back to the operator when a run ends,
and passes completed source frames to the DriveMode telemetry observer.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Callable

from autonomy.runtime.cycle_host import AutonomyCycleHost
from autonomy.runtime.frame_loop import FrameLoop, detach_image
from autonomy.runtime.session import DEFAULT_INTERVAL_S, RunConfiguration
from .control import drive_mode, execution_mode


class AutonomyPilotPart(FrameLoop):
    """Run the shared frame loop from Donkey's drive loop.

    Runs this part starts or ends set the web controller's drive mode;
    drive-mode changes the part did not make are the operator's.
    """

    def __init__(
        self,
        *,
        host: AutonomyCycleHost,
        interval_s: float = DEFAULT_INTERVAL_S,
        monotonic: Callable[[], float] | None = None,
        preset: str | None = None,
        vehicle_id: str | None = None,
        source_id: str | None = None,
        decision_activations: dict[str, dict[str, Any] | None] | None = None,
        generation_id: str | None = None,
        run_id: str | None = None,
        host_telemetry: Any | None = None,
        controller: Any | None = None,
        recording_root: Path | None = None,
    ) -> None:
        self.controller = controller
        self._drive_mode = drive_mode("manual")
        self._observed_drive_mode: str | None = None
        # Optional final-boundary observer. It receives only detached source
        # identity from this runtime owner; DriveMode remains responsible for
        # observing the actual user/pilot/selected values.
        self.host_telemetry = host_telemetry
        super().__init__(
            host=host, interval_s=interval_s, monotonic=monotonic, preset=preset,
            vehicle_id=vehicle_id, source_id=source_id,
            decision_activations=decision_activations, generation_id=generation_id,
            run_id=run_id or f"donkey-run-{secrets.token_hex(12)}",
            recording_root=recording_root,
            runtime="donkeycar", camera_source="donkeycar_vehicle_memory", frame_prefix="donkey",
        )

    def run(
        self,
        image_array=None,
        mode: str = "user",
        user_steering: float = 0.0,
        user_throttle: float = 0.0,
    ):
        mode = mode or drive_mode("manual")
        mode_name = execution_mode(mode)
        operator_changed = mode != self._observed_drive_mode and mode != self._drive_mode
        self._observed_drive_mode = mode
        if operator_changed:
            self._drive_mode = mode
            with self._lock:
                self._discard_pending_locked()
            if mode_name == "autonomy":
                self.host.start(RunConfiguration(interval_s=self.interval_s))
            else:
                self.host.stop()
        elif execution_mode(self._drive_mode) == "autonomy" and self.host.run_state != "running":
            # The host ended the run: bounded decisions completed or a cycle failed.
            self._hand_back()
        if self.due():
            self.submit(
                detach_image(image_array), mode=mode_name,
                user_steering=user_steering, user_throttle=user_throttle,
            )
        self._publish_host_source_frame()
        self.last_status = self.status()
        return self._held_outputs(mode_name)

    def _session_started(self, configuration: RunConfiguration) -> None:
        self._show_drive_mode(drive_mode(configuration.mode))

    def _session_stopped(self) -> None:
        self._hand_back()

    def _frame_completed(self) -> None:
        self._publish_host_source_frame()

    def _identity_applied(self, generation_id: str) -> None:
        store = getattr(self.host_telemetry, "store", None)
        if store is not None:
            store.generation_id = generation_id

    def _hand_back(self) -> None:
        """Return Donkey to the operator from rest."""
        if self.controller is not None:
            self.controller.angle = 0.0
            self.controller.throttle = 0.0
        self._show_drive_mode(drive_mode("manual"))

    def _show_drive_mode(self, mode: str) -> None:
        self._drive_mode = mode
        if self.controller is not None:
            # The latch outlasts the drive loop feeding user/mode back in.
            self.controller.mode = mode
            self.controller.mode_latch = mode

    def _publish_host_source_frame(self) -> None:
        """Hand off the latest completed source frame to the final observer.

        This is a bounded in-process identity handoff. The source frame is
        copied from the runtime-owned latest observation state and is never
        created by the DriveMode observer. A held cadence tick therefore
        repeats the same source identity instead of becoming a new frame.
        """

        setter = getattr(self.host_telemetry, "set_source_frame", None)
        if not callable(setter):
            return
        with self._lock:
            latest = self.latest_state
            source_frame = (
                None
                if latest is None
                else {
                    "frame_id": latest.frame_id,
                    "frame_index": latest.frame_index,
                    "captured_at_ms": latest.captured_at_ms,
                    "completed_at_ms": latest.completed_at_ms,
                }
            )
        try:
            setter(source_frame)
        except Exception:
            # Telemetry is diagnostic only and must never change host outputs.
            return
