"""``automa simulators status``: each simulator ``simulators create`` started.

A record whose process has exited stays in the list. ``vehicles status``
keeps the live records only.
"""

from __future__ import annotations

from typing import Any

from ..providers.metadata import CHASE_SIM
from ..providers.models import DEFAULT_READINESS_TIMEOUT_S, Candidate
from ..providers.registry import probe_candidates
from ..providers.urls import chase_operator_url
from ..shared.io.log_files import read_log_tail
from ..shared.io.processes import pid_alive
from ..shared.paths import display_path
from .process import SIMULATOR_LOG
from .service import hosts_by_simulator
from .store import simulator_dir, simulator_records

STATUS_SCHEMA = "automa_simulators_status"
# Each simulator entry's fields; a "not found" entry has them too.
_ENTRY_FIELDS = (
    "url", "port", "pid", "state", "frontend", "game", "error", "hosts",
    "started_at_ms", "metrics_ui_dir", "config", "log", "log_tail",
)


def simulators_status(
    *,
    url: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
) -> tuple[int, dict[str, Any]]:
    """The exit and the ``automa_simulators_status`` payload: each created simulator.

    ``url`` is a normalized Chase control URL. Listing exits 0, including
    when none are created or one has exited. With ``url``, the exit is 1
    when that simulator is missing, exited, or unreachable; a missing one is
    a "not found" entry.
    """

    records = simulator_records()
    selected = records if url is None else [record for record in records if record["url"] == url]
    alive = [record for record in selected if pid_alive(record.get("pid"))]
    probes = probe_candidates([Candidate(CHASE_SIM.name, record["url"], "created") for record in alive], timeout_s)
    by_url = {record["url"]: probe for record, probe in zip(alive, probes)}
    hosts = hosts_by_simulator()
    simulators = [_simulator(record, by_url.get(record["url"]), hosts.get(record["url"], [])) for record in selected]
    if url is not None and not simulators:
        missing = {"url": chase_operator_url(url), "state": "not found", "hosts": []}
        simulators.append({**dict.fromkeys(_ENTRY_FIELDS), **missing})
    failed = url is not None and simulators[0]["state"] in {"not found", "exited", "unreachable"}
    return (1 if failed else 0), {"schema": STATUS_SCHEMA, "simulators": simulators}


def _simulator(record: dict[str, Any], probe: dict[str, Any] | None, hosts: list[str]) -> dict[str, Any]:
    """One simulator's entry; ``probe`` is ``None`` when its process has exited.

    ``log_tail`` is the exited log's last lines, ``None`` while it runs or unreadable.
    """

    log_path = simulator_dir(record["port"]) / SIMULATOR_LOG
    exited = probe is None
    return {
        "url": chase_operator_url(record["url"]),
        "port": record["port"],
        "pid": record.get("pid"),
        "state": "exited" if exited else probe["state"],
        "frontend": None if exited else probe.get("frontend"),
        "game": None if exited else probe.get("game"),
        "error": None if exited else probe.get("error"),
        "hosts": hosts,
        "started_at_ms": record.get("started_at_ms"),
        "metrics_ui_dir": record.get("metrics_ui_dir"),
        "config": record.get("config"),
        "log": display_path(log_path),
        "log_tail": read_log_tail(log_path) if exited else None,
    }
