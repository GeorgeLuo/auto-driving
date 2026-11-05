"""Coupling factor: imports and public contracts through one shared context."""

from __future__ import annotations

from typing import Any

from ..indicators.context import AnalysisContext
from ..indicators import contracts as _contracts_indicator
from ..indicators import coupling as _coupling_indicator
from ._utils import _norm


def analyze_coupling(
    sources: dict[str, str], *, revision: str | None = None
) -> dict[str, dict[str, Any]]:
    """Measure imports and public contracts through one shared context."""

    source_map = {
        _norm(path): text if isinstance(text, (str, bytes)) else str(text)
        for path, text in sources.items()
    }
    return analyze_coupling_context(
        AnalysisContext.from_sources(source_map, revision=revision)
    )


def analyze_coupling_context(
    context: AnalysisContext,
) -> dict[str, dict[str, Any]]:
    """Run both coupling indicator families on an existing source context."""

    return {
        "coupling": _coupling_indicator.analyze(context),
        "contracts": _contracts_indicator.analyze(context),
    }


__all__ = ["analyze_coupling", "analyze_coupling_context"]
