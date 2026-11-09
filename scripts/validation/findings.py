"""Normalize quality-check evidence into location-based review questions."""

from __future__ import annotations

from collections.abc import Mapping, Set
from pathlib import Path

from qca.evaluation import finding


def ruff_records(items: list, root: Path) -> list[dict]:
    return [finding(
        "ruff." + item["code"],
        {"path": Path(item["filename"]).resolve().relative_to(root.resolve()).as_posix(),
         "line": item["location"]["row"]},
        "Does this indicate a problem: " + item["message"] + "?", "ruff", item["code"],
        evidence={"message": item["message"]}, status="blocking",
    ) for item in items]


def type_records(diagnostics: list, root: Path) -> list[dict]:
    records = []
    for item in diagnostics:
        path = Path(item["file"]).resolve().relative_to(root.resolve()).as_posix()
        records.append(finding(
            "type." + item.get("rule", "diagnostic"),
            {"path": path, "line": item["range"]["start"]["line"] + 1},
            "Does this indicate a problem: " + item["message"] + "?", "basedpyright", item.get("rule", "diagnostic"),
            evidence={"message": item["message"], "severity": item["severity"]},
            status="blocking" if item["severity"] == "error" else "review",
        ))
    return records


def import_location(module: str, line: int | None, root: Path) -> dict:
    path = root.joinpath(*module.split("."))
    source = path.with_suffix(".py") if path.with_suffix(".py").is_file() else path / "__init__.py"
    return {"path": source.relative_to(root).as_posix(), "line": line, "symbol": module}


def import_records(report, root: Path) -> list[dict]:
    records = []
    for contract, check in report.get_contracts_and_checks():
        if check.kept:
            continue
        rule = contract.contract_options["id"]
        for group in check.metadata.get("invalid_chains", []):
            for chain in group["chains"]:
                places = [import_location(edge["importer"], next(iter(edge["line_numbers"]), None), root)
                          for edge in chain]
                records.append(finding("architecture.forbidden_dependency", places[0],
                                       "Does this dependency violate the intended architecture?",
                                       "import-linter", rule, evidence={"chain": chain},
                                       related=places[1:], status="blocking"))
        for package, edges in check.metadata.get("cycle_breakers_by_package", {}).items():
            for importer, imported in sorted(edges):
                details = report.graph.get_import_details(importer=importer, imported=imported)
                line = details[0]["line_number"] if details else None
                records.append(finding("architecture.dependency_cycle", import_location(importer, line, root),
                                       "Does this dependency create an unwanted cycle?", "import-linter", rule,
                                       evidence={"package": package, "importer": importer, "imported": imported},
                                       related=[import_location(imported, None, root)], status="blocking"))
    return records


def qca_records(report: dict, inspection: dict, *, changed_lines: Mapping[str, Set[int]]) -> list[dict]:
    return (qca_pattern_records(report) + qca_complexity_records(report)
            + qca_test_records(report, changed_lines) + inspection_records(inspection))


def qca_test_records(report: dict, changed_lines: Mapping[str, Set[int]]) -> list[dict]:
    """Normalize test candidates whose primary or related evidence overlaps a change."""

    records = []
    for item in report["factors"]["test_effectiveness"]["findings"]:
        evidence = item["evidence"]
        primary = evidence["primary"]
        related = evidence["related"]
        blocks = ([primary] if primary else []) + related
        changed = any(
            any(block["range"]["start"]["line"] <= line <= block["range"]["end"]["line"]
                for line in changed_lines.get(block["path"], ()))
            for block in blocks
        )
        if not primary:
            changed = changed or item["line"] in changed_lines.get(item["path"], ())
        if not changed:
            continue
        places = [{"path": block["path"], "line": block["range"]["start"]["line"]} for block in related]
        records.append(finding(
            "qca." + item["kind"], {"path": item["path"], "line": item["line"]},
            evidence["inspection_question"], "qca", item["indicator"]["id"],
            evidence={**evidence["pattern"], "uncertainty": evidence["uncertainty"]}, related=places,
        ))
    return records


def qca_pattern_records(report: dict) -> list[dict]:
    changes = report["diff"]["changed_callables"]

    def changed(path: str, line: int) -> bool:
        return any(c["path"] == path and c["after"] is not None
                   and c["after"]["start_line"] <= line <= c["after"]["end_line"] for c in changes)

    records = []
    for item in report["factors"]["patterns"]["findings"]:
        if item["kind"] not in {"opaque_conditional", "redundant_any_guard", "swallowed_exception"}:
            continue
        if changed(item["path"], item["line"]):
            evidence = item["evidence"]
            records.append(finding("qca." + item["kind"], {"path": item["path"], "line": item["line"]},
                                   evidence["inspection_question"], "qca", item["indicator"]["id"], evidence=evidence["pattern"]))
    for item in report["factors"]["redundancy"]["findings"]:
        occurrences = item.get("occurrences", [item])
        if any(changed(o["path"], o["line"]) for o in occurrences):
            places = [{"path": o["path"], "line": o["line"], "symbol": o.get("name")} for o in occurrences]
            records.append(finding("qca." + item["kind"], places[0], item["evidence"]["inspection_question"],
                                   "qca", item["indicator"]["id"], evidence={"match_basis": item.get("match_basis")},
                                   related=places[1:]))
    return records


def qca_complexity_records(report: dict) -> list[dict]:
    records = []
    for change in report["diff"]["changed_callables"]:
        before, after = change["before"], change["after"]
        if after is None:
            continue
        for metric, threshold in {"cyclomatic_complexity": 10, "logical_loc": 50, "max_nesting": 5}.items():
            old, new = (before[metric] if before else 0), after[metric]
            if new > max(old, threshold):
                records.append(finding("complexity." + metric,
                                       {"path": change["path"], "line": after["start_line"],
                                        "symbol": change["qualified_name"]},
                                       "Is this function unnecessarily complex?", "qca", metric,
                                       evidence={"before": old, "after": new, "threshold": threshold}))
    return records


def inspection_records(inspection: dict) -> list[dict]:
    records = []
    for item in inspection["unused"]:
        records.append(finding("unused." + item["kind"],
                               {"path": item["path"], "line": item["lines"][0], "symbol": item["symbol"]},
                               "Is this production code unnecessary?", "vulture", item["kind"],
                               evidence={"reason": item["reason"], "test_references": item["test_references"]}))
    seen = set()
    for item in inspection["comparisons"]:
        left, right = item["changed"], item["related"]
        key = tuple(sorted(( (left["path"], left["name"]), (right["path"], right["name"]) )))
        if key in seen:
            continue
        seen.add(key)
        place = lambda s: {"path": s["path"], "line": s["line"], "symbol": s["name"]}
        records.append(finding("reuse.related_functions", place(left),
                               "Is there duplication worth consolidating between these functions?",
                               "inspection", "shared_calls", evidence={"shared_calls": item["shared_calls"],
                               "score": item["score"], "relationship": item["relationship"]},
                               related=[place(right)]))
    return records
