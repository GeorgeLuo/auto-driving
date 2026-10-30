"""Find hosts and the runtime endpoints that are already serving."""

from __future__ import annotations

from typing import Any

from ..providers.catalog import PROVIDERS
from ..releases.errors import RuntimeHostError
from . import staging
from .local import host_record
from .staging import staged_connection, staged_host
from .step_activations import host_names


def running_base_url(host_name: str) -> str | None:
    """The runtime host to address now, without discovery or starting one.

    A PiCar's is its staged connection; a Chase car's is its running host.
    """

    if PROVIDERS[staged_host(host_name)["provider"]].local_host:
        record = host_record(host_name)
        return None if record is None else record["base_url"]
    base_url = staged_connection(host_name).get("base_url")
    return base_url.rstrip("/") if isinstance(base_url, str) and base_url.strip() else None


def local_hosts() -> list[dict[str, Any]]:
    """The host each local vehicle addresses now, by ``running_base_url``.

    ``local`` names where the host was found: a Chase car's running host
    record, or a PiCar's staged connection. A host whose configuration cannot
    be read has only its ``error``, so it does not hide the others.
    """

    hosts = []
    for host_name in host_names(staging.RUNTIME_ROOT):
        try:
            configuration = staged_host(host_name)
        except RuntimeHostError as exc:
            hosts.append({"host_name": host_name, "url": None, "pid": None, "local": None, "provider": None,
                          "vehicle_url": None, "error": str(exc)})
            continue
        connection = configuration["connection"]
        vehicle = PROVIDERS[configuration["provider"]]
        record = host_record(host_name) if vehicle.local_host else None
        hosts.append({
            "host_name": host_name,
            "url": (record["base_url"] if record else None) if vehicle.local_host else connection["base_url"],
            "pid": record.get("pid") if record else None,
            "local": "host record" if vehicle.local_host else "staged connection",
            "provider": configuration["provider"],
            "vehicle_url": connection.get(vehicle.url_key),
            "error": None,
        })
    return hosts


def runtime_base_url(host_name: str) -> str:
    """The serving endpoint, or an error when the host is not running."""

    base_url = running_base_url(host_name)
    if base_url is not None:
        return base_url
    raise RuntimeHostError(f"Host {host_name!r} is not running.")
