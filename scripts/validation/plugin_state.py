"""Plugin state: a decision-cycle plugin keeps nothing on itself from one cycle to the next.

History that later frames need lives in shared memory (``autonomy/shared_memory.py``);
an instance holds only configuration and reusable resources, set when it is
constructed. This check reads each plugin class (a class with a ``plugin_id``
and a step method) and follows the calls its step methods and ``status`` make
on ``self``. A write to an attribute of ``self`` along those calls is a
finding: assigning, augmenting or deleting it, storing into it, calling a
mutating method on it, ``setattr``/``delattr`` on ``self``, or a ``global``
statement. Work done on another object (a per-call copy, a fresh worker built
inside the step) is not followed, so those patterns stay available.

Helpers in a ``shared`` directory are not plugins and are skipped; the step
runners in ``autonomy`` are not checked. It is a static check: state changed
through an alias or a helper function is not seen.

  python3 -m scripts.validation.plugin_state DIR_OR_FILE...   check any sources, such as a plugin collection
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from pathlib import Path

from qca.evaluation import Finding, finding, location

STEP_METHODS = frozenset({"perceive", "observe", "update", "propose", "plan", "decide"})
ENTRY_METHODS = STEP_METHODS | {"status"}
MUTATORS = frozenset({
    "append", "appendleft", "extend", "extendleft", "insert", "pop", "popleft", "popitem", "remove", "clear",
    "update", "setdefault", "add", "discard", "sort", "reverse", "rotate",
})


def retained_state(tree: Path, paths: list[str]) -> list[Finding]:
    """Findings for plugin classes in the Python files among ``paths``."""

    findings = []
    for path in paths:
        file = tree / path
        if not _plugin_source(path, file):
            continue
        try:
            module = ast.parse(file.read_text(encoding="utf-8"), filename=path)
        except (SyntaxError, UnicodeDecodeError):
            continue  # Ruff reports files that do not parse.
        findings.extend(_class_findings(path, module))
    return findings


def _class_findings(path: str, module: ast.Module) -> Iterator[Finding]:
    for plugin in (node for node in ast.walk(module) if isinstance(node, ast.ClassDef) and _is_plugin(node)):
        methods = {node.name: node for node in plugin.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}
        reported = set()
        for method in _reachable(methods):
            for name, line in _writes(methods[method]):
                if name in reported:
                    continue
                reported.add(name)
                subject = "module state (global)" if name == "global" else f"`self.{name}`"
                yield finding(
                    "plugin-state",
                    "retained-state",
                    location(path, line),
                    f"Does {plugin.name}.{method} keep {subject} from one cycle to the next? "
                    "Plugins keep history in shared memory, not on the instance.",
                    status="blocking",
                    evidence={"plugin": plugin.name, "method": method, "attribute": name},
                )


def _plugin_source(path: str, file: Path) -> bool:
    in_shared_directory = "shared" in Path(path).parts[:-1]
    return file.suffix == ".py" and file.is_file() and not in_shared_directory


def _is_plugin(node: ast.ClassDef) -> bool:
    declared = {name for item in node.body for name in _class_names(item)}
    steps = any(isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name in STEP_METHODS for item in node.body)
    return "plugin_id" in declared and steps


def _class_names(item: ast.stmt) -> Iterator[str]:
    """Names a class-body statement assigns, such as ``plugin_id = ...`` or ``plugin_id: str``."""

    targets = item.targets if isinstance(item, ast.Assign) else [item.target] if isinstance(item, ast.AnnAssign) else []
    yield from (target.id for target in targets if isinstance(target, ast.Name))


def _reachable(methods: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]) -> list[str]:
    """Entry methods and every method of the class they reach through calls on ``self``, in visiting order."""

    order, pending = [], [name for name in methods if name in ENTRY_METHODS]
    while pending:
        name = pending.pop(0)
        if name in order:
            continue
        order.append(name)
        called = (_self_method(node) for node in ast.walk(methods[name]))
        pending += [method for method in called if method in methods and method not in order]
    return order


def _self_method(node: ast.AST) -> str | None:
    """The method name of a ``self.name(...)`` call; None for anything else."""

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and _is_self(node.func.value):
        return node.func.attr
    return None


def _writes(method: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[tuple[str, int]]:
    """Attributes of ``self`` the method writes, with the line of each write (``global`` for module state)."""

    for node in ast.walk(method):
        if isinstance(node, ast.Call):
            yield from _call_writes(node)
        elif isinstance(node, ast.Global):
            yield "global", node.lineno
        else:
            names = (_self_attribute(element) for target in _targets(node) for element in _elements(target))
            yield from ((name, node.lineno) for name in names if name is not None)


def _targets(node: ast.AST) -> list[ast.expr]:
    """What an assignment, augmented or annotated assignment, or ``del`` writes; nothing for other nodes."""

    if isinstance(node, ast.Assign | ast.Delete):
        return node.targets
    if isinstance(node, ast.AugAssign) or isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target]
    return []


def _call_writes(node: ast.Call) -> Iterator[tuple[str, int]]:
    function = node.func
    if isinstance(function, ast.Attribute) and function.attr in MUTATORS:
        name = _self_attribute(function.value)
    else:
        name = _assigned_attribute(node)
    if name is not None:
        yield name, node.lineno


def _assigned_attribute(node: ast.Call) -> str | None:
    """The attribute a ``setattr`` or ``delattr`` call writes on ``self``."""

    function = node.func
    args = node.args
    if isinstance(function, ast.Name) and function.id in {"setattr", "delattr"}:
        if len(args) >= 2 and _is_self(args[0]) and _has_value(node, function.id, 3):
            return _attribute_name(args[1])
    elif isinstance(function, ast.Attribute) and function.attr in {"__setattr__", "__delattr__"}:
        if _is_self(function.value) and args and _has_value(node, function.attr, 2):
            return _attribute_name(args[0])
        if len(args) >= 2 and _is_self(args[0]) and _has_value(node, function.attr, 3):
            return _attribute_name(args[1])
    return None


def _has_value(node: ast.Call, operation: str, needed: int) -> bool:
    """A deletion is the write; a set also needs its value, positional or by keyword."""

    if operation in {"delattr", "__delattr__"}:
        return True
    return len(node.args) >= needed or any(item.arg == "value" for item in node.keywords)


def _attribute_name(node: ast.expr) -> str:
    """The attribute a ``setattr``/``delattr`` names, or ``?`` when it is computed."""

    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else "?"


def _elements(target: ast.expr) -> Iterator[ast.expr]:
    if isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _elements(element.value if isinstance(element, ast.Starred) else element)
    else:
        yield target


def _self_attribute(node: ast.expr) -> str | None:
    """The attribute of ``self`` at the root of ``self.a``, ``self.a.b`` or ``self.a[k]``; None otherwise."""

    while isinstance(node, ast.Attribute | ast.Subscript):
        if isinstance(node, ast.Attribute) and _is_self(node.value):
            return node.attr
        node = node.value
    return None


def _is_self(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "self"


def main(argv: list[str] | None = None) -> int:
    targets = [Path(item) for item in (sys.argv[1:] if argv is None else argv)]
    if not targets:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    missing = [target for target in targets if not target.is_file() and not target.is_dir()]
    if missing:
        for target in missing:
            print(f"{target}: not a file or directory", file=sys.stderr)
        return 2
    findings = []
    for target in targets:
        root = target if target.is_dir() else target.parent
        files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
        for item in retained_state(root, [file.relative_to(root).as_posix() for file in files]):
            item["location"]["path"] = (root / item["location"]["path"]).as_posix()
            findings.append(item)
    for item in findings:
        print(f"{item['location']['path']}:{item['location']['line']}: {item['question']}")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
