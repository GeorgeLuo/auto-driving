"""Independently callable static import-graph indicators."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from ..factors._utils import _sort_findings
from .context import AnalysisContext
from .evidence import LocatedNode, enrich_finding


FACTOR = "coupling"
INDICATOR_ID = "coupling.graph"
INDICATOR_VERSION = "1"
DETECTOR_IDS = {
    "external_import": f"{INDICATOR_ID}.external_import",
    "cycle": f"{INDICATOR_ID}.cycle",
    "fan_out": f"{INDICATOR_ID}.fan_out",
    "fan_in": f"{INDICATOR_ID}.fan_in",
    "syntax_error": f"{INDICATOR_ID}.syntax_error",
}


def analyze(context: AnalysisContext) -> dict[str, Any]:
    """Measure local import resolution and graph aggregates."""

    result = _coupling(context.trees(family="coupling"), context=context)
    for error in context.syntax_errors(family="coupling").values():
        finding = {
            "path": error.path,
            "line": int(error.line or 1),
            "kind": "syntax_error",
            "message": f"Python source could not be parsed: {error.message}",
        }
        result["findings"].append(
            enrich_finding(
                finding,
                context=context,
                indicator_id=DETECTOR_IDS["syntax_error"],
                indicator_version=INDICATOR_VERSION,
                path=error.path,
                pattern={"syntax_error": error.to_dict()},
                uncertainty=["syntax_error_prevents_ast_inspection"],
                inspection_question="Inspect the syntax error before interpreting import coupling.",
                primary_reason="syntax_error_no_ast_node",
            )
        )
        result["limitations"].append(
            f"{error.path} could not be parsed; imports from that file are unavailable."
        )
    result["findings"] = _sort_findings(result["findings"])
    result["limitations"] = sorted(set(result["limitations"]))
    return result


def _module_names(path: str) -> list[str]:
    parts = PurePosixPath(path).parts
    stem = parts[-1][:-3]
    module = parts[:-1] if stem == "__init__" else parts[:-1] + (stem,)
    names = [".".join(module)] if module else []
    # The repository's CLI is imported as either cli.automa_cli or automa_cli.
    if module and module[0] == "cli":
        names.append(".".join(module[1:]))
    return [name for name in names if name]


def _module(path: str) -> str:
    return _module_names(path)[0]


def _package(path: str) -> str:
    module = _module(path)
    return module if PurePosixPath(path).name == "__init__.py" else module.rpartition(".")[0]


def _index(trees: Mapping[str, ast.Module]) -> dict[str, str]:
    candidates: dict[str, list[str]] = defaultdict(list)
    for path in sorted(trees):
        for name in _module_names(path):
            candidates[name].append(path)
    return {name: sorted(paths)[0] for name, paths in candidates.items()}


def _lookup_resolution(name: str, index: Mapping[str, str]) -> dict[str, Any]:
    """Resolve a local module while retaining how much of its name matched."""

    requested = name
    candidate = name
    while candidate:
        if candidate in index:
            exact = candidate == requested
            return {
                "target": index[candidate],
                "resolution": "exact" if exact else "ancestor_fallback",
                "requested_name": requested,
                "matched_module": candidate,
                "unresolved_suffix": _unresolved_suffix(requested, candidate),
            }
        candidate = candidate.rpartition(".")[0]
    return {
        "target": None,
        "resolution": "unresolved",
        "requested_name": requested,
        "matched_module": None,
        "unresolved_suffix": requested or None,
    }


def _unresolved_suffix(requested: str, matched: str) -> str | None:
    if requested == matched:
        return None
    prefix = matched + "."
    if requested.startswith(prefix):
        return requested[len(prefix) :] or None
    return requested or None


def _from_resolution(
    path: str,
    node: ast.ImportFrom,
    imported: str,
    index: Mapping[str, str],
) -> dict[str, Any]:
    if node.level:
        package = [part for part in _package(path).split(".") if part]
        if node.level > 1:
            package = package[: max(0, len(package) - node.level + 1)]
        base_parts = package + [
            part for part in (node.module or "").split(".") if part
        ]
        base = ".".join(base_parts)
    else:
        base = node.module or ""
    if imported and imported != "*":
        child = f"{base}.{imported}" if base else imported
        child_resolution = _lookup_resolution(child, index)
        if child_resolution["resolution"] == "exact":
            return child_resolution

        # ``from module import symbol`` commonly resolves to the module that
        # owns the symbol.  Keep that edge distinct from parent fallback.
        base_resolution = _lookup_resolution(base, index)
        if base_resolution["resolution"] == "exact":
            return {
                **base_resolution,
                "resolution": "symbol_owner",
                "requested_name": child,
                "unresolved_suffix": _unresolved_suffix(
                    child, base_resolution["matched_module"]
                ),
            }
        return child_resolution
    return _lookup_resolution(base, index)


def _coupling(
    trees: Mapping[str, ast.Module], *, context: AnalysisContext | None = None
) -> dict[str, Any]:
    nodes = sorted(trees)
    index = _index(trees)
    edge_data: dict[tuple[str, str], dict[str, Any]] = {}
    edge_nodes: dict[tuple[str, str], list[LocatedNode]] = defaultdict(list)
    external: dict[tuple[str, int, str], dict[str, Any]] = {}
    external_nodes: dict[tuple[str, int, str], list[LocatedNode]] = defaultdict(list)
    for path in nodes:
        for node in ast.walk(trees[path]):
            if isinstance(node, ast.Import):
                imports = [
                    (alias.name, _lookup_resolution(alias.name, index), "import")
                    for alias in node.names
                ]
            elif isinstance(node, ast.ImportFrom):
                imports = [
                    (
                        "." * node.level
                        + (node.module or "")
                        + (f":{alias.name}" if alias.name != "*" else ""),
                        _from_resolution(path, node, alias.name, index),
                        "from",
                    )
                    for alias in node.names
                ]
            else:
                continue
            for name, resolution, kind in imports:
                target = resolution["target"]
                if target is None:
                    key = (path, int(node.lineno), name)
                    external.setdefault(
                        key,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "name": name,
                            "kind": "external",
                            **_resolution_fields(resolution),
                        },
                    )
                    external_nodes[key].append(LocatedNode(path, node, "coupling"))
                else:
                    key = (path, target)
                    _edge(
                        edge_data,
                        path,
                        target,
                        int(node.lineno),
                        kind,
                        name,
                        resolution,
                    )
                    edge_nodes[key].append(LocatedNode(path, node, "coupling"))

    edges = [edge_data[key] for key in sorted(edge_data)]
    fan_in = {path: 0 for path in nodes}
    fan_out = {path: 0 for path in nodes}
    adjacency = {path: set() for path in nodes}
    for edge in edges:
        adjacency[edge["source"]].add(edge["target"])
        fan_out[edge["source"]] += 1
        fan_in[edge["target"]] += 1
    cycles = _scc_cycles(adjacency)
    unresolved = [external[key] for key in sorted(external)]
    findings: list[dict[str, Any]] = []
    for item in unresolved:
        key = (item["path"], item["line"], item["name"])
        findings.append(
            _finding(
                context,
                {
                    "path": item["path"],
                    "line": item["line"],
                    "kind": "external_import",
                    "message": f"Import is not resolved to a supplied local module: {item['name']}",
                    "name": item["name"],
                    **_resolution_fields(item),
                },
                path=item["path"],
                node=external_nodes[key][0] if external_nodes[key] else None,
                related_nodes=tuple(external_nodes[key][1:]),
                pattern={"resolution": _resolution_fields(item)},
                inspection_question=(
                    "Inspect whether this unresolved import is available at the analyzed boundary."
                ),
            )
        )
    for cycle in cycles:
        cycle_edges = [
            edge
            for edge in edges
            if edge["source"] in cycle and edge["target"] in cycle
        ]
        related = [
            ref
            for edge in cycle_edges
            for ref in edge_nodes[(edge["source"], edge["target"])]
        ]
        line = min((edge["line"] for edge in cycle_edges), default=1)
        findings.append(
            _finding(
                context,
                {
                    "path": cycle[0],
                    "line": line,
                    "kind": "cycle",
                    "message": "Local import cycle component: " + ", ".join(cycle),
                    "members": cycle,
                    "paths": cycle,
                    "cycle_representation": "scc_members",
                },
                path=cycle[0],
                related_nodes=tuple(related),
                pattern={
                    "members": cycle,
                    "edges": [
                        {
                            "source": edge["source"],
                            "target": edge["target"],
                            "imports": edge["imports"],
                            "lines": edge["lines"],
                        }
                        for edge in cycle_edges
                    ],
                },
                primary_reason="graph_aggregate_no_single_source_range",
                inspection_question=(
                    "Inspect the listed import edges as a component; member ordering is not an import path."
                ),
            )
        )
    for path in nodes:
        if fan_out[path] >= 3:
            related = [
                ref
                for edge in edges
                if edge["source"] == path
                for ref in edge_nodes[(edge["source"], edge["target"])]
            ]
            findings.append(
                _finding(
                    context,
                    {
                        "path": path,
                        "line": 1,
                        "kind": "fan_out",
                        "message": f"Local dependency fan-out is {fan_out[path]} modules.",
                        "value": fan_out[path],
                    },
                    path=path,
                    related_nodes=tuple(related),
                    pattern={
                        "direction": "out",
                        "module": path,
                        "value": fan_out[path],
                    },
                    primary_reason="graph_aggregate_no_single_source_range",
                    inspection_question=(
                        "Inspect the related import sites and whether this module's dependency fan-out is intentional."
                    ),
                )
            )
        if fan_in[path] >= 3:
            related = [
                ref
                for edge in edges
                if edge["target"] == path
                for ref in edge_nodes[(edge["source"], edge["target"])]
            ]
            findings.append(
                _finding(
                    context,
                    {
                        "path": path,
                        "line": 1,
                        "kind": "fan_in",
                        "message": f"Local dependency fan-in is {fan_in[path]} modules.",
                        "value": fan_in[path],
                    },
                    path=path,
                    related_nodes=tuple(related),
                    pattern={
                        "direction": "in",
                        "module": path,
                        "value": fan_in[path],
                    },
                    primary_reason="graph_aggregate_no_single_source_range",
                    inspection_question=(
                        "Inspect the related import sites and whether this module's dependency fan-in is intentional."
                    ),
                )
            )
    metrics = {
        "module_count": len(nodes),
        "edge_count": len(edges),
        "external_import_count": len(unresolved),
        "cycle_count": len(cycles),
        "cyclic_node_count": sum(map(len, cycles)),
        "max_fan_in": max(fan_in.values(), default=0),
        "max_fan_out": max(fan_out.values(), default=0),
        "fan_in_hotspot_count": sum(value >= 3 for value in fan_in.values()),
        "fan_out_hotspot_count": sum(value >= 3 for value in fan_out.values()),
    }
    graph = {
        "nodes": nodes,
        "edges": edges,
        "fan": {"in": fan_in, "out": fan_out},
        "cycles": cycles,
        "cycle_representation": "scc_members",
        "unresolved_external": unresolved,
    }
    return {
        "status": "measured",
        "metrics": metrics,
        "graph": graph,
        "findings": findings,
        "limitations": [
            "Import resolution is AST-based and does not execute module search hooks or dynamic imports.",
            "Unresolved imports are separate observations; installability and runtime availability are not measured.",
            "Cycles are deterministic strongly connected component member sets, not every distinct runtime import path; member ordering does not imply an import path.",
        ],
    }


def _finding(
    context: AnalysisContext | None,
    finding: dict[str, Any],
    *,
    path: str,
    node: ast.AST | LocatedNode | None = None,
    related_nodes: tuple[LocatedNode, ...] = (),
    pattern: dict[str, Any] | None = None,
    primary_reason: str | None = None,
    inspection_question: str | None = None,
) -> dict[str, Any]:
    finding = dict(finding)
    detector_id = DETECTOR_IDS[finding["kind"]]
    if context is None:
        return finding
    if isinstance(node, LocatedNode):
        primary_path = node.path
        primary_node = node.node
    else:
        primary_path = path
        primary_node = node
    return enrich_finding(
        finding,
        context=context,
        indicator_id=detector_id,
        indicator_version=INDICATOR_VERSION,
        path=primary_path,
        node=primary_node,
        family="coupling",
        related_nodes=related_nodes,
        pattern=pattern or {},
        uncertainty=["static_import_graph"],
        inspection_question=inspection_question,
        primary_reason=primary_reason,
    )


def _edge(
    edges: dict[tuple[str, str], dict[str, Any]],
    source: str,
    target: str,
    line: int,
    kind: str,
    name: str,
    resolution: dict[str, Any],
) -> None:
    item = edges.setdefault(
        (source, target),
        {
            "source": source,
            "target": target,
            "line": line,
            "kind": kind,
            "imports": [],
            "lines": [],
            "resolution_details": [],
        },
    )
    item["imports"] = sorted(set(item["imports"]) | {name})
    item["lines"] = sorted(set(item["lines"]) | {line})
    detail = {"line": line, "import": name, **_resolution_fields(resolution)}
    if detail not in item["resolution_details"]:
        item["resolution_details"].append(detail)
        item["resolution_details"].sort(
            key=lambda value: (
                value["requested_name"],
                value["matched_module"] or "",
                value["unresolved_suffix"] or "",
                value["resolution"],
                value["line"],
                value["import"],
            )
        )
    _set_edge_resolution(item)


def _resolution_fields(resolution: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "resolution": resolution["resolution"],
        "requested_name": resolution["requested_name"],
        "matched_module": resolution["matched_module"],
        "unresolved_suffix": resolution["unresolved_suffix"],
    }


def _set_edge_resolution(edge: dict[str, Any]) -> None:
    details = edge["resolution_details"]
    if len(details) == 1:
        edge.update(_resolution_fields(details[0]))
        return
    resolutions = {detail["resolution"] for detail in details}
    edge.update(
        {
            "resolution": next(iter(resolutions)) if len(resolutions) == 1 else "mixed",
            "requested_name": None,
            "matched_module": None,
            "unresolved_suffix": None,
        }
    )


def _scc_cycles(adjacency: Mapping[str, set[str]]) -> list[list[str]]:
    """Tarjan's bounded linear traversal gives deterministic cyclic components."""

    counter = 0
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal counter
        indices[node] = low[node] = counter
        counter += 1
        stack.append(node)
        active.add(node)
        for child in sorted(adjacency[node]):
            if child not in indices:
                visit(child)
                low[node] = min(low[node], low[child])
            elif child in active:
                low[node] = min(low[node], indices[child])
        if low[node] == indices[node]:
            component: list[str] = []
            while True:
                child = stack.pop()
                active.remove(child)
                component.append(child)
                if child == node:
                    break
            component.sort()
            if len(component) > 1 or component[0] in adjacency[component[0]]:
                components.append(component)

    for node in sorted(adjacency):
        if node not in indices:
            visit(node)
    return sorted(components)


__all__ = [
    "DETECTOR_IDS",
    "FACTOR",
    "INDICATOR_ID",
    "INDICATOR_VERSION",
    "analyze",
    "_coupling",
    "_edge",
    "_from_resolution",
    "_index",
    "_lookup_resolution",
    "_module",
    "_module_names",
    "_package",
    "_resolution_fields",
    "_scc_cycles",
]
