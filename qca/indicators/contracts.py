"""Independently callable static public-contract indicators."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .context import AnalysisContext
from .evidence import LocatedNode, enrich_finding


FACTOR = "contracts"
INDICATOR_ID = "coupling.contracts"
INDICATOR_VERSION = "1"
DETECTOR_IDS = {
    "callable_contract": f"{INDICATOR_ID}.callable_contract",
    "return_shape": f"{INDICATOR_ID}.return_shape",
    "cli_argument": f"{INDICATOR_ID}.cli_argument",
    "cli_command": f"{INDICATOR_ID}.cli_command",
    "syntax_error": f"{INDICATOR_ID}.syntax_error",
}


def analyze(context: AnalysisContext) -> dict[str, Any]:
    """Measure static public callable, return-shape, and CLI contracts."""

    result = _contracts(context.trees(family="coupling"), context=context)
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
                inspection_question="Inspect the syntax error before interpreting contract coverage.",
                primary_reason="syntax_error_no_ast_node",
            )
        )
        result["limitations"].append(
            f"{error.path} could not be parsed; its public contract is unavailable."
        )
    result["findings"] = sorted(
        result["findings"],
        key=lambda item: (
            str(item.get("path", "")),
            int(item.get("line", 0)),
            str(item.get("kind", "")),
            str(item.get("message", "")),
        ),
    )
    result["limitations"] = sorted(set(result["limitations"]))
    return result


def _contracts(
    trees: Mapping[str, ast.Module], *, context: AnalysisContext | None = None
) -> dict[str, Any]:
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
            shape_nodes = _return_shape_nodes(node)
            shapes = [shape for shape, _ in shape_nodes]
            if shapes:
                descriptor["return_dict_keys"] = [shape["keys"] for shape in shapes]
                returns[key] = shapes
            callables[key] = descriptor
            surface[key] = descriptor
            findings.append(
                _finding(
                    context,
                    {
                        "path": path,
                        "line": int(node.lineno),
                        "kind": "callable_contract",
                        "message": f"Public callable contract measured: {qualname}",
                        "symbol": key,
                    },
                    path=path,
                    node=node,
                    pattern={"symbol": key, "signature": descriptor},
                    inspection_question=(
                        "Inspect whether the public callable signature remains the intended contract."
                    ),
                )
            )
            if shape_nodes:
                shape, primary_shape_node = shape_nodes[0]
                related_shape_nodes = tuple(
                    LocatedNode(path, shape_node, "coupling")
                    for _, shape_node in shape_nodes[1:]
                )
                findings.append(
                    _finding(
                        context,
                        {
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "return_shape",
                            "message": (
                                f"Direct dict return shape measured for {qualname}: "
                                + "; ".join(
                                    ",".join(shape["keys"])
                                    for shape, _ in shape_nodes
                                )
                            ),
                            "symbol": key,
                        },
                        path=path,
                        node=primary_shape_node,
                        related_nodes=related_shape_nodes,
                        pattern={
                            "symbol": key,
                            "keys": shape["keys"],
                            "shapes": shapes,
                        },
                        inspection_question=(
                            "Inspect whether this returned mapping shape is part of the consumer contract."
                        ),
                    )
                )
        for call in sorted(
            (node for node in ast.walk(trees[path]) if isinstance(node, ast.Call)),
            key=lambda node: (node.lineno, node.col_offset),
        ):
            parsed = _cli_call(path, call)
            if parsed is None:
                continue
            item = {**parsed[1], "_node": call}
            (arguments if parsed[0] == "argument" else commands).append(item)

    public_arguments = [_without_node(item) for item in arguments]
    public_commands = [_without_node(item) for item in commands]
    _add_cli_surface(surface, public_arguments, "argument")
    _add_cli_surface(surface, public_commands, "command")
    for item in arguments:
        public_item = _without_node(item)
        findings.append(
            _finding(
                context,
                {
                    "path": public_item["path"],
                    "line": public_item["line"],
                    "kind": "cli_argument",
                    "message": "CLI argument declaration measured: "
                    + ", ".join(public_item["flags"]),
                    "flags": public_item["flags"],
                },
                path=public_item["path"],
                node=item["_node"],
                pattern={"declaration": public_item},
                inspection_question=(
                    "Inspect whether this CLI argument's flags and options remain compatible with callers."
                ),
            )
        )
    for item in commands:
        public_item = _without_node(item)
        findings.append(
            _finding(
                context,
                {
                    "path": public_item["path"],
                    "line": public_item["line"],
                    "kind": "cli_command",
                    "message": f"CLI command declaration measured: {public_item['name']}",
                    "name": public_item["name"],
                },
                path=public_item["path"],
                node=item["_node"],
                pattern={"declaration": public_item},
                inspection_question=(
                    "Inspect whether this CLI command remains reachable with its declared parser options."
                ),
            )
        )
    metrics = {
        "public_callable_count": len(callables),
        "return_shape_count": sum(map(len, returns.values())),
        "cli_argument_count": len(public_arguments),
        "cli_command_count": len(public_commands),
        "surface_count": len(surface),
    }
    return {
        "status": "measured",
        "metrics": metrics,
        "surface": dict(sorted(surface.items())),
        "cli_arguments": public_arguments,
        "cli_commands": public_commands,
        "findings": findings,
        "limitations": [
            "Public contracts are static AST approximations; runtime decorators, dispatch, inheritance, and annotations are not evaluated.",
            "Returned shapes include only direct dict literals in public callable return statements.",
            "CLI inventory recognizes literal argparse-style add_argument and add_parser calls; it is not a schema or compatibility proof.",
        ],
    }


def _without_node(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "_node"}


def _finding(
    context: AnalysisContext | None,
    finding: dict[str, Any],
    *,
    path: str,
    node: ast.AST,
    related_nodes: Sequence[LocatedNode] = (),
    pattern: dict[str, Any],
    inspection_question: str,
) -> dict[str, Any]:
    finding = dict(finding)
    detector_id = DETECTOR_IDS[finding["kind"]]
    if context is None:
        return finding
    return enrich_finding(
        finding,
        context=context,
        indicator_id=detector_id,
        indicator_version=INDICATOR_VERSION,
        path=path,
        node=node,
        family="coupling",
        related_nodes=related_nodes,
        pattern=pattern,
        uncertainty=["static_ast_contract"],
        inspection_question=inspection_question,
    )


def _public_callables(
    tree: ast.Module,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
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
    defaults = (
        {
            arg: _expr(value)
            for arg, value in zip(positional[-len(args.defaults) :], args.defaults)
        }
        if args.defaults
        else {}
    )
    kwonly = [arg.arg for arg in args.kwonlyargs]
    kw_defaults = {
        arg.arg: _expr(value)
        for arg, value in zip(args.kwonlyargs, args.kw_defaults)
        if value is not None
    }
    return {
        "kind": "callable",
        "name": node.name,
        "async": isinstance(node, ast.AsyncFunctionDef),
        "positional": positional,
        "positional_only": positional_only,
        "positional_or_keyword": positional_or_keyword,
        "kwonly": kwonly,
        "defaults": defaults,
        "kwonly_defaults": kw_defaults,
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


def _return_shape_nodes(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[dict[str, Any], ast.Dict]]:
    visitor = _Returns()
    for statement in node.body:
        visitor.visit(statement)
    shapes: dict[tuple[str, ...], ast.Dict] = {}
    for value in visitor.values:
        keys: list[str] = []
        for key in value.keys:
            if key is None:
                keys.append("<unpacked>")
            elif isinstance(key, ast.Constant) and isinstance(
                key.value, (str, int, float, bool, type(None))
            ):
                keys.append(str(key.value))
            else:
                keys.append("<dynamic>")
        shape = tuple(sorted(set(keys)))
        shapes.setdefault(shape, value)
    return [({"keys": list(keys)}, shapes[keys]) for keys in sorted(shapes)]


def _return_shapes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    """Return legacy shape descriptors without exposing AST nodes."""

    return [shape for shape, _ in _return_shape_nodes(node)]


def _cli_call(path: str, call: ast.Call) -> tuple[str, dict[str, Any]] | None:
    if (
        not isinstance(call.func, ast.Attribute)
        or call.func.attr not in {"add_argument", "add_parser"}
    ):
        return None
    values = [_literal(arg) for arg in call.args]
    options = {
        keyword.arg: _literal(keyword.value)
        for keyword in call.keywords
        if keyword.arg
    }
    if call.func.attr == "add_parser":
        if not values or not isinstance(values[0], str):
            return None
        return "command", {
            "path": path,
            "line": int(call.lineno),
            "name": values[0],
            "options": options,
        }
    flags = sorted(
        {value for value in values if isinstance(value, str) and value.startswith("-")}
    )
    if not flags:
        return None
    dest = options.get("dest") if isinstance(options.get("dest"), str) else None
    if dest is None:
        dest = _cli_dest(flags)
    return "argument", {
        "path": path,
        "line": int(call.lineno),
        "flags": flags,
        "dest": dest,
        "options": options,
    }


def _cli_dest(flags: list[str]) -> str | None:
    """Match argparse's dest inference from the first long option, then short."""

    for flag in flags:
        if flag.startswith("--") and len(flag) > 2:
            return flag[2:].replace("-", "_")
    for flag in flags:
        if flag.startswith("-") and not flag.startswith("--") and len(flag) > 1:
            return flag[1:].replace("-", "_")
    return None


def _add_cli_surface(
    surface: dict[str, dict[str, Any]], items: list[dict[str, Any]], kind: str
) -> None:
    seen: defaultdict[str, int] = defaultdict(int)
    for item in sorted(
        items,
        key=lambda value: (
            value["path"],
            value["line"],
            value.get("name", ""),
            value.get("flags", []),
        ),
    ):
        if kind == "argument":
            label = item["dest"] or "|".join(item["flags"])
            base = f"{item['path']}::cli-argument:{label}"
            descriptor = {
                "kind": "cli_argument",
                "flags": item["flags"],
                "dest": item["dest"],
                "options": item["options"],
            }
        else:
            base = f"{item['path']}::cli-command:{item['name']}"
            descriptor = {
                "kind": "cli_command",
                "name": item["name"],
                "options": item["options"],
            }
        seen[base] += 1
        surface[base if seen[base] == 1 else f"{base}#{seen[base]}"] = descriptor


def _literal(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and isinstance(
        node.value, (str, int, float, bool, type(None))
    ):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [_literal(item) for item in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.Dict):
        return {
            key.value: _literal(value)
            for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    return "<dynamic>"


def _expr(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<dynamic>"


__all__ = [
    "DETECTOR_IDS",
    "FACTOR",
    "INDICATOR_ID",
    "INDICATOR_VERSION",
    "analyze",
    "_add_cli_surface",
    "_cli_call",
    "_cli_dest",
    "_contracts",
    "_expr",
    "_literal",
    "_public_callables",
    "_return_shapes",
    "_signature",
]
