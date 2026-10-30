"""What differs between the vehicles automa hosts, one entry per ``--provider``.

Code that treats a simulator and a PiCar differently reads the difference
here instead of comparing provider names, so a new kind of vehicle is one
more entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from implementations.vehicle.chase_sim.car import VEHICLE_KIND as CHASE_VEHICLE_KIND
from implementations.vehicle.picar.car import VEHICLE_KIND as PICAR_VEHICLE_KIND

from .probes import (
    Candidate,
    ProbeResult,
    chase_operator_url,
    normalize_chase_url,
    picar_state,
    probe_chase,
    probe_picar,
    simulator_status,
)


@dataclass(frozen=True)
class Provider:
    name: str
    # Names the --provider that discover and hosts create take for the vehicle.
    heading: str
    # What can or cannot take a host, in discover and hosts create.
    machine: str
    # What a host runs for, in hosts status and hosts create.
    label: str
    vehicle_kind: str
    # The connection key a host record keeps the vehicle's URL under.
    url_key: str
    # How a host's controller reaches the vehicle, in each staged activation.
    runtime_kind: str
    # Whether automa runs the vehicle's host on this machine.
    local_host: bool
    normalize_url: Callable[[str], str]
    # The page an operator opens for the vehicle.
    operator_url: Callable[[str], str]
    probe: Callable[..., ProbeResult]
    # The vehicle's entry in vehicles status.
    status: Callable[[Candidate, Callable[[], float]], dict[str, Any]]
    # A simulator takes a host as hosts create requires; a PiCar once its machine answers.
    can_take_host: Callable[[ProbeResult], bool]

    def heading_at(self, url: str) -> str:
        return f"{self.heading} at {self.operator_url(url)}"


def _normalize_picar_url(url: str) -> str:
    value = url.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("A PiCar --url must be an HTTP(S) URL.")
    return value


def _picar_status(candidate: Candidate, remaining: Callable[[], float]) -> dict[str, Any]:
    probe = PROVIDERS[candidate.provider].probe(candidate, timeout_s=remaining())
    return {"provider": candidate.provider, "url": candidate.url, "state": picar_state(probe), "error": probe.error}


CHASE_SIM = Provider(
    name="chase-sim",
    heading="Simulator (chase-sim)",
    machine="Chase simulator",
    label="chaser",
    vehicle_kind=CHASE_VEHICLE_KIND,
    url_key="ws_url",
    runtime_kind="ws_cli_controller",
    local_host=True,
    normalize_url=normalize_chase_url,
    operator_url=chase_operator_url,
    probe=probe_chase,
    status=simulator_status,
    can_take_host=lambda probe: probe.active,
)
PICAR = Provider(
    name="picar",
    heading="PiCar (picar)",
    machine="PiCar",
    label="PiCar",
    vehicle_kind=PICAR_VEHICLE_KIND,
    url_key="base_url",
    runtime_kind="onboard_controller",
    local_host=False,
    normalize_url=_normalize_picar_url,
    operator_url=lambda url: url,
    probe=probe_picar,
    status=_picar_status,
    can_take_host=lambda probe: picar_state(probe) != "unreachable",
)
PROVIDERS = {provider.name: provider for provider in (CHASE_SIM, PICAR)}
