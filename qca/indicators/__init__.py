"""Small, explicit core surface for QCA indicator implementations.

``INDICATOR_REGISTRY`` lists the shipped detector identities.  It is an
ordinary mapping assembled from the indicator modules named below: there is
no import scanning, decorator registration, or plugin loading.
"""

from __future__ import annotations

from dataclasses import dataclass

from .context import (
    AnalysisContext,
    SourceBlock,
    SourceLocation,
    SyntaxErrorInfo,
    normalize_path,
    normalize_source,
)
from .evidence import (
    LocatedNode,
    build_evidence,
    enrich_finding,
)
from . import contracts
from . import coupling
from . import functional_style
from . import functionality
from . import lifecycle
from . import patterns
from . import redundancy
from . import test_effectiveness


@dataclass(frozen=True)
class IndicatorSpec:
    """Stable identity for one explicitly listed detector indicator."""

    id: str
    version: str


def _module_entries(*modules: object) -> dict[str, IndicatorSpec]:
    registry: dict[str, IndicatorSpec] = {}
    for module in modules:
        indicator_id = str(getattr(module, "INDICATOR_ID"))
        version = str(getattr(module, "INDICATOR_VERSION"))
        registry[indicator_id] = IndicatorSpec(indicator_id, version)
        detector_ids = getattr(module, "DETECTOR_IDS")
        for detector_id in detector_ids.values():
            registry[str(detector_id)] = IndicatorSpec(str(detector_id), version)
    return registry


# Wrapper-owned syntax-error identity is listed here because it is fanned out
# across the four structure factors rather than living in one detector module.
INDICATOR_REGISTRY: dict[str, IndicatorSpec] = {
    **_module_entries(
        redundancy,
        patterns,
        functional_style,
        functionality,
        coupling,
        contracts,
        test_effectiveness,
        lifecycle,
    ),
    "structure.syntax_error": IndicatorSpec("structure.syntax_error", "1"),
}


__all__ = [
    "AnalysisContext",
    "INDICATOR_REGISTRY",
    "IndicatorSpec",
    "LocatedNode",
    "SourceBlock",
    "SourceLocation",
    "SyntaxErrorInfo",
    "build_evidence",
    "enrich_finding",
    "normalize_path",
    "normalize_source",
]
