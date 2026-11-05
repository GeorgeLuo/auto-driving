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
from ..shared.io.processes import pid_alive
from ..shared.paths import display_path
from ..shared.text import read_log_tail
from .models import SimulatorsStatus
from .process import SIMULATOR_LOG
from .service import hosts_by_simulator
from .store import simulator_dir, simulator_records

STATUS_SCHEMA = "automa_simulators_status"


def simulators_status(
    *,
    url: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
) -> SimulatorsStatus:
    """Each created simulator, as the ``automa_simulators_status`` document.

    ``url`` is a normalized Chase control URL. Listing exits 0, including
    when none are created or one has exited. With ``url``, the exit is 1
    when that simulator is missing, exited, or unreachable.
    """

    records = simulator_records()
    created = tuple(chase_operator_url(record["url"]) for record in records)
    selected = records if url is None else [record for record in records if record["url"] == url]
    alive = [record for record in selected if pid_alive(record.get("pid"))]
    probes = probe_candidates([Candidate(CHASE_SIM.name, record["url"], "created") for record in alive], timeout_s)
    by_url = {record["url"]: probe for record, probe in zip(alive, probes)}
    hosts = hosts_by_simulator()
    simulators = [_simulator(record, by_url.get(record["url"]), hosts.get(record["url"], [])) for record in selected]
    payload = {"schema": STATUS_SCHEMA, "simulators": simulators}
    failed = url is not None and (not simulators or simulators[0]["state"] in {"exited", "unreachable"})
    return SimulatorsStatus(1 if failed else 0, payload, created)


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
