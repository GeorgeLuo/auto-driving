"""``automa simulators create`` and ``delete``.

Create picks a free port and asks the process owner to start the headless
launcher. Delete ends that process unless a host was created at its URL.
"""

from __future__ import annotations

import os
import shlex
import socket
from pathlib import Path

from ..hosts.discovery import local_hosts
from ..providers.catalog import CHASE_SIM
from ..providers.urls import normalize_chase_url
from ..releases.errors import RuntimeHostError
from .models import SimulatorResult
from .process import start_simulator, stop_simulator
from .store import created_simulators

# simulators create runs Metrics UI's headless launcher from this checkout.
METRICS_UI_DIR_ENV = "AUTOMA_METRICS_UI_DIR"
HEADLESS_SIM_SCRIPT = "scripts/play-chase-headless.mjs"
# 5050 stays the default simulator's.
SIMULATOR_PORTS = range(5051, 5100)
SIMULATOR_START_TIMEOUT_S = 45.0


def create_simulator(*, timeout_s: float = SIMULATOR_START_TIMEOUT_S) -> SimulatorResult:
    """Start a headless Chase simulator on the first free port in ``SIMULATOR_PORTS``.

    Metrics UI's launcher runs it with its default configuration and checks
    it is ready. It outlives this command, and ``vehicles status`` lists it
    until ``delete_simulator`` ends it.
    """

    ui_dir, error = _metrics_ui_dir()
    if ui_dir is None:
        return SimulatorResult(2, "create_error", error=error)
    port = next((port for port in SIMULATOR_PORTS if not _port_in_use(port)), None)
    if port is None:
        return SimulatorResult(2, "create_error", error=f"ports {SIMULATOR_PORTS[0]}-{SIMULATOR_PORTS[-1]} are all in use.")
    try:
        record = start_simulator(_headless_sim_command(port), cwd=ui_dir, port=port, timeout_s=timeout_s)
    except OSError as exc:
        return SimulatorResult(2, "create_error", error=str(exc))
    except RuntimeHostError as exc:
        return SimulatorResult(2, "error", error=str(exc))
    return SimulatorResult(0, "created", url=record["url"])


def delete_simulator(*, url: str) -> SimulatorResult:
    """End a simulator ``simulators create`` started, unless a host was created at it."""

    try:
        ws_url = normalize_chase_url(url)
    except ValueError as exc:
        return SimulatorResult(2, "error", error=str(exc))
    created = created_simulators()
    record = next((simulator for simulator in created if simulator["url"] == ws_url), None)
    if record is None:
        return SimulatorResult(2, "missing", url=url, urls=tuple(item["url"] for item in created))
    hosts = [host["host_name"] for host in local_hosts()
             if host["provider"] == CHASE_SIM.name and host["vehicle_url"] == ws_url]
    if hosts:
        return SimulatorResult(2, "blocked", url=ws_url, hosts=tuple(hosts))
    killed = stop_simulator(record)
    return SimulatorResult(0, "deleted", url=ws_url, killed=killed)


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
