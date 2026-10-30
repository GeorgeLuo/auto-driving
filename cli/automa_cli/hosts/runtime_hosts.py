"""Find, start, and replace the runtime host behind each vehicle.

Every vehicle host serves the routes in ``autonomy.runtime.routes``, so the
CLI addresses a runtime only by ``base_url``. A PiCar's host is its Donkey
service at the staged connection. A Chase car's host is a local process
(``implementations.runtime.chase_sim.service``) that runs from the vehicle's
controller release, as the Pi runs the release ``hosts update`` installed.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from autonomy.runtime.client import RuntimeClient
from implementations.runtime.chase_sim.service import HOST_RECORD_SCHEMA, RESTART_EXIT_CODE

from ..shared.controller_releases import (
    RuntimeHostError, bundle_root, release_code_dir, release_manifest, release_summary,
)
from ..shared.paths import ROOT, RUNTIME_ROOT, display_path
from ..shared.processes import pid_alive
from ..vehicles.providers import PROVIDERS
from .step_activations import host_names, staging_host

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


def automation_dir(host_name: str) -> Path:
    """The vehicle's local runtime directory: run state, records, host, and view."""

    return bundle_root(host_name) / "runtime" / "automation"


def staged_host(host_name: str) -> dict[str, Any]:
    vehicle, error = staging_host(host_name, runtime_root=RUNTIME_ROOT)
    if vehicle is None:
        raise RuntimeHostError(error or f"Host {host_name!r} is not staged.")
    return vehicle


# The Chase host ------------------------------------------------------------

def host_record(host_name: str) -> dict[str, Any] | None:
    """The running Chase host's record; ``None`` once its process is gone."""

    try:
        record = json.loads((automation_dir(host_name) / HOST_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("schema") != HOST_RECORD_SCHEMA:
        return None
    return record if pid_alive(record.get("pid")) else None


def log_tail(path: Path, name: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return f"(no {name.lower()} log at {display_path(path)})"
    return "\n".join([f"{name} log {display_path(path)}:", *lines[-LOG_TAIL_LINES:]])


def host_log_tail(host_name: str) -> str:
    return log_tail(automation_dir(host_name) / HOST_LOG, "Host")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def supervise_chase_host(host_name: str) -> int:
    """Supervise the Chase host: start it from the latest release, again on restart.

    Runs as this module's main, which ``ensure_chase_host`` starts. The port
    stays the same across restarts so a client's ``base_url`` survives them.
    """

    vehicle = staged_host(host_name)
    ws_url = (vehicle.get("connection") or {}).get("ws_url")
    runtime_dir = bundle_root(host_name) / "runtime"
    record_path = automation_dir(host_name) / HOST_RECORD
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
            release_dir, release = release_code_dir(host_name)
        except RuntimeHostError as exc:
            print(f"Chase host cannot start: {exc}", file=sys.stderr, flush=True)
            return 2
        print(f"Starting the Chase host for {host_name} from release {release['archive_sha256'][:12]}",
              file=sys.stderr, flush=True)
        command = [
            sys.executable, "-I", "-c", BOOT, str(release_dir),
            "--host-name", host_name, "--runtime-dir", str(runtime_dir), "--port", str(port),
            "--host-record", str(record_path), "--release", json.dumps(release),
            *(["--ws-url", ws_url] if isinstance(ws_url, str) and ws_url else []),
        ]
        child = subprocess.Popen(command, cwd=record_path.parent)
        code = child.wait()
        if code != RESTART_EXIT_CODE:
            return code


def _wait_for_host(host_name: str, process: subprocess.Popen[Any] | None, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        record = host_record(host_name)
        if record is not None:
            try:
                RuntimeClient(record["base_url"], timeout_s=1.0).status()
                return record
            except RuntimeError:
                pass
        if process is not None and process.poll() is not None:
            raise RuntimeHostError(
                f"The Chase host for {host_name} exited with code {process.returncode} before serving.\n"
                + host_log_tail(host_name)
            )
        time.sleep(0.1)
    raise RuntimeHostError(
        f"The Chase host for {host_name} did not serve within {timeout_s:g} s.\n" + host_log_tail(host_name)
    )


def ensure_chase_host(host_name: str, *, timeout_s: float = HOST_START_TIMEOUT_S) -> str:
    """The base URL of a Chase host running the latest release, started if needed.

    An idle host on an older release is restarted onto the latest one; a host
    with a running session is never replaced. A host started here that does
    not serve within ``timeout_s``, or whose start is interrupted, is ended.
    """

    latest = release_summary(release_manifest(host_name))
    record = host_record(host_name)
    if record is not None:
        client = RuntimeClient(record["base_url"], timeout_s=2.0)
        try:
            session = client.status()["session"]
        except RuntimeError as exc:
            raise RuntimeHostError(
                f"The Chase host for {host_name} (pid {record['pid']}) is not answering: {exc}\n"
                f"Run: ./cli/automa hosts stop --name {host_name}\n" + host_log_tail(host_name)
            ) from exc
        running = (record.get("release") or {}).get("archive_sha256")
        if running == latest["archive_sha256"]:
            return record["base_url"]
        if session.get("status") == "running":
            raise RuntimeHostError(
                f"{host_name} is running release {str(running)[:12]}; the latest is "
                f"{latest['archive_sha256'][:12]}.\n"
                f"Stop the run first: ./cli/automa hosts stop --name {host_name}"
            )
        try:
            client.restart(timeout_s=timeout_s)
        except (RuntimeError, TimeoutError) as exc:
            raise RuntimeHostError(f"Could not restart the Chase host onto the latest release: {exc}") from exc
        return _wait_for_host(host_name, None, timeout_s)["base_url"]

    release_code_dir(host_name)
    directory = automation_dir(host_name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / HOST_RECORD).unlink(missing_ok=True)
    with (directory / HOST_LOG).open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "cli.automa_cli.hosts.runtime_hosts", host_name],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    try:
        return _wait_for_host(host_name, process, timeout_s)["base_url"]
    except BaseException:
        # The supervisor leads its own session, so this ends its host too.
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        raise


def stop_chase_host(host_name: str, *, wait_s: float = 3.0) -> bool:
    """End the Chase host process; ``True`` when it had to be killed.

    A terminated host releases control on its way out; a killed one cannot.
    """

    record = host_record(host_name)
    if record is None:
        return False
    pids = [pid for pid in (record.get("supervisor_pid"), record["pid"]) if pid_alive(pid)]
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline and any(pid_alive(pid) for pid in pids):
        time.sleep(0.05)
    killed = False
    for pid in pids:
        if pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
            killed = True
    (automation_dir(host_name) / HOST_RECORD).unlink(missing_ok=True)
    return killed


# Any vehicle ----------------------------------------------------------------

def staged_connection(host_name: str) -> dict[str, Any]:
    """The connection ``hosts create`` recorded for the vehicle, without discovery."""

    vehicle = staged_host(host_name)
    connection = vehicle.get("connection")
    return connection if isinstance(connection, dict) else {}


def running_base_url(host_name: str) -> str | None:
    """The runtime host to address now, without discovery or starting one.

    A PiCar's is its staged connection; a Chase car's is its running host.
    """

    if PROVIDERS[staged_host(host_name)["provider"]].local_host:
        record = host_record(host_name)
        return None if record is None else record["base_url"]
    base_url = staged_connection(host_name).get("base_url")
    return base_url.rstrip("/") if isinstance(base_url, str) and base_url.strip() else None


def local_hosts() -> list[dict[str, Any]]:
    """The host each local vehicle addresses now, by ``running_base_url``.

    ``local`` names where the host was found: a Chase car's running host
    record, or a PiCar's staged connection. A host whose configuration cannot
    be read has only its ``error``, so it does not hide the others.
    """

    hosts = []
    for host_name in host_names(RUNTIME_ROOT):
        try:
            configuration = staged_host(host_name)
        except RuntimeHostError as exc:
            hosts.append({"host_name": host_name, "url": None, "pid": None, "local": None, "provider": None,
                          "vehicle_url": None, "error": str(exc)})
            continue
        connection = configuration["connection"]
        vehicle = PROVIDERS[configuration["provider"]]
        record = host_record(host_name) if vehicle.local_host else None
        hosts.append({
            "host_name": host_name,
            "url": (record["base_url"] if record else None) if vehicle.local_host else connection["base_url"],
            "pid": record.get("pid") if record else None,
            "local": "host record" if vehicle.local_host else "staged connection",
            "provider": configuration["provider"],
            "vehicle_url": connection.get(vehicle.url_key),
            "error": None,
        })
    return hosts


def runtime_base_url(host_name: str, *, start: bool = False, timeout_s: float = HOST_START_TIMEOUT_S) -> str:
    """Where the vehicle's runtime serves the shared routes, or why it cannot be reached.

    ``start`` starts (or moves onto the latest release) a local Chase host.
    """

    if start and PROVIDERS[staged_host(host_name)["provider"]].local_host:
        return ensure_chase_host(host_name, timeout_s=timeout_s)
    base_url = running_base_url(host_name)
    if base_url is not None:
        return base_url
    raise RuntimeHostError(f"Host {host_name!r} is not running.")


if __name__ == "__main__":
    raise SystemExit(supervise_chase_host(sys.argv[1]))
