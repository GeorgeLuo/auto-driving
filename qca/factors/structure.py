"""Static structure measurements: redundancy, patterns, style, and stubs."""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from typing import Any


_MUTATING_METHODS = frozenset({
    "append", "extend", "insert", "remove", "pop", "clear",
    "update", "add", "discard", "write", "close",
})
_MUTABLE_CTORS = frozenset({"list", "dict", "set"})


def analyze_structure(sources: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Measure redundancy, error patterns, effects, and stubs in Python sources.

    ``sources`` maps repository-relative paths to text.  Only ``.py`` files are
    measured.  Unparsable files yield a ``syntax_error`` finding on each factor
    and do not abort the remaining measurements.
    """
    source_map = {
        _norm(path): text if isinstance(text, str) else str(text)
        for path, text in sources.items()
    }
    trees: dict[str, ast.Module] = {}
    errors: list[dict[str, Any]] = []
    for path in sorted(source_map):
        if not path.endswith(".py"):
            continue
        try:
            trees[path] = ast.parse(source_map[path], filename=path)
        except SyntaxError as exc:
            errors.append({
                "path": path,
                "line": int(exc.lineno or 1),
                "message": str(exc.msg or "syntax error"),
            })

    factors = {
        "redundancy": _redundancy(trees),
        "patterns": _patterns(trees),
        "functional_style": _functional_style(trees),
        "functionality": _functionality(trees),
    }
    for error in errors:
        finding = {
            "path": error["path"],
            "line": error["line"],
            "kind": "syntax_error",
            "message": f"Python source could not be parsed: {error['message']}",
        }
        for factor in factors.values():
            factor["findings"].append(dict(finding))
            factor["limitations"].append(
                f"{error['path']} could not be parsed; structure observations "
                "from that file are unavailable."
            )
    for factor in factors.values():
        factor["findings"] = _sort_findings(factor["findings"])
        factor["limitations"] = sorted(set(factor["limitations"]))
    return factors


def _norm(path: str) -> str:
    value = str(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value or "."


def _sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        findings,
        key=lambda item: (
            str(item.get("path", "")),
            int(item.get("line", 0)),
            str(item.get("kind", "")),
            str(item.get("message", "")),
        ),
    )


def _factor(
    metrics: dict[str, int],
    findings: list[dict[str, Any]],
    limitations: list[str],
) -> dict[str, Any]:
    return {
        "status": "measured",
        "metrics": metrics,
        "findings": findings,
        "limitations": limitations,
    }


# --- redundancy -----------------------------------------------------------------

def _redundancy(trees: dict[str, ast.Module]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stmt_counts: dict[str, int] = {}
    findings: list[dict[str, Any]] = []
    repeated_branch_count = 0

    for path in sorted(trees):
        for node in ast.walk(trees[path]):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _is_trivial_callable(node):
                    continue
                digest = _callable_digest(node)
                entry = {"path": path, "line": int(node.lineno), "name": node.name}
                groups[digest].append(entry)
                stmt_counts[digest] = _logical_stmt_count(node)
            elif isinstance(node, ast.If):
                if len(node.body) >= 2 and len(node.orelse) >= 2:
                    body_dump = [
                        ast.dump(stmt, include_attributes=False) for stmt in node.body
                    ]
                    else_dump = [
                        ast.dump(stmt, include_attributes=False) for stmt in node.orelse
                    ]
                    if body_dump == else_dump:
                        repeated_branch_count += 1
                        findings.append({
                            "path": path,
                            "line": int(node.lineno),
                            "kind": "repeated_branch",
                            "message": (
                                "If body and else branch have identical "
                                "statement structure."
                            ),
                        })

    clone_group_count = 0
    cloned_callable_count = 0
    duplicate_ast_loc = 0
    for digest in sorted(groups):
        occurrences = sorted(
            groups[digest],
            key=lambda item: (item["path"], item["line"], item["name"]),
        )
        if len(occurrences) < 2:
            continue
        clone_group_count += 1
        cloned_callable_count += len(occurrences)
        duplicate_ast_loc += stmt_counts[digest] * (len(occurrences) - 1)
        names = ", ".join(item["name"] for item in occurrences)
        findings.append({
            "path": occurrences[0]["path"],
            "line": occurrences[0]["line"],
            "kind": "callable_clone",
            "message": (
                f"Nontrivial callable body is shared by {len(occurrences)} "
                f"callables: {names}."
            ),
            "occurrences": occurrences,
            "paths": [item["path"] for item in occurrences],
        })

    return _factor(
        {
            "clone_group_count": clone_group_count,
            "cloned_callable_count": cloned_callable_count,
            "repeated_branch_count": repeated_branch_count,
            "duplicate_ast_loc": duplicate_ast_loc,
        },
        findings,
        ["Identifier normalization is approximate; renamed locals may still look identical."],
    )


def _is_trivial_callable(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if len(node.body) != 1:
        return False
    stmt = node.body[0]
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is ...:
        return True
    if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant):
        return True
    return False


def _callable_digest(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    normalized = _NameNormalizer().visit(_copy_ast(node))
    assert isinstance(normalized, (ast.FunctionDef, ast.AsyncFunctionDef))
    normalized.name = "_"
    payload = ast.dump(normalized, include_attributes=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _NameNormalizer(ast.NodeTransformer):
    """Replace Name ids and parameter names; keep Attribute.attr intact."""

    def visit_Name(self, node: ast.Name) -> ast.Name:
        return ast.Name(id="_n", ctx=node.ctx)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        self.generic_visit(node)
        return ast.arg(arg="_n", annotation=node.annotation)


def _copy_ast(node: Any) -> Any:
    if isinstance(node, ast.AST):
        return type(node)(**{field: _copy_ast(getattr(node, field)) for field in node._fields})
    if isinstance(node, list):
        return [_copy_ast(item) for item in node]
    return node


def _logical_stmt_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return sum(
        1
        for child in ast.walk(node)
        if isinstance(child, ast.stmt) and child is not node
    )


# --- patterns -------------------------------------------------------------------

def _patterns(trees: dict[str, ast.Module]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    bare_except_count = 0
    broad_except_count = 0
    raise_count = 0
    logged_error_count = 0
    swallowed_exception_count = 0

    for path in sorted(trees):
        for node in ast.walk(trees[path]):
            if isinstance(node, ast.Raise):
                raise_count += 1
                findings.append({
                    "path": path,
                    "line": int(node.lineno),
                    "kind": "raise",
                    "message": "Raise statement observed.",
                })
            elif isinstance(node, ast.Call) and _is_logged_error(node):
                logged_error_count += 1
                findings.append({
                    "path": path,
                    "line": int(node.lineno),
                    "kind": "logged_error",
                    "message": "Recognized error-logging call observed.",
                })
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    bare_except_count += 1
                    findings.append({
                        "path": path,
                        "line": int(node.lineno),
                        "kind": "bare_except",
                        "message": "Bare except clause observed.",
                    })
                elif _is_broad_except(node.type):
                    broad_except_count += 1
                    findings.append({
                        "path": path,
                        "line": int(node.lineno),
                        "kind": "broad_except",
                        "message": "Broad except for Exception or BaseException observed.",
                    })
                if _is_pass_only(node.body):
                    swallowed_exception_count += 1
                    findings.append({
                        "path": path,
                        "line": int(node.lineno),
                        "kind": "swallowed_exception",
                        "message": "Except body is only pass; exception may be swallowed.",
                    })

    return _factor(
        {
            "bare_except_count": bare_except_count,
            "broad_except_count": broad_except_count,
            "raise_count": raise_count,
            "logged_error_count": logged_error_count,
            "swallowed_exception_count": swallowed_exception_count,
        },
        findings,
        ["Inspect intent at the owning boundary; recognized patterns are not a style grade."],
    )


def _is_broad_except(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id in {"Exception", "BaseException"}:
        return True
    if isinstance(node, ast.Tuple):
        return any(_is_broad_except(elt) for elt in node.elts)
    return False


def _is_pass_only(body: list[ast.stmt]) -> bool:
    return len(body) == 1 and isinstance(body[0], ast.Pass)


def _is_logged_error(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return False
    if func.value.id == "logging" and func.attr in {"exception", "error"}:
        return True
    return func.value.id == "logger" and func.attr == "exception"


# --- functional_style -----------------------------------------------------------

def _functional_style(trees: dict[str, ast.Module]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    mutable_default_count = 0
    mutating_call_count = 0
    attribute_write_count = 0
    global_or_nonlocal_count = 0
    open_count = 0

    for path in sorted(trees):
        for node in ast.walk(trees[path]):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = list(node.args.defaults)
                defaults.extend(
                    value for value in node.args.kw_defaults if value is not None
                )
                for default in defaults:
                    if _is_mutable_default(default):
                        mutable_default_count += 1
                        findings.append({
                            "path": path,
                            "line": int(getattr(default, "lineno", node.lineno)),
                            "kind": "mutable_default",
                            "message": "Mutable default argument observed.",
                        })
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in _MUTATING_METHODS:
                    mutating_call_count += 1
                    findings.append({
                        "path": path,
                        "line": int(node.lineno),
                        "kind": "mutating_call",
                        "message": f"Mutating method call observed: {node.func.attr}.",
                    })
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                open_count += 1
                findings.append({
                    "path": path,
                    "line": int(node.lineno),
                    "kind": "open_call",
                    "message": "open() call observed.",
                })
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                global_or_nonlocal_count += 1
                findings.append({
                    "path": path,
                    "line": int(node.lineno),
                    "kind": "global_or_nonlocal",
                    "message": f"{type(node).__name__.lower()} statement observed.",
                })
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    attribute_write_count += _count_attribute_writes(
                        path, target, findings, int(node.lineno)
                    )
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                attribute_write_count += _count_attribute_writes(
                    path, node.target, findings, int(node.lineno)
                )
            elif isinstance(node, ast.AugAssign):
                attribute_write_count += _count_attribute_writes(
                    path, node.target, findings, int(node.lineno)
                )

    recognized_effect_count = (
        mutable_default_count
        + mutating_call_count
        + attribute_write_count
        + global_or_nonlocal_count
        + open_count
    )
    return _factor(
        {
            "mutable_default_count": mutable_default_count,
            "mutating_call_count": mutating_call_count,
            "attribute_write_count": attribute_write_count,
            "global_or_nonlocal_count": global_or_nonlocal_count,
            "recognized_effect_count": recognized_effect_count,
        },
        findings,
        ["Absence of recognized effects does not prove purity."],
    )


def _is_mutable_default(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _MUTABLE_CTORS
    )


def _count_attribute_writes(
    path: str,
    target: ast.AST,
    findings: list[dict[str, Any]],
    line: int,
) -> int:
    count = 0
    if isinstance(target, ast.Attribute):
        count += 1
        findings.append({
            "path": path,
            "line": int(getattr(target, "lineno", line)),
            "kind": "attribute_write",
            "message": f"Attribute write observed: {target.attr}.",
        })
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            count += _count_attribute_writes(path, elt, findings, line)
    return count


# --- functionality --------------------------------------------------------------

def _functionality(trees: dict[str, ast.Module]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    stub_count = 0
    unreachable_count = 0

    for path in sorted(trees):
        tree = trees[path]
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_stub(node):
                stub_count += 1
                findings.append({
                    "path": path,
                    "line": int(node.lineno),
                    "kind": "stub",
                    "message": f"Stub callable observed: {node.name}.",
                    "name": node.name,
                })

        unreachable_count += _collect_unreachable(path, tree.body, findings)
        for node in ast.walk(tree):
            if node is tree:
                continue
            for attr in ("body", "orelse", "finalbody"):
                block = getattr(node, attr, None)
                if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                    unreachable_count += _collect_unreachable(path, block, findings)

    return _factor(
        {
            "stub_count": stub_count,
            "unreachable_count": unreachable_count,
        },
        findings,
        ["Inspect intentional hooks and protocols before removing code."],
    )


def _is_stub(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if len(node.body) != 1:
        return False
    stmt = node.body[0]
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is ...:
        return True
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc
        if isinstance(exc, ast.Name) and exc.id in {"NotImplementedError", "NotImplemented"}:
            return True
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            if exc.func.id in {"NotImplementedError", "NotImplemented"}:
                return True
    return False


def _collect_unreachable(
    path: str,
    body: list[ast.stmt],
    findings: list[dict[str, Any]],
) -> int:
    count = 0
    seen_terminal = False
    for stmt in body:
        if seen_terminal:
            count += 1
            findings.append({
                "path": path,
                "line": int(stmt.lineno),
                "kind": "unreachable",
                "message": (
                    "Statement appears after a terminal return or raise "
                    "in the same body list."
                ),
            })
            continue
        if isinstance(stmt, (ast.Return, ast.Raise)):
            seen_terminal = True
    return count
