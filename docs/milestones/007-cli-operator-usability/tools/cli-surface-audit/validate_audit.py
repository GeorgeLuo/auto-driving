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
    from .parser_walk import leaf_skeleton, public_leaf_ids, walk_leaves
except ImportError:  # script / path execution
    from argv_validate import ArgvValidationError, normalize_placeholders, validate_argv
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
    "not_applicable",
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


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    data = _load_json(path)
    if data.get("schema") != "m007_us88_catalog_v1":
        raise AuditError(f"unexpected catalog schema {data.get('schema')!r}")
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
            ),
            where=f"catalog {entry.get('id')}",
        )
    return data


def validate_leaf_membership(
    *,
    inventory: list[dict[str, Any]],
    parser: argparse.ArgumentParser | None = None,
) -> list[str]:
    actual = public_leaf_ids(parser)
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
    return actual


def validate_overlay(
    *,
    leaf_ids: list[str],
    overlay: dict[str, Any],
) -> None:
    rows = overlay.get("leaves")
    if not isinstance(rows, dict):
        raise AuditError("leaf_overlay.leaves must be an object keyed by leaf_id")
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
                    elif not str(usage.get("reason") or "").strip() and usage.get(
                        "value"
                    ) != "unsupported":
                        pass
                    continue
                if isinstance(usage, list) and usage:
                    continue
                if isinstance(usage, str) and usage.strip():
                    continue
                raise AuditError(f"overlay {leaf_id}.usage invalid: {usage!r}")
            _na_ok(row[field], field=field, where=f"overlay {leaf_id}")
            if row[field] == "not_applicable":
                raise AuditError(
                    f"overlay {leaf_id}.{field} must use "
                    "{value: not_applicable, reason: ...} object form"
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

        if row["disposition"] not in DISPOSITIONS:
            raise AuditError(f"{us_id} invalid disposition")
        if row["completeness"] not in COMPLETENESS:
            raise AuditError(f"{us_id} invalid completeness")
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


def validate_semantic_cite(
    *,
    sequences: dict[str, Any],
    claim_map: dict[str, Any],
    repo_root: Path = ROOT,
) -> list[dict[str, Any]]:
    claims = claim_map.get("claims")
    if not isinstance(claims, dict):
        raise AuditError("claim_map.claims must be an object")
    receipts: list[dict[str, Any]] = []
    for row in sequences["sequences"]:
        if row["disposition"] != "passed":
            continue
        evidence = row["evidence"]
        mode = evidence.get("evidence_mode")
        if mode == "executed":
            receipts.append({"id": row["id"], "mode": "executed", "ok": True})
            continue
        if mode != "cited":
            raise AuditError(f"{row['id']} evidence_mode must be cited or executed")
        claim_id = evidence.get("claim_map_id")
        claim = claims.get(claim_id)
        if not isinstance(claim, dict):
            raise AuditError(f"{row['id']} unknown claim_map_id {claim_id!r}")
        paths = claim.get("paths") or []
        if not paths:
            raise AuditError(f"claim {claim_id} has no paths")
        path_digests: list[dict[str, str]] = []
        parsed: dict[str, Any] = {}
        for rel in paths:
            abs_path = repo_root / rel
            if not abs_path.is_file():
                raise AuditError(f"cite path missing: {rel}")
            digest = _sha256_file(abs_path)
            expected = (evidence.get("digests") or {}).get(rel)
            if expected and expected != digest:
                raise AuditError(
                    f"{row['id']} digest mismatch for {rel}: "
                    f"registry {expected[:12]} != disk {digest[:12]}"
                )
            path_digests.append({"path": rel, "sha256": digest})
            if rel.endswith(".json"):
                parsed = _load_json(abs_path)
        for predicate in claim.get("predicates") or []:
            path = predicate["path"]
            expect = predicate["equals"]
            actual = _json_path(path, parsed)
            if actual != expect:
                raise AuditError(
                    f"{row['id']} cite predicate failed {path}: "
                    f"{actual!r} != {expect!r}"
                )
        if evidence.get("head_claim", "historical") != "historical":
            # allowed values; default historical
            pass
        receipts.append(
            {
                "id": row["id"],
                "mode": "cited",
                "claim_map_id": claim_id,
                "paths": path_digests,
                "ok": True,
                "head_claim": evidence.get("head_claim", "historical"),
            }
        )
    return receipts


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
    seen = {row.get("id") for row in residuals}
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
        links = row["links"]
        if not isinstance(links, dict):
            raise AuditError(f"LIVE {row['id']} links must be object")
        leaves = links.get("leaves") or []
        seqs = links.get("sequences") or []
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
    for row in sequences["sequences"]:
        if row["disposition"] in {"deferred", "blocked"}:
            deferred_lines.append(
                f"- `{row['id']}` {row['disposition']}: owner={row.get('owner')}; "
                f"unlock={row.get('unlock')}"
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
            f"- Passed evidence: cited={cited}, executed={executed}",
            f"- Help drift: {help_drift.get('status', 'unknown')}",
            "",
            "## Deferred / blocked",
            *(deferred_lines or ["- (none)"]),
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


def help_drift_report(parser: argparse.ArgumentParser | None = None) -> dict[str, Any]:
    """Soft help check: report only for first Met."""

    from cli.automa_cli.app import build_parser

    parser = parser or build_parser()
    leaves = set(public_leaf_ids(parser))
    # Soft: we do not parse help text recursively here; record that soft mode
    # is active and membership authority remains argparse.
    return {
        "status": "soft_report_only",
        "authority": "argparse",
        "leaf_count": len(leaves),
        "note": (
            "Help equality is not Met-blocking in v1; argparse is membership "
            "authority. Drift report reserved for future hard gate."
        ),
    }


def run_audit(*, repo_root: Path = ROOT) -> dict[str, Any]:
    from cli.automa_cli.app import build_parser

    parser = build_parser()
    catalog = load_catalog(repo_root / CATALOG_PATH.relative_to(ROOT))
    catalog_path = repo_root / CATALOG_PATH.relative_to(ROOT)
    cat_sha = catalog_digest(catalog_path)
    inventory = _load_json(repo_root / LEAVES_PATH.relative_to(ROOT))
    overlay = _load_json(repo_root / OVERLAY_PATH.relative_to(ROOT))
    sequences = _load_json(repo_root / SEQUENCES_PATH.relative_to(ROOT))
    claim_map = _load_json(repo_root / CLAIM_MAP_PATH.relative_to(ROOT))
    residuals_path = repo_root / TOOL_DIR.relative_to(ROOT) / "live_residuals.json"
    residuals = _load_json(residuals_path)

    leaf_rows = inventory["leaves"]
    leaf_ids = validate_leaf_membership(inventory=leaf_rows, parser=parser)
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
