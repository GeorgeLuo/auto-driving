"""``automa vehicles discover`` and ``remove``, and the vehicles status lists."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from implementations.vehicle.chase_sim.defaults import (
    CHASE_UI_WS_URL_ENV,
    DEFAULT_CHASE_UI_WS_URL,
)

from ..shared.command_result import CommandResult
from .probes import (
    DEFAULT_READINESS_TIMEOUT_S,
    Candidate,
    _picar_state,
    _probe_candidate,
    chase_operator_url,
    normalize_chase_url,
)

# Each vehicle's heading names the --provider that discover and hosts create take for it.
_HEADINGS = {"chase-sim": "Simulator (chase-sim)", "picar": "PiCar (picar)"}


def default_chase_url() -> str:
    """The simulator status always lists: ``CHASE_UI_WS_URL``, else localhost:5050."""

    return normalize_chase_url(os.environ.get(CHASE_UI_WS_URL_ENV, DEFAULT_CHASE_UI_WS_URL))


def listed_vehicles() -> list[Candidate]:
    """Where vehicles status looks: the default simulator, each created simulator, then each discovered vehicle."""

    return _dedupe_candidates([
        Candidate("chase-sim", default_chase_url(), "default"), *_created_candidates(), *_discovered_candidates(),
    ])


def _created_candidates() -> list[Candidate]:
    from ..simulators.created import created_simulators

    return [Candidate("chase-sim", simulator["url"], "created") for simulator in created_simulators()]


def _discovered_candidates() -> list[Candidate]:
    from ..hosts.runtime_hosts import discovered_vehicles

    return [Candidate(vehicle["provider"], vehicle["url"], "discovered") for vehicle in discovered_vehicles()]


def _dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, str]] = set()
    unique: list[Candidate] = []
    for candidate in candidates:
        key = (candidate.provider, candidate.url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def normalize_vehicle_url(provider: str, url: str) -> str:
    """A vehicle's URL as a host record and the discovered list keep it."""

    if provider == "chase-sim":
        return normalize_chase_url(url)
    value = url.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("A PiCar --url must be an HTTP(S) URL.")
    return value


def _vehicle_heading(provider: str, url: str) -> str:
    return f"{_HEADINGS[provider]} at {chase_operator_url(url) if provider == 'chase-sim' else url}"


def discover_vehicle(*, provider: str, url: str, force: bool = False) -> CommandResult:
    """List a vehicle in vehicles status once it can take a host, or regardless with ``force``."""

    from ..hosts.runtime_hosts import discovered_vehicles, write_discovered_vehicles

    try:
        url = normalize_vehicle_url(provider, url)
    except ValueError as exc:
        return CommandResult(2, str(exc))
    heading = _vehicle_heading(provider, url)
    listed = discovered_vehicles()
    entry = {"provider": provider, "url": url}
    if entry in listed or (provider == "chase-sim" and url == default_chase_url()):
        return CommandResult(0, f"Already listed: {heading}")
    probe = _probe_candidate(Candidate(provider, url, "discovered"), timeout_s=DEFAULT_READINESS_TIMEOUT_S)
    # A simulator takes a host as hosts create requires; a PiCar once its machine answers.
    ready = probe.active if provider == "chase-sim" else _picar_state(probe) != "unreachable"
    if not ready and not force:
        name = "Chase simulator" if provider == "chase-sim" else "PiCar"
        return CommandResult(2, f"No {name} can take a host at {url}: {probe.error}")
    write_discovered_vehicles([*listed, entry])
    return CommandResult(0, f"Discovered {heading}" + ("" if ready else f", which cannot take a host now: {probe.error}"))


def remove_vehicle(*, url: str) -> CommandResult:
    """Stop listing a discovered vehicle; the default simulator stays."""

    from ..hosts.runtime_hosts import discovered_vehicles, write_discovered_vehicles

    def normalized(provider: str) -> str | None:
        try:
            return normalize_vehicle_url(provider, url)
        except ValueError:
            return None

    default = default_chase_url()
    if normalized("chase-sim") == default:
        return CommandResult(2, f"{chase_operator_url(default)} is the default simulator and is always listed.")
    listed = discovered_vehicles()
    removed = [vehicle for vehicle in listed if normalized(vehicle["provider"]) == vehicle["url"]]
    if not removed:
        names = ", ".join(_vehicle_heading(vehicle["provider"], vehicle["url"]) for vehicle in listed)
        return CommandResult(2, f"No discovered vehicle at {url}. " + (f"Discovered: {names}." if names else "None discovered."))
    write_discovered_vehicles([vehicle for vehicle in listed if vehicle not in removed])
    return CommandResult(0, "\n".join(f"Removed {_vehicle_heading(vehicle['provider'], vehicle['url'])}" for vehicle in removed))
