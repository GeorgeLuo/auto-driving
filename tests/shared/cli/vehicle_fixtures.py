"""Stand-ins for a vehicle the tests do not run."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import replace
from unittest.mock import Mock, patch

from cli.automa_cli.providers.catalog import PROVIDERS
from cli.automa_cli.providers.probes import ProbeResult


def probe_returning(probe: ProbeResult) -> AbstractContextManager[object]:
    """Make the probe of ``probe``'s provider return it; every caller reads the probe from ``PROVIDERS``."""

    provider = PROVIDERS[probe.candidate.provider]
    return patch.dict(PROVIDERS, {provider.name: replace(provider, probe=Mock(return_value=probe))})
