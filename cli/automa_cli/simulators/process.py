"""Start and stop the headless simulator process.

The process leads its own session, so it outlives the command that started
it. A start that fails or is interrupted ends that process group.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path
from typing import Literal

from ..hosts.local import log_tail
from ..hosts.runs.process import process_command
from ..shared.io.processes import pid_alive, reap_on_exit
from ..shared.timestamps import timestamp_ms
from .store import SIMULATOR_SCHEMA, remove_simulator_record, simulator_dir, write_simulator_record


# Beside the simulator's record, in its ``simulator_dir``.
SIMULATOR_LOG = "simulator.log"
# The headless launcher prints this line once its simulator is ready.
SIMULATOR_READY = "CHASE_UI_WS_URL="
# Basename of the script ``simulators create`` launches. ``ps`` may show a full path.
_HEADLESS_SIM_NAME = "play-chase-headless.mjs"


class SimulatorStartError(RuntimeError):
    """The simulator exited or timed out before it was ready; the message ends with its log."""


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


def _wait_for_simulator(log_path: Path, process: subprocess.Popen[bytes], timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while True:
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(SIMULATOR_READY):
                return line.removeprefix(SIMULATOR_READY).strip()
        if process.poll() is not None:
            raise SimulatorStartError(
                f"The simulator exited with code {process.returncode} before it was ready.\n"
                + log_tail(log_path, "Simulator")
            )
        if time.monotonic() >= deadline:
            raise SimulatorStartError(
                f"The simulator was not ready within {timeout_s:g} s.\n" + log_tail(log_path, "Simulator")
            )
        time.sleep(0.1)


def start_simulator(command: list[str], *, cwd: Path, port: int, timeout_s: float) -> dict[str, object]:
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
    reap_on_exit(process)
    try:
        record = {
            "schema": SIMULATOR_SCHEMA,
            "pid": process.pid,
            "port": port,
            "url": _wait_for_simulator(log_path, process, timeout_s),
            "metrics_ui_dir": str(cwd),
            "config": "default",
            "started_at_ms": timestamp_ms(),
        }
        write_simulator_record(record)
        return record
    except BaseException:
        _end_process_group(process.pid)
        raise


def _command_tokens(command: str) -> list[str] | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    return tokens or None


def _option_is(tokens: list[str], flag: str, value: str) -> bool:
    prefix = f"{flag}="
    for index, token in enumerate(tokens):
        if token == flag and index + 1 < len(tokens) and tokens[index + 1] == value:
            return True
        if token.startswith(prefix) and token.removeprefix(prefix) == value:
            return True
    return False


def _launcher_matches(tokens: list[str]) -> bool:
    override = os.environ.get("AUTOMA_HEADLESS_SIM_COMMAND")
    if not override:
        return any(Path(token).name == _HEADLESS_SIM_NAME for token in tokens)
    try:
        prefix = shlex.split(override)
    except ValueError:
        return False
    # ps reports a resolved interpreter, so the executable token is not compared.
    return len(prefix) >= 2 and len(tokens) >= len(prefix) and tokens[1:len(prefix)] == prefix[1:]


def simulator_command_matches(command: str, port: int) -> bool:
    """Pure check: command is the headless simulator on exactly ``port``.

    ``simulators create`` runs ``node scripts/play-chase-headless.mjs --port``,
    or the ``AUTOMA_HEADLESS_SIM_COMMAND`` it was given. ``--port`` matches
    exactly. The interpreter is ignored because ``ps`` may show a resolved binary.
    """

    tokens = _command_tokens(command)
    if tokens is None or not _option_is(tokens, "--port", str(port)):
        return False
    return _launcher_matches(tokens)


def _our_simulator(pid: int, port: int) -> bool:
    if not pid_alive(pid):
        return False
    command = process_command(pid)
    return command is None or simulator_command_matches(command, port)


def stop_simulator(record: dict[str, object]) -> Literal["exited", "stopped", "killed"]:
    """End a created simulator and remove its record; how it ended.

    A pid whose process has exited, or whose command is not this simulator,
    is not signalled: the pid may belong to another process. An unreadable
    command is still stopped.
    """

    pid = record.get("pid")
    port = record.get("port")
    if type(pid) is int and type(port) is int and _our_simulator(pid, port):
        ended = "killed" if _end_process_group(pid) else "stopped"
    else:
        ended = "exited"
    remove_simulator_record(record)
    return ended
