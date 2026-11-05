"""``automa vehicles discover`` and ``remove``, and the vehicles status lists."""

from __future__ import annotations

from typing import Any

from ..providers.registry import CHASE_SIM, PROVIDERS
from ..providers.models import DEFAULT_READINESS_TIMEOUT_S, Candidate
from ..simulators.store import created_simulators, simulator_records
from .models import VehicleResult
from .store import (
    discovered_vehicles,
    simulator_mark,
    untracked_simulators,
    write_discovered_vehicles,
    write_untracked_simulators,
)


def listed_vehicles() -> list[Candidate]:
    """Where vehicles status looks: each created simulator still listed, then each discovered vehicle."""

    return _dedupe_candidates([*_created_candidates(), *_discovered_candidates()])


def _created_candidates() -> list[Candidate]:
    return [
        Candidate(CHASE_SIM.name, simulator["url"], "created")
        for simulator in created_simulators()
        if not _is_untracked(simulator)
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
    ``vehicles remove``, discovering it probes it and clears its mark, without
    writing a discovered entry.
    """

    vehicle = PROVIDERS[provider]
    try:
        url = vehicle.normalize_url(url)
    except ValueError as exc:
        return VehicleResult(2, "error", error=str(exc))
    created = _created_record(url) if provider == CHASE_SIM.name else None
    if created is not None and not _is_untracked(created):
        return VehicleResult(0, "listed", url=url, provider=provider)
    listed = discovered_vehicles()
    entry = {"provider": provider, "url": url}
    if created is None and entry in listed:
        return VehicleResult(0, "listed", url=url, provider=provider)
    probe = vehicle.probe(Candidate(provider, url, "discovered"), timeout_s=DEFAULT_READINESS_TIMEOUT_S)
    ready = vehicle.can_take_host(probe)
    if not ready and not force:
        return VehicleResult(2, "unready", url=url, provider=provider, ready=False, error=probe.error)
    if created is not None:
        _forget_untracked(created)
        return VehicleResult(0, "discovered", url=url, provider=provider, ready=ready, error=probe.error)
    write_discovered_vehicles([*listed, entry])
    return VehicleResult(0, "discovered", url=url, provider=provider, ready=ready, error=probe.error)


def remove_vehicle(*, url: str) -> VehicleResult:
    """Stop listing a vehicle. A created simulator keeps running."""

    discovered = discovered_vehicles()
    removed = [vehicle for vehicle in discovered if _same_url(vehicle["provider"], url, vehicle["url"])]
    created = [
        record for record in created_simulators()
        if _same_url(CHASE_SIM.name, url, record.get("url")) and not _is_untracked(record)
    ]
    if not removed and not created:
        return VehicleResult(2, "missing", url=url, vehicles=_entries(listed_vehicles()))
    if removed:
        write_discovered_vehicles([vehicle for vehicle in discovered if vehicle not in removed])
    for record in created:
        _remember_untracked(record)
    return VehicleResult(0, "removed", vehicles=_entries(_dedupe_candidates([
        *(Candidate(vehicle["provider"], vehicle["url"], "discovered") for vehicle in removed),
        *(Candidate(CHASE_SIM.name, record["url"], "created") for record in created),
    ])))


def _created_record(url: str) -> dict[str, Any] | None:
    return next((record for record in created_simulators() if record.get("url") == url), None)


def _same_url(provider: str, given: str, stored: object) -> bool:
    if not isinstance(stored, str):
        return False
    try:
        return PROVIDERS[provider].normalize_url(given) == stored
    except ValueError:
        return False


def _entries(candidates: list[Candidate]) -> tuple[dict[str, str], ...]:
    """The ``{provider, url}`` a result names each vehicle by."""

    return tuple({"provider": candidate.provider, "url": candidate.url} for candidate in candidates)


def _is_untracked(record: dict[str, Any]) -> bool:
    mark = simulator_mark(record)
    return mark is not None and mark in untracked_simulators()


def _marks_for_records() -> list[dict[str, Any]]:
    """Marks that still name a simulator record. One whose record is gone is dropped."""

    identities = []
    for record in simulator_records():
        mark = simulator_mark(record)
        if mark is not None:
            identities.append(mark)
    return [mark for mark in untracked_simulators() if mark in identities]


def _remember_untracked(record: dict[str, Any]) -> None:
    mark = simulator_mark(record)
    if mark is None:
        return
    marks = _marks_for_records()
    if mark not in marks:
        marks.append(mark)
    write_untracked_simulators(marks)


def _forget_untracked(record: dict[str, Any]) -> None:
    mark = simulator_mark(record)
    write_untracked_simulators([item for item in _marks_for_records() if item != mark])
