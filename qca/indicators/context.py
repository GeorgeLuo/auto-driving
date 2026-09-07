"""Shared, lazy source context for QCA indicators.

The factor implementations historically each built their own source map and
ASTs.  :class:`AnalysisContext` is the small common seam for indicators that
need to refer to the same source text and syntax tree.  It deliberately does
not import or execute repository code.

AST ``col_offset`` and ``end_col_offset`` values are UTF-8 byte offsets.  The
context keeps those offsets in source locations and also records character
columns so callers can display a range without accidentally slicing a Unicode
line at the middle of a code point.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any


_PYTHON_SUFFIXES = (".py", ".pyi")


def normalize_path(path: str | os.PathLike[str]) -> str:
    """Return the repository-relative spelling used by the context.

    This matches the existing QCA path convention: separators are slash
    separated and leading ``./`` components are removed.  The function does
    not resolve or access the filesystem; source keys are intentionally
    treated as caller-provided repository paths.
    """

    value = os.fspath(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value or "."


def normalize_source(text: str | bytes) -> str:
    """Normalize source text to UTF-8 text with LF line endings.

    AST locations are defined against the text given to ``ast.parse``.  A
    single line-ending representation keeps snippets deterministic for source
    maps assembled on different platforms while preserving every source
    character and not adding a trailing newline.
    """

    if isinstance(text, bytes):
        text = text.decode("utf-8")
    if not isinstance(text, str):
        raise TypeError("source text must be str or UTF-8 bytes")
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class SourceLocation:
    """An exact AST-backed source range.

    Lines are one-based.  ``start_col`` and ``end_col`` are zero-based UTF-8
    byte offsets, matching Python's AST.  Character columns are included for
    display and are also zero-based.  A location is only created when the AST
    supplies both start and end positions and the offsets can be decoded
    exactly; no best-effort end range is fabricated.
    """

    path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    start_char_col: int
    end_char_col: int

    @property
    def start_column(self) -> int:
        """Alias for the AST's UTF-8 byte start column."""

        return self.start_col

    @property
    def end_column(self) -> int:
        """Alias for the AST's UTF-8 byte end column."""

        return self.end_col

    def to_dict(self, *, include_path: bool = True) -> dict[str, Any]:
        """Return a JSON-compatible range descriptor."""

        value: dict[str, Any] = {
            "start": {
                "line": self.start_line,
                "column": self.start_char_col,
                "byte_column": self.start_col,
            },
            "end": {
                "line": self.end_line,
                "column": self.end_char_col,
                "byte_column": self.end_col,
            },
        }
        if include_path:
            value["path"] = self.path
        return value


@dataclass(frozen=True)
class SourceBlock:
    """A source range and the exact source text covered by that range."""

    path: str
    location: SourceLocation
    code: str
    symbols: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """Readable alias for :attr:`code`."""

        return self.code

    @property
    def range(self) -> SourceLocation:
        """Readable alias for :attr:`location`."""

        return self.location

    def to_dict(self) -> dict[str, Any]:
        """Return the machine-readable code-block representation."""

        return {
            "path": self.path,
            "range": self.location.to_dict(include_path=False),
            "code": self.code,
            "symbols": list(self.symbols),
        }


@dataclass(frozen=True)
class SyntaxErrorInfo:
    """A parse failure retained without stopping other source files."""

    path: str
    message: str
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_col: int | None = None

    @property
    def lineno(self) -> int | None:
        return self.line

    @property
    def offset(self) -> int | None:
        return self.column

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "path": self.path,
            "message": self.message,
            "line": self.line,
            "column": self.column,
        }
        if self.end_line is not None:
            value["end_line"] = self.end_line
        if self.end_col is not None:
            value["end_column"] = self.end_col
        return value


@dataclass(frozen=True)
class _ParseFamily:
    """The source suffixes and AST options used by one indicator family."""

    suffixes: tuple[str, ...]
    type_comments: bool
    case_insensitive_suffixes: bool = False


_FAMILIES: dict[str, _ParseFamily] = {
    # These two entries preserve the current structure/coupling behavior.
    "structure": _ParseFamily((".py",), type_comments=False),
    "coupling": _ParseFamily((".py",), type_comments=False),
    # Verification historically parses stubs and asks the AST to validate
    # type comments, so its behavior is intentionally different.
    "verification": _ParseFamily(
        _PYTHON_SUFFIXES,
        type_comments=True,
        case_insensitive_suffixes=True,
    ),
    # Generic structure indicators retain the historical .py-only behavior;
    # callers that need stubs choose the verification family explicitly.
    "default": _ParseFamily((".py",), type_comments=False),
    "all": _ParseFamily(_PYTHON_SUFFIXES, type_comments=False),
}


@dataclass
class _ParseRecord:
    tree: ast.Module | None
    error: SyntaxErrorInfo | None
    parents: dict[int, ast.AST | None] = field(default_factory=dict)
    locations: dict[int, SourceLocation] = field(default_factory=dict)
    symbols: dict[int, tuple[str, ...]] = field(default_factory=dict)
    nodes: tuple[ast.AST, ...] = ()


def _family_policy(family: str) -> _ParseFamily:
    try:
        return _FAMILIES[family]
    except KeyError as exc:
        known = ", ".join(sorted(_FAMILIES))
        raise ValueError(f"unknown indicator source family {family!r}; choose {known}") from exc


def _path_matches_family(path: str, policy: _ParseFamily) -> bool:
    suffix = PurePosixPath(path).suffix
    if policy.case_insensitive_suffixes:
        suffix = suffix.lower()
    return suffix in policy.suffixes


def _line_texts(source: str) -> list[str]:
    # splitlines(keepends=True) omits the final empty item, but a source with
    # no text still has a useful conceptual line for defensive range checks.
    return source.splitlines(keepends=True) or [""]


def _byte_to_char_column(line: str, byte_column: int) -> int | None:
    if byte_column < 0:
        return None
    encoded = line.encode("utf-8")
    if byte_column > len(encoded):
        return None
    try:
        prefix = encoded[:byte_column].decode("utf-8")
    except UnicodeDecodeError:
        # A location in the middle of a code point is never an exact range.
        return None
    return len(prefix)


def _node_location(path: str, source: str, node: ast.AST) -> SourceLocation | None:
    fields = ("lineno", "col_offset", "end_lineno", "end_col_offset")
    if not all(hasattr(node, field_name) for field_name in fields):
        return None
    start_line = getattr(node, "lineno")
    start_col = getattr(node, "col_offset")
    end_line = getattr(node, "end_lineno")
    end_col = getattr(node, "end_col_offset")
    if not all(isinstance(value, int) for value in (start_line, start_col, end_line, end_col)):
        return None
    if start_line < 1 or end_line < start_line or start_col < 0 or end_col < 0:
        return None
    lines = _line_texts(source)
    if start_line > len(lines) or end_line > len(lines):
        return None
    start_char_col = _byte_to_char_column(lines[start_line - 1], start_col)
    end_char_col = _byte_to_char_column(lines[end_line - 1], end_col)
    if start_char_col is None or end_char_col is None:
        return None
    if (start_line, start_col) > (end_line, end_col):
        return None
    return SourceLocation(
        path=path,
        start_line=start_line,
        start_col=start_col,
        end_line=end_line,
        end_col=end_col,
        start_char_col=start_char_col,
        end_char_col=end_char_col,
    )


def _source_for_location(source: str, location: SourceLocation) -> str | None:
    lines = _line_texts(source)
    start_line = lines[location.start_line - 1]
    end_line = lines[location.end_line - 1]
    if location.start_line == location.end_line:
        return start_line[location.start_char_col : location.end_char_col]
    parts = [start_line[location.start_char_col :]]
    if location.end_line - location.start_line > 1:
        parts.extend(lines[location.start_line : location.end_line - 1])
    parts.append(end_line[: location.end_char_col])
    return "".join(parts)


def _symbol_name(node: ast.AST) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Lambda):
        return "<lambda>"
    return None


def _build_record(path: str, source: str, tree: ast.Module) -> _ParseRecord:
    parents: dict[int, ast.AST | None] = {}
    locations: dict[int, SourceLocation] = {}
    symbols: dict[int, tuple[str, ...]] = {}
    nodes: list[ast.AST] = []

    def visit(node: ast.AST, parent: ast.AST | None, symbol_path: tuple[str, ...]) -> None:
        parents[id(node)] = parent
        locations[id(node)] = _node_location(path, source, node)  # type: ignore[assignment]
        own_name = _symbol_name(node)
        node_symbols = symbol_path + (own_name,) if own_name is not None else symbol_path
        symbols[id(node)] = node_symbols
        nodes.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child, node, node_symbols)

    visit(tree, None, ())
    # Nodes are gathered in source/tree order.  This order is stable for
    # ``nodes_at`` and retains nested nodes instead of selecting one by line.
    exact_locations = {key: value for key, value in locations.items() if value is not None}
    return _ParseRecord(
        tree=tree,
        error=None,
        parents=parents,
        locations=exact_locations,
        symbols=symbols,
        nodes=tuple(nodes),
    )


@dataclass
class AnalysisContext:
    """Normalized sources plus lazy, family-specific syntax indexes."""

    sources: dict[str, str]
    revision: str | None = None
    _cache: dict[tuple[bool, str], _ParseRecord] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        normalized: dict[str, str] = {}
        for raw_path, raw_text in self.sources.items():
            path = normalize_path(raw_path)
            text = normalize_source(raw_text)
            previous = normalized.get(path)
            if previous is not None and previous != text:
                raise ValueError(f"source paths normalize to the same key with different text: {path!r}")
            normalized[path] = text
        self.sources = normalized
        if self.revision is not None and not isinstance(self.revision, str):
            raise TypeError("revision must be a string or None")

    @classmethod
    def from_sources(
        cls,
        sources: Mapping[str | os.PathLike[str], str | bytes],
        revision: str | None = None,
    ) -> "AnalysisContext":
        """Build a context from in-memory source text.

        Parsing is deferred until a family requests a file.  The returned
        source map is normalized once and shared by every family.
        """

        if not isinstance(sources, Mapping):
            raise TypeError("sources must be a path-to-source mapping")
        return cls(dict(sources), revision=revision)

    def paths(self, family: str = "default") -> tuple[str, ...]:
        """Return normalized Python source paths selected by a family."""

        policy = _family_policy(family)
        return tuple(
            path
            for path in sorted(self.sources)
            if _path_matches_family(path, policy)
        )

    def _record(self, path: str | os.PathLike[str], family: str) -> _ParseRecord | None:
        normalized_path = normalize_path(path)
        policy = _family_policy(family)
        if (
            normalized_path not in self.sources
            or not _path_matches_family(normalized_path, policy)
        ):
            return None
        # Families intentionally share a record when their AST parse mode is
        # identical.  This is the central benefit of the context: structure,
        # coupling, and generic scans do not each walk and parse the file.
        key = (policy.type_comments, normalized_path)
        if key in self._cache:
            return self._cache[key]
        source = self.sources[normalized_path]
        try:
            tree = ast.parse(
                source,
                filename=normalized_path,
                type_comments=policy.type_comments,
            )
        except SyntaxError as exc:
            error = SyntaxErrorInfo(
                path=normalized_path,
                message=str(exc.msg or "syntax error"),
                line=int(exc.lineno) if exc.lineno is not None else None,
                column=int(exc.offset) if exc.offset is not None else None,
                end_line=int(exc.end_lineno) if exc.end_lineno is not None else None,
                end_col=int(exc.end_offset) if exc.end_offset is not None else None,
            )
            record = _ParseRecord(tree=None, error=error)
        else:
            record = _build_record(normalized_path, source, tree)
        self._cache[key] = record
        return record

    def tree(self, path: str | os.PathLike[str], family: str = "default") -> ast.Module | None:
        """Return one cached AST, or ``None`` for unsupported/unparseable input."""

        record = self._record(path, family)
        return record.tree if record is not None else None

    def trees(self, family: str = "default") -> dict[str, ast.Module]:
        """Return successful trees in deterministic path order."""

        result: dict[str, ast.Module] = {}
        for path in self.paths(family):
            tree = self.tree(path, family)
            if tree is not None:
                result[path] = tree
        return result

    def syntax_errors(
        self,
        family: str = "default",
    ) -> dict[str, SyntaxErrorInfo]:
        """Return parse errors keyed by normalized path."""

        errors: dict[str, SyntaxErrorInfo] = {}
        for path in self.paths(family):
            record = self._record(path, family)
            if record is not None and record.error is not None:
                errors[path] = record.error
        return errors

    def parent(
        self,
        path: str | os.PathLike[str],
        node: ast.AST,
        family: str = "default",
    ) -> ast.AST | None:
        """Return an AST node's direct parent, preserving root ``None``."""

        record = self._record(path, family)
        if record is None or record.tree is None:
            return None
        return record.parents.get(id(node))

    def parents(
        self,
        path: str | os.PathLike[str],
        family: str = "default",
    ) -> dict[ast.AST, ast.AST | None]:
        """Return the cached parent index for one file."""

        record = self._record(path, family)
        if record is None or record.tree is None:
            return {}
        return {node: record.parents[id(node)] for node in record.nodes}

    def location(
        self,
        path: str | os.PathLike[str],
        node: ast.AST,
        family: str = "default",
    ) -> SourceLocation | None:
        """Return an exact location for an AST node when the AST supplies one."""

        record = self._record(path, family)
        if record is None or record.tree is None:
            return None
        return record.locations.get(id(node))

    def locations(
        self,
        path: str | os.PathLike[str],
        family: str = "default",
    ) -> dict[ast.AST, SourceLocation]:
        """Return exact locations for all indexed nodes that support ranges."""

        record = self._record(path, family)
        if record is None or record.tree is None:
            return {}
        return {
            node: record.locations[id(node)]
            for node in record.nodes
            if id(node) in record.locations
        }

    def enclosing_symbols(
        self,
        path: str | os.PathLike[str],
        node: ast.AST,
        family: str = "default",
    ) -> tuple[str, ...]:
        """Return the nested class/function names containing ``node``."""

        record = self._record(path, family)
        if record is None or record.tree is None:
            return ()
        return record.symbols.get(id(node), ())

    def source_block(
        self,
        path: str | os.PathLike[str],
        node: ast.AST,
        family: str = "default",
    ) -> SourceBlock | None:
        """Return an exact code block for an explicitly supplied AST node."""

        normalized_path = normalize_path(path)
        location = self.location(normalized_path, node, family)
        if location is None:
            return None
        code = _source_for_location(self.sources[normalized_path], location)
        if code is None:
            return None
        return SourceBlock(
            path=normalized_path,
            location=location,
            code=code,
            symbols=self.enclosing_symbols(normalized_path, node, family),
        )

    def nodes_at(
        self,
        path: str | os.PathLike[str],
        line: int,
        *,
        column: int | None = None,
        family: str = "default",
    ) -> tuple[ast.AST, ...]:
        """Return every indexed node on a line, in stable source order.

        A column, when supplied, is a UTF-8 byte column.  The method retains
        all matching nested nodes; it never guesses one node from a line-only
        finding.
        """

        if not isinstance(line, int) or line < 1:
            return ()
        record = self._record(path, family)
        if record is None or record.tree is None:
            return ()
        result: list[ast.AST] = []
        for node in record.nodes:
            location = record.locations.get(id(node))
            if location is None:
                continue
            if not (location.start_line <= line <= location.end_line):
                continue
            if column is not None:
                if not isinstance(column, int) or column < 0:
                    continue
                if location.start_line == location.end_line == line:
                    if not (location.start_col <= column < location.end_col):
                        continue
                elif line == location.start_line and column < location.start_col:
                    continue
                elif line == location.end_line and column >= location.end_col:
                    continue
            result.append(node)
        return tuple(result)

__all__ = [
    "AnalysisContext",
    "SourceBlock",
    "SourceLocation",
    "SyntaxErrorInfo",
    "normalize_path",
    "normalize_source",
]
