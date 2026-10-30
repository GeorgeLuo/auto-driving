"""Start and supervise the local Chase host process.

``ensure_chase_host`` launches this module. A PiCar has no process here; its
host is the Donkey service at the staged connection.
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

from ..releases.errors import RuntimeHostError
from ..releases.paths import bundle_root
from ..releases.store import release_code_dir, release_manifest, release_summary
from ..shared.io.processes import pid_alive
from ..shared.paths import ROOT, display_path
from .staging import automation_dir, staged_host

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
            [sys.executable, "-m", "cli.automa_cli.hosts.local", host_name],
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


if __name__ == "__main__":
    raise SystemExit(supervise_chase_host(sys.argv[1]))
