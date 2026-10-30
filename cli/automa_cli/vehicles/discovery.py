"""``automa vehicles discover`` and ``remove``, and the vehicles status lists."""

from __future__ import annotations

import os

from implementations.vehicle.chase_sim.defaults import (
    CHASE_UI_WS_URL_ENV,
    DEFAULT_CHASE_UI_WS_URL,
)

from ..providers.catalog import CHASE_SIM, PROVIDERS
from ..providers.probes import (
    DEFAULT_READINESS_TIMEOUT_S,
    Candidate,
    normalize_chase_url,
)
from .models import VehicleResult
from ..simulators.store import created_simulators
from .store import discovered_vehicles, write_discovered_vehicles


def default_chase_url() -> str:
    """The simulator status always lists: ``CHASE_UI_WS_URL``, else localhost:5050."""

    return normalize_chase_url(os.environ.get(CHASE_UI_WS_URL_ENV, DEFAULT_CHASE_UI_WS_URL))


def listed_vehicles() -> list[Candidate]:
    """Where vehicles status looks: the default simulator, each created simulator, then each discovered vehicle."""

    return _dedupe_candidates([
        Candidate(CHASE_SIM.name, default_chase_url(), "default"), *_created_candidates(), *_discovered_candidates(),
    ])


def _created_candidates() -> list[Candidate]:
    return [Candidate(CHASE_SIM.name, simulator["url"], "created") for simulator in created_simulators()]


def _discovered_candidates() -> list[Candidate]:
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


def discover_vehicle(*, provider: str, url: str, force: bool = False) -> VehicleResult:
    """List a vehicle in vehicles status once it can take a host, or regardless with ``force``."""

    vehicle = PROVIDERS[provider]
    try:
        url = vehicle.normalize_url(url)
    except ValueError as exc:
        return VehicleResult(2, "error", error=str(exc))
    listed = discovered_vehicles()
    entry = {"provider": provider, "url": url}
    if entry in listed or (provider == CHASE_SIM.name and url == default_chase_url()):
        return VehicleResult(0, "listed", url=url, provider=provider)
    probe = vehicle.probe(Candidate(provider, url, "discovered"), timeout_s=DEFAULT_READINESS_TIMEOUT_S)
    ready = vehicle.can_take_host(probe)
    if not ready and not force:
        return VehicleResult(2, "unready", url=url, provider=provider, ready=False, error=probe.error)
    write_discovered_vehicles([*listed, entry])
    return VehicleResult(0, "discovered", url=url, provider=provider, ready=ready, error=probe.error)


def remove_vehicle(*, url: str) -> VehicleResult:
    """Stop listing a discovered vehicle; the default simulator stays."""

    def normalized(provider: str) -> str | None:
        try:
            return PROVIDERS[provider].normalize_url(url)
        except ValueError:
            return None

    default = default_chase_url()
    if normalized(CHASE_SIM.name) == default:
        return VehicleResult(2, "default", url=default)
    listed = discovered_vehicles()
    removed = [vehicle for vehicle in listed if normalized(vehicle["provider"]) == vehicle["url"]]
    if not removed:
        return VehicleResult(2, "missing", url=url, vehicles=tuple(listed))
    write_discovered_vehicles([vehicle for vehicle in listed if vehicle not in removed])
    return VehicleResult(0, "removed", vehicles=tuple(removed))
