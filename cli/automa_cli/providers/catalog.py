"""Provider operations registered against the provider metadata."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from . import metadata
from .metadata import ProviderMetadata
from .models import Candidate, ProbeResult, picar_state
from .probes import probe_chase, probe_picar, simulator_status


@dataclass(frozen=True)
class Provider(ProviderMetadata):
    probe: Callable[..., ProbeResult]
    status: Callable[[Candidate, Callable[[], float]], dict[str, Any]]
    can_take_host: Callable[[ProbeResult], bool]


def _picar_status(candidate: Candidate, remaining: Callable[[], float]) -> dict[str, Any]:
    probe = PROVIDERS[candidate.provider].probe(candidate, timeout_s=remaining())
    return {"provider": candidate.provider, "url": candidate.url, "state": picar_state(probe), "error": probe.error}


CHASE_SIM = Provider(**asdict(metadata.CHASE_SIM), probe=probe_chase, status=simulator_status,
                     can_take_host=lambda probe: probe.active)
PICAR = Provider(**asdict(metadata.PICAR), probe=probe_picar, status=_picar_status,
                 can_take_host=lambda probe: picar_state(probe) != "unreachable")
PROVIDERS = {provider.name: provider for provider in (CHASE_SIM, PICAR)}
