"""Start and supervise the local Chase host process.

``ensure_chase_host`` launches ``local_worker``. A PiCar has no process here; its
host is the Donkey service at the staged connection.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from autonomy.runtime.client import RuntimeClient
from implementations.runtime.chase_sim.service import HOST_RECORD_SCHEMA

from .errors import RuntimeHostError
from ..releases.store import release_code_dir, release_manifest, release_summary
from ..shared.io.log_files import read_log_tail
from ..shared.io.processes import pid_alive, reap_on_exit
from ..shared.paths import ROOT, display_path
from ..shared.text import automa_command, format_log_tail
from .runs.process import process_command
from .staging import automation_dir

HOST_RECORD = "host.json"
HOST_LOG = "host.log"
HOST_START_TIMEOUT_S = 20.0
LOCAL_WORKER_MODULE = "cli.automa_cli.hosts.local_worker"
# The supervisor's ``-c`` boot imports this; ``ps`` leaves it in the child command.
_CHASE_SERVICE = "implementations.runtime.chase_sim.service"


def host_record(host_name: str) -> dict[str, object] | None:
    """The running Chase host's record; ``None`` once its process is gone."""

    try:
        record = json.loads((automation_dir(host_name) / HOST_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("schema") != HOST_RECORD_SCHEMA:
        return None
    return record if pid_alive(record.get("pid")) else None


def log_tail(path: Path, name: str) -> str:
    return format_log_tail(name, display_path(path), read_log_tail(path))


def host_log_tail(host_name: str) -> str:
    return log_tail(automation_dir(host_name) / HOST_LOG, "Host")


def _wait_for_host(host_name: str, process: subprocess.Popen[bytes] | None, timeout_s: float) -> dict[str, object]:
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
                f"Run: {automa_command('hosts', 'stop', '--name', host_name)}\n" + host_log_tail(host_name)
            ) from exc
        running = (record.get("release") or {}).get("archive_sha256")
        if running == latest["archive_sha256"]:
            return record["base_url"]
        if session.get("status") == "running":
            raise RuntimeHostError(
                f"{host_name} is running release {str(running)[:12]}; the latest is "
                f"{latest['archive_sha256'][:12]}.\n"
                f"Stop the run first: {automa_command('hosts', 'stop', '--name', host_name)}"
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
            [sys.executable, "-m", LOCAL_WORKER_MODULE, host_name],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    reap_on_exit(process)
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


def _command_tokens(command: str) -> list[str] | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    return tokens or None


def _option_is(tokens: list[str], flag: str, value: str) -> bool:
    if f"{flag}={value}" in tokens:
        return True
    pair = [flag, value]
    return any(tokens[index:index + 2] == pair for index in range(len(tokens) - 1))


def supervisor_command_matches(command: str, host_name: str) -> bool:
    """Pure check: command is the local Chase supervisor for exactly ``host_name``.

    The supervisor is ``python -m cli.automa_cli.hosts.local_worker <host_name>``.
    The host name is the next token, matched exactly.
    """

    tokens = _command_tokens(command)
    if tokens is None or not host_name:
        return False
    return any(
        tokens[index:index + 3] == ["-m", LOCAL_WORKER_MODULE, host_name]
        for index in range(len(tokens) - 2)
    )


def chase_host_command_matches(command: str, host_name: str) -> bool:
    """Pure check: command is the Chase host service for exactly ``host_name``.

    The service is the supervisor's ``-c`` boot plus an exact ``--host-name``.
    """

    tokens = _command_tokens(command)
    if tokens is None or not host_name:
        return False
    return any(_CHASE_SERVICE in token for token in tokens) and _option_is(tokens, "--host-name", host_name)


def _command_allows(pid: int, matches: Callable[[str, str], bool], host_name: str) -> bool:
    """True when the command is this host's, or it cannot be read."""

    command = process_command(pid)
    if command is None:
        return True
    return matches(command, host_name)


def _signal_pid(pid: int, sig: signal.Signals) -> bool:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return False
    return True


def _matching_pids(
    record: dict[str, object], host_name: str,
) -> list[tuple[int, Callable[[str, str], bool]]]:
    candidates = (
        (record.get("supervisor_pid"), supervisor_command_matches),
        (record.get("pid"), chase_host_command_matches),
    )
    matched: list[tuple[int, Callable[[str, str], bool]]] = []
    for pid, matches in candidates:
        if type(pid) is int and pid_alive(pid) and _command_allows(pid, matches, host_name):
            matched.append((pid, matches))
    return matched


def stop_chase_host(host_name: str, *, wait_s: float = 3.0) -> bool:
    """End this host's processes; ``True`` when one had to be killed.

    A pid is signalled only when its command is this host's supervisor or
    Chase service, or the command cannot be read. Any other live pid is left
    alone. The record is removed either way. A process that disappears before
    the signal counts as already stopped, not killed.
    """

    record = host_record(host_name)
    if record is None:
        return False
    matched = _matching_pids(record, host_name)
    for pid, _matches in matched:
        _signal_pid(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline and any(pid_alive(pid) for pid, _matches in matched):
        time.sleep(0.05)
    killed = False
    for pid, matches in matched:
        if pid_alive(pid) and _command_allows(pid, matches, host_name) and _signal_pid(pid, signal.SIGKILL):
            killed = True
    (automation_dir(host_name) / HOST_RECORD).unlink(missing_ok=True)
    return killed
