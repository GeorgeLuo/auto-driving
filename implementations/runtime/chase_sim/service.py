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

from autonomy.runtime.assembly import staged_runtime
from autonomy.runtime.frame_loop import FrameLoop
from autonomy.runtime.routes import RuntimeRoutes
from autonomy.runtime.session import RunConfiguration
from implementations.vehicle.chase_sim import (
    ChasePassiveCaptureError,
    ChaseSimCar,
)
from implementations.vehicle.chase_sim.session import RUN_STABLE_FIELDS, compare_chase_session_fingerprints
from implementations.vehicle.chase_sim.capture import ChaseCameraCapture

from . import create_host

logger = logging.getLogger(__name__)

# The supervisor starts a fresh host from the current release on this code.
RESTART_EXIT_CODE = 75
HOST_RECORD_SCHEMA = "automa_runtime_host_v0"
CAPTURE_POLL_S = 0.01
RECORD_CHECK_S = 1.0


class ChaseFrameLoop(FrameLoop):
    """Receive encoded captures into the shared latest-only decision slot."""

    def __init__(self, *, car: ChaseSimCar, **options: Any) -> None:
        self.car = car
        self._passive_baseline: dict[str, Any] | None = None
        self._capture_generation = 0
        self._capture_enabled = False
        self._active_stream: Any = None
        super().__init__(runtime="chase_sim", camera_source="chase_sim_front_camera", frame_prefix="chase", **options)

    def _retire_capture(self) -> Any:
        with self._lock:
            self._capture_enabled = False
            self._capture_generation += 1
            self._discard_pending_locked()
            stream, self._active_stream = self._active_stream, None
            return stream

    def start(self, configuration: RunConfiguration, *, record: bool = False) -> dict[str, Any]:
        with self._lifecycle_lock:
            stream = self._retire_capture()
            if stream is not None:
                stream.close()
            self.car.begin_capture_session()
            with self._lock:
                self._passive_baseline = None
            status = super().start(configuration, record=record)
            with self._lock:
                self._capture_enabled = True
            return status

    def stop(self, *, reason: str = "stopped", error: str | None = None) -> dict[str, Any]:
        with self._lifecycle_lock:
            stream = self._retire_capture()
            if stream is not None:
                stream.close()
            return super().stop(reason=reason, error=error)

    def shutdown(self) -> None:
        stream = self._retire_capture()
        if stream is not None:
            stream.close()
        super().shutdown()

    def observation_status(self) -> dict[str, Any]:
        return {**super().observation_status(),
                "camera_transport": getattr(self.car, "camera_transport", None)}

    def capture(self) -> None:
        generation = self._capture_generation
        self._submit_capture(self.car.read_camera_capture(), generation=generation, check_session=True)

    def _submit_capture(self, capture: ChaseCameraCapture, *, generation: int,
                        check_session: bool = False) -> None:
        if check_session:
            self._check_session(generation)
        with self._lock:
            if (generation != self._capture_generation or not self._capture_enabled
                    or self.host.run_state != "running"):
                return
            self.submit(
                None, frame_id=capture.frame_id, frame_index=capture.frame_index,
                captured_at_ms=capture.captured_at_ms, jpeg_bytes=capture.jpeg_bytes,
                image_loader=capture.load_image,
                metadata={"simulator_frame_index": capture.frame_index,
                          "simulation_epoch": capture.simulation_epoch,
                          "camera_transport": capture.transport},
            )

    def _check_session(self, generation: int) -> None:
        passive = self.car.last_passive_capture or {}
        preservation = passive.get("session_preservation") or {}
        before, after = preservation.get("before"), preservation.get("after")
        with self._lock:
            if generation != self._capture_generation:
                return
            if self._passive_baseline is None and isinstance(before, dict):
                self._passive_baseline = before
            baseline = self._passive_baseline
        if baseline is None or not isinstance(after, dict):
            return
        receipt = compare_chase_session_fingerprints(baseline, after, field_names=RUN_STABLE_FIELDS)
        if not receipt["preserved"]:
            raise ChasePassiveCaptureError(
                code="simulator_state_changed" if receipt["changed_fields"] else "simulator_capability_missing",
                message="Simulator identity or control authority changed during this run.",
                details={"session_preservation": receipt, "mutation_attempted": False},
            )

    def serve_captures(self, stop: threading.Event) -> None:
        while not stop.is_set():
            if not self._capture_enabled or self.host.run_state != "running":
                stop.wait(CAPTURE_POLL_S)
                continue
            generation = self._capture_generation
            stream = None
            try:
                open_stream = getattr(self.car, "camera_stream", None)
                stream = open_stream() if callable(open_stream) else None
                if stream is None:
                    while (not stop.is_set() and self._capture_enabled
                           and self.host.run_state == "running" and generation == self._capture_generation):
                        if self.wait_until_ready(CAPTURE_POLL_S):
                            self.capture()
                        else:
                            stop.wait(CAPTURE_POLL_S)
                else:
                    with self._lock:
                        if generation != self._capture_generation or not self._capture_enabled:
                            continue
                        self._active_stream = stream
                    with stream:
                        stream.set_consumer(lambda capture, generation=generation: self._submit_capture(capture, generation=generation))
                        while (not stop.is_set() and self._capture_enabled
                               and self.host.run_state == "running" and generation == self._capture_generation):
                            stream.check_progress()
                            stop.wait(CAPTURE_POLL_S)
            except Exception as exc:
                if generation == self._capture_generation and self._capture_enabled:
                    logger.exception("Chase capture failed")
                    self.stop(reason="error", error=f"{type(exc).__name__}: {exc}")
                else:
                    logger.info("Capture ended after the run changed: %s: %s", type(exc).__name__, exc)
            finally:
                if stream is not None:
                    stream.close()
                with self._lock:
                    if self._active_stream is stream:
                        self._active_stream = None


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
        self.loop = ChaseFrameLoop(
            host=host, car=car, vehicle_id=vehicle_id, **options,
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
