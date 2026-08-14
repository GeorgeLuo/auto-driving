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
    from .argv_validate import ArgvValidationError, normalize_placeholders, validate_argv
    from .frozen_authority import (
        CANONICAL_US88_SOURCE,
        FROZEN_CLAIM_MAP,
        FROZEN_US_TEMPLATES,
        US88_SOURCE_CONTENT_SHA256,
        US88_SOURCE_RELPATH,
        USAGE_PATTERNS,
    )
    from .parser_walk import leaf_skeleton, public_leaf_ids, walk_leaves
except ImportError:  # script / path execution
    from argv_validate import ArgvValidationError, normalize_placeholders, validate_argv
    from frozen_authority import (
        CANONICAL_US88_SOURCE,
        FROZEN_CLAIM_MAP,
        FROZEN_US_TEMPLATES,
        US88_SOURCE_CONTENT_SHA256,
        US88_SOURCE_RELPATH,
        USAGE_PATTERNS,
    )
    from parser_walk import leaf_skeleton, public_leaf_ids, walk_leaves

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
        ):
            if entry.get(field) != frozen.get(field):
                raise AuditError(
                    f"catalog {us_id}.{field} must equal frozen FROZEN_US_TEMPLATES"
                )
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
        _require_keys(row, ("leaf_id", "tokens", "help"), where="inventory row")
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
                supports = row.get("supports_json")
                if not isinstance(supports, bool):
                    raise AuditError(
                        f"overlay {leaf_id}.supports_json bool is required"
                    )
                mentions_json = bool(re.search(r"--json", out))
                if supports and not mentions_json:
                    raise AuditError(
                        f"overlay {leaf_id} supports --json but output_contract omits it"
                    )
                if not supports and re.search(r"optional --json|Supports --json", out):
                    raise AuditError(
                        f"overlay {leaf_id} claims --json but parser has no --json"
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
        # Parser/--json parity table check
        if "supports_json" not in row:
            raise AuditError(f"overlay {leaf_id} missing supports_json")


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
    catalog_by_id = {entry["id"]: entry for entry in catalog["entries"]}
    seen: set[str] = set()
    argv_receipts: list[dict[str, Any]] = []

    if len(rows) != 10:
        raise AuditError(f"expected 10 sequence rows, got {len(rows)}")

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
        for predicate in claim.get("predicates") or []:
            path = predicate["path"]
            expect = predicate["equals"]
            actual = _json_path(path, parsed)
            if actual != expect:
                raise AuditError(
                    f"{us_id} cite predicate failed {path}: "
                    f"{actual!r} != {expect!r}"
                )
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
            }
        )
    return receipts


LIVE_DISPOSITIONS = {"deferred", "wontfix", "fixed_elsewhere"}


def validate_live_residuals(
    *,
    sequences: dict[str, Any],
    overlay: dict[str, Any],
    residuals: list[dict[str, Any]],
) -> None:
    required_ids = {
        "M007-LIVE-001",
        "M007-LIVE-002",
        "M007-LIVE-003",
        "M007-LIVE-004",
        "M007-LIVE-005",
    }
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
    missing = sorted(required_ids - seen)
    if missing:
        raise AuditError(f"LIVE residuals missing: {missing}")
    leaf_ids = set(overlay.get("leaves", {}))
    seq_ids = {row["id"] for row in sequences["sequences"]}
    for row in residuals:
        _require_keys(
            row,
            ("id", "owner", "disposition", "links"),
            where=f"LIVE {row.get('id')}",
        )
        if not str(row.get("owner") or "").strip():
            raise AuditError(f"LIVE {row['id']} owner must be non-empty")
        if row.get("disposition") not in LIVE_DISPOSITIONS:
            raise AuditError(
                f"LIVE {row['id']} disposition must be one of "
                f"{sorted(LIVE_DISPOSITIONS)}; got {row.get('disposition')!r}"
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
            f"- Leaves: **{len(leaf_ids)}** (all classified; residual unclassified: 0)",
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
    leaf_ids = set(public_leaf_ids(parser))
    help_leaves, invented = help_derived_leaf_ids(parser)
    if not help_leaves:
        raise AuditError("help-derived leaf walk produced no leaves")
    missing_from_help = sorted(leaf_ids - help_leaves)
    extra_in_help = sorted((help_leaves - leaf_ids) | invented)
    status = "ok"
    if missing_from_help or extra_in_help:
        status = "drift_reported"
    return {
        "status": status,
        "authority": "argparse",
        "leaf_count": len(leaf_ids),
        "help_leaf_count": len(help_leaves),
        "missing_from_help": missing_from_help,
        "extra_in_help": extra_in_help,
        "note": (
            "Help-derived set comes from rendered format_help() choice groups. "
            "Membership authority remains argparse; hard equality is not Met-blocking."
        ),
    }


def run_audit(*, repo_root: Path = ROOT) -> dict[str, Any]:
    from cli.automa_cli.app import build_parser

    parser = build_parser()
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
    validate_overlay(leaf_ids=leaf_ids, overlay=overlay)
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
