"""Each local host's state, and whether its simulator lists it.

A host's configuration names its vehicle. A Chase host is attached when its
simulator lists it; a PiCar's host is its staged connection. ``hosts status``
and the runtime view's host list read hosts here.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from implementations.vehicle.chase_sim.metrics_ws import (
    MetricsUiWebSocketError,
    MetricsUiWsClient,
)

from ..providers.catalog import CHASE_SIM
from ..providers.models import DEFAULT_READINESS_TIMEOUT_S
from ..providers.urls import chase_operator_url, require_positive_timeout
from .discovery import local_hosts
from .publications.host_publications import host_state


def host_statuses(
    *, host_name: str | None = None, timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """Each local host's state, or a "not found" entry for an unknown ``host_name``."""

    return [host for host, _ in read_hosts(host_name=host_name, timeout_s=timeout_s)]


def read_hosts(
    *, host_name: str | None = None, timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Each host's status, with the ``host_state`` read it came from.

    Hosts come from explicit local records; a Chase host is attached when its
    simulator lists it. Every host's ``/host/status`` and each simulator's
    agent list are read in parallel within the one deadline. A host that is
    not running or not found has no read.
    """

    deadline = time.monotonic() + require_positive_timeout(timeout_s)

    def remaining() -> float:
        # The client refuses a zero timeout; a spent budget times out at once instead.
        return max(deadline - time.monotonic(), 1e-3)

    hosts = {
        local["host_name"]: _host_entry(**local)
        for local in local_hosts()
        if host_name in (None, local["host_name"])
    }
    simulators = {host["vehicle_url"] for host in hosts.values()
                  if host["provider"] == CHASE_SIM.name and host["vehicle_url"]}
    with ThreadPoolExecutor() as pool:
        reads = {}
        for host in hosts.values():
            if host["error"] is not None:
                host["state"] = "error"
            elif host["url"] is None:
                host["state"] = "not running"
            else:
                reads[host["host_name"]] = pool.submit(
                    host_state, host["url"], host_name=host["host_name"], timeout_s=remaining()
                )
        # One agent list per simulator, however many of its hosts are listed.
        listings = {endpoint: pool.submit(_listed_agents, endpoint, remaining) for endpoint in simulators}
        states = {name: read.result() for name, read in reads.items()}
        agents_by_endpoint = {endpoint: listing.result() for endpoint, listing in listings.items()}

    for host in hosts.values():
        if host["provider"] != CHASE_SIM.name or not host["vehicle_url"]:
            continue
        listed = agents_by_endpoint[host["vehicle_url"]]
        # The agent list is read at the simulator's control endpoint; status reports its page.
        host["vehicle_url"] = chase_operator_url(host["vehicle_url"])
        if listed is None:
            continue
        host["attached"] = False
        for agent in listed:
            client_info = agent.get("client")
            if (
                isinstance(client_info, dict)
                and client_info.get("kind") == "host"
                and client_info.get("host_name") == host["host_name"]
                and isinstance(client_info.get("url"), str)
                and client_info["url"].rstrip("/") == host["url"]
            ):
                host.update(attached=True, attached_since=agent.get("connectedAt"))
                if host["pid"] is None:
                    host["pid"] = client_info.get("pid")
    for name, state in states.items():
        hosts[name].update({key: value for key, value in state.items() if key != "reply"})
        # A bounded run ends "completed", but the CLI's closing stop leaves it "stopped".
        if hosts[name]["state"] in {"stopped", "completed"}:
            hosts[name]["state"] = "finished_run"
    ordered = sorted(hosts.values(), key=lambda host: (str(host["host_name"]), host["url"] or ""))
    if host_name is not None and not ordered:
        ordered.append({**_host_entry(host_name, None), "state": "not found"})
    return [(host, states.get(host["host_name"])) for host in ordered]


def _listed_agents(endpoint: str, remaining: Callable[[], float]) -> list[dict[str, Any]] | None:
    """The agents a simulator lists, or ``None`` when it could not list them."""

    try:
        return MetricsUiWsClient(endpoint, timeout_s=remaining()).list_agents(timeout_s=remaining())
    except (OSError, ValueError, MetricsUiWebSocketError):
        return None


def _host_entry(host_name: Any, url: str | None, pid: Any = None, local: str | None = None,
                provider: str | None = None, vehicle_url: str | None = None, error: str | None = None) -> dict[str, Any]:
    return {
        "host_name": host_name, "url": url, "pid": pid, "attached": None, "attached_since": None,
        "provider": provider, "vehicle_url": vehicle_url,
        "local": local, "state": None, "mode": None, "decision_count": None, "error_count": None,
        "last_frame_at_ms": None, "error": error,
    }
