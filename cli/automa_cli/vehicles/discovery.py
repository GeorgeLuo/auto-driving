"""``automa vehicles discover`` and ``remove``, and the vehicles status lists."""

from __future__ import annotations

from ..providers.registry import CHASE_SIM, PROVIDERS
from ..providers.models import DEFAULT_READINESS_TIMEOUT_S, Candidate
from ..shared.io.lock import registry
from ..simulators.store import created_simulators, write_simulator_record
from .models import VehicleResult
from .store import discovered_vehicles, write_discovered_vehicles


def listed_vehicles() -> list[Candidate]:
    """Where vehicles status looks: each created simulator still listed, then each discovered vehicle."""

    return _dedupe_candidates([*_created_candidates(), *_discovered_candidates()])


def normalized_url(provider: str, url: str) -> str | None:
    """``url`` in the form ``provider`` lists vehicles by, or ``None`` when it is not that provider's URL."""

    try:
        return PROVIDERS[provider].normalize_url(url)
    except ValueError:
        return None


def _created_candidates() -> list[Candidate]:
    return [
        Candidate(CHASE_SIM.name, simulator["url"], "created")
        for simulator in created_simulators()
        if simulator.get("listed", True)
    ]


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
    """List a vehicle in vehicles status once it can take a host, or regardless with ``force``.

    A simulator that ``simulators create`` started is already listed. After
    ``vehicles remove``, discovering it probes it and lists it again on its
    simulator record, without writing a discovered entry. The probe stays
    outside the registry lock, and the list is read again before it is written.
    """

    vehicle = PROVIDERS[provider]
    try:
        url = vehicle.normalize_url(url)
    except ValueError as exc:
        return VehicleResult(2, "error", error=str(exc))
    with registry():
        if _already_listed(provider, url):
            return VehicleResult(0, "listed", url=url, provider=provider)
    probe = vehicle.probe(Candidate(provider, url, "discovered"), timeout_s=DEFAULT_READINESS_TIMEOUT_S)
    ready = vehicle.can_take_host(probe)
    with registry():
        if _already_listed(provider, url):
            return VehicleResult(0, "listed", url=url, provider=provider)
        if not ready and not force:
            return VehicleResult(2, "unready", url=url, provider=provider, ready=False, error=probe.error)
        _record_discovered(provider, url)
    return VehicleResult(0, "discovered", url=url, provider=provider, ready=ready, error=probe.error)


def remove_vehicle(*, url: str) -> VehicleResult:
    """Stop listing a vehicle. A created simulator keeps running, marked unlisted on its record."""

    with registry():
        return _remove_listed(url)


def _already_listed(provider: str, url: str) -> bool:
    created = _created_record(url) if provider == CHASE_SIM.name else None
    entry = {"provider": provider, "url": url}
    return entry in discovered_vehicles() or (created is not None and created.get("listed", True))


def _record_discovered(provider: str, url: str) -> None:
    created = _created_record(url) if provider == CHASE_SIM.name else None
    if created is not None:
        write_simulator_record({**created, "listed": True})
        return
    entry = {"provider": provider, "url": url}
    write_discovered_vehicles([*discovered_vehicles(), entry])


def _remove_listed(url: str) -> VehicleResult:
    discovered = discovered_vehicles()
    removed = [vehicle for vehicle in discovered if normalized_url(vehicle["provider"], url) == vehicle["url"]]
    created = _created_record(normalized_url(CHASE_SIM.name, url))
    if created is not None and not created.get("listed", True):
        created = None
    if not removed and created is None:
        listed = tuple({"provider": item.provider, "url": item.url} for item in listed_vehicles())
        return VehicleResult(2, "missing", url=url, vehicles=listed)
    if removed:
        write_discovered_vehicles([vehicle for vehicle in discovered if vehicle not in removed])
    if created is not None:
        write_simulator_record({**created, "listed": False})
        entry = {"provider": CHASE_SIM.name, "url": created["url"]}
        if entry not in removed:
            removed.append(entry)
    return VehicleResult(0, "removed", vehicles=tuple(removed))


def _created_record(url: str | None) -> dict[str, object] | None:
    return next((record for record in created_simulators() if record["url"] == url), None)
