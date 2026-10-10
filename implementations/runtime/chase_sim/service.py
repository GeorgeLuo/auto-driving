"""Host the shared runtime for one Chase car behind the shared routes.

A PiCar's Donkey process feeds camera frames to the shared frame loop and
serves ``RuntimeRoutes`` from Tornado. This host feeds simulator captures to
the same loop and serves the same routes from a stdlib server. It runs from
the vehicle's controller release; ``automa vehicles automation host``
supervises it and replaces it on a restart command.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from autonomy.runtime.assembly import staged_runtime
from autonomy.runtime.frame_loop import FrameLoop
from autonomy.runtime.routes import RuntimeRoutes
from autonomy.runtime.session import RunConfiguration
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim import (
    ChasePassiveCaptureError,
    ChaseSimCar,
    format_chase_frame_id,
    simulator_epoch_from_sensor_frame,
    simulator_frame_index_from_sensor_frame,
)
from implementations.vehicle.chase_sim.metrics_ws import compare_chase_session_fingerprints

from . import create_host

logger = logging.getLogger(__name__)

# The supervisor starts a fresh host from the current release on this code.
RESTART_EXIT_CODE = 75
HOST_RECORD_SCHEMA = "automa_runtime_host_v0"
# An observe-only run lets playback and input evolve; simulator identity and
# control authority must hold until the run stops.
PASSIVE_RUN_STABLE_FIELDS = ("game_id", "scenario_id", "simulation_epoch", "control_source")
CAPTURE_POLL_S = 0.01
RECORD_CHECK_S = 1.0


class ChaseFrameLoop(FrameLoop):
    """Capture simulator frames into the shared loop while a session runs."""

    def __init__(self, *, car: ChaseSimCar, capture_dir: Path, **options: Any) -> None:
        self.car = car
        self.capture_dir = Path(capture_dir)
        self._passive_baseline: dict[str, Any] | None = None
        self._capture_count = 0
        super().__init__(runtime="chase_sim", camera_source="chase_sim_front_camera", frame_prefix="chase", **options)

    def _session_started(self, configuration: RunConfiguration) -> None:
        self._passive_baseline = None

    def capture(self) -> None:
        """Read one simulator frame and submit it with its simulator identity."""

        sensor_frame = self.car.read_sensors(SensorReadRequest(
            output_dir=self.capture_dir,
            read_id=f"capture_{self._capture_count:06d}",
            requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
            image_extension="png",
            front_camera_endpoint="atomic-evaluation-capture",
        ))
        self._capture_count += 1
        reading = sensor_frame.readings.get(FRONT_CAMERA_SENSOR_ID)
        path = Path(reading.path) if reading is not None and isinstance(reading.path, str) else None
        try:
            if self.host.configuration.mode == "observe_only":
                self._check_passive_session()
            index = simulator_frame_index_from_sensor_frame(sensor_frame)
            if index is None:
                index = self.car.last_simulator_frame_index
            epoch = simulator_epoch_from_sensor_frame(sensor_frame)
            if index is None or epoch is None:
                raise ValueError("Chase capture is missing the simulator frameIndex or simulationEpoch")
            if path is None:
                raise ValueError("Chase capture returned no front camera image")
            with Image.open(path) as image:
                array = np.asarray(image.convert("RGB"))
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
        self.submit(
            array, frame_id=format_chase_frame_id(index), frame_index=index,
            captured_at_ms=sensor_frame.completed_at_ms,
            metadata={"simulator_frame_index": index, "simulation_epoch": epoch},
        )

    def submit(self, image: Any, **frame: Any) -> Any:
        # The simulator is captured only for a session, so a capture that lands
        # after the session ended is dropped instead of deciding past its bound.
        with self._lock:
            if self.host.run_state != "running":
                return None
            return super().submit(image, **frame)

    def _check_passive_session(self) -> None:
        passive = self.car.last_passive_capture or {}
        preservation = passive.get("session_preservation") or {}
        before, after = preservation.get("before"), preservation.get("after")
        if self._passive_baseline is None and isinstance(before, dict):
            self._passive_baseline = before
        if self._passive_baseline is None or not isinstance(after, dict):
            return
        receipt = compare_chase_session_fingerprints(
            self._passive_baseline, after, field_names=PASSIVE_RUN_STABLE_FIELDS,
        )
        if not receipt.get("preserved"):
            raise ChasePassiveCaptureError(
                code="simulator_state_changed" if receipt.get("changed_fields") else "simulator_capability_missing",
                message=(
                    "the simulator identity or control authority changed during an observe-only run "
                    f"(changed: {', '.join(receipt.get('changed_fields') or []) or 'unknown'})"
                ),
                details={"session_preservation": receipt, "mutation_attempted": False},
            )

    def serve_captures(self, stop: threading.Event) -> None:
        """Capture at the session cadence; a failed capture ends the session with its error."""

        while not stop.is_set():
            if self.host.run_state != "running" or not self.due():
                stop.wait(CAPTURE_POLL_S)
                continue
            try:
                self.capture()
            except Exception as exc:  # noqa: BLE001 - reported on the session
                logger.exception("Chase capture failed")
                self.stop(reason="error", error=f"{type(exc).__name__}: {exc}")


class _RouteHandler(BaseHTTPRequestHandler):
    server: "RuntimeHTTPServer"

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        status, headers, payload = self.server.routes.handle(
            self.command, self.path, body, headers=self.headers,
            origin=f"http://{self.headers.get('Host', '')}",
        )
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        if "Content-Length" not in headers:
            self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = do_POST = do_HEAD = _serve

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s %s", self.address_string(), format % args)


class RuntimeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], routes: RuntimeRoutes) -> None:
        self.routes = routes
        super().__init__(address, _RouteHandler)


class ChaseRuntimeHost:
    """One Chase car's frame loop and route server, until ``close``."""

    def __init__(self, *, car: Any, runtime_dir: Path, vehicle_id: str, port: int = 0,
                 on_restart: Callable[[], None] | None = None) -> None:
        host, options = staged_runtime(runtime_dir, lambda steps: create_host(car, steps=steps))
        self._capture_dir = tempfile.TemporaryDirectory(prefix="chase-capture-")
        self.loop = ChaseFrameLoop(
            host=host, car=car, capture_dir=Path(self._capture_dir.name), vehicle_id=vehicle_id, **options,
        )
        self.server = RuntimeHTTPServer(("127.0.0.1", port), RuntimeRoutes(self.loop, on_restart=on_restart))
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._stop = threading.Event()
        self._threads = (
            threading.Thread(target=self.loop.serve_captures, args=(self._stop,), name="chase-capture", daemon=True),
            threading.Thread(target=self.server.serve_forever, name="chase-routes", daemon=True),
        )

    def start(self) -> "ChaseRuntimeHost":
        for thread in self._threads:
            thread.start()
        return self

    def close(self) -> None:
        # Release control before the routes go away.
        self.loop.stop()
        self._stop.set()
        self.server.shutdown()
        self.server.server_close()
        for thread in self._threads:
            thread.join(timeout=5.0)
        self.loop.shutdown()
        self._capture_dir.cleanup()

    def __enter__(self) -> "ChaseRuntimeHost":
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.close()


def write_host_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
    Path(handle.name).replace(path)


def _record_pid(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError, AttributeError):
        return None


def serve(*, vehicle_id: str, ws_url: str | None, runtime_dir: Path, port: int,
          host_record: Path, release: dict[str, Any]) -> int:
    restart = threading.Event()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    car = ChaseSimCar(ws_url=ws_url, timeout_s=5.0, vehicle_id=vehicle_id)
    runtime = ChaseRuntimeHost(
        car=car, runtime_dir=runtime_dir, vehicle_id=vehicle_id, port=port,
        on_restart=lambda: (restart.set(), stop.set()),
    )
    with runtime:
        write_host_record(host_record, {
            "schema": HOST_RECORD_SCHEMA,
            "vehicle_id": vehicle_id,
            "pid": os.getpid(),
            "supervisor_pid": os.getppid(),
            "base_url": runtime.base_url,
            "host_run_id": runtime.loop.run_id,
            "release": release,
            "started_at_ms": int(time.time() * 1000),
        })
        logger.info("Chase runtime for %s serving %s (release %s)", vehicle_id, runtime.base_url,
                    release.get("archive_sha256"))
        try:
            while not stop.wait(RECORD_CHECK_S):
                # The CLI finds a host only by its record; one it cannot find exits.
                if _record_pid(host_record) != os.getpid():
                    logger.warning("Host record %s was removed or replaced; exiting", host_record)
                    break
        finally:
            if _record_pid(host_record) == os.getpid():
                host_record.unlink(missing_ok=True)
    return RESTART_EXIT_CODE if restart.is_set() else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vehicle-id", required=True)
    parser.add_argument("--ws-url")
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host-record", type=Path, required=True)
    parser.add_argument("--release", type=json.loads, default={})
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return serve(
        vehicle_id=args.vehicle_id, ws_url=args.ws_url, runtime_dir=args.runtime_dir,
        port=args.port, host_record=args.host_record, release=args.release,
    )


if __name__ == "__main__":
    raise SystemExit(main())
