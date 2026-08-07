"""M007-10 continuity contract: safety preflight, families, aggregation, finalizer.

Pure helpers used by the live CLI session runner for track=continuity catalogs.
Does not execute CLI commands.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

CONTINUITY_TRACK = "continuity"

REQUIRED_FAMILY_IDS: tuple[str, ...] = (
    "continuity.offline_perception",
    "continuity.live_config_swap",
    "continuity.memory_lifecycle",
)

OPTIONAL_FAMILY_IDS: tuple[str, ...] = (
    "continuity.plugin_ablation",
    "continuity.temporal_backpressure",
    "continuity.memory_replay",
)

ALL_FAMILY_IDS: frozenset[str] = frozenset(REQUIRED_FAMILY_IDS + OPTIONAL_FAMILY_IDS)

# Explicit argv prefixes allowed for continuity catalogs (after ./cli/automa).
# Membership is fail-closed: anything not matching is rejected.
_ALLOWED_COMMAND_SPECS: tuple[tuple[str, ...], ...] = (
    ("vehicles", "help"),
    ("vehicles", "status"),
    ("vehicles", "info"),
    ("vehicles", "update", "perception"),
    ("vehicles", "update", "memory"),
    ("vehicles", "automation", "run"),
    ("vehicles", "automation", "stop"),
    ("vehicles", "automation", "status"),
    ("vehicles", "perception", "run"),
    ("vehicles", "perception", "apply"),
    ("vehicles", "perception", "compare"),
    ("vehicles", "perception", "candidates"),
    ("vehicles", "memory", "check"),
    ("vehicles", "memory", "reset"),
    ("vehicles", "memory", "replay"),
    ("help",),
)

_FORBIDDEN_SUBSTRINGS = (
    ("vehicles", "operation"),
    ("vehicles", "update", "core"),
    ("vehicles", "update", "autonomy"),
    ("simulators", "ensure"),
    ("vehicles", "perception", "setup"),  # may mutate env; not in allowlist families
)

_HELP_STATUS_ONLY_PREFIXES = (
    ("help",),
    ("vehicles", "help"),
    ("vehicles", "status"),
    ("vehicles", "info"),
    ("vehicles", "automation", "status"),
    ("vehicles", "perception", "candidates"),
)


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    parts = [str(x) for x in argv]
    if parts and parts[0] in {"./cli/automa", "cli/automa", "automa"}:
        return parts[1:]
    if len(parts) >= 2 and parts[0] in {"./cli", "cli"} and parts[1] == "automa":
        return parts[2:]
    return parts


def derive_safety_class(argv: Sequence[str]) -> str:
    """Derive a coarse safety class from argv (not catalog labels)."""

    rest = _normalize_argv(argv)
    if not rest:
        return "unknown"
    joined = " ".join(rest)
    if any(
        len(rest) >= len(prefix) and tuple(rest[: len(prefix)]) == prefix
        for prefix in _FORBIDDEN_SUBSTRINGS
    ):
        return "forbidden"
    if "operation" in rest:
        return "forbidden"
    if rest[:2] == ["simulators", "ensure"]:
        return "forbidden"
    if rest[:3] == ["vehicles", "automation", "run"]:
        if "--observe-only" not in rest:
            return "forbidden"
        return "live_mutation"
    if rest[:3] == ["vehicles", "automation", "stop"]:
        return "live_mutation"
    if rest[:2] == ["vehicles", "update"] and len(rest) >= 3:
        if rest[2] in {"perception", "memory", "decision"}:
            return "local_write"
        return "forbidden"
    if rest[:2] == ["vehicles", "perception"] and len(rest) >= 3:
        if rest[2] in {"run", "apply", "compare"}:
            return "local_write" if "--record" in rest or rest[2] != "candidates" else "read"
        if rest[2] == "candidates":
            return "read"
    if rest[:2] == ["vehicles", "memory"] and len(rest) >= 3:
        if rest[2] in {"check", "reset", "replay"}:
            return "live_mutation" if rest[2] in {"check", "reset"} else "local_write"
    if rest[:2] == ["vehicles", "status"] or rest[:2] == ["vehicles", "info"]:
        return "read"
    if rest[0] == "help" or rest[:2] == ["vehicles", "help"]:
        return "read"
    return "unknown"


def argv_allowed(argv: Sequence[str]) -> tuple[bool, str]:
    rest = _normalize_argv(argv)
    if not rest:
        return False, "empty argv"
    safety = derive_safety_class(argv)
    if safety == "forbidden":
        return False, f"forbidden command path: {' '.join(rest[:4])}"
    if safety == "unknown":
        return False, f"unknown/not allowlisted command: {' '.join(rest[:5])}"
    for prefix in _ALLOWED_COMMAND_SPECS:
        if len(rest) >= len(prefix) and tuple(rest[: len(prefix)]) == prefix:
            if rest[:3] == ["vehicles", "automation", "run"] and "--observe-only" not in rest:
                return False, "automation run requires --observe-only"
            return True, "allowlisted"
    return False, f"not on continuity allowlist: {' '.join(rest[:5])}"


def _catalog_safety_label(step: Mapping[str, Any]) -> str | None:
    value = step.get("safety")
    return str(value) if isinstance(value, str) else None


def validate_continuity_safety_preflight(
    catalog: Mapping[str, Any],
) -> tuple[bool, str, list[dict[str, Any]]]:
    """Reject the entire catalog before any CLI execution if unsafe."""

    if catalog.get("track") != CONTINUITY_TRACK:
        return False, f"track must be {CONTINUITY_TRACK!r}", []
    if catalog.get("id") == "m007-acceptance":
        return False, "continuity catalogs cannot use m007-acceptance identity", []

    findings: list[dict[str, Any]] = []
    steps = catalog.get("steps")
    if not isinstance(steps, list) or not steps:
        return False, "continuity catalog has no steps", []

    for step in steps:
        if not isinstance(step, dict):
            return False, "invalid step entry", findings
        step_id = str(step.get("id") or "?")
        commands = step.get("commands") or []
        if not isinstance(commands, list):
            return False, f"step {step_id}: commands must be a list", findings
        label = _catalog_safety_label(step)
        for raw_cmd in commands:
            if not isinstance(raw_cmd, (list, tuple)) or not raw_cmd:
                return False, f"step {step_id}: empty command", findings
            argv = [str(x) for x in raw_cmd]
            ok, reason = argv_allowed(argv)
            if not ok:
                findings.append({"step_id": step_id, "argv": argv, "reason": reason})
                return False, f"step {step_id}: {reason}", findings
            derived = derive_safety_class(argv)
            if label is not None and label != derived:
                # Step labels are ceilings: a live_mutation step may include read
                # follow-ups (e.g. status after stop). Labels must not understate risk.
                compatible = (
                    (label == "live_mutation" and derived in {"live_mutation", "local_write", "read"})
                    or (label == "local_write" and derived in {"local_write", "read"})
                    or (label == "read" and derived == "read")
                )
                if not compatible:
                    msg = (
                        f"step {step_id}: catalog safety {label!r} mismatches "
                        f"derived {derived!r}"
                    )
                    findings.append({"step_id": step_id, "argv": argv, "reason": msg})
                    return False, msg, findings
    return True, "continuity safety preflight ok", findings


def _step_commands_help_status_only(step: Mapping[str, Any]) -> bool:
    commands = step.get("commands") or []
    if not isinstance(commands, list) or not commands:
        return True
    for raw in commands:
        if not isinstance(raw, (list, tuple)):
            return False
        rest = _normalize_argv([str(x) for x in raw])
        if not any(
            len(rest) >= len(prefix) and tuple(rest[: len(prefix)]) == prefix
            for prefix in _HELP_STATUS_ONLY_PREFIXES
        ):
            return False
    return True


def validate_continuity_families(
    catalog: Mapping[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    """Ensure required family IDs are present and not help/status-only mislabeled."""

    steps = catalog.get("steps")
    if not isinstance(steps, list):
        return False, "steps missing", {}

    by_family: dict[str, list[str]] = {fid: [] for fid in ALL_FAMILY_IDS}
    unknown: list[str] = []

    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "")
        family_id = step.get("family_id")
        if family_id is None:
            continue
        family_id = str(family_id)
        if family_id not in ALL_FAMILY_IDS:
            unknown.append(family_id)
            continue
        by_family[family_id].append(step_id)
        if family_id in REQUIRED_FAMILY_IDS and _step_commands_help_status_only(step):
            return (
                False,
                f"required family {family_id} step {step_id} is help/status-only",
                {"by_family": by_family},
            )

    if unknown:
        return False, f"unknown family_id values: {sorted(set(unknown))}", {"by_family": by_family}

    missing = [fid for fid in REQUIRED_FAMILY_IDS if not by_family.get(fid)]
    if missing:
        return False, f"missing required family_id coverage: {missing}", {"by_family": by_family}

    return True, "family validation ok", {"by_family": by_family, "required": list(REQUIRED_FAMILY_IDS)}


def _normalize_step_status(status: str) -> str:
    value = (status or "incomplete").strip().lower()
    if value in {"pass", "passed", "ok"}:
        return "passed"
    if value in {"fail", "failed", "error"}:
        return "fail"
    if value in {"partial"}:
        return "partial"
    if value in {"blocked"}:
        return "blocked"
    if value in {"finding", "findings"}:
        return "finding"
    if value in {"skip", "skipped"}:
        return "skip"
    return value or "incomplete"


def aggregate_family_status(
    sequences: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Roll per-sequence status into per-family aggregates.

    A family is `passed` only if at least one sequence for that family has
    status `passed`/`pass`/`ok`. Partial alone never yields family `passed`.
    Machine-only visual skips do not count as family `passed` (HITL still required).
    """

    aggregates: dict[str, str] = {}
    by_family: dict[str, list[str]] = {}
    hitl_pending: dict[str, bool] = {}
    for seq in sequences:
        fid = str(seq.get("family_id") or "")
        if not fid:
            continue
        status = _normalize_step_status(str(seq.get("status") or "incomplete"))
        # Visual skip under machine-only leaves family HITL-pending.
        if status == "skip" and seq.get("visual_required"):
            hitl_pending[fid] = True
            status = "partial"
        by_family.setdefault(fid, []).append(status)

    for fid, statuses in by_family.items():
        if "fail" in statuses or "finding" in statuses:
            aggregates[fid] = "finding" if "finding" in statuses else "fail"
        elif hitl_pending.get(fid) or "partial" in statuses:
            # HITL still required or incomplete family contract.
            aggregates[fid] = "partial"
        elif "passed" in statuses:
            aggregates[fid] = "passed"
        elif "blocked" in statuses:
            aggregates[fid] = "blocked"
        else:
            aggregates[fid] = statuses[-1] if statuses else "incomplete"
    return aggregates


def overall_pass_allowed(
    *,
    family_aggregates: Mapping[str, str],
    safety_preflight_ok: bool,
    finalizer_ok: bool,
) -> tuple[bool, str]:
    if not safety_preflight_ok:
        return False, "safety preflight not ok"
    if not finalizer_ok:
        return False, "evidence freshness finalizer not ok"
    for fid in REQUIRED_FAMILY_IDS:
        if family_aggregates.get(fid) != "passed":
            return False, f"required family {fid} aggregate is {family_aggregates.get(fid)!r}"
    return True, "required family aggregates passed"


def snapshot_activation(path: Path) -> dict[str, Any]:
    """Capture restorable activation bytes + verification hash.

    Hash-only snapshots are rejected by callers that require restorable_bytes.
    """

    if not path.is_file():
        return {
            "ok": False,
            "error": f"activation file missing: {path}",
            "restorable_bytes": None,
            "sha256": None,
            "path": str(path),
        }
    raw = path.read_bytes()
    return {
        "ok": True,
        "error": None,
        "restorable_bytes": raw.decode("utf-8"),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "path": str(path),
        "encoding": "utf-8",
    }


def snapshot_is_restorable(snapshot: Mapping[str, Any]) -> bool:
    if snapshot.get("ok") is not True:
        return False
    body = snapshot.get("restorable_bytes")
    return isinstance(body, str) and len(body) > 0 and isinstance(snapshot.get("sha256"), str)


def restore_activation(snapshot: Mapping[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    if not snapshot_is_restorable(snapshot):
        return {"ok": False, "error": "snapshot is not restorable (need restorable_bytes)"}
    target = Path(path or snapshot["path"])
    body = str(snapshot["restorable_bytes"]).encode("utf-8")
    expected = str(snapshot["sha256"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        return {"ok": False, "error": "restore verification hash mismatch", "expected": expected, "actual": actual}
    return {"ok": True, "error": None, "path": str(target), "sha256": actual}


def tree_file_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def collect_identity_bundle(
    *,
    repo_root: Path,
    catalog_path: Path,
    runner_path: Path | None = None,
    metrics_ui: Mapping[str, Any] | None = None,
    product_paths: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """Collect digests for evidence freshness finalizer."""

    runner = runner_path or (
        repo_root
        / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py"
    )
    continuity = (
        repo_root
        / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/continuity_contract.py"
    )
    defaults = [
        repo_root / "cli/automa_cli/perception_runs.py",
        repo_root / "cli/automa_cli/lab_plugins.py",
    ]
    paths = list(product_paths) if product_paths is not None else defaults
    product: dict[str, str | None] = {}
    for path in paths:
        try:
            rel = str(path.resolve().relative_to(repo_root.resolve()))
        except ValueError:
            rel = str(path)
        product[rel] = tree_file_digest(path)

    return {
        "schema": "continuity_identity_bundle_v0",
        "catalog_path": str(catalog_path),
        "catalog_sha256": tree_file_digest(catalog_path),
        "runner_sha256": tree_file_digest(runner),
        "continuity_contract_sha256": tree_file_digest(continuity),
        "product_sha256": product,
        "metrics_ui": dict(metrics_ui) if metrics_ui else None,
    }


def finalize_evidence_freshness(
    recorded: Mapping[str, Any],
    current: Mapping[str, Any],
) -> tuple[bool, str]:
    """Compare recorded identities to final tree identities; refuse pass on mismatch."""

    for key in ("catalog_sha256", "runner_sha256", "continuity_contract_sha256"):
        if recorded.get(key) != current.get(key):
            return False, f"mismatch {key}: recorded={recorded.get(key)!r} current={current.get(key)!r}"
    rec_prod = recorded.get("product_sha256") or {}
    cur_prod = current.get("product_sha256") or {}
    if not isinstance(rec_prod, dict) or not isinstance(cur_prod, dict):
        return False, "product_sha256 missing or invalid"
    for path, digest in rec_prod.items():
        if cur_prod.get(path) != digest:
            return False, f"product mismatch {path}"
    rec_mui = recorded.get("metrics_ui")
    cur_mui = current.get("metrics_ui")
    if rec_mui is not None or cur_mui is not None:
        if rec_mui != cur_mui:
            return False, "metrics_ui identity mismatch"
    return True, "evidence freshness finalizer ok"
