"""``automa simulators create`` and ``delete``: the headless simulators automa starts and ends.

A simulator ``simulators create`` started is a local process, recorded
by port so ``vehicles status`` lists it while it runs.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from ..shared.command_result import CommandResult
from ..hosts.runtime_hosts import RUNTIME_ROOT, RuntimeHostError, _log_tail
from ..shared.processes import pid_alive


# simulators create runs Metrics UI's headless launcher from this checkout.
METRICS_UI_DIR_ENV = "AUTOMA_METRICS_UI_DIR"
HEADLESS_SIM_SCRIPT = "scripts/play-chase-headless.mjs"
# 5050 stays the default simulator's.
SIMULATOR_PORTS = range(5051, 5100)
SIMULATOR_START_TIMEOUT_S = 45.0
# Each simulator ``simulators create`` started, in ``simulators/<port>/``.
SIMULATORS = "simulators"
SIMULATOR_RECORD = "simulator.json"
SIMULATOR_LOG = "simulator.log"
SIMULATOR_SCHEMA = "automa_created_simulator_v0"
# The headless launcher prints this line once its simulator is ready.
SIMULATOR_READY = "CHASE_UI_WS_URL="


def create_simulator(*, timeout_s: float = SIMULATOR_START_TIMEOUT_S) -> CommandResult:
    """Start a headless Chase simulator on the first free port in ``SIMULATOR_PORTS``.

    Metrics UI's launcher runs it with its default configuration and checks
    it is ready. It outlives this command, and ``vehicles status`` lists it
    until ``delete_simulator`` ends it.
    """

    from ..vehicles.discovery import _vehicle_heading

    ui_dir, error = _metrics_ui_dir()
    if ui_dir is None:
        return CommandResult(2, f"Cannot create a simulator: {error}")
    port = next((port for port in SIMULATOR_PORTS if not _port_in_use(port)), None)
    if port is None:
        return CommandResult(
            2, f"Cannot create a simulator: ports {SIMULATOR_PORTS[0]}-{SIMULATOR_PORTS[-1]} are all in use."
        )
    try:
        record = start_simulator(_headless_sim_command(port), cwd=ui_dir, port=port, timeout_s=timeout_s)
    except OSError as exc:
        return CommandResult(2, f"Cannot create a simulator: {exc}")
    except RuntimeHostError as exc:
        return CommandResult(2, str(exc))
    return CommandResult(0, f"Created {_vehicle_heading('chase-sim', record['url'])}")


def delete_simulator(*, url: str) -> CommandResult:
    """End a simulator ``simulators create`` started, unless a host was created at it."""

    from ..hosts.runtime_hosts import local_hosts
    from ..vehicles.discovery import _vehicle_heading
    from ..vehicles.probes import chase_operator_url, normalize_chase_url

    try:
        ws_url = normalize_chase_url(url)
    except ValueError as exc:
        return CommandResult(2, str(exc))
    created = created_simulators()
    record = next((simulator for simulator in created if simulator["url"] == ws_url), None)
    if record is None:
        names = ", ".join(chase_operator_url(simulator["url"]) for simulator in created)
        return CommandResult(2, f"No created simulator at {url}. " + (f"Created: {names}." if names else "None created."))
    hosts = [host["host_name"] for host in local_hosts()
             if host["provider"] == "chase-sim" and host["vehicle_url"] == ws_url]
    if hosts:
        subject = f"Host {hosts[0]} uses" if len(hosts) == 1 else f"Hosts {', '.join(hosts)} use"
        return CommandResult(2, f"{subject} the simulator at {chase_operator_url(ws_url)}; "
                                f"delete {'it' if len(hosts) == 1 else 'them'} first.")
    killed = stop_simulator(record)
    return CommandResult(0, f"Deleted {_vehicle_heading('chase-sim', ws_url)}" + (", killed" if killed else ""))


def _metrics_ui_dir() -> tuple[Path | None, str | None]:
    value = os.environ.get(METRICS_UI_DIR_ENV, "").strip()
    if not value:
        return None, f"{METRICS_UI_DIR_ENV}, the Metrics UI checkout to run it from, is not set."
    ui_dir = Path(value).expanduser()
    if not (ui_dir / HEADLESS_SIM_SCRIPT).is_file():
        return None, f"{ui_dir} has no {HEADLESS_SIM_SCRIPT}."
    if not (ui_dir / "node_modules" / ".bin" / "tsx").exists():
        return None, f"{ui_dir} has no node_modules/.bin/tsx; its packages are not installed."
    return ui_dir, None


def _port_in_use(port: int) -> bool:
    # As the launcher checks: something answers on the loopback port.
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
    except OSError:
        return False
    return True


def _headless_sim_command(port: int) -> list[str]:
    override = os.environ.get("AUTOMA_HEADLESS_SIM_COMMAND")
    launcher = shlex.split(override) if override else ["node", HEADLESS_SIM_SCRIPT]
    return [*launcher, "--port", str(port)]


def simulator_dir(port: int) -> Path:
    return RUNTIME_ROOT / SIMULATORS / str(port)


def created_simulators() -> list[dict[str, Any]]:
    """The record of each simulator ``simulators create`` started that is still running, by port."""

    records = []
    for path in (RUNTIME_ROOT / SIMULATORS).glob(f"*/{SIMULATOR_RECORD}"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("schema") == SIMULATOR_SCHEMA and pid_alive(record.get("pid")):
            records.append(record)
    return sorted(records, key=lambda record: record["port"])


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
                + _log_tail(log_path, "Simulator")
            )
        if time.monotonic() >= deadline:
            raise RuntimeHostError(
                f"The simulator was not ready within {timeout_s:g} s.\n" + _log_tail(log_path, "Simulator")
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
        (directory / SIMULATOR_RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return record
    except BaseException:
        _end_process_group(process.pid)
        raise


def stop_simulator(record: dict[str, Any]) -> bool:
    """End a created simulator and remove its record; ``True`` when it had to be killed."""

    killed = _end_process_group(record["pid"])
    (simulator_dir(record["port"]) / SIMULATOR_RECORD).unlink(missing_ok=True)
    return killed
