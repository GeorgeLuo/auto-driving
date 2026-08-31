"""M007-08 CLI surface audit finalizer and validators."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .argv_validate import (
        ArgvValidationError,
        argv_from_shell_line,
        normalize_placeholders,
        validate_argv,
    )
    from .frozen_authority import (
        CANONICAL_US88_SOURCE,
        FROZEN_CITE_PATH_DIGESTS,
        FROZEN_CLAIM_MAP,
        FROZEN_LIVE_LEDGER_SHA256,
        FROZEN_LIVE_RESIDUALS,
        FROZEN_US_TEMPLATES,
        US88_SOURCE_CONTENT_SHA256,
        US88_SOURCE_RELPATH,
        USAGE_PATTERNS,
    )
    from .frozen_parser import (
        FROZEN_PARSER_SOURCE_COMMIT,
        FrozenParserError,
        run_frozen_parser_audit,
    )
    from .parser_walk import (
        action_leaf_ids,
        leaf_skeleton,
        leaf_supports_json,
        public_leaf_ids,
        walk_leaves,
    )
except ImportError:  # script / path execution
    from argv_validate import (
        ArgvValidationError,
        argv_from_shell_line,
        normalize_placeholders,
        validate_argv,
    )
    from frozen_authority import (
        CANONICAL_US88_SOURCE,
        FROZEN_CITE_PATH_DIGESTS,
        FROZEN_CLAIM_MAP,
        FROZEN_LIVE_LEDGER_SHA256,
        FROZEN_LIVE_RESIDUALS,
        FROZEN_US_TEMPLATES,
        US88_SOURCE_CONTENT_SHA256,
        US88_SOURCE_RELPATH,
        USAGE_PATTERNS,
    )
    from frozen_parser import (
        FROZEN_PARSER_SOURCE_COMMIT,
        FrozenParserError,
        run_frozen_parser_audit,
    )
    from parser_walk import (
        action_leaf_ids,
        leaf_skeleton,
        leaf_supports_json,
        public_leaf_ids,
        walk_leaves,
    )

ROOT = Path(__file__).resolve().parents[5]
TOOL_DIR = Path(__file__).resolve().parent
M007 = ROOT / "docs" / "milestones" / "007-cli-operator-usability"
EVIDENCE_DIR = M007 / "evidence" / "cli-surface-audit"

SCHEMA = "m007_cli_surface_audit_v1"
CATALOG_PATH = TOOL_DIR / "us88_catalog.json"
LEAVES_PATH = TOOL_DIR / "leaf_inventory.json"
OVERLAY_PATH = TOOL_DIR / "leaf_overlay.json"
SEQUENCES_PATH = TOOL_DIR / "sequence_registry.json"
CLAIM_MAP_PATH = TOOL_DIR / "claim_map.json"
LIVE_FINDINGS_PATH = (
    M007 / "evidence" / "live-cli-acceptance" / "exploratory-findings.md"
)

REQUIRED_OVERLAY_FIELDS = (
    "usage",
    "prerequisites",
    "side_effects",
    "safety_class",
    "output_contract",
    "json_capability",
    "owning_boundary",
    "validation_class",
    "open_finding_links",
)
SAFETY_CLASSES = {
    "observe_only",
    "local_write",
    "live_mutation",
    "hazardous_external",
    "meta_docs",
}
VALIDATION_CLASSES = {
    "deterministic",
    "live",
    "documented_only",
    "unsafe_not_executed",
}
# Offline / process-local leaves whose validation path is deterministic evidence.
DETERMINISTIC_LEAVES = {
    "vehicles.memory.replay",
    "vehicles.perception.apply",
    "vehicles.perception.candidates",
    "vehicles.perception.compare",
    "vehicles.perception.qualify",
}
LEAF_KINDS = {"action", "meta", "alias"}
JSON_CAPABLE_SENTENCE = "Supports --json."
JSON_ABSENT_SENTENCE = "No --json flag on this leaf."
BOILERPLATE_OUTPUT_CONTRACTS = {
    "Human summary and optional machine payload for this leaf. Supports --json.",
    "Human summary and optional machine payload for this leaf. No --json flag on this leaf.",
    "Human summary and optional machine payload for this leaf.",
}
BOILERPLATE_PREREQUISITES = {
    "Repository checkout; Metrics UI when live",
}


def derived_json_capability(parser_json: bool) -> str:
    return JSON_CAPABLE_SENTENCE if parser_json else JSON_ABSENT_SENTENCE
DISPOSITIONS = {"passed", "ready", "blocked", "deferred"}
COMPLETENESS = {"stub", "template", "catalog_ready", "evidenced"}
COVERAGE = {"measured", "unmeasured", "not_applicable"}
EXECUTION = {"never", "machine_only", "hitl", "blocked"}


class AuditError(Exception):
    """Audit validation failed."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_keys(obj: dict[str, Any], keys: tuple[str, ...], *, where: str) -> None:
    missing = [key for key in keys if key not in obj]
    if missing:
        raise AuditError(f"{where} missing required fields: {', '.join(missing)}")


def _na_ok(value: Any, *, field: str, where: str) -> None:
    if value == "not_applicable" or (
        isinstance(value, dict)
        and value.get("value") == "not_applicable"
    ):
        reason = value.get("reason") if isinstance(value, dict) else None
        if isinstance(value, dict) and (not reason or not str(reason).strip()):
            raise AuditError(f"{where}.{field} not_applicable requires reason")
        return
    if value is None or value == "" or value == []:
        raise AuditError(f"{where}.{field} omitted or empty")


def catalog_digest(path: Path = CATALOG_PATH) -> str:
    return _sha256_file(path)


def _require_document_schema(
    doc: dict[str, Any],
    expected: str,
    *,
    where: str,
) -> None:
    if not isinstance(doc, dict):
        raise AuditError(f"{where} must be an object")
    if doc.get("schema") != expected:
        raise AuditError(
            f"{where} schema must be {expected!r}; got {doc.get('schema')!r}"
        )


def load_catalog(
    path: Path = CATALOG_PATH,
    *,
    anchor_path: Path | None = None,
) -> dict[str, Any]:
    data = _load_json(path)
    if data.get("schema") != "m007_us88_catalog_v1":
        raise AuditError(f"unexpected catalog schema {data.get('schema')!r}")
    source = data.get("source")
    if not isinstance(source, dict):
        raise AuditError("catalog source provenance object is required")
    _require_keys(
        source,
        ("url", "comment_id", "title"),
        where="catalog.source",
    )
    if source != CANONICAL_US88_SOURCE:
        raise AuditError(
            "catalog.source must equal frozen CANONICAL_US88_SOURCE "
            f"(url/comment_id/title); got {source!r}"
        )
    if data.get("source_content_sha256") != US88_SOURCE_CONTENT_SHA256:
        raise AuditError(
            "catalog.source_content_sha256 must equal frozen US88_SOURCE_CONTENT_SHA256"
        )
    if data.get("source_path") != US88_SOURCE_RELPATH:
        raise AuditError(f"catalog.source_path must be {US88_SOURCE_RELPATH!r}")
    source_file = path.parent / "us88_source.md"
    if not source_file.is_file():
        # also allow repo-relative resolution via CWD root
        alt = ROOT / US88_SOURCE_RELPATH
        source_file = alt if alt.is_file() else source_file
    if not source_file.is_file():
        raise AuditError(f"independent #88 source file missing: {US88_SOURCE_RELPATH}")
    source_digest = _sha256_file(source_file)
    if source_digest != US88_SOURCE_CONTENT_SHA256:
        raise AuditError(
            "us88_source.md digest does not match frozen US88_SOURCE_CONTENT_SHA256"
        )
    anchor_src = source_file.with_suffix(".sha256")
    if anchor_src.is_file():
        if anchor_src.read_text(encoding="utf-8").strip().split()[0] != source_digest:
            raise AuditError("us88_source.sha256 does not match us88_source.md")
    entries = data.get("entries")
    if not isinstance(entries, list) or len(entries) != 10:
        raise AuditError("catalog must contain exactly 10 US entries")
    ids = [entry.get("id") for entry in entries]
    expected = [f"US-{i:02d}" for i in range(1, 11)]
    if ids != expected:
        raise AuditError(f"catalog ids must be ordered US-01..US-10, got {ids}")
    for entry in entries:
        _require_keys(
            entry,
            (
                "id",
                "operator_outcome",
                "primary_human_confirmation",
                "operator_question",
                "required_command_templates",
                "required_cleanup_templates",
                "required_command_leaves",
                "required_cleanup_leaves",
            ),
            where=f"catalog {entry.get('id')}",
        )
        us_id = entry["id"]
        frozen = FROZEN_US_TEMPLATES.get(us_id)
        if frozen is None:
            raise AuditError(f"no frozen template for {us_id}")
        for field in (
            "operator_question",
            "operator_outcome",
            "primary_human_confirmation",
            "required_command_templates",
            "required_cleanup_templates",
            "required_command_leaves",
            "required_cleanup_leaves",
            "source_anchor",
            "command_deltas",
            "source_status",
        ):
            if entry.get(field) != frozen.get(field):
                raise AuditError(
                    f"catalog {us_id}.{field} must equal frozen FROZEN_US_TEMPLATES"
                )
        anchor = entry.get("source_anchor")
        if not isinstance(anchor, str) or not anchor.startswith("### US-"):
            raise AuditError(
                f"catalog {us_id}.source_anchor must be a ### US-… heading string"
            )
        deltas = entry.get("command_deltas")
        if not isinstance(deltas, list):
            raise AuditError(f"catalog {us_id}.command_deltas must be a list")
        for i, delta in enumerate(deltas):
            if not isinstance(delta, dict):
                raise AuditError(f"catalog {us_id}.command_deltas[{i}] must be object")
            for key in (
                "dimension",
                "source_value",
                "current_value",
                "rationale",
                "evidence",
            ):
                if not str(delta.get(key) or "").strip():
                    raise AuditError(
                        f"catalog {us_id}.command_deltas[{i}].{key} required"
                    )
    validate_source_command_shapes(data)
    digest = catalog_digest(path)
    anchor = anchor_path or path.with_name("us88_catalog.sha256")
    if not anchor.is_file():
        raise AuditError(f"catalog content anchor missing: {anchor}")
    anchored = anchor.read_text(encoding="utf-8").strip().split()[0]
    if anchored != digest:
        raise AuditError(
            f"catalog digest {digest[:12]} != committed anchor {anchored[:12]}"
        )
    return data



def _cmd_prefix(cmd: list[Any], n: int) -> list[str]:
    return [str(part) for part in cmd[:n]]


def _cmd_has(cmd: list[Any], path: list[str], required: list[str]) -> bool:
    if _cmd_prefix(cmd, len(path)) != path:
        return False
    return all(token in [str(part) for part in cmd] for token in required)


def _require_ordered_phases(
    cmds: list[Any],
    phases: list[tuple[str, list[str], list[str]]],
    *,
    where: str,
) -> None:
    cursor = 0
    for name, path, required in phases:
        found = None
        for index in range(cursor, len(cmds)):
            if _cmd_has(cmds[index], path, required):
                found = index
                break
        if found is None:
            raise AuditError(f"{where} missing ordered phase {name}")
        cursor = found + 1


# Derived from us88_source.md US-05/US-06/US-07 narratives, not from FROZEN_US_TEMPLATES.
US06_SOURCE_PHASES = [
    ("baseline_update_perception", ["vehicles", "update", "perception"], ["--algorithm", "visual_observer"]),
    ("baseline_update_memory", ["vehicles", "update", "memory"], ["--implementation", "bounded_evidence"]),
    ("baseline_run", ["vehicles", "automation", "run"], ["--observe-only", "--open-view"]),
    ("baseline_stream_memory", ["vehicles", "stream", "memory"], ["--once"]),
    ("baseline_check", ["vehicles", "memory", "check"], ["--record"]),
    ("baseline_stop", ["vehicles", "automation", "stop"], []),
    ("ablate_disable", ["vehicles", "perception", "disable"], ["motion_tracks"]),
    ("ablate_run", ["vehicles", "automation", "run"], ["--observe-only", "--open-view"]),
    ("ablate_check", ["vehicles", "memory", "check"], ["--record"]),
]
US07_SOURCE_PHASES = [
    ("run_interval", ["vehicles", "automation", "run"], ["--interval-s", "--open-view"]),
    ("inspect1_status", ["vehicles", "automation", "status"], []),
    ("inspect1_memory", ["vehicles", "stream", "memory"], ["--once"]),
    ("inspect2_status", ["vehicles", "automation", "status"], []),
    ("inspect2_memory", ["vehicles", "stream", "memory"], ["--once"]),
    ("stop", ["vehicles", "automation", "stop"], []),
    ("post_stop_status", ["vehicles", "automation", "status"], []),
]


def validate_source_command_shapes(catalog: dict[str, Any]) -> None:
    """Fail closed if templates drop #88 ordered narrative phases.

    Phase lists live outside ``FROZEN_US_TEMPLATES`` so duplicating an
    omission into catalog JSON and frozen constants cannot pass.
    """

    by_id = {entry["id"]: entry for entry in catalog["entries"]}
    us06 = by_id["US-06"]
    _require_ordered_phases(
        us06["required_command_templates"],
        US06_SOURCE_PHASES,
        where="US-06",
    )
    if not any(
        _cmd_has(cmd, ["vehicles", "perception", "enable"], ["motion_tracks"])
        for cmd in us06["required_cleanup_templates"]
    ):
        raise AuditError("US-06 source shape requires cleanup restore of motion_tracks")

    us07 = by_id["US-07"]
    _require_ordered_phases(
        us07["required_command_templates"],
        US07_SOURCE_PHASES,
        where="US-07",
    )


def validate_sequence_source_prerequisites(sequences: dict[str, Any]) -> None:
    """Bind narrative prerequisites that are not themselves CLI commands."""

    by_id = {row["id"]: row for row in sequences["sequences"]}
    us06 = " ".join(str(by_id["US-06"].get("prerequisites") or "").lower().split())
    if "us-05" not in us06 or "motion_tracks" not in us06:
        raise AuditError(
            "US-06 prerequisites must name the US-05 baseline and motion_tracks ablation"
        )
    us07 = " ".join(str(by_id["US-07"].get("prerequisites") or "").lower().split())
    if "observer" not in us07 and "perception" not in us07:
        raise AuditError(
            "US-07 prerequisites must name the preconfigured expensive observer/perception"
        )
    if "interval" not in us07:
        raise AuditError(
            "US-07 prerequisites must name the operator-chosen inspection interval"
        )


def validate_leaf_inventory_document(
    inventory_doc: dict[str, Any],
    *,
    parser: argparse.ArgumentParser | None = None,
) -> list[str]:
    """Validate full leaf inventory document schema, membership, and help skeleton."""

    if not isinstance(inventory_doc, dict):
        raise AuditError("leaf inventory document must be an object")
    if inventory_doc.get("schema") != "m007_leaf_inventory_v1":
        raise AuditError(
            f"unexpected leaf inventory schema {inventory_doc.get('schema')!r}"
        )
    if inventory_doc.get("program") != "automa":
        raise AuditError("leaf inventory program must be 'automa'")
    generator = inventory_doc.get("generator")
    if not isinstance(generator, dict):
        raise AuditError("leaf inventory generator provenance object is required")
    _require_keys(
        generator,
        ("name", "revision"),
        where="leaf inventory generator",
    )
    if not str(generator.get("name") or "").strip():
        raise AuditError("leaf inventory generator.name must be non-empty")
    revision = str(generator.get("revision") or "").strip()
    if not revision:
        raise AuditError("leaf inventory generator.revision must be non-empty")
    if re.fullmatch(r"[0-9a-f]{7,40}", revision) is None:
        raise AuditError(
            "leaf inventory generator.revision must be a git commit id "
            f"(got {revision!r})"
        )
    source_commit = str(generator.get("source_commit") or revision).strip()
    if re.fullmatch(r"[0-9a-f]{7,40}", source_commit) is None:
        raise AuditError(
            "leaf inventory generator.source_commit must be a git commit id"
        )
    if revision != FROZEN_PARSER_SOURCE_COMMIT:
        raise AuditError(
            "leaf inventory generator.revision must equal frozen parser source "
            f"{FROZEN_PARSER_SOURCE_COMMIT}"
        )
    if source_commit != FROZEN_PARSER_SOURCE_COMMIT:
        raise AuditError(
            "leaf inventory generator.source_commit must equal frozen parser source "
            f"{FROZEN_PARSER_SOURCE_COMMIT}"
        )
    inventory = inventory_doc.get("leaves")
    if not isinstance(inventory, list):
        raise AuditError("leaf inventory leaves must be a list")
    return validate_leaf_membership(inventory=inventory, parser=parser)


def validate_leaf_membership(
    *,
    inventory: list[dict[str, Any]],
    parser: argparse.ArgumentParser | None = None,
) -> list[str]:
    from cli.automa_cli.app import build_parser

    parser = parser or build_parser()
    actual_leaves = {
        leaf.leaf_id: leaf
        for leaf in walk_leaves(parser, include_help_meta=True)
    }
    actual = list(actual_leaves)
    recorded = [row["leaf_id"] for row in inventory]
    if sorted(actual) != sorted(recorded):
        missing = sorted(set(actual) - set(recorded))
        extra = sorted(set(recorded) - set(actual))
        raise AuditError(
            "leaf membership mismatch "
            f"missing={missing} extra={extra}"
        )
    if len(recorded) != len(set(recorded)):
        raise AuditError("duplicate leaf ids in inventory")
    for row in inventory:
        if not isinstance(row, dict):
            raise AuditError("inventory row must be an object")
        _require_keys(
            row, ("leaf_id", "tokens", "help", "kind"), where="inventory row"
        )
        leaf_id = row["leaf_id"]
        tokens = row["tokens"]
        if not isinstance(tokens, list) or not tokens:
            raise AuditError(f"inventory {leaf_id} tokens must be non-empty list")
        if ".".join(str(t) for t in tokens) != leaf_id:
            raise AuditError(
                f"inventory {leaf_id} tokens {tokens!r} do not form leaf_id"
            )
        expected = actual_leaves[leaf_id]
        if tuple(tokens) != expected.tokens:
            raise AuditError(
                f"inventory {leaf_id} tokens mismatch parser walk"
            )
        kind = row.get("kind")
        if kind not in LEAF_KINDS:
            raise AuditError(f"inventory {leaf_id}.kind invalid: {kind!r}")
        if kind != expected.kind:
            raise AuditError(
                f"inventory {leaf_id}.kind {kind!r} != parser-derived {expected.kind!r}"
            )
        if kind == "alias":
            alias_of = row.get("alias_of")
            if alias_of != expected.alias_of or not alias_of:
                raise AuditError(
                    f"inventory {leaf_id}.alias_of must be {expected.alias_of!r}"
                )
            if alias_of not in actual_leaves or actual_leaves[alias_of].kind != "meta":
                raise AuditError(
                    f"inventory {leaf_id}.alias_of {alias_of!r} must be a meta help leaf"
                )
        help_text = row.get("help")
        if not isinstance(help_text, str) or not help_text.strip():
            raise AuditError(f"inventory {leaf_id} help must be a non-empty string")
        if help_text.strip() != (expected.help or "").strip():
            raise AuditError(
                f"inventory {leaf_id} help summary does not match parser-derived "
                f"skeleton ({help_text!r} != {expected.help!r})"
            )
    return actual


def validate_overlay(
    *,
    leaf_ids: list[str],
    overlay: dict[str, Any],
    parser: argparse.ArgumentParser | None = None,
    inventory_kinds: dict[str, str] | None = None,
) -> None:
    rows = overlay.get("leaves")
    if not isinstance(rows, dict):
        raise AuditError("leaf_overlay.leaves must be an object keyed by leaf_id")
    patterns = overlay.get("usage_patterns")
    if not isinstance(patterns, dict) or not patterns:
        raise AuditError("leaf_overlay.usage_patterns vocabulary is required")
    for key, desc in USAGE_PATTERNS.items():
        if patterns.get(key) != desc:
            raise AuditError(
                f"leaf_overlay.usage_patterns[{key!r}] must match frozen vocabulary"
            )
    missing = sorted(set(leaf_ids) - set(rows))
    extra = sorted(set(rows) - set(leaf_ids))
    if missing or extra:
        raise AuditError(
            f"overlay membership mismatch missing={missing} extra={extra}"
        )
    if parser is None:
        from cli.automa_cli.app import build_parser

        parser = build_parser()
    actual_kinds = {
        leaf.leaf_id: leaf.kind
        for leaf in walk_leaves(parser, include_help_meta=True)
    }
    if inventory_kinds is None:
        inventory_kinds = actual_kinds
    for leaf_id, row in rows.items():
        if not isinstance(row, dict):
            raise AuditError(f"overlay {leaf_id} must be an object")
        _require_keys(row, REQUIRED_OVERLAY_FIELDS, where=f"overlay {leaf_id}")
        for field in REQUIRED_OVERLAY_FIELDS:
            if field == "open_finding_links":
                if not isinstance(row[field], list):
                    raise AuditError(
                        f"overlay {leaf_id}.open_finding_links must be a list"
                    )
                continue
            if field == "safety_class":
                val = row[field]
                if isinstance(val, dict) and val.get("value") == "not_applicable":
                    _na_ok(val, field=field, where=f"overlay {leaf_id}")
                elif val == "not_applicable":
                    raise AuditError(
                        f"overlay {leaf_id}.safety_class not_applicable must use "
                        "{value, reason} object form"
                    )
                elif val not in SAFETY_CLASSES:
                    raise AuditError(
                        f"overlay {leaf_id}.safety_class invalid: {val!r}"
                    )
                continue
            if field == "validation_class":
                if row[field] not in VALIDATION_CLASSES:
                    raise AuditError(
                        f"overlay {leaf_id}.validation_class invalid: {row[field]!r}"
                    )
                continue
            if field == "usage":
                usage = row[field]
                if isinstance(usage, dict) and usage.get("value") in {
                    "unsupported",
                    "deprecated",
                    "not_applicable",
                }:
                    if usage.get("value") == "not_applicable":
                        _na_ok(usage, field=field, where=f"overlay {leaf_id}")
                    continue
                if isinstance(usage, list) and usage:
                    unknown = [u for u in usage if u not in patterns]
                    if unknown:
                        raise AuditError(
                            f"overlay {leaf_id}.usage unknown patterns {unknown}"
                        )
                    continue
                if isinstance(usage, str) and usage.strip():
                    if usage not in patterns:
                        raise AuditError(
                            f"overlay {leaf_id}.usage unknown pattern {usage!r}"
                        )
                    continue
                raise AuditError(f"overlay {leaf_id}.usage invalid: {usage!r}")
            if field == "side_effects":
                side = row[field]
                if isinstance(side, dict) and side.get("value") == "not_applicable":
                    _na_ok(side, field=field, where=f"overlay {leaf_id}")
                    continue
                if not isinstance(side, str) or not side.strip():
                    raise AuditError(f"overlay {leaf_id}.side_effects must be non-empty")
                if leaf_id in {
                    "vehicles.automation.stop",
                    "vehicles.memory.check",
                    "vehicles.memory.reset",
                    "vehicles.perception.enable",
                    "vehicles.perception.disable",
                    "vehicles.perception.setup",
                    "simulators.ensure",
                } and re.search(r"read-?only", side, re.I):
                    raise AuditError(
                        f"overlay {leaf_id}.side_effects falsely claims read-only"
                    )
                if leaf_id == "simulators.status" and re.search(
                    r"reconfigure|prepare", side, re.I
                ):
                    raise AuditError(
                        "simulators.status side_effects must not claim prepare/reconfigure"
                    )
                continue
            if field == "output_contract":
                out = row[field]
                if not isinstance(out, str) or not out.strip():
                    raise AuditError(
                        f"overlay {leaf_id}.output_contract must be non-empty"
                    )
                if out.strip() in BOILERPLATE_OUTPUT_CONTRACTS:
                    raise AuditError(
                        f"overlay {leaf_id}.output_contract is boilerplate filler"
                    )
                if "--json" in out:
                    raise AuditError(
                        f"overlay {leaf_id}.output_contract must not mention --json; "
                        "store capability only in json_capability"
                    )
                continue
            if field == "json_capability":
                cap = row[field]
                if not isinstance(cap, str) or not cap.strip():
                    raise AuditError(
                        f"overlay {leaf_id}.json_capability must be non-empty"
                    )
                continue
            if field == "prerequisites":
                prereq = row[field]
                if isinstance(prereq, dict) and prereq.get("value") == "not_applicable":
                    _na_ok(prereq, field=field, where=f"overlay {leaf_id}")
                    continue
                if not isinstance(prereq, str) or not prereq.strip():
                    raise AuditError(
                        f"overlay {leaf_id}.prerequisites must be non-empty"
                    )
                if prereq.strip() in BOILERPLATE_PREREQUISITES:
                    raise AuditError(
                        f"overlay {leaf_id}.prerequisites is boilerplate filler"
                    )
                continue
            if field == "owning_boundary":
                owner = row[field]
                if not isinstance(owner, str) or not owner.strip():
                    raise AuditError(
                        f"overlay {leaf_id}.owning_boundary must be non-empty"
                    )
                expected_owners = {
                    "vehicles.update.perception": "cli/automa_cli/perception.py",
                    "vehicles.update.decision": "cli/automa_cli/decision.py",
                    "vehicles.update.memory": "cli/automa_cli/memory.py",
                    "vehicles.update.core": "cli/automa_cli/deploy.py",
                    "vehicles.update.autonomy": "cli/automa_cli/deploy.py",
                    "simulators.status": "cli/automa_cli/simulators.py",
                    "simulators.ensure": "cli/automa_cli/simulators.py",
                    "vehicles.memory.replay": "cli/automa_cli/memory.py",
                    "vehicles.memory.reset": "cli/automa_cli/memory.py",
                    "vehicles.memory.check": "cli/automa_cli/memory_check.py",
                    "vehicles.perception.qualify": "cli/automa_cli/physical_qualify.py",
                    "vehicles.perception.check": "cli/automa_cli/physical_check.py",
                    "vehicles.perception.viability": "cli/automa_cli/physical_viability.py",
                    "vehicles.stream.memory": "cli/automa_cli/memory.py",
                    "vehicles.stream.perception": "cli/automa_cli/streaming.py",
                }
                if leaf_id in expected_owners and owner != expected_owners[leaf_id]:
                    raise AuditError(
                        f"overlay {leaf_id}.owning_boundary must be "
                        f"{expected_owners[leaf_id]!r}"
                    )
                continue
            _na_ok(row[field], field=field, where=f"overlay {leaf_id}")
            if row[field] == "not_applicable":
                raise AuditError(
                    f"overlay {leaf_id}.{field} must use "
                    "{value: not_applicable, reason: ...} object form"
                )
        # Argparse owns JSON capability. Store it structurally, not in prose.
        if "supports_json" not in row:
            raise AuditError(f"overlay {leaf_id} missing supports_json")
        supports = row.get("supports_json")
        if not isinstance(supports, bool):
            raise AuditError(f"overlay {leaf_id}.supports_json bool is required")
        tokens = leaf_id.split(".")
        parser_json = leaf_supports_json(parser, tokens)
        if supports != parser_json:
            raise AuditError(
                f"overlay {leaf_id}.supports_json={supports} does not match "
                f"argparse --json presence={parser_json}"
            )
        expected_cap = derived_json_capability(parser_json)
        if row.get("json_capability") != expected_cap:
            raise AuditError(
                f"overlay {leaf_id}.json_capability must equal {expected_cap!r} "
                f"(argparse --json={parser_json})"
            )

        kind = row.get("kind")
        if kind not in LEAF_KINDS:
            raise AuditError(f"overlay {leaf_id}.kind invalid or missing: {kind!r}")
        expected_kind = inventory_kinds.get(leaf_id) or actual_kinds.get(leaf_id)
        if kind != expected_kind:
            raise AuditError(
                f"overlay {leaf_id}.kind {kind!r} != inventory/parser {expected_kind!r}"
            )
        if kind in {"meta", "alias"}:
            if row.get("safety_class") != "meta_docs":
                raise AuditError(
                    f"overlay {leaf_id} {kind} leaf must use safety_class meta_docs"
                )
            if row.get("validation_class") != "documented_only":
                raise AuditError(
                    f"overlay {leaf_id} {kind} leaf must use validation_class documented_only"
                )
            if kind == "alias":
                alias_of = row.get("alias_of")
                if not isinstance(alias_of, str) or not (
                    alias_of == "help" or alias_of.endswith(".help")
                ):
                    raise AuditError(
                        f"overlay {leaf_id}.alias_of must bind a help leaf"
                    )

        if leaf_id == "vehicles.perception.qualify":
            if row.get("safety_class") != "local_write":
                raise AuditError(
                    "overlay vehicles.perception.qualify safety_class must be local_write"
                )
        vclass = row.get("validation_class")
        if leaf_id in DETERMINISTIC_LEAVES and vclass != "deterministic":
            raise AuditError(
                f"overlay {leaf_id}.validation_class must be deterministic "
                f"(offline/process-local boundary); got {vclass!r}"
            )
        if kind in {"meta", "alias"} and vclass not in {"documented_only"}:
            raise AuditError(
                f"overlay {leaf_id} {kind} validation_class must be documented_only"
            )


def _normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def validate_sequences(
    *,
    catalog: dict[str, Any],
    catalog_sha: str,
    sequences: dict[str, Any],
    leaf_ids: set[str],
    parser: argparse.ArgumentParser | None = None,
) -> list[dict[str, Any]]:
    rows = sequences.get("sequences")
    if not isinstance(rows, list):
        raise AuditError("sequence_registry.sequences must be a list")
    root_digest = sequences.get("catalog_digest")
    if root_digest != catalog_sha:
        raise AuditError(
            "sequence_registry.catalog_digest "
            f"{str(root_digest)[:12]} != catalog {catalog_sha[:12]}"
        )
    catalog_by_id = {entry["id"]: entry for entry in catalog["entries"]}
    seen: set[str] = set()
    argv_receipts: list[dict[str, Any]] = []

    if len(rows) != 10:
        raise AuditError(f"expected 10 sequence rows, got {len(rows)}")
    validate_sequence_source_prerequisites(sequences)

    for row in rows:
        if not isinstance(row, dict):
            raise AuditError("sequence row must be an object")
        _require_keys(
            row,
            (
                "id",
                "source_us_id",
                "catalog_digest",
                "operator_question",
                "operator_outcome",
                "primary_confirmation",
                "prerequisites",
                "commands",
                "cleanup",
                "safety_class",
                "execution",
                "disposition",
                "completeness",
                "coverage",
            ),
            where="sequence row",
        )
        us_id = row["id"]
        if us_id in seen:
            raise AuditError(f"duplicate sequence id {us_id}")
        seen.add(us_id)
        if row["source_us_id"] != us_id:
            raise AuditError(f"{us_id} source_us_id mismatch")
        if row["catalog_digest"] != catalog_sha:
            raise AuditError(
                f"{us_id} catalog_digest mismatch "
                f"{row['catalog_digest'][:12]} != {catalog_sha[:12]}"
            )
        source = catalog_by_id.get(us_id)
        if source is None:
            raise AuditError(f"{us_id} not in catalog")
        if _normalize_question(row["operator_question"]) != _normalize_question(
            source["operator_question"]
        ):
            raise AuditError(
                f"{us_id} operator_question does not match catalog (swap/alter)"
            )
        if _normalize_question(row["operator_outcome"]) != _normalize_question(
            source["operator_outcome"]
        ):
            raise AuditError(
                f"{us_id} operator_outcome does not match catalog (swap/alter)"
            )
        if _normalize_question(row["primary_confirmation"]) != _normalize_question(
            source["primary_human_confirmation"]
        ):
            raise AuditError(
                f"{us_id} primary_confirmation does not match catalog "
                "(primary_human_confirmation)"
            )

        # Commands/cleanup must equal the frozen #88-derived templates.
        expected_commands = source.get("required_command_templates")
        expected_cleanup = source.get("required_cleanup_templates")
        if not isinstance(expected_commands, list) or not expected_commands:
            raise AuditError(f"catalog {us_id} missing required_command_templates")
        if row.get("commands") != expected_commands:
            raise AuditError(
                f"{us_id} commands must equal catalog required_command_templates"
            )
        if expected_cleanup is None:
            expected_cleanup = []
        if row.get("cleanup") != expected_cleanup:
            raise AuditError(
                f"{us_id} cleanup must equal catalog required_cleanup_templates"
            )
        required_leaves = source.get("required_command_leaves") or []
        required_cleanup_leaves = source.get("required_cleanup_leaves") or []

        if row["disposition"] not in DISPOSITIONS:
            raise AuditError(f"{us_id} invalid disposition")
        if row["completeness"] not in COMPLETENESS:
            raise AuditError(f"{us_id} invalid completeness")
        for field in (
            "operator_question",
            "operator_outcome",
            "primary_confirmation",
            "prerequisites",
        ):
            if not str(row.get(field) or "").strip():
                raise AuditError(f"{us_id} {field} must be non-empty")
        if row.get("safety_class") not in SAFETY_CLASSES:
            raise AuditError(
                f"{us_id} safety_class invalid: {row.get('safety_class')!r}"
            )
        cov = row["coverage"]
        if not isinstance(cov, dict) or cov.get("value") not in COVERAGE:
            raise AuditError(f"{us_id} coverage must be object with measured|unmeasured|not_applicable")
        if cov["value"] == "not_applicable" and not str(cov.get("reason") or "").strip():
            raise AuditError(f"{us_id} coverage not_applicable requires reason")
        if cov["value"] == "measured" and not cov.get("evidence"):
            raise AuditError(f"{us_id} coverage measured requires evidence pointer")
        if cov["value"] == "unmeasured" and not str(cov.get("reason") or "").strip():
            raise AuditError(f"{us_id} coverage unmeasured requires reason")

        if row["disposition"] in {"blocked", "deferred"}:
            if not str(row.get("owner") or "").strip():
                raise AuditError(f"{us_id} {row['disposition']} requires owner")
            unlock = str(row.get("unlock") or "").strip().lower()
            if not unlock or unlock in {"later", "someday", "tbd", "none"}:
                raise AuditError(f"{us_id} requires concrete unlock condition")

        if row["disposition"] == "passed":
            if row["completeness"] != "evidenced":
                raise AuditError(f"{us_id} passed requires completeness evidenced")
            if not isinstance(row.get("evidence"), dict):
                raise AuditError(f"{us_id} passed requires evidence object")

        if row["completeness"] == "stub":
            raise AuditError(f"{us_id} stub completeness fails template floor")

        if row["execution"] not in EXECUTION:
            raise AuditError(f"{us_id} invalid execution")

        commands = row["commands"]
        if not isinstance(commands, list) or not commands:
            raise AuditError(f"{us_id} commands must be a non-empty list")
        for index, command in enumerate(commands):
            if not isinstance(command, list) or not command:
                raise AuditError(f"{us_id} command {index} must be argv list")
            receipt = validate_argv(
                command,
                parser=parser,
                template_id=f"{us_id}.cmd-{index:02d}",
            )
            argv_receipts.append(
                {
                    "template_id": receipt.template_id,
                    "argv": receipt.argv,
                    "leaf_id": receipt.leaf_id,
                    "ok": receipt.ok,
                    "reason": receipt.reason,
                }
            )
            if not receipt.ok:
                raise AuditError(
                    f"{us_id} command {index} argv invalid: {receipt.reason}"
                )
            if receipt.leaf_id not in leaf_ids:
                raise AuditError(
                    f"{us_id} command {index} leaf {receipt.leaf_id} not in inventory"
                )
        observed_leaves = [
            validate_argv(cmd, parser=parser, template_id=f"{us_id}.leaf").leaf_id
            for cmd in commands
        ]
        for required in required_leaves:
            if required not in observed_leaves:
                raise AuditError(
                    f"{us_id} missing required command leaf {required} from catalog"
                )
        if us_id == "US-03":
            apply_count = sum(
                1 for leaf in observed_leaves if leaf == "vehicles.perception.apply"
            )
            if apply_count < 2:
                raise AuditError(f"{us_id} requires at least two perception apply steps")
            if "vehicles.perception.compare" not in observed_leaves:
                raise AuditError(f"{us_id} requires perception compare command")

        cleanup = row["cleanup"]
        if not isinstance(cleanup, list):
            raise AuditError(f"{us_id} cleanup must be argv list list or empty list")
        for index, command in enumerate(cleanup):
            if not command:
                continue
            receipt = validate_argv(
                command,
                parser=parser,
                template_id=f"{us_id}.cleanup-{index:02d}",
            )
            argv_receipts.append(
                {
                    "template_id": receipt.template_id,
                    "argv": receipt.argv,
                    "leaf_id": receipt.leaf_id,
                    "ok": receipt.ok,
                    "reason": receipt.reason,
                }
            )
            if not receipt.ok:
                raise AuditError(
                    f"{us_id} cleanup {index} argv invalid: {receipt.reason}"
                )
            if receipt.leaf_id not in leaf_ids:
                raise AuditError(
                    f"{us_id} cleanup {index} leaf {receipt.leaf_id} not in inventory"
                )
        cleanup_leaves = [
            validate_argv(cmd, parser=parser, template_id=f"{us_id}.cleaf").leaf_id
            for cmd in cleanup
            if cmd
        ]
        for required in required_cleanup_leaves:
            if required not in cleanup_leaves:
                raise AuditError(
                    f"{us_id} missing required cleanup leaf {required} from catalog"
                )

    missing = sorted(set(catalog_by_id) - seen)
    if missing:
        raise AuditError(f"sequence registry missing catalog ids: {missing}")
    return argv_receipts


def _eval_cite_predicate(us_id: str, predicate: dict[str, Any], parsed: Any) -> None:
    """Apply one frozen cite predicate against a parsed evidence document."""

    if "contains_where" in predicate:
        path = predicate.get("path")
        if path is None:
            raise AuditError(f"{us_id} contains_where predicate missing path")
        rows = _json_path(path, parsed)
        if not isinstance(rows, list):
            raise AuditError(
                f"{us_id} cite predicate {path} is not a list for contains_where"
            )
        wanted = predicate["contains_where"]
        if not isinstance(wanted, dict) or not wanted:
            raise AuditError(f"{us_id} contains_where must be a non-empty object")
        for row in rows:
            if not isinstance(row, dict):
                continue
            if all(row.get(key) == value for key, value in wanted.items()):
                return
        raise AuditError(
            f"{us_id} cite predicate missing row matching {wanted!r} under {path}"
        )
    if "equals" not in predicate:
        raise AuditError(f"{us_id} cite predicate must use equals or contains_where")
    path = predicate["path"]
    expect = predicate["equals"]
    actual = _json_path(path, parsed)
    if actual != expect:
        raise AuditError(
            f"{us_id} cite predicate failed {path}: {actual!r} != {expect!r}"
        )


def eval_cite_predicates(us_id: str, claim: dict[str, Any], parsed: Any) -> None:
    preds = claim.get("predicates") or []
    if not preds:
        raise AuditError(f"{us_id} claim predicates must be non-empty")
    for predicate in preds:
        if not isinstance(predicate, dict):
            raise AuditError(f"{us_id} cite predicate must be an object")
        _eval_cite_predicate(us_id, predicate, parsed)


def parse_exploratory_ledger(text: str) -> dict[str, dict[str, str]]:
    """Parse M007-LIVE identity rows from the exploratory ledger summary."""

    parsed: dict[str, dict[str, str]] = {}
    for match in re.finditer(
        r"\|\s*`(?P<id>M007-LIVE-\d+)`\s*\|\s*`(?P<classification>[^`]+)`\s*\|\s*"
        r"(?P<severity>P\d)\s*\|\s*(?P<one_line>[^|]+)\|",
        text,
    ):
        parsed[match.group("id")] = {
            "classification": match.group("classification").strip(),
            "severity": match.group("severity").strip(),
            "one_line": match.group("one_line").strip(),
        }
    owners: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = re.fullmatch(r"###\s+(M007-LIVE-\d+)\s*", line)
        if heading:
            current = heading.group(1)
            continue
        owner = re.fullmatch(r"\|\s*Owner\s*\|\s*(.+?)\s*\|", line)
        if owner and current:
            owners[current] = owner.group(1).strip()
    for live_id, row in parsed.items():
        if live_id not in owners:
            raise AuditError(f"exploratory ledger missing Owner for {live_id}")
        row["ledger_owner"] = owners[live_id]
    return parsed


def _json_path(path: str | list[str], data: Any) -> Any:
    """Resolve a dotted path, or an explicit list path for keys that contain dots."""

    parts: list[str]
    if isinstance(path, list):
        parts = [str(p) for p in path]
    else:
        parts = path.split(".")
    cur = data
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise AuditError(f"path {path!r} missing at {part!r}")
    return cur


def _strip_program_name(argv: list[str]) -> list[str]:
    """Remove the public CLI executable prefix from an evidence argv."""

    if not argv:
        return argv
    first = argv[0]
    if first in {"automa", "./cli/automa", "cli/automa"} or Path(first).name == "automa":
        return argv[1:]
    return argv


_TRACE_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _trace_placeholder_bindings(
    expected: list[str],
    actual: list[str],
    bindings: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Match tokens and extend a consistent binding for every placeholder."""

    if len(actual) != len(expected):
        return None
    candidate = dict(bindings or {})
    for expected_token, actual_token in zip(expected, actual):
        placeholder = _TRACE_PLACEHOLDER_RE.fullmatch(expected_token)
        if placeholder:
            name = placeholder.group(1)
            prior = candidate.get(name)
            if prior is not None and prior != actual_token:
                return None
            candidate[name] = actual_token
            continue
        if expected_token != actual_token:
            return None
    return candidate


def _trace_tokens_shape_matches(expected: list[str], actual: list[str]) -> bool:
    """Match fixed tokens while ignoring bindings, for conflict diagnostics."""

    if len(actual) != len(expected):
        return False
    return all(
        _TRACE_PLACEHOLDER_RE.fullmatch(expected_token)
        or expected_token == actual_token
        for expected_token, actual_token in zip(expected, actual)
    )


def _trace_command_bindings(
    template: list[Any],
    evidence_command: Any,
    bindings: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Match a shell command and return the extended placeholder bindings."""

    if not isinstance(evidence_command, str) or not evidence_command.strip():
        return None
    try:
        actual = _strip_program_name(argv_from_shell_line(evidence_command))
    except (ValueError, TypeError):
        return None
    expected = _strip_program_name([str(token) for token in template])
    return _trace_placeholder_bindings(expected, actual, bindings)


def _trace_command_shape_matches(template: list[Any], evidence_command: Any) -> bool:
    """Return whether fixed command tokens match, ignoring identity values."""

    if not isinstance(evidence_command, str) or not evidence_command.strip():
        return False
    try:
        actual = _strip_program_name(argv_from_shell_line(evidence_command))
    except (ValueError, TypeError):
        return False
    expected = _strip_program_name([str(token) for token in template])
    return _trace_tokens_shape_matches(expected, actual)


def _trace_command_matches(
    template: list[Any],
    evidence_command: Any,
    bindings: dict[str, str] | None = None,
) -> bool:
    """Match an evidence command to a registry template and bindings."""

    return _trace_command_bindings(template, evidence_command, bindings) is not None


def _trace_tokens_match(
    expected: list[str],
    actual: list[str],
    bindings: dict[str, str] | None = None,
) -> bool:
    """Compare tokenized templates, binding repeated placeholders consistently."""

    return _trace_placeholder_bindings(expected, actual, bindings) is not None


def validate_exact_step_trace(
    us_id: str,
    row: dict[str, Any],
    claim: dict[str, Any],
    parsed: Any,
) -> dict[str, Any]:
    """Require a cited result to prove the row's ordered commands and cues.

    A family aggregate is not an exact scenario trace. The frozen claim map
    therefore names an ordered outcome list and semantic confirmation/cleanup
    predicates. Every registered command must occur in order as a successful
    outcome; extra diagnostic commands are allowed, but a strict subset is not.
    """

    trace = claim.get("trace")
    if not isinstance(trace, dict):
        raise AuditError(f"{us_id} cite claim must declare an exact trace contract")
    if trace.get("mode") != "ordered_subsequence":
        raise AuditError(
            f"{us_id} cite trace mode must be ordered_subsequence until a reviewed "
            "equivalence artifact is implemented"
        )
    outcomes_path = trace.get("outcomes_path")
    command_field = str(trace.get("command_field") or "").strip()
    success_fields = trace.get("success")
    if not outcomes_path or not command_field:
        raise AuditError(f"{us_id} cite trace must name outcomes_path and command_field")
    if not isinstance(success_fields, dict) or not success_fields:
        raise AuditError(f"{us_id} cite trace success fields must be non-empty")
    try:
        outcomes = _json_path(outcomes_path, parsed)
    except AuditError as exc:
        raise AuditError(
            f"{us_id} cite exact trace missing ordered outcome list at "
            f"{outcomes_path!r}: {exc}"
        ) from exc
    if not isinstance(outcomes, list):
        raise AuditError(
            f"{us_id} cite exact trace missing ordered outcome list at {outcomes_path!r}"
        )

    def is_successful(outcome: Any) -> bool:
        return isinstance(outcome, dict) and all(
            outcome.get(field) == expected
            for field, expected in success_fields.items()
        )

    commands = row.get("commands")
    if not isinstance(commands, list) or not commands:
        raise AuditError(f"{us_id} exact trace requires non-empty registered commands")
    command_matches: list[int] = []
    cursor = 0
    placeholder_bindings: dict[str, str] = {}
    for command_index, template in enumerate(commands):
        if not isinstance(template, list) or not template:
            raise AuditError(f"{us_id} command {command_index} is not an argv template")
        found: int | None = None
        for outcome_index in range(cursor, len(outcomes)):
            outcome = outcomes[outcome_index]
            if not isinstance(outcome, dict):
                continue
            if not is_successful(outcome):
                continue
            candidate_bindings = _trace_command_bindings(
                template,
                outcome.get(command_field),
                placeholder_bindings,
            )
            if candidate_bindings is None:
                if _trace_command_shape_matches(template, outcome.get(command_field)):
                    raise AuditError(
                        f"{us_id} cite exact trace has inconsistent placeholder "
                        f"identity at registered command {command_index}"
                    )
                continue
            found = outcome_index
            placeholder_bindings = candidate_bindings
            break
        if found is None:
            raise AuditError(
                f"{us_id} cite exact trace is missing successful registered command "
                f"{command_index}; family/subset evidence cannot promote this row"
            )
        command_matches.append(found)
        cursor = found + 1

    cleanup_matches: list[int] = []
    cleanup = row.get("cleanup") or []
    if not isinstance(cleanup, list):
        raise AuditError(f"{us_id} exact trace cleanup must be an argv list")
    permitted_overlaps = trace.get("permitted_cleanup_overlaps")
    if not isinstance(permitted_overlaps, list):
        raise AuditError(
            f"{us_id} cite trace permitted_cleanup_overlaps must be a list"
        )
    overlap_by_cleanup: dict[int, int] = {}
    for overlap in permitted_overlaps:
        if not isinstance(overlap, dict):
            raise AuditError(f"{us_id} cite trace cleanup overlap must be an object")
        if set(overlap) != {"cleanup_index", "journey_command_index"}:
            raise AuditError(
                f"{us_id} cite trace cleanup overlap must name only cleanup_index "
                "and journey_command_index"
            )
        cleanup_index = overlap.get("cleanup_index")
        journey_command_index = overlap.get("journey_command_index")
        if (
            isinstance(cleanup_index, bool)
            or not isinstance(cleanup_index, int)
            or isinstance(journey_command_index, bool)
            or not isinstance(journey_command_index, int)
            or cleanup_index < 0
            or journey_command_index < 0
        ):
            raise AuditError(
                f"{us_id} cite trace cleanup overlap indexes must be non-negative integers"
            )
        if cleanup_index in overlap_by_cleanup:
            raise AuditError(
                f"{us_id} cite trace cleanup overlap repeats cleanup index {cleanup_index}"
            )
        if cleanup_index >= len(cleanup) or journey_command_index >= len(commands):
            raise AuditError(
                f"{us_id} cite trace cleanup overlap index is outside the registered sequence"
            )
        cleanup_template = _strip_program_name(
            [str(token) for token in cleanup[cleanup_index]]
        )
        journey_template = _strip_program_name(
            [str(token) for token in commands[journey_command_index]]
        )
        if cleanup_template != journey_template:
            raise AuditError(
                f"{us_id} cite trace cleanup overlap must name an identical "
                "registered journey command"
            )
        overlap_by_cleanup[cleanup_index] = journey_command_index

    cleanup_cursor = 0
    last_command = command_matches[-1]
    for cleanup_index, template in enumerate(cleanup):
        if not isinstance(template, list) or not template:
            raise AuditError(f"{us_id} cleanup {cleanup_index} is not an argv template")
        found: int | None = None
        identity_conflict = False
        # Prefer a distinct post-sequence cleanup event. This is the normal
        # case for plugin restore/stop commands.
        for outcome_index in range(max(last_command + 1, cleanup_cursor), len(outcomes)):
            outcome = outcomes[outcome_index]
            if not isinstance(outcome, dict) or not is_successful(outcome):
                continue
            if not _trace_command_shape_matches(template, outcome.get(command_field)):
                continue
            candidate_bindings = _trace_command_bindings(
                template,
                outcome.get(command_field),
                placeholder_bindings,
            )
            if candidate_bindings is None:
                identity_conflict = True
                continue
            found = outcome_index
            placeholder_bindings = candidate_bindings
            break
        if identity_conflict:
            raise AuditError(
                f"{us_id} cite exact trace has inconsistent placeholder identity "
                f"in cleanup command {cleanup_index}"
            )
        if found is None:
            overlap_command_index = overlap_by_cleanup.get(cleanup_index)
            if overlap_command_index is not None:
                overlap_outcome_index = command_matches[overlap_command_index]
                overlap_outcome = outcomes[overlap_outcome_index]
                candidate_bindings = _trace_command_bindings(
                    template,
                    overlap_outcome.get(command_field)
                    if isinstance(overlap_outcome, dict)
                    else None,
                    placeholder_bindings,
                )
                if (
                    isinstance(overlap_outcome, dict)
                    and is_successful(overlap_outcome)
                    and candidate_bindings is not None
                    and overlap_outcome_index >= cleanup_cursor
                ):
                    found = overlap_outcome_index
                    placeholder_bindings = candidate_bindings
            if found is None:
                if overlap_command_index is None:
                    raise AuditError(
                        f"{us_id} cite exact trace is missing distinct post-sequence "
                        f"cleanup command {cleanup_index}; family/subset evidence "
                        "cannot promote this row"
                    )
                raise AuditError(
                    f"{us_id} cite exact trace is missing its permitted cleanup "
                    f"overlap {cleanup_index}; placeholder identity or successful "
                    "journey evidence is inconsistent"
                )
        if found is None:
            raise AuditError(
                f"{us_id} cite exact trace is missing successful cleanup command "
                f"{cleanup_index}; family/subset evidence cannot promote this row"
            )
        cleanup_matches.append(found)
        cleanup_cursor = found + 1

    confirmation_predicates = trace.get("confirmation_predicates")
    cleanup_predicates = trace.get("cleanup_predicates")
    if not isinstance(confirmation_predicates, list) or not confirmation_predicates:
        raise AuditError(f"{us_id} cite trace must bind primary confirmation predicates")
    if not isinstance(cleanup_predicates, list):
        raise AuditError(f"{us_id} cite trace cleanup_predicates must be a list")
    for predicate in confirmation_predicates:
        if not isinstance(predicate, dict):
            raise AuditError(f"{us_id} cite trace confirmation predicate must be an object")
        _eval_cite_predicate(us_id, predicate, parsed)
    for predicate in cleanup_predicates:
        if not isinstance(predicate, dict):
            raise AuditError(f"{us_id} cite trace cleanup predicate must be an object")
        _eval_cite_predicate(us_id, predicate, parsed)

    return {
        "mode": trace["mode"],
        "outcomes_path": outcomes_path,
        "command_matches": command_matches,
        "cleanup_matches": cleanup_matches,
        "confirmation_predicates": len(confirmation_predicates),
        "cleanup_predicates": len(cleanup_predicates),
        "placeholder_bindings": dict(sorted(placeholder_bindings.items())),
    }


def validate_frozen_claim_map(claim_map: dict[str, Any]) -> None:
    """Require claim_map.json to match the independent frozen authority constant."""

    if claim_map != FROZEN_CLAIM_MAP:
        raise AuditError(
            "claim_map.json must exactly match frozen_authority.FROZEN_CLAIM_MAP "
            "(bindings/paths/predicates/source_pr cannot be reconfigured in-repo)"
        )
    for claim_id, claim in FROZEN_CLAIM_MAP["claims"].items():
        preds = claim.get("predicates") or []
        if not preds:
            raise AuditError(f"frozen claim {claim_id} has empty predicates")
        trace = claim.get("trace")
        if not isinstance(trace, dict):
            raise AuditError(f"frozen claim {claim_id} must declare an exact trace")
        if trace.get("mode") != "ordered_subsequence":
            raise AuditError(
                f"frozen claim {claim_id} must use ordered_subsequence trace mode"
            )
        if not str(trace.get("outcomes_path") or "").strip():
            raise AuditError(f"frozen claim {claim_id} trace outcomes_path is required")
        if not str(trace.get("command_field") or "").strip():
            raise AuditError(f"frozen claim {claim_id} trace command_field is required")
        if not isinstance(trace.get("success"), dict) or not trace["success"]:
            raise AuditError(f"frozen claim {claim_id} trace success fields are required")
        confirmation = trace.get("confirmation_predicates")
        cleanup = trace.get("cleanup_predicates")
        if not isinstance(confirmation, list) or not confirmation:
            raise AuditError(
                f"frozen claim {claim_id} trace confirmation predicates are required"
            )
        if not isinstance(cleanup, list):
            raise AuditError(
                f"frozen claim {claim_id} trace cleanup predicates must be a list"
            )
        overlaps = trace.get("permitted_cleanup_overlaps")
        if not isinstance(overlaps, list):
            raise AuditError(
                f"frozen claim {claim_id} trace permitted_cleanup_overlaps must be a list"
            )
        for overlap in overlaps:
            if not isinstance(overlap, dict) or set(overlap) != {
                "cleanup_index",
                "journey_command_index",
            }:
                raise AuditError(
                    f"frozen claim {claim_id} trace cleanup overlap is invalid"
                )
            if any(
                isinstance(overlap[field], bool)
                or not isinstance(overlap[field], int)
                or overlap[field] < 0
                for field in ("cleanup_index", "journey_command_index")
            ):
                raise AuditError(
                    f"frozen claim {claim_id} trace cleanup overlap indexes are invalid"
                )
        for rel in claim.get("paths") or []:
            if rel not in FROZEN_CITE_PATH_DIGESTS:
                raise AuditError(
                    f"frozen claim {claim_id} path {rel} missing FROZEN_CITE_PATH_DIGESTS"
                )


def validate_semantic_cite(
    *,
    sequences: dict[str, Any],
    claim_map: dict[str, Any],
    repo_root: Path = ROOT,
) -> list[dict[str, Any]]:
    validate_frozen_claim_map(claim_map)
    claims = claim_map.get("claims")
    if not isinstance(claims, dict):
        raise AuditError("claim_map.claims must be an object")
    bindings = claim_map.get("us_claim_bindings")
    if not isinstance(bindings, dict) or not bindings:
        raise AuditError("claim_map.us_claim_bindings is required")
    receipts: list[dict[str, Any]] = []
    for row in sequences["sequences"]:
        if row["disposition"] != "passed":
            continue
        evidence = row["evidence"]
        if not isinstance(evidence, dict):
            raise AuditError(f"{row['id']} evidence must be an object")
        mode = evidence.get("evidence_mode")
        if mode == "executed":
            # Fail closed: executed rows require a committed package with the
            # same claim-map predicate model under the audit evidence tree.
            # Fabricating {"evidence_mode":"executed"} alone is rejected.
            raise AuditError(
                f"{row['id']} evidence_mode executed is not accepted without a "
                "committed execution package; use cited hybrid evidence or "
                "add an execution claim package in a proposal amendment"
            )
        if mode != "cited":
            raise AuditError(f"{row['id']} evidence_mode must be cited or executed")
        us_id = row["id"]
        expected_claim = bindings.get(us_id)
        if not expected_claim:
            raise AuditError(
                f"{us_id} has no fixed us_claim_bindings entry for passed rows"
            )
        claim_id = evidence.get("claim_map_id")
        if claim_id != expected_claim:
            raise AuditError(
                f"{us_id} claim_map_id {claim_id!r} != binding {expected_claim!r}"
            )
        claim = claims.get(claim_id)
        if not isinstance(claim, dict):
            raise AuditError(f"{us_id} unknown claim_map_id {claim_id!r}")
        preds = claim.get("predicates") or []
        if not preds:
            raise AuditError(f"{us_id} claim {claim_id} predicates must be non-empty")
        allowed = claim.get("allowed_us_ids") or []
        if us_id not in allowed:
            raise AuditError(
                f"{us_id} not in claim {claim_id} allowed_us_ids {allowed!r}"
            )
        source_pr = claim.get("source_pr")
        if evidence.get("source_pr") != source_pr:
            raise AuditError(
                f"{us_id} evidence.source_pr {evidence.get('source_pr')!r} "
                f"!= claim source_pr {source_pr!r}"
            )
        expected_schema = claim.get("source_result_schema")
        if not expected_schema:
            raise AuditError(f"claim {claim_id} missing source_result_schema")
        expected_commit = claim.get("source_commit")
        if not expected_commit or re.fullmatch(r"[0-9a-f]{40}", str(expected_commit)) is None:
            raise AuditError(
                f"claim {claim_id} source_commit must be full 40-char git id"
            )
        if evidence.get("source_commit") != expected_commit:
            raise AuditError(
                f"{us_id} evidence.source_commit must equal sealed package revision "
                f"{expected_commit}"
            )
        actor = str(evidence.get("disposition_set_by") or "").strip()
        when = str(evidence.get("disposition_set_at") or "").strip()
        if len(actor) < 2 or actor.lower() in {"x", "n/a", "none", "tbd"}:
            raise AuditError(f"{us_id} disposition_set_by must be a real actor identity")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", when) is None:
            raise AuditError(
                f"{us_id} disposition_set_at must be ISO-8601 UTC ...Z (got {when!r})"
            )
        paths = claim.get("paths") or []
        if not paths:
            raise AuditError(f"claim {claim_id} has no paths")
        digests = evidence.get("digests")
        if not isinstance(digests, dict) or not digests:
            raise AuditError(f"{us_id} evidence.digests is required for every cite path")
        path_digests: list[dict[str, str]] = []
        parsed: dict[str, Any] = {}
        repo_root = repo_root.resolve()
        for rel in paths:
            if not isinstance(rel, str) or not rel.strip():
                raise AuditError(f"{us_id} claim path must be a non-empty string")
            if Path(rel).is_absolute() or rel.startswith("/") or rel.startswith("\\"):
                raise AuditError(
                    f"{us_id} claim path must be repository-relative, not absolute: {rel!r}"
                )
            if ".." in Path(rel).parts:
                raise AuditError(
                    f"{us_id} claim path must not contain '..' traversal: {rel!r}"
                )
            abs_path = (repo_root / rel).resolve()
            try:
                abs_path.relative_to(repo_root)
            except ValueError as exc:
                raise AuditError(
                    f"{us_id} claim path escapes repo root: {rel!r}"
                ) from exc
            if not abs_path.is_file():
                raise AuditError(f"cite path missing: {rel}")
            digest = _sha256_file(abs_path)
            frozen_digest = FROZEN_CITE_PATH_DIGESTS.get(rel)
            if not frozen_digest:
                raise AuditError(
                    f"{us_id} cite path {rel} has no frozen FROZEN_CITE_PATH_DIGESTS entry"
                )
            if digest != frozen_digest:
                raise AuditError(
                    f"{us_id} cited bytes {rel} digest {digest[:12]} != "
                    f"frozen {frozen_digest[:12]}"
                )
            if rel not in digests:
                raise AuditError(f"{us_id} missing required digest for {rel}")
            expected = digests[rel]
            if expected != digest:
                raise AuditError(
                    f"{us_id} digest mismatch for {rel}: "
                    f"registry {str(expected)[:12]} != disk {digest[:12]}"
                )
            path_digests.append({"path": rel, "sha256": digest})
            if rel.endswith(".json"):
                parsed = _load_json(abs_path)
        extra = sorted(set(digests) - set(paths))
        if extra:
            raise AuditError(f"{us_id} evidence.digests has unknown paths {extra}")
        eval_cite_predicates(us_id, claim, parsed)
        trace_receipt = validate_exact_step_trace(us_id, row, claim, parsed)
        actual_schema = parsed.get("schema") if isinstance(parsed, dict) else None
        if actual_schema != expected_schema:
            raise AuditError(
                f"{us_id} cited schema {actual_schema!r} != "
                f"source_result_schema {expected_schema!r}"
            )
        head_claim = evidence.get("head_claim", "historical")
        if head_claim != "historical":
            raise AuditError(
                f"{us_id} head_claim must be historical for cite-backed passed rows"
            )
        receipts.append(
            {
                "id": us_id,
                "mode": "cited",
                "claim_map_id": claim_id,
                "paths": path_digests,
                "ok": True,
                "head_claim": head_claim,
                "source_pr": source_pr,
                "source_commit": expected_commit,
                "source_result_schema": expected_schema,
                "disposition_set_by": actor,
                "disposition_set_at": when,
                "trace": trace_receipt,
            }
        )
    return receipts


LIVE_DISPOSITIONS = {"deferred", "wontfix", "fixed_elsewhere"}


def validate_live_residuals(
    *,
    sequences: dict[str, Any],
    overlay: dict[str, Any],
    residuals: list[dict[str, Any]],
    repo_root: Path = ROOT,
) -> None:
    ledger_path = repo_root / LIVE_FINDINGS_PATH.relative_to(ROOT)
    if not ledger_path.is_file():
        raise AuditError(f"exploratory LIVE ledger missing: {LIVE_FINDINGS_PATH}")
    ledger_text = ledger_path.read_text(encoding="utf-8")
    ledger_digest = _sha256_bytes(ledger_text.encode("utf-8"))
    if ledger_digest != FROZEN_LIVE_LEDGER_SHA256:
        raise AuditError(
            "exploratory LIVE ledger digest "
            f"{ledger_digest[:12]} != frozen {FROZEN_LIVE_LEDGER_SHA256[:12]}"
        )
    parsed_ledger = parse_exploratory_ledger(ledger_text)

    def _identity(row: dict[str, Any]) -> dict[str, str]:
        return {
            key: str(row[key])
            for key in ("classification", "severity", "one_line", "ledger_owner")
        }

    frozen_ident = {key: _identity(val) for key, val in FROZEN_LIVE_RESIDUALS.items()}
    if parsed_ledger != frozen_ident:
        raise AuditError(
            "parsed exploratory LIVE ledger identity must equal FROZEN_LIVE_RESIDUALS"
        )
    if not isinstance(residuals, list):
        raise AuditError("LIVE residuals must be a list")
    seen: set[str] = set()
    for row in residuals:
        if not isinstance(row, dict):
            raise AuditError("LIVE residual row must be an object")
        rid = row.get("id")
        if not isinstance(rid, str) or not rid.strip():
            raise AuditError("LIVE residual id must be a non-empty string")
        if rid in seen:
            raise AuditError(f"duplicate LIVE residual id {rid}")
        seen.add(rid)
    required_ids = set(FROZEN_LIVE_RESIDUALS)
    missing = sorted(required_ids - seen)
    extra = sorted(seen - required_ids)
    if missing or extra:
        raise AuditError(
            f"LIVE residuals must exactly match ledger IDs missing={missing} extra={extra}"
        )
    leaf_ids = set(overlay.get("leaves", {}))
    seq_ids = {row["id"] for row in sequences["sequences"]}
    for row in residuals:
        _require_keys(
            row,
            (
                "id",
                "owner",
                "ledger_owner",
                "classification",
                "severity",
                "one_line",
                "disposition",
                "links",
            ),
            where=f"LIVE {row.get('id')}",
        )
        ident = FROZEN_LIVE_RESIDUALS[row["id"]]
        for field in ("classification", "severity", "one_line", "ledger_owner"):
            if row.get(field) != ident[field]:
                raise AuditError(
                    f"LIVE {row['id']}.{field} must equal ledger identity "
                    f"{ident[field]!r}; got {row.get(field)!r}"
                )
        if not str(row.get("owner") or "").strip():
            raise AuditError(f"LIVE {row['id']} owner must be non-empty")
        if row.get("disposition") not in LIVE_DISPOSITIONS:
            raise AuditError(
                f"LIVE {row['id']} disposition must be one of "
                f"{sorted(LIVE_DISPOSITIONS)}; got {row.get('disposition')!r}"
            )
        expected_links = ident.get("links")
        if expected_links is not None and row.get("links") != expected_links:
            raise AuditError(
                f"LIVE {row['id']} links must equal frozen ledger links"
            )
        links = row["links"]
        if not isinstance(links, dict):
            raise AuditError(f"LIVE {row['id']} links must be object")
        leaves = links.get("leaves") or []
        seqs = links.get("sequences") or []
        if not isinstance(leaves, list) or not isinstance(seqs, list):
            raise AuditError(f"LIVE {row['id']} links.leaves/sequences must be lists")
        if not leaves and not seqs:
            raise AuditError(f"LIVE {row['id']} must link leaf or sequence")
        for leaf in leaves:
            if leaf not in leaf_ids:
                raise AuditError(f"LIVE {row['id']} unknown leaf {leaf}")
        for seq in seqs:
            if seq not in seq_ids:
                raise AuditError(f"LIVE {row['id']} unknown sequence {seq}")

    residual_to_leaves: dict[str, set[str]] = {}
    for row in residuals:
        residual_to_leaves.setdefault(row["id"], set()).update(
            row["links"].get("leaves") or []
        )
    overlay_to_residuals: dict[str, set[str]] = {}
    for leaf_id, leaf_row in overlay.get("leaves", {}).items():
        links = leaf_row.get("open_finding_links") or []
        if not isinstance(links, list):
            raise AuditError(f"overlay {leaf_id}.open_finding_links must be a list")
        overlay_to_residuals[leaf_id] = {str(item) for item in links}
        unknown = sorted(overlay_to_residuals[leaf_id] - set(residual_to_leaves))
        if unknown:
            raise AuditError(
                f"overlay {leaf_id}.open_finding_links unknown residual ids {unknown}"
            )
    leaf_to_residuals: dict[str, set[str]] = {}
    for residual_id, leaves in residual_to_leaves.items():
        for leaf_id in leaves:
            leaf_to_residuals.setdefault(leaf_id, set()).add(residual_id)
    for leaf_id, expected in leaf_to_residuals.items():
        actual = overlay_to_residuals.get(leaf_id, set())
        if actual != expected:
            raise AuditError(
                f"LIVE residual linkage not reciprocal for {leaf_id}: "
                f"residuals={sorted(expected)} overlay={sorted(actual)}"
            )


def build_rollup(
    *,
    leaf_ids: list[str],
    sequences: dict[str, Any],
    cite_receipts: list[dict[str, Any]],
    residuals: list[dict[str, Any]],
    help_drift: dict[str, Any],
) -> str:
    by_disp: dict[str, int] = {}
    by_comp: dict[str, int] = {}
    for row in sequences["sequences"]:
        by_disp[row["disposition"]] = by_disp.get(row["disposition"], 0) + 1
        by_comp[row["completeness"]] = by_comp.get(row["completeness"], 0) + 1
    cited = sum(1 for r in cite_receipts if r.get("mode") == "cited")
    executed = sum(1 for r in cite_receipts if r.get("mode") == "executed")
    deferred_lines = []
    coverage_lines = []
    by_cov: dict[str, int] = {}
    for row in sequences["sequences"]:
        if row["disposition"] in {"deferred", "blocked"}:
            deferred_lines.append(
                f"- `{row['id']}` {row['disposition']}: owner={row.get('owner')}; "
                f"unlock={row.get('unlock')}"
            )
        cov = row.get("coverage") or {}
        cov_val = cov.get("value") if isinstance(cov, dict) else None
        if cov_val:
            by_cov[cov_val] = by_cov.get(cov_val, 0) + 1
        if cov_val in {"unmeasured", "not_applicable"}:
            reason = cov.get("reason") or cov.get("evidence") or ""
            coverage_lines.append(
                f"- `{row['id']}` coverage={cov_val}: {reason}"
            )
    residual_lines = [
        f"- `{row['id']}` {row['disposition']} owner={row['owner']}"
        for row in residuals
    ]
    return "\n".join(
        [
            "# M007-08 CLI surface audit rollup",
            "",
            (
                f"- Leaves: **{len(leaf_ids)}** "
                f"(action={help_drift.get('action_leaf_count', '?')}, "
                f"meta={help_drift.get('meta_leaf_count', '?')}, "
                f"alias={help_drift.get('alias_leaf_count', '?')}; "
                "all classified; residual unclassified: 0)"
            ),
            f"- Sequences by disposition: {by_disp}",
            f"- Sequences by completeness: {by_comp}",
            f"- Sequences by coverage: {by_cov}",
            f"- Passed evidence: cited={cited}, executed={executed}",
            f"- Help drift: {help_drift.get('status', 'unknown')}",
            "",
            "## Deferred / blocked",
            *(deferred_lines or ["- (none)"]),
            "",
            "## Coverage residuals (unmeasured / not_applicable)",
            *(coverage_lines or ["- (none)"]),
            "",
            "## LIVE residuals",
            *residual_lines,
            "",
            "## Non-claims",
            "- Cited `passed` is **historical** (`head_claim: historical`), not HEAD re-verification.",
            "- Template / deferred rows are not product roadmap commitments.",
            "- Coverage treatment is annotation, not a percentage gate.",
            "",
        ]
    )


def _subcommands_from_help_text(help_text: str) -> set[str]:
    """Extract subcommand names from rendered argparse help choice groups."""

    found: set[str] = set()
    for match in re.finditer(r"\{([^{}]+)\}", help_text):
        parts = [part.strip() for part in match.group(1).split(",")]
        if not parts:
            continue
        if not all(re.fullmatch(r"[a-z][a-z0-9_-]*", part) for part in parts):
            continue
        found.update(parts)
    return found


def _subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def help_derived_leaf_ids(
    parser: argparse.ArgumentParser,
) -> tuple[set[str], set[str]]:
    """Discover terminal command paths from rendered ``format_help()`` text.

    Returns ``(help_leaves, invented)`` where ``invented`` names appear in help
    choice groups but not in the parser's registered subcommands at that node.
    """

    help_leaves: set[str] = set()
    invented: set[str] = set()
    stack: list[tuple[argparse.ArgumentParser, tuple[str, ...]]] = [(parser, ())]
    while stack:
        current, prefix = stack.pop()
        help_text = current.format_help()
        help_names = _subcommands_from_help_text(help_text)
        sub = _subparsers_action(current)
        if sub is None or not sub.choices:
            if prefix:
                help_leaves.add(".".join(prefix))
            continue
        parser_names = set(sub.choices)
        for name in sorted(help_names - parser_names):
            if name == "help":
                continue
            invented.add(".".join(prefix + (name,)) if prefix else name)
        for name in sorted(help_names & parser_names):
            if name == "help":
                continue
            child = sub.choices[name]
            path = prefix + (name,)
            child_sub = _subparsers_action(child)
            if child_sub is None or not child_sub.choices:
                help_leaves.add(".".join(path))
            else:
                stack.append((child, path))
    return help_leaves, invented


def help_drift_report(parser: argparse.ArgumentParser | None = None) -> dict[str, Any]:
    """Compare rendered-help-derived leaves to argparse membership authority.

    Argparse remains membership authority. Soft Met reports drift both ways; an
    empty help-derived set fails closed as a broken help walk.
    """

    from cli.automa_cli.app import build_parser

    parser = parser or build_parser()
    all_leaves = list(walk_leaves(parser, include_help_meta=True))
    action_ids = {leaf.leaf_id for leaf in all_leaves if leaf.kind == "action"}
    meta_ids = {leaf.leaf_id for leaf in all_leaves if leaf.kind == "meta"}
    alias_ids = {leaf.leaf_id for leaf in all_leaves if leaf.kind == "alias"}
    help_leaves, invented = help_derived_leaf_ids(parser)
    if not help_leaves:
        raise AuditError("help-derived leaf walk produced no leaves")
    # Like-for-like: help walk already excludes help meta/alias nodes.
    missing_from_help = sorted(action_ids - help_leaves)
    extra_in_help = sorted((help_leaves - action_ids) | invented)
    status = "ok"
    if missing_from_help or extra_in_help:
        status = "drift_reported"
    return {
        "status": status,
        "authority": "argparse",
        "membership_rule": (
            "kind: meta for help tokens; kind: alias for optional-subparser "
            "parent terminals bound to their help child; action otherwise"
        ),
        "leaf_count": len(all_leaves),
        "action_leaf_count": len(action_ids),
        "meta_leaf_count": len(meta_ids),
        "alias_leaf_count": len(alias_ids),
        "help_leaf_count": len(help_leaves),
        "missing_from_help": missing_from_help,
        "extra_in_help": extra_in_help,
        "meta_leaf_ids": sorted(meta_ids),
        "alias_leaf_ids": sorted(alias_ids),
        "note": (
            "Help-derived set comes from rendered format_help() choice groups and "
            "excludes help meta nodes. Drift compares action leaves only. "
            "Membership authority remains argparse; hard equality is not Met-blocking."
        ),
    }


def _run_audit_with_parser(
    *,
    repo_root: Path,
    parser: argparse.ArgumentParser,
) -> dict[str, Any]:
    catalog_path = repo_root / CATALOG_PATH.relative_to(ROOT)
    catalog = load_catalog(
        catalog_path,
        anchor_path=repo_root / TOOL_DIR.relative_to(ROOT) / "us88_catalog.sha256",
    )
    cat_sha = catalog_digest(catalog_path)
    inventory = _load_json(repo_root / LEAVES_PATH.relative_to(ROOT))
    overlay = _load_json(repo_root / OVERLAY_PATH.relative_to(ROOT))
    sequences = _load_json(repo_root / SEQUENCES_PATH.relative_to(ROOT))
    claim_map = _load_json(repo_root / CLAIM_MAP_PATH.relative_to(ROOT))
    residuals_path = repo_root / TOOL_DIR.relative_to(ROOT) / "live_residuals.json"
    residuals = _load_json(residuals_path)

    _require_document_schema(overlay, "m007_leaf_overlay_v1", where="leaf_overlay")
    if not isinstance(overlay.get("leaves"), dict):
        raise AuditError("leaf_overlay.leaves must be an object")
    _require_document_schema(
        sequences, "m007_sequence_registry_v1", where="sequence_registry"
    )
    if not isinstance(sequences.get("sequences"), list):
        raise AuditError("sequence_registry.sequences must be a list")
    _require_document_schema(claim_map, "m007_claim_map_v1", where="claim_map")
    if not isinstance(claim_map.get("claims"), dict):
        raise AuditError("claim_map.claims must be an object")
    if not isinstance(claim_map.get("us_claim_bindings"), dict):
        raise AuditError("claim_map.us_claim_bindings must be an object")
    validate_frozen_claim_map(claim_map)
    _require_document_schema(
        residuals, "m007_live_residuals_v1", where="live_residuals"
    )
    if not isinstance(residuals.get("findings"), list):
        raise AuditError("live_residuals.findings must be a list")

    leaf_ids = validate_leaf_inventory_document(inventory, parser=parser)
    inventory_kinds = {
        row["leaf_id"]: row["kind"] for row in inventory["leaves"]
    }
    validate_overlay(
        leaf_ids=leaf_ids,
        overlay=overlay,
        parser=parser,
        inventory_kinds=inventory_kinds,
    )
    argv_receipts = validate_sequences(
        catalog=catalog,
        catalog_sha=cat_sha,
        sequences=sequences,
        leaf_ids=set(leaf_ids),
        parser=parser,
    )
    cite_receipts = validate_semantic_cite(
        sequences=sequences,
        claim_map=claim_map,
        repo_root=repo_root,
    )
    validate_live_residuals(
        sequences=sequences,
        overlay=overlay,
        residuals=residuals["findings"],
        repo_root=repo_root,
    )
    help_drift = help_drift_report(parser)
    rollup = build_rollup(
        leaf_ids=leaf_ids,
        sequences=sequences,
        cite_receipts=cite_receipts,
        residuals=residuals["findings"],
        help_drift=help_drift,
    )

    report = {
        "schema": SCHEMA,
        "result": "pass",
        "catalog": {
            "path": str(CATALOG_PATH.relative_to(ROOT)),
            "sha256": cat_sha,
            "source": catalog.get("source"),
        },
        "leaves": {
            "count": len(leaf_ids),
            "action_count": sum(
                1 for row in inventory["leaves"] if row.get("kind") == "action"
            ),
            "meta_count": sum(
                1 for row in inventory["leaves"] if row.get("kind") == "meta"
            ),
            "alias_count": sum(
                1 for row in inventory["leaves"] if row.get("kind") == "alias"
            ),
            "ids": leaf_ids,
            "inventory_sha256": _sha256_file(repo_root / LEAVES_PATH.relative_to(ROOT)),
            "overlay_sha256": _sha256_file(repo_root / OVERLAY_PATH.relative_to(ROOT)),
        },
        "sequences": {
            "count": len(sequences["sequences"]),
            "registry_sha256": _sha256_file(
                repo_root / SEQUENCES_PATH.relative_to(ROOT)
            ),
            "dispositions": {
                row["id"]: row["disposition"] for row in sequences["sequences"]
            },
        },
        "argv_receipts": argv_receipts,
        "cite_receipts": cite_receipts,
        "live_residuals": residuals["findings"],
        "help_drift": help_drift,
        "non_claims": {
            "head_reverification": False,
            "coverage_gate": False,
            "product_repair": False,
            "template_is_roadmap": False,
        },
    }
    return {"report": report, "rollup": rollup}


def run_audit(
    *,
    repo_root: Path = ROOT,
) -> dict[str, Any]:
    """Validate current M007 evidence against its historical parser authority."""

    repo_root = Path(repo_root).resolve()
    try:
        return run_frozen_parser_audit(
            repo_root=repo_root,
            validator_path=Path(__file__),
        )
    except FrozenParserError as exc:
        raise AuditError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate M007-08 CLI surface audit")
    parser.add_argument(
        "--write-evidence",
        action="store_true",
        help="Write report.json and rollup.md under evidence/cli-surface-audit/",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        result = run_audit(repo_root=repo_root)
    except AuditError as exc:
        print(f"FAIL: {exc}")
        return 1
    report = result["report"]
    print(json.dumps({"result": report["result"], "leaves": report["leaves"]["count"]}, indent=2))
    if args.write_evidence:
        out = args.repo_root / EVIDENCE_DIR.relative_to(ROOT)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (out / "rollup.md").write_text(result["rollup"], encoding="utf-8")
        print(f"wrote {out / 'report.json'}")
        print(f"wrote {out / 'rollup.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
