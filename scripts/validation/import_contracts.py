"""Print the import contracts the project in the working directory breaks, as JSON.

The quality check runs this in a fresh interpreter from each directory it
compares, so import-linter locates that directory's packages rather than the
checker's own.  It imports nothing from the repository.

Each broken contract yields import chains: lists of ``{importer, imported,
line}`` edges, where the first edge's importer is where the chain starts.  A
contract's ``broken_contract_guidance`` option, if set, travels with them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from importlinter import api  # noqa: F401 - configures import-linter's adapters on import
from importlinter.application import use_cases


def main() -> int:
    sys.path.insert(0, str(Path.cwd()))
    options = use_cases.read_user_options(config_filename="pyproject.toml")
    # import-linter's own command registers contract types this way; the pinned version has no public equivalent.
    use_cases._register_contract_types(options)  # pyright: ignore[reportPrivateUsage]
    report = use_cases.create_report(options, cache_dir=None)
    broken = [
        {
            "contract": contract.contract_options["id"],
            "name": contract.name,
            "guidance": contract.contract_options.get("broken_contract_guidance", ""),
            "chain": chain,
        }
        for contract, check in report.get_contracts_and_checks() if not check.kept
        for chain in _chains(report.graph, check.metadata)
    ]
    json.dump(broken, sys.stdout)
    return 0


def _chains(graph, metadata: dict) -> list[list[dict]]:
    """Forbidden contracts report chains; acyclic contracts report package edges that close a cycle."""

    chains = [
        [{"importer": edge["importer"], "imported": edge["imported"], "line": min(edge["line_numbers"])} for edge in chain]
        for group in metadata.get("invalid_chains", [])
        for chain in group["chains"]
    ]
    for edges in metadata.get("cycle_breakers_by_package", {}).values():
        chains.extend([_direct_import(graph, importer, imported)] for importer, imported in sorted(edges))
    return chains


def _direct_import(graph, importer: str, imported: str) -> dict:
    """The first import from ``importer`` or its submodules into ``imported`` or its submodules."""

    targets = {imported, *graph.find_descendants(imported)}
    for module in sorted({importer, *graph.find_descendants(importer)}):
        for target in sorted(graph.find_modules_directly_imported_by(module) & targets):
            details = graph.get_import_details(importer=module, imported=target)
            return {"importer": module, "imported": target, "line": min(item["line_number"] for item in details)}
    return {"importer": importer, "imported": imported, "line": 1}


if __name__ == "__main__":
    raise SystemExit(main())
