"""Start and supervise the local Chase host process.

``ensure_chase_host`` launches ``local_worker``. A PiCar has no process here; its
host is the Donkey service at the staged connection.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from autonomy.runtime.client import RuntimeClient
from implementations.runtime.chase_sim.service import HOST_RECORD_SCHEMA

from ..releases.errors import RuntimeHostError
from ..releases.store import release_code_dir, release_manifest, release_summary
from ..shared.io.processes import pid_alive
from ..shared.paths import ROOT, display_path
from .staging import automation_dir

HOST_RECORD = "host.json"
HOST_LOG = "host.log"
HOST_START_TIMEOUT_S = 20.0
LOG_TAIL_LINES = 20


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
            [sys.executable, "-m", "cli.automa_cli.hosts.local_worker", host_name],
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
