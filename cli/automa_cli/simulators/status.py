"""``automa simulators status``: each simulator ``simulators create`` started.

A record whose process has exited stays in the list. ``vehicles status``
keeps the live records only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..hosts.local import LOG_TAIL_LINES, log_tail
from ..providers.metadata import CHASE_SIM
from ..providers.models import DEFAULT_READINESS_TIMEOUT_S, Candidate
from ..providers.registry import probe_candidates
from ..providers.urls import chase_operator_url
from ..shared.io.processes import pid_alive
from ..shared.paths import display_path
from .process import SIMULATOR_LOG
from .service import hosts_at_simulator
from .store import simulator_dir, simulator_records


def simulators_status(
    *,
    url: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
) -> tuple[int, list[dict[str, Any]], tuple[str, ...]]:
    """Each created simulator, and the operator URL of every record.

    ``url`` is a normalized Chase control URL. Listing exits 0, including
    when none are created or one has exited. With ``url``, the exit is 1
    when that simulator is missing, exited, or unreachable.
    """

    records = simulator_records()
    created = tuple(chase_operator_url(record["url"]) for record in records)
    selected = records if url is None else [record for record in records if record["url"] == url]
    if url is not None and not selected:
        return 1, [], created
    alive = [record for record in selected if pid_alive(record.get("pid"))]
    probes = probe_candidates(
        [Candidate(CHASE_SIM.name, record["url"], "created") for record in alive],
        timeout_s,
    ) if alive else []
    by_url = {record["url"]: probe for record, probe in zip(alive, probes)}
    simulators = [_simulator(record, by_url.get(record["url"])) for record in selected]
    if url is not None and simulators[0]["state"] in {"exited", "unreachable"}:
        return 1, simulators, created
    return 0, simulators, created


def _simulator(record: dict[str, Any], probe: dict[str, Any] | None) -> dict[str, Any]:
    log_path = simulator_dir(record["port"]) / SIMULATOR_LOG
    if probe is None:
        state, error, frontend, game = "exited", None, None, None
        log_lines, log_text = _exited_log(log_path)
    else:
        state, error = probe["state"], probe.get("error")
        frontend, game = probe.get("frontend"), probe.get("game")
        log_lines, log_text = None, None
    return {
        "url": chase_operator_url(record["url"]),
        "port": record["port"],
        "pid": record.get("pid"),
        "state": state,
        "frontend": frontend,
        "game": game,
        "error": error,
        "hosts": hosts_at_simulator(record["url"]),
        "started_at_ms": record.get("started_at_ms"),
        "metrics_ui_dir": record.get("metrics_ui_dir"),
        "config": record.get("config"),
        "log": display_path(log_path),
        "log_tail": log_lines,
        "log_text": log_text,
    }


def _exited_log(path: Path) -> tuple[list[str], str]:
    """The lines ``log_tail`` prints, and that same text for the block."""

    text = log_tail(path, "Simulator")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], text
    return lines[-LOG_TAIL_LINES:], text
