"""Static coupling and contract measurements for Python source snapshots."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any


def analyze_coupling(sources: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Measure local imports and static public contracts without importing code.

    ``sources`` contains repository-relative paths and source text.  Findings
    are concrete inspection candidates, not a compatibility proof or quality
    grade.  The returned ``contracts.surface`` descriptors intentionally do
    not contain line numbers so a moved declaration is not a contract change.
    """
    source_map = {_norm(path): text if isinstance(text, str) else str(text)
                  for path, text in sources.items()}
    trees: dict[str, ast.Module] = {}
    errors: list[dict[str, Any]] = []
    for path in sorted(source_map):
        if not path.endswith(".py"):
            continue
        try:
            trees[path] = ast.parse(source_map[path], filename=path)
        except SyntaxError as exc:
            errors.append({"path": path, "line": int(exc.lineno or 1),
                           "message": str(exc.msg or "syntax error")})

    coupling = _coupling(trees)
    contracts = _contracts(trees)
    for error in errors:
        finding = {"path": error["path"], "line": error["line"],
                   "kind": "syntax_error",
                   "message": f"Python source could not be parsed: {error['message']}"}
        coupling["findings"].append(finding)
        contracts["findings"].append(dict(finding))
        coupling["limitations"].append(
            f"{error['path']} could not be parsed; imports from that file are unavailable."
        )
        contracts["limitations"].append(
            f"{error['path']} could not be parsed; its public contract is unavailable."
        )
    for factor in (coupling, contracts):
        factor["findings"] = _sort_findings(factor["findings"])
        factor["limitations"] = sorted(set(factor["limitations"]))
    return {"coupling": coupling, "contracts": contracts}


def _norm(path: str) -> str:
    value = str(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value or "."


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


def _index(trees: dict[str, ast.Module]) -> dict[str, str]:
    candidates: dict[str, list[str]] = defaultdict(list)
    for path in sorted(trees):
        for name in _module_names(path):
            candidates[name].append(path)
    return {name: sorted(paths)[0] for name, paths in candidates.items()}


def _lookup(name: str, index: dict[str, str]) -> str | None:
    """Resolve exact local names before falling back to local package parents."""
    while name:
        if name in index:
            return index[name]
        name = name.rpartition(".")[0]
    return None


def _from_target(path: str, node: ast.ImportFrom, imported: str,
                 index: dict[str, str]) -> str | None:
    if node.level:
        package = [part for part in _package(path).split(".") if part]
        if node.level > 1:
            package = package[:max(0, len(package) - node.level + 1)]
        base_parts = package + ([part for part in (node.module or "").split(".") if part])
        base = ".".join(base_parts)
    else:
        base = node.module or ""
    if imported and imported != "*":
        child = f"{base}.{imported}" if base else imported
        target = _lookup(child, index)
        if target is not None:
            return target
    return _lookup(base, index)


def _coupling(trees: dict[str, ast.Module]) -> dict[str, Any]:
    nodes = sorted(trees)
    index = _index(trees)
    edge_data: dict[tuple[str, str], dict[str, Any]] = {}
    external: dict[tuple[str, int, str], dict[str, Any]] = {}
    for path in nodes:
        for node in ast.walk(trees[path]):
            if isinstance(node, ast.Import):
                imports = [(alias.name, _lookup(alias.name, index), "import")
                           for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [("." * node.level + (node.module or "") +
                            (f":{alias.name}" if alias.name != "*" else ""),
                            _from_target(path, node, alias.name, index), "from")
                           for alias in node.names]
            else:
                continue
            for name, target, kind in imports:
                if target is None:
                    key = (path, int(node.lineno), name)
                    external.setdefault(key, {"path": path, "line": int(node.lineno),
                                              "name": name, "kind": "external"})
                else:
                    _edge(edge_data, path, target, int(node.lineno), kind, name)

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
    findings: list[dict[str, Any]] = [
        {"path": item["path"], "line": item["line"], "kind": "external_import",
         "message": f"Import is not resolved to a supplied local module: {item['name']}",
         "name": item["name"]}
        for item in unresolved
    ]
    for cycle in cycles:
        line = min((edge["line"] for edge in edges
                    if edge["source"] in cycle and edge["target"] in cycle), default=1)
        findings.append({"path": cycle[0], "line": line, "kind": "cycle",
                         "message": "Local import cycle: " + " -> ".join(cycle + [cycle[0]]),
                         "members": cycle, "paths": cycle})
    for path in nodes:
        if fan_out[path] >= 3:
            findings.append({"path": path, "line": 1, "kind": "fan_out",
                             "message": f"Local dependency fan-out is {fan_out[path]} modules.",
                             "value": fan_out[path]})
        if fan_in[path] >= 3:
            findings.append({"path": path, "line": 1, "kind": "fan_in",
                             "message": f"Local dependency fan-in is {fan_in[path]} modules.",
                             "value": fan_in[path]})
    metrics = {
        "module_count": len(nodes), "edge_count": len(edges),
        "external_import_count": len(unresolved),
        "cycle_count": len(cycles), "cyclic_node_count": sum(map(len, cycles)),
        "max_fan_in": max(fan_in.values(), default=0),
        "max_fan_out": max(fan_out.values(), default=0),
        "fan_in_hotspot_count": sum(value >= 3 for value in fan_in.values()),
        "fan_out_hotspot_count": sum(value >= 3 for value in fan_out.values()),
    }
    graph = {"nodes": nodes, "edges": edges,
             "fan": {"in": fan_in, "out": fan_out},
             "cycles": cycles,
             "unresolved_external": unresolved}
    return {
        "status": "measured", "metrics": metrics, "graph": graph,
        "findings": findings,
        "limitations": [
            "Import resolution is AST-based and does not execute module search hooks or dynamic imports.",
            "Unresolved imports are separate observations; installability and runtime availability are not measured.",
            "Cycles are deterministic strongly connected components, not every distinct runtime import path.",
        ],
    }


def _edge(edges: dict[tuple[str, str], dict[str, Any]], source: str, target: str,
          line: int, kind: str, name: str) -> None:
    item = edges.setdefault((source, target), {"source": source, "target": target,
                                                "line": line, "kind": kind,
                                                "imports": [], "lines": []})
    item["imports"] = sorted(set(item["imports"]) | {name})
    item["lines"] = sorted(set(item["lines"]) | {line})


def _scc_cycles(adjacency: dict[str, set[str]]) -> list[list[str]]:
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


def _contracts(trees: dict[str, ast.Module]) -> dict[str, Any]:
    surface: dict[str, dict[str, Any]] = {}
    callables: dict[str, dict[str, Any]] = {}
    returns: dict[str, list[dict[str, Any]]] = {}
    arguments: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for path in sorted(trees):
        for node, qualname in _public_callables(trees[path]):
            key = f"{path}::{qualname}"
            descriptor = _signature(node)
            shapes = _return_shapes(node)
            if shapes:
                descriptor["return_dict_keys"] = [shape["keys"] for shape in shapes]
                returns[key] = shapes
            callables[key] = descriptor
            surface[key] = descriptor
            findings.append({"path": path, "line": int(node.lineno),
                             "kind": "callable_contract",
                             "message": f"Public callable contract measured: {qualname}",
                             "symbol": key})
            if shapes:
                findings.append({"path": path, "line": int(node.lineno),
                                 "kind": "return_shape",
                                 "message": f"Direct dict return shape measured for {qualname}: "
                                 + "; ".join(",".join(shape["keys"]) for shape in shapes),
                                 "symbol": key})
        for call in sorted((node for node in ast.walk(trees[path]) if isinstance(node, ast.Call)),
                           key=lambda node: (node.lineno, node.col_offset)):
            parsed = _cli_call(path, call)
            if parsed is None:
                continue
            (arguments if parsed[0] == "argument" else commands).append(parsed[1])

    _add_cli_surface(surface, arguments, "argument")
    _add_cli_surface(surface, commands, "command")
    for item in arguments:
        findings.append({"path": item["path"], "line": item["line"],
                         "kind": "cli_argument",
                         "message": "CLI argument declaration measured: " + ", ".join(item["flags"]),
                         "flags": item["flags"]})
    for item in commands:
        findings.append({"path": item["path"], "line": item["line"],
                         "kind": "cli_command",
                         "message": f"CLI command declaration measured: {item['name']}",
                         "name": item["name"]})
    metrics = {
        "public_callable_count": len(callables),
        "return_shape_count": sum(map(len, returns.values())),
        "cli_argument_count": len(arguments), "cli_command_count": len(commands),
        "surface_count": len(surface),
    }
    return {
        "status": "measured", "metrics": metrics,
        "surface": dict(sorted(surface.items())),
        "cli_arguments": arguments, "cli_commands": commands,
        "findings": findings,
        "limitations": [
            "Public contracts are static AST approximations; runtime decorators, dispatch, inheritance, and annotations are not evaluated.",
            "Returned shapes include only direct dict literals in public callable return statements.",
            "CLI inventory recognizes literal argparse-style add_argument and add_parser calls; it is not a schema or compatibility proof.",
        ],
    }


def _public_callables(tree: ast.Module) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    result: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]] = []

    def visit(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    result.append((node, prefix + node.name))
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                visit(node.body, prefix + node.name + ".")
            elif isinstance(node, ast.If):
                visit(node.body, prefix)
                visit(node.orelse, prefix)
            elif isinstance(node, ast.Try):
                visit(node.body, prefix)
                visit(node.orelse, prefix)
                visit(node.finalbody, prefix)
                for handler in node.handlers:
                    visit(handler.body, prefix)

    visit(tree.body)
    return sorted(result, key=lambda item: (item[0].lineno, item[1]))


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    args = node.args
    positional_only = [arg.arg for arg in args.posonlyargs]
    positional_or_keyword = [arg.arg for arg in args.args]
    positional = positional_only + positional_or_keyword
    defaults = {arg: _expr(value) for arg, value in
                zip(positional[-len(args.defaults):], args.defaults)} if args.defaults else {}
    kwonly = [arg.arg for arg in args.kwonlyargs]
    kw_defaults = {arg.arg: _expr(value) for arg, value in
                   zip(args.kwonlyargs, args.kw_defaults) if value is not None}
    return {
        "kind": "callable", "name": node.name,
        "async": isinstance(node, ast.AsyncFunctionDef),
        "positional": positional, "positional_only": positional_only,
        "positional_or_keyword": positional_or_keyword,
        "kwonly": kwonly,
        "defaults": defaults, "kwonly_defaults": kw_defaults,
        "vararg": args.vararg.arg if args.vararg else None,
        "kwarg": args.kwarg.arg if args.kwarg else None,
    }


class _Returns(ast.NodeVisitor):
    def __init__(self) -> None:
        self.values: list[ast.Dict] = []

    def visit_Return(self, node: ast.Return) -> None:
        if isinstance(node.value, ast.Dict):
            self.values.append(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef


def _return_shapes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    visitor = _Returns()
    for statement in node.body:
        visitor.visit(statement)
    shapes: set[tuple[str, ...]] = set()
    for value in visitor.values:
        keys = []
        for key in value.keys:
            if key is None:
                keys.append("<unpacked>")
            elif isinstance(key, ast.Constant) and isinstance(key.value, (str, int, float, bool, type(None))):
                keys.append(str(key.value))
            else:
                keys.append("<dynamic>")
        shapes.add(tuple(sorted(set(keys))))
    return [{"keys": list(keys)} for keys in sorted(shapes)]


def _cli_call(path: str, call: ast.Call) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in {"add_argument", "add_parser"}:
        return None
    values = [_literal(arg) for arg in call.args]
    options = {keyword.arg: _literal(keyword.value) for keyword in call.keywords if keyword.arg}
    if call.func.attr == "add_parser":
        if not values or not isinstance(values[0], str):
            return None
        return "command", {"path": path, "line": int(call.lineno), "name": values[0], "options": options}
    flags = sorted({value for value in values if isinstance(value, str) and value.startswith("-")})
    if not flags:
        return None
    dest = options.get("dest") if isinstance(options.get("dest"), str) else None
    if dest is None:
        dest = _cli_dest(flags)
    return "argument", {"path": path, "line": int(call.lineno), "flags": flags,
                         "dest": dest, "options": options}


def _cli_dest(flags: list[str]) -> str | None:
    """Match argparse's dest inference from the first long option, then short."""
    for flag in flags:
        if flag.startswith("--") and len(flag) > 2:
            return flag[2:].replace("-", "_")
    for flag in flags:
        if flag.startswith("-") and not flag.startswith("--") and len(flag) > 1:
            return flag[1:].replace("-", "_")
    return None


def _add_cli_surface(surface: dict[str, dict[str, Any]], items: list[dict[str, Any]], kind: str) -> None:
    seen: defaultdict[str, int] = defaultdict(int)
    for item in sorted(items, key=lambda value: (value["path"], value["line"], value.get("name", ""), value.get("flags", []))):
        if kind == "argument":
            label = item["dest"] or "|".join(item["flags"])
            base = f"{item['path']}::cli-argument:{label}"
            descriptor = {"kind": "cli_argument", "flags": item["flags"],
                          "dest": item["dest"], "options": item["options"]}
        else:
            base = f"{item['path']}::cli-command:{item['name']}"
            descriptor = {"kind": "cli_command", "name": item["name"], "options": item["options"]}
        seen[base] += 1
        surface[base if seen[base] == 1 else f"{base}#{seen[base]}"] = descriptor


def _literal(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool, type(None))):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [_literal(item) for item in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.Dict):
        return {key.value: _literal(value) for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    return "<dynamic>"


def _expr(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<dynamic>"


def _sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(findings, key=lambda item: (str(item.get("path", "")),
                                               int(item.get("line", 0)),
                                               str(item.get("kind", "")),
                                               str(item.get("message", ""))))
