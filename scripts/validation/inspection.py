"""Deterministic inspection candidates beyond lint; these are not removal proofs."""

from __future__ import annotations

import argparse
import io
import json
import math
import subprocess
import tarfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from vulture import Vulture, __version__ as VULTURE_VERSION

from qca.analyzer import classify_source
from qca.indicators import AnalysisContext, coupling
from scripts.validation.source_index import Symbol, reference_sites, symbols


PATHS = ("autonomy", "cli", "implementations", "deploy", "qca", "scripts/validation", "tests")
UNUSED_KINDS = {"attribute", "class", "function", "method", "property"}
LIMITATIONS = (
    "Vulture uses name-based static references; dynamic entrypoints and callbacks need inspection.",
    "Test references are listed separately and do not establish production use.",
    "Shared call spellings rank comparisons; aliases, bindings and equivalent behavior are not proved.",
    "Related functions are limited to the same module, directory or one import edge in either direction.",
)


@dataclass(frozen=True)
class Unused:
    path: str
    symbol: str
    kind: str
    lines: tuple[int, ...]
    confidence: int
    reason: str
    test_references: tuple[str, ...]
    code: tuple[str, ...]


@dataclass(frozen=True)
class Comparison:
    changed: Symbol
    related: Symbol
    shared_calls: tuple[str, ...]
    score: float
    relationship: str


@dataclass
class Inspection:
    unused: list[Unused]
    comparisons: list[Comparison]
    limitations: tuple[str, ...] = LIMITATIONS


def production(sources: dict[str, str]) -> dict[str, str]:
    return {path: source for path, source in sources.items() if classify_source(path, source) != "tests"}


@dataclass
class UnusedDefinition:
    lines: list[int]
    confidence: int
    spelling: str
    code: list[str]


def unused_symbols(sources: dict[str, str]) -> dict[tuple[str, str, str], UnusedDefinition]:
    scanner = Vulture()
    indexed = {path: symbols(path, source) for path, source in sources.items()}
    for path, source in sorted(sources.items()):
        scanner.scan(source, filename=path)
    # Apply Vulture's bundled allowances for imported libraries (e.g. AST visitors).
    scanner.scavenge([])
    grouped: dict[tuple[str, str, str], UnusedDefinition] = {}
    for item in scanner.get_unused_code():
        if item.typ not in UNUSED_KINDS or str(item.filename) not in indexed:
            continue
        path = str(item.filename)
        scopes = indexed[path]
        if item.typ == "attribute":
            scopes = [symbol for symbol in scopes if symbol.kind == "class"]
        owners = [symbol for symbol in scopes if symbol.line <= item.first_lineno <= symbol.end_line]
        owner = owners[-1].name if owners else ""
        name = f"{owner}.{item.name}" if item.typ == "attribute" and owner else owner or item.name
        definition = grouped.setdefault((path, name, item.typ), UnusedDefinition([], item.confidence, item.name, []))
        definition.lines.append(item.first_lineno)
        definition.code.append("\n".join(sources[path].splitlines()[item.first_lineno - 1:item.last_lineno]))
    return grouped


def unused_candidates(before: dict[str, str], after: dict[str, str]) -> list[Unused]:
    previous = unused_symbols(production(before))
    current = unused_symbols(production(after))
    tests = {path: source for path, source in after.items() if classify_source(path, source) == "tests"}
    references = reference_sites(tests)
    findings = []
    for key, definition in current.items():
        path, name, kind = key
        if key in previous and before.get(path) == after[path]:
            continue
        reason = "newly unused" if key not in previous else "existing in changed file"
        findings.append(Unused(path, name, kind, tuple(sorted(set(definition.lines))), definition.confidence, reason,
                               tuple(references.get(definition.spelling, ())), tuple(definition.code)))
    return sorted(findings, key=lambda item: (item.reason != "newly unused", item.path, item.symbol))


def import_neighbors(sources: dict[str, str]) -> dict[str, set[str]]:
    graph = coupling.analyze(AnalysisContext.from_sources(sources))["graph"]
    neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in graph["edges"]:
        neighbors[edge["source"]].add(edge["target"])
        neighbors[edge["target"]].add(edge["source"])
    return neighbors


def relationship(left: Symbol, right: Symbol, neighbors: dict[str, set[str]]) -> str:
    if left.path == right.path:
        # A nested function and its parent are not independent implementations.
        if left.name.startswith(right.name + ".") or right.name.startswith(left.name + "."):
            return ""
        return "same module"
    if right.path in neighbors.get(left.path, set()):
        return "import neighbor (static resolution)"
    if Path(left.path).parent == Path(right.path).parent:
        return "same directory"
    return ""


def related_candidates(before: dict[str, str], after: dict[str, str]) -> list[Comparison]:
    old = {symbol.key: symbol for path, source in production(before).items() for symbol in symbols(path, source)}
    current = [symbol for path, source in sorted(production(after).items()) for symbol in symbols(path, source)
               if symbol.kind == "callable"]
    neighbors = import_neighbors(production(after))
    frequencies = Counter(call for symbol in current for call in symbol.calls)
    weights = {call: math.log((1 + len(current)) / (1 + count)) + 1 for call, count in frequencies.items()}
    by_call: dict[str, set[int]] = defaultdict(set)
    for index, symbol in enumerate(current):
        for call in symbol.calls:
            by_call[call].add(index)
    findings = []
    for changed in current:
        previous = old.get(changed.key)
        if previous is not None and previous.shape == changed.shape:
            continue
        candidates = set().union(*(by_call[call] for call in changed.calls))
        findings.extend(rank_comparisons(changed, [current[index] for index in sorted(candidates)], neighbors, weights))
    return findings


def rank_comparisons(changed: Symbol, candidates: list[Symbol], neighbors: dict[str, set[str]],
                     weights: dict[str, float]) -> list[Comparison]:
    matches = []
    for related in candidates:
        if related.key == changed.key:
            continue
        shared = changed.calls & related.calls
        relation = relationship(changed, related, neighbors)
        if len(shared) < 2 or not relation:
            continue
        score = sum(weights[call] for call in sorted(shared)) / sum(
            weights[call] for call in sorted(changed.calls | related.calls))
        matches.append(Comparison(changed, related, tuple(sorted(shared)), round(score, 4), relation))
    return sorted(matches, key=lambda item: (-item.score, item.related.key))[:3]


def inspect_sources(before: dict[str, str], after: dict[str, str]) -> Inspection:
    return Inspection(unused_candidates(before, after), related_candidates(before, after))


def revision_sources(ref: str, paths: tuple[str, ...] = PATHS) -> tuple[str, dict[str, str]]:
    sha = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"], text=True).strip()
    archive = subprocess.check_output(["git", "archive", "--format=tar", sha])
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as entries:
        for entry in entries:
            if not entry.isfile() or not entry.name.endswith(".py"):
                continue
            if not any(Path(entry.name).is_relative_to(root) for root in paths):
                continue
            stream = entries.extractfile(entry)
            assert stream is not None
            source = stream.read().decode("utf-8")
            if classify_source(entry.name, source) in {"production", "tooling/scripts", "tests"}:
                sources[entry.name] = source
    return sha, sources


def inspect_revisions(base: str, head: str, output: Path, paths: tuple[str, ...] = PATHS) -> Inspection:
    base_sha, before = revision_sources(base, paths)
    head_sha, after = revision_sources(head, paths)
    inspection = inspect_sources(before, after)
    output.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "change_inspection_v1", "base_sha": base_sha, "head_sha": head_sha,
               "vulture_version": VULTURE_VERSION, **asdict(inspection)}
    # frozensets are emitted explicitly in sorted order for reproducible artifacts.
    output.joinpath("inspection.json").write_text(
        json.dumps(payload, indent=2, default=lambda value: sorted(value)) + "\n", encoding="utf-8")
    return inspection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    inspection = inspect_revisions(args.base, args.head, args.output)
    print(f"Inspection: {len(inspection.unused)} unused-code candidates; "
          f"{len(inspection.comparisons)} related-function comparisons. See {args.output / 'inspection.json'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
