"""Provider operations registered against the provider metadata."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any, Callable

from . import metadata
from .metadata import ProviderMetadata
from .models import Candidate, ProbeResult, picar_state
from .probes import probe_chase, probe_picar, simulator_status
from .urls import require_positive_timeout


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


def probe_candidates(candidates: list[Candidate], timeout_s: float) -> list[dict[str, Any]]:
    """Each candidate's status, probed together under one deadline, in candidate order.

    The loop lives here, beside the provider table, because ``probes`` cannot
    import this module: this module imports ``probes`` to build that table.
    """

    deadline = time.monotonic() + require_positive_timeout(timeout_s)

    def remaining() -> float:
        # The client refuses a zero timeout; a spent budget times out at once instead.
        return max(deadline - time.monotonic(), 1e-3)

    with ThreadPoolExecutor() as pool:
        probes = [
            pool.submit(PROVIDERS[candidate.provider].status, candidate, remaining)
            for candidate in candidates
        ]
        return [probe.result() for probe in probes]
