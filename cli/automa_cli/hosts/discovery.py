"""Find hosts and the runtime endpoints that are already serving."""

from __future__ import annotations

from typing import Any

from ..providers.registry import PROVIDERS
from .errors import RuntimeHostError
from . import staging
from .local import host_record
from .staging import staged_host
from .step_activations import host_names


def running_base_url(host_name: str) -> str | None:
    """The runtime host to address now, without discovery or starting one.

    A PiCar's is its staged connection; a Chase car's is its running host.
    """

    return _address(staged_host(host_name))[0]


def local_hosts() -> list[dict[str, Any]]:
    """The host each local vehicle addresses now, as ``running_base_url`` finds it.

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
        vehicle = PROVIDERS[configuration["provider"]]
        url, record = _address(configuration)
        hosts.append({
            "host_name": host_name,
            "url": url,
            "pid": record.get("pid") if record else None,
            "local": "host record" if vehicle.local_host else "staged connection",
            "provider": configuration["provider"],
            "vehicle_url": configuration["connection"].get(vehicle.url_key),
            "error": None,
        })
    return hosts


def _address(configuration: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """The URL a host answers at now, and the running Chase host's record.

    A blank staged PiCar URL names no host.
    """

    if PROVIDERS[configuration["provider"]].local_host:
        record = host_record(configuration["host_name"])
        return (None if record is None else record["base_url"]), record
    base_url = configuration["connection"].get("base_url")
    return (base_url.rstrip("/") if isinstance(base_url, str) and base_url.strip() else None), None


def runtime_base_url(host_name: str) -> str:
    """The serving endpoint, or an error when the host is not running."""

    base_url = running_base_url(host_name)
    if base_url is not None:
        return base_url
    raise RuntimeHostError(f"Host {host_name!r} is not running.")
