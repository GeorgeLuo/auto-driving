"""Find, start, and replace the runtime host behind each vehicle.

Every vehicle host serves the routes in ``autonomy.runtime.routes``, so the
CLI addresses a runtime only by ``base_url``. A PiCar's host is its Donkey
service at the staged connection. A Chase car's host is a local process
(``implementations.runtime.chase_sim.service``) that runs from the vehicle's
controller release, as the Pi runs the release ``update autonomy`` installed.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

from autonomy.runtime.client import RuntimeClient
from implementations.runtime.chase_sim.service import HOST_RECORD_SCHEMA, RESTART_EXIT_CODE

from .bundles import controller_bundle_paths
from .paths import ROOT, display_path, safe_path_part
from .step_activations import _offline_staged_vehicle, staging_vehicle
from .vehicles import is_chase_vehicle_id

RUNTIME_ROOT = Path(os.environ.get("AUTOMA_RUNTIME_ROOT", ROOT / "runtime" / "vehicles"))
AUTOMA_EXECUTABLE = ROOT / "cli" / "automa"
HOST_RECORD = "host.json"
HOST_LOG = "host.log"
HOST_START_TIMEOUT_S = 20.0
LOG_TAIL_LINES = 20
# The host imports only the release: ``-I`` drops the working tree and user
# site from ``sys.path``; the release directory is put first.
BOOT = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from implementations.runtime.chase_sim.service import main; "
    "raise SystemExit(main(sys.argv[1:]))"
)


class RuntimeHostError(RuntimeError):
    """A vehicle runtime is unreachable; the message names what to run next."""


def bundle_paths(vehicle_id: str) -> dict[str, str]:
    return controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))


def bundle_root(vehicle_id: str) -> Path:
    return Path(bundle_paths(vehicle_id)["root_dir"])


def automation_dir(vehicle_id: str) -> Path:
    """The vehicle's local runtime directory: run state, records, host, and view."""

    return bundle_root(vehicle_id) / "runtime" / "automation"


def staged_vehicle(vehicle_id: str) -> dict[str, Any]:
    vehicle, error = staging_vehicle(vehicle_id, runtime_root=RUNTIME_ROOT, timeout_s=1.0)
    if vehicle is None:
        raise RuntimeHostError(error or f"Vehicle {vehicle_id!r} is not staged.")
    return vehicle


# Controller releases -------------------------------------------------------

def update_command(vehicle_id: str) -> str:
    return f"./cli/automa vehicles update autonomy --id {vehicle_id}"


def release_manifest(vehicle_id: str, archive_sha256: str | None = None) -> dict[str, Any]:
    """The latest release, or the one whose archive has ``archive_sha256``."""

    releases = bundle_root(vehicle_id) / "releases"
    if archive_sha256 is None:
        candidates = [releases / "latest-controller-bundle.json"]
    else:
        candidates = sorted(releases.glob("*.manifest.json"))
    for path in candidates:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sha = (manifest.get("archive") or {}).get("sha256")
        if archive_sha256 is None or sha == archive_sha256:
            return manifest
    named = "No controller release" if archive_sha256 is None else f"Controller release {archive_sha256[:12]} is not"
    raise RuntimeHostError(
        f"{named} in {display_path(releases)}.\nRun: {update_command(vehicle_id)}"
    )


def release_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive_sha256": manifest["archive"]["sha256"],
        "archive": Path(manifest["archive"]["path"]).name,
        "tree_sha256": manifest.get("tree_sha256"),
        "created_at_ms": manifest.get("created_at_ms"),
    }


def release_code_dir(vehicle_id: str, archive_sha256: str | None = None) -> tuple[Path, dict[str, Any]]:
    """The release's code, extracted once into ``releases/<archive sha256>``."""

    manifest = release_manifest(vehicle_id, archive_sha256)
    summary = release_summary(manifest)
    sha = summary["archive_sha256"]
    releases = bundle_root(vehicle_id) / "releases"
    target = releases / sha
    if target.is_dir():
        return target, summary
    archive = releases / summary["archive"]
    try:
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeHostError(
            f"Controller release archive {display_path(archive)} is unreadable: {exc}\n"
            f"Run: {update_command(vehicle_id)}"
        ) from exc
    if digest != sha:
        raise RuntimeHostError(
            f"Controller release archive {display_path(archive)} has sha256 {digest[:12]}, "
            f"not {sha[:12]}.\nRun: {update_command(vehicle_id)}"
        )
    pending = releases / f".{sha}.{os.getpid()}.pending"
    shutil.rmtree(pending, ignore_errors=True)
    pending.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(pending, filter="data")
    try:
        pending.rename(target)
    except OSError:
        # Another command extracted the same release first.
        shutil.rmtree(pending, ignore_errors=True)
    return target, summary


# The Chase host ------------------------------------------------------------

def _pid_alive(pid: Any) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def host_record(vehicle_id: str) -> dict[str, Any] | None:
    """The running Chase host's record; ``None`` once its process is gone."""

    try:
        record = json.loads((automation_dir(vehicle_id) / HOST_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("schema") != HOST_RECORD_SCHEMA:
        return None
    return record if _pid_alive(record.get("pid")) else None


def host_log_tail(vehicle_id: str) -> str:
    path = automation_dir(vehicle_id) / HOST_LOG
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"(no host log at {display_path(path)})"
    return "\n".join([f"Host log {display_path(path)}:", *lines[-LOG_TAIL_LINES:]])


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def serve_chase_host(vehicle_id: str) -> int:
    """Supervise the Chase host: start it from the latest release, again on restart.

    Runs as the hidden ``vehicles automation host`` command. The port stays
    the same across restarts so a client's ``base_url`` survives them.
    """

    vehicle = staged_vehicle(vehicle_id)
    ws_url = (vehicle.get("connection") or {}).get("ws_url")
    runtime_dir = bundle_root(vehicle_id) / "runtime"
    record_path = automation_dir(vehicle_id) / HOST_RECORD
    port = _free_port()
    child: subprocess.Popen[bytes] | None = None

    def forward(signum: int, _frame: Any) -> None:
        if child is not None and child.poll() is None:
            child.send_signal(signum)
        else:
            raise SystemExit(0)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    while True:
        try:
            release_dir, release = release_code_dir(vehicle_id)
        except RuntimeHostError as exc:
            print(f"Chase host cannot start: {exc}", file=sys.stderr, flush=True)
            return 2
        print(f"Starting the Chase host for {vehicle_id} from release {release['archive_sha256'][:12]}",
              file=sys.stderr, flush=True)
        command = [
            sys.executable, "-I", "-c", BOOT, str(release_dir),
            "--vehicle-id", vehicle_id, "--runtime-dir", str(runtime_dir), "--port", str(port),
            "--host-record", str(record_path), "--release", json.dumps(release),
            *(["--ws-url", ws_url] if isinstance(ws_url, str) and ws_url else []),
        ]
        child = subprocess.Popen(command, cwd=record_path.parent)
        code = child.wait()
        if code != RESTART_EXIT_CODE:
            return code


def _wait_for_host(vehicle_id: str, process: subprocess.Popen[Any] | None, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        record = host_record(vehicle_id)
        if record is not None:
            try:
                RuntimeClient(record["base_url"], timeout_s=1.0).status()
                return record
            except RuntimeError:
                pass
        if process is not None and process.poll() is not None:
            raise RuntimeHostError(
                f"The Chase host for {vehicle_id} exited with code {process.returncode} before serving.\n"
                + host_log_tail(vehicle_id)
            )
        time.sleep(0.1)
    raise RuntimeHostError(
        f"The Chase host for {vehicle_id} did not serve within {timeout_s:g} s.\n" + host_log_tail(vehicle_id)
    )


def ensure_chase_host(vehicle_id: str, *, timeout_s: float = HOST_START_TIMEOUT_S) -> str:
    """The base URL of a Chase host running the latest release, started if needed.

    An idle host on an older release is restarted onto the latest one; a host
    with a running session is never replaced.
    """

    latest = release_summary(release_manifest(vehicle_id))
    record = host_record(vehicle_id)
    if record is not None:
        client = RuntimeClient(record["base_url"], timeout_s=2.0)
        try:
            session = client.status()["session"]
        except RuntimeError as exc:
            raise RuntimeHostError(
                f"The Chase host for {vehicle_id} (pid {record['pid']}) is not answering: {exc}\n"
                f"Run: ./cli/automa vehicles automation stop --id {vehicle_id}\n" + host_log_tail(vehicle_id)
            ) from exc
        running = (record.get("release") or {}).get("archive_sha256")
        if running == latest["archive_sha256"]:
            return record["base_url"]
        if session.get("status") == "running":
            raise RuntimeHostError(
                f"{vehicle_id} is running release {str(running)[:12]}; the latest is "
                f"{latest['archive_sha256'][:12]}.\n"
                f"Stop the run first: ./cli/automa vehicles automation stop --id {vehicle_id}"
            )
        try:
            client.restart(timeout_s=timeout_s)
        except (RuntimeError, TimeoutError) as exc:
            raise RuntimeHostError(f"Could not restart the Chase host onto the latest release: {exc}") from exc
        return _wait_for_host(vehicle_id, None, timeout_s)["base_url"]

    release_code_dir(vehicle_id)
    directory = automation_dir(vehicle_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / HOST_RECORD).unlink(missing_ok=True)
    with (directory / HOST_LOG).open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(AUTOMA_EXECUTABLE), "vehicles", "automation", "host", "--id", vehicle_id],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    return _wait_for_host(vehicle_id, process, timeout_s)["base_url"]


def stop_chase_host(vehicle_id: str, *, wait_s: float = 3.0) -> bool:
    """End the Chase host process; ``True`` when it had to be killed.

    A terminated host releases control on its way out; a killed one cannot.
    """

    record = host_record(vehicle_id)
    if record is None:
        return False
    pids = [pid for pid in (record.get("supervisor_pid"), record["pid"]) if _pid_alive(pid)]
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline and any(_pid_alive(pid) for pid in pids):
        time.sleep(0.05)
    killed = False
    for pid in pids:
        if _pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
            killed = True
    (automation_dir(vehicle_id) / HOST_RECORD).unlink(missing_ok=True)
    return killed


# Any vehicle ----------------------------------------------------------------

def running_base_url(vehicle_id: str) -> str | None:
    """The runtime host to address now, without discovery or starting one.

    A PiCar's is its staged connection; a Chase car's is its running host.
    """

    if is_chase_vehicle_id(vehicle_id):
        record = host_record(vehicle_id)
        return None if record is None else record["base_url"]
    vehicle = _offline_staged_vehicle(bundle_paths(vehicle_id), vehicle_id) or {}
    base_url = (vehicle.get("connection") or {}).get("base_url")
    return base_url.rstrip("/") if isinstance(base_url, str) and base_url.strip() else None


def runtime_base_url(vehicle_id: str, *, start: bool = False, timeout_s: float = HOST_START_TIMEOUT_S) -> str:
    """Where the vehicle's runtime serves the shared routes, or why it cannot be reached.

    ``start`` starts (or moves onto the latest release) a local Chase host.
    """

    if start and is_chase_vehicle_id(vehicle_id):
        return ensure_chase_host(vehicle_id, timeout_s=timeout_s)
    base_url = running_base_url(vehicle_id)
    if base_url is not None:
        return base_url
    if is_chase_vehicle_id(vehicle_id):
        raise RuntimeHostError(
            f"No Chase host is running for {vehicle_id}.\n"
            f"Run: ./cli/automa vehicles automation run --id {vehicle_id} --observe-only"
        )
    raise RuntimeHostError(
        f"Vehicle {vehicle_id!r} has no staged runtime base URL.\n"
        f"Run: ./cli/automa vehicles update autonomy --id {vehicle_id}"
    )
