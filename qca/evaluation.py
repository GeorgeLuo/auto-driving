"""Evaluate text, files or a change, and report every finding in one shape.

A context names the code in question:

* ``text_context``: a body of text, evaluated as if it were the named file;
* ``files_context``: whole files in a working tree;
* ``change_context``: a pull request or a commit, as a base revision against a
  head revision or the working tree.

Evaluators see only a directory and the paths to inspect, so one evaluator
serves every form.  For a change, each evaluator runs on both sides and only
the findings the base lacks are reported.  Existing debt therefore needs no
stored baseline, and a change still answers for what it breaks outside its own
lines, such as a caller of a changed signature.

A finding is a review question about a location, not a verdict.  Every
evaluator returns the same fields, so findings from different tools can be
compared, filtered, or handed to a reviewer as one flat list.
"""

from __future__ import annotations

import ast
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NotRequired, TypedDict

from .analyzer import AnalysisError

SCHEMA = "qca/findings/v1"
FORMS = frozenset({"text", "files", "change"})
STATUSES = frozenset({"blocking", "review"})


class Location(TypedDict):
    """A place in a file, in 1-based lines, with its enclosing symbol when known."""

    path: str
    line: int
    end_line: int
    symbol: NotRequired[str]


class Measure(TypedDict):
    """A measured value and the limit it exceeds."""

    value: int
    limit: int


class Finding(TypedDict):
    """A review question about a location.

    ``status`` is ``blocking`` when the check should fail and ``review``
    otherwise.  A ``measure`` makes a change accountable only for growth.
    """

    tool: str
    rule: str
    status: str
    location: Location
    related: list[Location]
    question: str
    measure: Measure | None
    evidence: dict[str, object]


class ContextRecord(TypedDict):
    """The code evaluated: ``base`` and ``head`` revisions are set for a change.

    A change with a null ``head`` compares the base with the working tree.
    """

    form: str
    base: str | None
    head: str | None
    paths: list[str]


class EvaluationRecord(TypedDict):
    """Whether an evaluator ``completed``, was ``skipped`` or ``failed``, and why."""

    evaluator: str
    required: bool
    status: str
    findings: NotRequired[int]
    reason: NotRequired[str]
    error: NotRequired[str]


class Document(TypedDict):
    schema: str
    context: ContextRecord
    evaluations: list[EvaluationRecord]
    findings: list[Finding]


def location(path: str, line: int, end_line: int | None = None, symbol: str | None = None) -> Location:
    place = Location(path=path, line=line, end_line=max(line, end_line or line))
    if symbol:
        place["symbol"] = symbol
    return place


def finding(
    tool: str,
    rule: str,
    place: Location,
    question: str,
    *,
    status: str = "review",
    evidence: dict[str, object] | None = None,
    related: Sequence[Location] = (),
    measure: Measure | None = None,
) -> Finding:
    return Finding(
        tool=tool,
        rule=rule,
        status=status,
        location=place,
        related=list(related),
        question=question,
        measure=measure,
        evidence=evidence or {},
    )


def validate_finding(item: object) -> None:
    """Reject a finding whose shape would break consumers of the flat list."""

    if not isinstance(item, dict) or frozenset(item) != Finding.__required_keys__:
        raise ValueError(f"a finding has exactly the fields {sorted(Finding.__required_keys__)}")
    for requirement, holds in _REQUIREMENTS:
        if not holds(item):
            raise ValueError(f"a finding's {requirement}: {item}")


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_location(place: object) -> bool:
    if not isinstance(place, dict) or not _is_text(place.get("path")):
        return False
    line, end_line = place.get("line"), place.get("end_line")
    if not (isinstance(line, int) and isinstance(end_line, int)):
        return False
    return 1 <= line <= end_line


def _is_measure(measure: object) -> bool:
    if measure is None:
        return True
    return isinstance(measure, dict) and all(isinstance(measure.get(key), int) for key in Measure.__required_keys__)


# Checked in order: related locations are known to be a list before each is inspected.
_REQUIREMENTS: tuple[tuple[str, Callable[[dict[str, object]], bool]], ...] = (
    ("tool, rule and question are text", lambda item: all(_is_text(item[key]) for key in ("tool", "rule", "question"))),
    ("status is blocking or review", lambda item: item["status"] in STATUSES),
    ("related locations are a list", lambda item: isinstance(item["related"], list)),
    ("locations name a path and 1-based lines", lambda item: all(map(_is_location, [item["location"], *item["related"]]))),
    ("measure is null or an integer value and limit", lambda item: _is_measure(item["measure"])),
    ("evidence is a mapping", lambda item: isinstance(item["evidence"], dict)),
)


@dataclass(frozen=True)
class Context:
    """``paths`` in the ``head`` directory, compared with ``base`` for a change."""

    form: str
    head: Path
    paths: tuple[str, ...]
    base: Path | None = None
    base_revision: str | None = None
    head_revision: str | None = None

    def describe(self) -> ContextRecord:
        return ContextRecord(form=self.form, base=self.base_revision, head=self.head_revision, paths=list(self.paths))


@dataclass(frozen=True)
class Evaluator:
    """A check that turns paths in a directory into findings.

    ``roots`` bound what the check maintains.  ``reach`` is ``project`` when a
    change can produce findings outside its own files, so the whole project is
    compared for a change.  A failure of an evaluator that is not ``required``
    is reported without failing the evaluation.
    """

    name: str
    run: Callable[[Path, list[str]], list[Finding]]
    roots: tuple[str, ...] = (".",)
    forms: frozenset[str] = FORMS
    reach: str = "files"
    required: bool = True


def evaluate(context: Context, evaluators: Sequence[Evaluator]) -> Document:
    """Run each evaluator on the context and return one findings document."""

    findings: list[Finding] = []
    evaluations: list[EvaluationRecord] = []
    for evaluator in evaluators:
        record = EvaluationRecord(evaluator=evaluator.name, required=evaluator.required, status="completed")
        if context.form not in evaluator.forms:
            record.update(status="skipped", reason=f"does not evaluate {context.form} contexts")
        else:
            try:
                found = _evaluate(evaluator, context)
            except Exception as exc:  # One failing tool is reported beside the others' findings.
                record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            else:
                if found is None:
                    record.update(status="skipped", reason="no paths in scope")
                else:
                    record.update(findings=len(found))
                    findings.extend(found)
        evaluations.append(record)
    return Document(schema=SCHEMA, context=context.describe(), evaluations=evaluations, findings=findings)


def _evaluate(evaluator: Evaluator, context: Context) -> list[Finding] | None:
    trees = [context.head] if context.base is None else [context.base, context.head]
    scopes = [_scope(evaluator, context, tree) for tree in trees]
    if not any(scopes):
        return None
    results = [_run(evaluator, tree, paths) for tree, paths in zip(trees, scopes, strict=True)]
    if context.base is None:
        return results[0]
    return introduced(results[0], results[1], base_tree=context.base, head_tree=context.head)


def _scope(evaluator: Evaluator, context: Context, tree: Path) -> list[str]:
    whole_project = evaluator.reach == "project" and context.base is not None
    requested = evaluator.roots if whole_project else context.paths
    return [path for path in _within(requested, evaluator.roots) if (tree / path).exists()]


def _run(evaluator: Evaluator, tree: Path, paths: list[str]) -> list[Finding]:
    if not paths:
        return []
    found = evaluator.run(tree, paths)
    for item in found:
        validate_finding(item)
    return found


def in_scope(path: str, scopes: Sequence[str]) -> bool:
    """Whether ``path`` is one of ``scopes`` or lies beneath one."""

    return any(PurePosixPath(path).is_relative_to(scope) for scope in scopes)


def _within(paths: Sequence[str], roots: Sequence[str]) -> list[str]:
    """The parts of ``paths`` under ``roots``: paths inside a root, and roots inside a path."""

    selected = {path for path in paths if in_scope(path, roots)}
    selected.update(root for root in roots if in_scope(root, paths))
    return sorted(selected)


def introduced(base: list[Finding], head: list[Finding], *, base_tree: Path, head_tree: Path) -> list[Finding]:
    """Return the head findings the base does not have.

    Findings match by tool, rule, file, enclosing symbol and source text, so
    lines that only moved still match.  A measured finding matches a base
    finding of at least the same value: a function may shrink while above a
    limit, but growing past its base value is new.
    """

    available: defaultdict[tuple[str, ...], list[int]] = defaultdict(list)
    base_identity = _Identities(base_tree)
    for item in base:
        available[base_identity.of(item)].append(_value(item))
    for values in available.values():
        values.sort()
    head_identity = _Identities(head_tree)
    new: set[int] = set()
    for index, item in sorted(enumerate(head), key=lambda pair: _value(pair[1])):
        values = available[head_identity.of(item)]
        match = next((position for position, value in enumerate(values) if value >= _value(item)), None)
        if match is None:
            new.add(index)
        else:
            del values[match]
    return [item for index, item in enumerate(head) if index in new]


def _value(item: Finding) -> int:
    return item["measure"]["value"] if item["measure"] else 0


class _Identities:
    """Identify findings in one directory without their line numbers."""

    def __init__(self, tree: Path) -> None:
        self._tree = tree
        self._files: dict[str, tuple[list[str], list[tuple[int, int, str]]]] = {}

    def of(self, item: Finding) -> tuple[str, ...]:
        place = item["location"]
        lines, symbols = self._file(place["path"])
        owner = next(
            (name for start, end, name in reversed(symbols) if start <= place["line"] <= end),
            "<module>",
        )
        # A measured finding is the same finding at any size; its value is compared instead.
        text = "" if item["measure"] else "\n".join(
            line.strip() for line in lines[place["line"] - 1:place["end_line"]]
        )
        return (item["tool"], item["rule"], place["path"], owner, text)

    def _file(self, path: str) -> tuple[list[str], list[tuple[int, int, str]]]:
        if path not in self._files:
            file = self._tree / path
            source = file.read_text(encoding="utf-8", errors="replace") if file.is_file() else ""
            self._files[path] = (source.splitlines(), _symbols(source))
        return self._files[path]


def _symbols(source: str) -> list[tuple[int, int, str]]:
    """Classes and functions as (start, end, qualified name), outermost first."""

    found: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, parent: str) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            parent = f"{parent}.{node.name}" if parent else node.name
            found.append((node.lineno, node.end_lineno or node.lineno, parent))
        for child in ast.iter_child_nodes(node):
            visit(child, parent)

    try:
        visit(ast.parse(source), "")
    except (SyntaxError, ValueError):
        return []  # Text that is not Python has no symbols; its findings match by text.
    return found


@contextmanager
def text_context(name: str, text: str, *, root: Path, shared: Sequence[str] = ()) -> Iterator[Context]:
    """Evaluate ``text`` as the file ``name``, configured by ``root``'s ``shared`` paths."""

    relative = _relative_name(name)
    with tempfile.TemporaryDirectory(prefix="qca-text-") as directory:
        tree = Path(directory)
        _mirror(root, tree, shared)
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        yield Context("text", tree, (relative,))


@contextmanager
def files_context(root: Path, paths: Sequence[str]) -> Iterator[Context]:
    """Evaluate whole files; a directory stands for its tracked and unignored files."""

    files: set[str] = set()
    for path in map(_relative_name, paths):
        if (root / path).is_dir():
            listed = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", path)
            files.update(name for name in _names(listed) if (root / name).is_file())
        elif (root / path).is_file():
            files.add(path)
        else:
            raise ValueError(f"no such file or directory: {path}")
    yield Context("files", root, tuple(sorted(files)))


@contextmanager
def change_context(root: Path, base: str, head: str | None = None, *, shared: Sequence[str] = ()) -> Iterator[Context]:
    """Evaluate the change from ``base`` to ``head``, or to the working tree when ``head`` is None.

    The base side is the merge base, so a pull request and a single commit
    (``base`` set to its parent) are evaluated alike.  ``shared`` paths, such
    as tool configuration, are copied from the head side so both sides are
    judged by the same rules.
    """

    head_sha = _resolve(root, head) if head else None
    base_sha = _git(root, "merge-base", base, head_sha or "HEAD").decode().strip()
    with tempfile.TemporaryDirectory(prefix="qca-change-") as directory:
        base_tree = _extract(root, base_sha, Path(directory) / "base")
        head_tree = _extract(root, head_sha, Path(directory) / "head") if head_sha else root
        _mirror(head_tree, base_tree, shared)
        yield Context(
            "change",
            head_tree,
            _changed(root, base_sha, head_sha),
            base=base_tree,
            base_revision=base_sha,
            head_revision=head_sha,
        )


def _changed(root: Path, base: str, head: str | None) -> tuple[str, ...]:
    names = _names(_git(root, "diff", "--name-only", "--no-renames", "-z", base, *([head] if head else []), "--"))
    if head is None:
        names += _names(_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    return tuple(sorted(set(names)))


def _extract(root: Path, revision: str, target: Path) -> Path:
    archive = _git(root, "archive", "--format=tar", revision)
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter="data")
    return target


def _mirror(source: Path, target: Path, paths: Sequence[str]) -> None:
    """Make each of ``paths`` in ``target`` match ``source``, including its absence."""

    for path in paths:
        destination = target / path
        if destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()
        origin = source / path
        if origin.is_dir():
            shutil.copytree(origin, destination)
        elif origin.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)


def _resolve(root: Path, revision: str) -> str:
    return _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()


def _relative_name(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"paths are relative to the repository: {path}")
    return normalized.as_posix()


def _names(output: bytes) -> list[str]:
    return [os.fsdecode(name) for name in output.split(b"\0") if name]


def _git(root: Path, *args: str) -> bytes:
    completed = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if completed.returncode:
        message = completed.stderr.decode(errors="replace").strip()
        raise AnalysisError(f"git {' '.join(args[:2])} failed: {message}")
    return completed.stdout
