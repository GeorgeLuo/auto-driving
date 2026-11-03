"""Start and stop the headless simulator process.

The process leads its own session, so it outlives the command that started
it. A start that fails or is interrupted ends that process group.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ..hosts.local import log_tail
from ..releases.errors import RuntimeHostError
from ..shared.io.processes import pid_alive
from .store import SIMULATOR_SCHEMA, remove_simulator_record, simulator_dir, write_simulator_record


# Beside the simulator's record, in its ``simulator_dir``.
SIMULATOR_LOG = "simulator.log"
# The headless launcher prints this line once its simulator is ready.
SIMULATOR_READY = "CHASE_UI_WS_URL="


def _ended(pid: int) -> bool:
    """Whether the process is gone, reaping it when this process started it."""

    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return True
    except ChildProcessError:
        pass
    return not pid_alive(pid)


def _end_process_group(pid: int, *, wait_s: float = 3.0) -> bool:
    """Terminate the process group ``pid`` leads; ``True`` when it had to be killed."""

    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_s
    while not _ended(pid):
        if time.monotonic() >= deadline:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
            return True
        time.sleep(0.05)
    return False


def _wait_for_simulator(log_path: Path, process: subprocess.Popen[Any], timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while True:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(SIMULATOR_READY):
                return line.removeprefix(SIMULATOR_READY).strip()
        if process.poll() is not None:
            raise RuntimeHostError(
                f"The simulator exited with code {process.returncode} before it was ready.\n"
                + log_tail(log_path, "Simulator")
            )
        if time.monotonic() >= deadline:
            raise RuntimeHostError(
                f"The simulator was not ready within {timeout_s:g} s.\n" + log_tail(log_path, "Simulator")
            )
        time.sleep(0.1)


def start_simulator(command: list[str], *, cwd: Path, port: int, timeout_s: float) -> dict[str, Any]:
    """Run a headless simulator launcher on ``port`` and record it once it prints its URL.

    The launcher leads its own session, so the simulator outlives this
    command. One that exits, is not ready within ``timeout_s``, or whose
    start is interrupted is ended.
    """

    directory = simulator_dir(port)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / SIMULATOR_LOG
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        record = {
            "schema": SIMULATOR_SCHEMA,
            "pid": process.pid,
            "port": port,
            "url": _wait_for_simulator(log_path, process, timeout_s),
            "metrics_ui_dir": str(cwd),
            "config": "default",
            "started_at_ms": int(time.time() * 1000),
        }
        write_simulator_record(record)
        return record
    except BaseException:
        _end_process_group(process.pid)
        raise


def stop_simulator(record: dict[str, Any]) -> bool:
    """End a created simulator and remove its record; ``True`` when it had to be killed."""

    killed = _end_process_group(record["pid"])
    remove_simulator_record(record)
    return killed
