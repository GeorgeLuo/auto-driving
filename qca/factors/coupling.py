"""Compatibility wrapper for independently testable coupling indicators."""

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


# Preserve the helper names that were part of the factor's practical import
# surface while keeping their implementation in the indicator owners.
_module_names = _coupling_indicator._module_names
_module = _coupling_indicator._module
_package = _coupling_indicator._package
_index = _coupling_indicator._index
_lookup_resolution = _coupling_indicator._lookup_resolution
_unresolved_suffix = _coupling_indicator._unresolved_suffix
_from_resolution = _coupling_indicator._from_resolution
_coupling = _coupling_indicator._coupling
_edge = _coupling_indicator._edge
_resolution_fields = _coupling_indicator._resolution_fields
_set_edge_resolution = _coupling_indicator._set_edge_resolution
_scc_cycles = _coupling_indicator._scc_cycles

_contracts = _contracts_indicator._contracts
_public_callables = _contracts_indicator._public_callables
_signature = _contracts_indicator._signature
_return_shapes = _contracts_indicator._return_shapes
_cli_call = _contracts_indicator._cli_call
_cli_dest = _contracts_indicator._cli_dest
_add_cli_surface = _contracts_indicator._add_cli_surface
_literal = _contracts_indicator._literal
_expr = _contracts_indicator._expr


__all__ = ["analyze_coupling", "analyze_coupling_context"]
