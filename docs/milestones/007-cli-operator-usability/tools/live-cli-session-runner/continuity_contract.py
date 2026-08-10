"""M007-10 continuity contract: safety preflight, families, aggregation, finalizer.

Pure helpers used by the live CLI session runner for track=continuity catalogs.
Does not execute CLI commands (except optional post-hoc finalize CLI).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat as statmod
import sys
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

# Per-leaf allowed long flags (fail-closed). Values free unless noted.
# Recording is only allowed on offline/memory artifact leaves, never on automation run.
_ALLOWED_FLAGS: dict[tuple[str, ...], frozenset[str]] = {
    ("help",): frozenset(),
    ("vehicles", "help"): frozenset(),
    ("vehicles", "status"): frozenset({"--id", "--json"}),
    ("vehicles", "info"): frozenset({"--id", "--json"}),
    ("vehicles", "automation", "status"): frozenset({"--id", "--json"}),
    ("vehicles", "perception", "candidates"): frozenset({"--json"}),
    ("vehicles", "update", "perception"): frozenset(
        {
            "--id",
            "--algorithm",
            "--candidate",
            "--timeout-s",
            "--dry-run",
            "--json",
            "--restart",
            "--verbose",
        }
    ),
    ("vehicles", "update", "memory"): frozenset(
        {
            "--id",
            "--implementation",
            "--timeout-s",
            "--json",
            "--verbose",
        }
    ),
    ("vehicles", "automation", "run"): frozenset(
        {
            "--id",
            "--observe-only",
            "--frames",
            "--interval-s",
            "--timeout-s",
            "--open-view",
            "--verbose",
            # intentionally no --record / --log (history recording not authorized)
        }
    ),
    ("vehicles", "automation", "stop"): frozenset({"--id", "--timeout-s", "--json", "--verbose"}),
    ("vehicles", "perception", "run"): frozenset(
        {
            "--id",
            "--frames",
            "--interval-s",
            "--timeout-s",
            "--algorithm",
            "--candidate",
            "--set",
            "--record",
            "--json",
        }
    ),
    ("vehicles", "perception", "apply"): frozenset(
        {
            "--algorithm",
            "--candidate",
            "--set",
            "--record",
            "--json",
        }
    ),
    ("vehicles", "perception", "compare"): frozenset(
        {
            "--record",
            "--json",
            "--candidate",
            "--set",
        }
    ),
    ("vehicles", "memory", "check"): frozenset(
        {
            "--id",
            "--implementation",
            "--record",
            "--auto",
            "--timeout-s",
            "--fresh-timeout-s",
            "--expiry-timeout-s",
            "--json",
        }
    ),
    ("vehicles", "memory", "reset"): frozenset({"--id", "--timeout-s", "--json"}),
    ("vehicles", "memory", "replay"): frozenset(
        {
            "--id",
            "--src",
            "--source",
            "--record",
            "--json",
            "--timeout-s",
        }
    ),
}

# Flags that are never allowed on continuity automation run even if parser-valid.
_UNSAFE_AUTOMATION_RUN_FLAGS = frozenset({"--record", "--log"})

_FORBIDDEN_SUBSTRINGS = (
    ("vehicles", "operation"),
    ("vehicles", "update", "core"),
    ("vehicles", "update", "autonomy"),
    ("simulators", "ensure"),
    ("vehicles", "perception", "setup"),
)

_HELP_STATUS_ONLY_PREFIXES = (
    ("help",),
    ("vehicles", "help"),
    ("vehicles", "status"),
    ("vehicles", "info"),
    ("vehicles", "automation", "status"),
    ("vehicles", "perception", "candidates"),
)

# Behavioral product surface hashed for evidence freshness (CLI + runtime trees used by catalog).
DEFAULT_PRODUCT_RELATIVE_PATHS: tuple[str, ...] = (
    "cli/automa",
    "cli/automa_cli/perception_runs.py",
    "cli/automa_cli/lab_plugins.py",
    "cli/automa_cli/memory_check.py",
    "cli/automa_cli/perception.py",
    "cli/automa_cli/memory.py",
    "cli/automa_cli/app.py",
    "cli/automa_cli/automation.py",
    "cli/automa_cli/vehicles.py",
    "cli/automa_cli/bundles.py",
    "cli/automa_cli/decision.py",
    "cli/automa_cli/operations.py",
    "cli/automa_cli/perception_view.py",
    "cli/automa_cli/streaming.py",
)

# Workspace trees loaded by the continuity catalog command surface.
DEFAULT_PRODUCT_TREE_ROOTS: tuple[str, ...] = (
    "autonomy",
    "implementations",
    "cli/automa_cli",
)


def required_product_keys() -> frozenset[str]:
    """Exact product identity keys the finalizer requires on recorded and current bundles."""

    keys = set(DEFAULT_PRODUCT_RELATIVE_PATHS)
    for tree in DEFAULT_PRODUCT_TREE_ROOTS:
        keys.add(f"{tree}/")
    return frozenset(keys)


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    parts = [str(x) for x in argv]
    if parts and parts[0] in {"./cli/automa", "cli/automa", "automa"}:
        return parts[1:]
    if len(parts) >= 2 and parts[0] in {"./cli", "cli"} and parts[1] == "automa":
        return parts[2:]
    return parts


def _match_allowed_prefix(rest: Sequence[str]) -> tuple[str, ...] | None:
    best: tuple[str, ...] | None = None
    for prefix in _ALLOWED_COMMAND_SPECS:
        if len(rest) >= len(prefix) and tuple(rest[: len(prefix)]) == prefix:
            if best is None or len(prefix) > len(best):
                best = prefix
    return best


def _extract_flags(rest: Sequence[str], prefix: Sequence[str]) -> tuple[list[str], list[str]]:
    """Return (flag_names, positionals) after command prefix."""

    args = list(rest[len(prefix) :])
    flags: list[str] = []
    positionals: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith("--"):
            name = token.split("=", 1)[0]
            flags.append(name)
            if "=" not in token and i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 1  # skip value
        elif token.startswith("-") and len(token) > 1 and not token[1:].isdigit():
            flags.append(token)
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 1
        else:
            positionals.append(token)
        i += 1
    return flags, positionals


def derive_safety_class(argv: Sequence[str]) -> str:
    """Derive a coarse safety class from argv (not catalog labels)."""

    rest = _normalize_argv(argv)
    if not rest:
        return "unknown"
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
        if any(f in rest for f in _UNSAFE_AUTOMATION_RUN_FLAGS):
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


def _parse_argv_against_cli(rest: Sequence[str]) -> tuple[bool, str]:
    """Reject unknown flags/values using the real public CLI parser."""

    try:
        repo_candidates = [
            Path.cwd(),
            Path(__file__).resolve().parents[5],
        ]
        for root in repo_candidates:
            if (root / "cli" / "automa_cli" / "app.py").is_file():
                root_s = str(root.resolve())
                if root_s not in sys.path:
                    sys.path.insert(0, root_s)
                break
        from cli.automa_cli.app import build_parser
    except Exception as exc:  # noqa: BLE001
        return False, f"cannot load CLI parser: {type(exc).__name__}: {exc}"

    normalized: list[str] = []
    for part in rest:
        if part.startswith("{") and part.endswith("}"):
            if "url" in part:
                normalized.append("http://localhost:5050")
            elif "dir" in part or "src" in part or "path" in part:
                normalized.append("/tmp/continuity-placeholder")
            else:
                normalized.append("continuity-placeholder")
        else:
            normalized.append(part)

    parser = build_parser()
    try:
        _ns, unknown = parser.parse_known_args(list(normalized))
    except SystemExit as exc:
        return False, f"CLI parser rejected argv: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"CLI parser error: {type(exc).__name__}: {exc}"
    if unknown:
        return False, f"unregistered flags/args: {unknown}"
    return True, "parser-valid"


def _flags_allowed(rest: Sequence[str], prefix: tuple[str, ...]) -> tuple[bool, str]:
    allowed = _ALLOWED_FLAGS.get(prefix)
    if allowed is None:
        return False, f"no flag allowlist for {' '.join(prefix)}"
    flags, _positionals = _extract_flags(rest, prefix)
    bad = [f for f in flags if f not in allowed and f != "-h" and f != "--help"]
    if bad:
        return False, f"flags not on continuity allowlist for {' '.join(prefix)}: {bad}"
    if prefix == ("vehicles", "automation", "run"):
        if "--observe-only" not in rest:
            return False, "automation run requires --observe-only"
        if any(f in rest for f in _UNSAFE_AUTOMATION_RUN_FLAGS):
            return False, "automation run forbids --record/--log on continuity track"
    return True, "flag-allowlist-ok"


def argv_allowed(argv: Sequence[str]) -> tuple[bool, str]:
    rest = _normalize_argv(argv)
    if not rest:
        return False, "empty argv"
    safety = derive_safety_class(argv)
    if safety == "forbidden":
        return False, f"forbidden command path: {' '.join(rest[:4])}"
    if safety == "unknown":
        return False, f"unknown/not allowlisted command: {' '.join(rest[:5])}"
    prefix = _match_allowed_prefix(rest)
    if prefix is None:
        return False, f"not on continuity allowlist: {' '.join(rest[:5])}"
    ok_flags, flag_reason = _flags_allowed(rest, prefix)
    if not ok_flags:
        return False, flag_reason
    ok_parse, parse_reason = _parse_argv_against_cli(rest)
    if not ok_parse:
        return False, parse_reason
    return True, "allowlisted+flags+parser-valid"


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


def _step_has_marker(step: Mapping[str, Any], marker: tuple[str, ...]) -> bool:
    for raw in step.get("commands") or []:
        if not isinstance(raw, (list, tuple)):
            continue
        rest = _normalize_argv([str(x) for x in raw])
        if len(rest) >= len(marker) and tuple(rest[: len(marker)]) == marker:
            return True
    return False


def _step_argv_list(step: Mapping[str, Any]) -> list[list[str]]:
    out: list[list[str]] = []
    for raw in step.get("commands") or []:
        if isinstance(raw, (list, tuple)) and raw:
            out.append(_normalize_argv([str(x) for x in raw]))
    return out


def _is_path_only_cue(cue: str) -> bool:
    text = (cue or "").strip()
    if not text:
        return True
    # Path/digest-only cues are not human-scannable confirmations.
    if re.fullmatch(r"[A-Za-z0-9_./\-]+", text) and ("/" in text or text.endswith(".json")):
        return True
    if re.fullmatch(r"[a-fA-F0-9]{32,}", text):
        return True
    return False


def validate_continuity_families(
    catalog: Mapping[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    """Ensure required family IDs, receipt topology, unique IDs, confirmation cues."""

    steps = catalog.get("steps")
    if not isinstance(steps, list):
        return False, "steps missing", {}

    by_family: dict[str, list[str]] = {fid: [] for fid in ALL_FAMILY_IDS}
    family_steps: dict[str, list[dict[str, Any]]] = {fid: [] for fid in ALL_FAMILY_IDS}
    unknown: list[str] = []
    seen_ids: set[str] = set()
    duplicate_ids: list[str] = []

    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "")
        if not step_id:
            return False, "step missing id", {"by_family": by_family}
        if step_id in seen_ids:
            duplicate_ids.append(step_id)
        seen_ids.add(step_id)
        family_id = step.get("family_id")
        if family_id is None:
            continue
        family_id = str(family_id)
        if family_id not in ALL_FAMILY_IDS:
            unknown.append(family_id)
            continue
        by_family[family_id].append(step_id)
        family_steps[family_id].append(step)
        if family_id in REQUIRED_FAMILY_IDS and _step_commands_help_status_only(step):
            return (
                False,
                f"required family {family_id} step {step_id} is help/status-only",
                {"by_family": by_family},
            )
        # Confirmation standard: primary_cue required and not path/JSON-only.
        if family_id in REQUIRED_FAMILY_IDS and step.get("required_for_verdict"):
            cue = str(step.get("primary_cue") or "")
            if _is_path_only_cue(cue):
                return (
                    False,
                    f"step {step_id}: primary_cue missing or path/JSON-only (not human-scannable)",
                    {"by_family": by_family},
                )

    if duplicate_ids:
        return False, f"duplicate step ids: {sorted(set(duplicate_ids))}", {"by_family": by_family}

    if unknown:
        return False, f"unknown family_id values: {sorted(set(unknown))}", {"by_family": by_family}

    missing = [fid for fid in REQUIRED_FAMILY_IDS if not by_family.get(fid)]
    if missing:
        return False, f"missing required family_id coverage: {missing}", {"by_family": by_family}

    # --- offline_perception: recorded capture + apply(s) sharing src_dir lineage ---
    offline = family_steps["continuity.offline_perception"]
    has_recorded_run = False
    apply_count = 0
    apply_uses_src_var = False
    apply_hardcoded_tmp = False
    for step in offline:
        for rest in _step_argv_list(step):
            if rest[:3] == ["vehicles", "perception", "run"]:
                if "--record" in rest:
                    has_recorded_run = True
            if rest[:3] == ["vehicles", "perception", "apply"]:
                apply_count += 1
                joined = " ".join(rest)
                if "{src_dir}" in joined or "src_dir" in joined:
                    apply_uses_src_var = True
                # Hardcoded absolute /tmp (not a continuity placeholder variable).
                for token in rest:
                    if token.startswith("/tmp/") and not token.startswith("{"):
                        apply_hardcoded_tmp = True
    if not has_recorded_run:
        return (
            False,
            "family continuity.offline_perception requires recorded perception run (--record)",
            {"by_family": by_family},
        )
    if apply_count < 1:
        return (
            False,
            "family continuity.offline_perception requires at least one perception apply",
            {"by_family": by_family},
        )
    if apply_hardcoded_tmp and not apply_uses_src_var:
        return (
            False,
            "family continuity.offline_perception apply must use shared {src_dir} lineage, not ad-hoc /tmp",
            {"by_family": by_family},
        )
    if not apply_uses_src_var:
        return (
            False,
            "family continuity.offline_perception apply must reference {src_dir} for same-source lineage",
            {"by_family": by_family},
        )

    # --- live_config_swap: update + observe-only run + stop + visual human cue ---
    live = family_steps["continuity.live_config_swap"]
    has_update = any(_step_has_marker(s, ("vehicles", "update", "perception")) for s in live)
    has_run = any(_step_has_marker(s, ("vehicles", "automation", "run")) for s in live)
    has_stop = any(_step_has_marker(s, ("vehicles", "automation", "stop")) for s in live)
    if not (has_update and has_run and has_stop):
        return (
            False,
            "family continuity.live_config_swap requires update perception + automation run + stop",
            {"by_family": by_family},
        )
    visual_steps = [
        s
        for s in live
        if s.get("visual_required") is True and s.get("required_for_verdict")
    ]
    if not visual_steps:
        return (
            False,
            "family continuity.live_config_swap requires at least one required visual_required step "
            "(healthy-view human confirmation)",
            {"by_family": by_family},
        )
    for s in visual_steps:
        if not str(s.get("visual_prompt") or s.get("primary_cue") or "").strip():
            return (
                False,
                f"live_config_swap step {s.get('id')}: visual step needs visual_prompt or primary_cue",
                {"by_family": by_family},
            )
    # observe-only on every automation run in this family
    for step in live:
        for rest in _step_argv_list(step):
            if rest[:3] == ["vehicles", "automation", "run"] and "--observe-only" not in rest:
                return (
                    False,
                    f"live_config_swap step {step.get('id')}: automation run must be --observe-only",
                    {"by_family": by_family},
                )

    # --- memory_lifecycle: memory check (+ not label-only) ---
    memory = family_steps["continuity.memory_lifecycle"]
    has_check = any(_step_has_marker(s, ("vehicles", "memory", "check")) for s in memory)
    if not has_check:
        return (
            False,
            "family continuity.memory_lifecycle requires vehicles memory check",
            {"by_family": by_family},
        )
    # Must not be pure perception apply mislabel.
    if any(_step_has_marker(s, ("vehicles", "perception", "apply")) for s in memory) and not has_check:
        return False, "memory family mislabeled without memory check", {"by_family": by_family}
    # Human-scannable memory cue must mention PASS/FAIL or Memory check.
    mem_cues = " ".join(str(s.get("primary_cue") or "") for s in memory)
    if not re.search(r"PASS|FAIL|Memory check|memory check", mem_cues, re.I):
        return (
            False,
            "family continuity.memory_lifecycle primary_cue must name a PASS/FAIL or Memory check verdict",
            {"by_family": by_family},
        )

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
    """Roll per-sequence status into per-family aggregates."""

    aggregates: dict[str, str] = {}
    by_family: dict[str, list[str]] = {}
    hitl_pending: dict[str, bool] = {}
    for seq in sequences:
        fid = str(seq.get("family_id") or "")
        if not fid:
            continue
        status = _normalize_step_status(str(seq.get("status") or "incomplete"))
        if status == "skip" and seq.get("visual_required"):
            hitl_pending[fid] = True
            status = "partial"
        elif seq.get("required_for_verdict") and status in {
            "skip",
            "blocked",
            "incomplete",
        }:
            # A required leaf cannot be hidden by a passing sibling. Visual
            # skips are the one expected machine-only hold; all other required
            # incomplete states remain partial and block an overall pass.
            status = "partial"
        by_family.setdefault(fid, []).append(status)

    for fid, statuses in by_family.items():
        if "fail" in statuses or "finding" in statuses:
            aggregates[fid] = "finding" if "finding" in statuses else "fail"
        elif hitl_pending.get(fid) or "partial" in statuses:
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


def activation_paths(repo_root: Path, vehicle_id: str) -> dict[str, Path]:
    base = repo_root / "runtime" / "vehicles" / vehicle_id / "bundle" / "runtime"
    return {
        "perception": base / "perception" / "active.json",
        "decision": base / "decision" / "active.json",
        "memory": base / "memory" / "active.json",
    }


def bundle_root(repo_root: Path, vehicle_id: str) -> Path:
    return repo_root / "runtime" / "vehicles" / vehicle_id / "bundle"


def snapshot_activation(path: Path) -> dict[str, Any]:
    """Capture restorable activation bytes + verification hash for one file."""

    if not path.is_file():
        return {
            "ok": False,
            "error": f"activation file missing: {path}",
            "restorable_bytes": None,
            "sha256": None,
            "path": str(path),
            "existed": False,
        }
    raw = path.read_bytes()
    return {
        "ok": True,
        "error": None,
        "restorable_bytes": raw.decode("utf-8"),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "path": str(path),
        "encoding": "utf-8",
        "existed": True,
    }


class TreeIdentityCollectionError(RuntimeError):
    """Raised when a product-tree identity cannot be collected completely."""


def _tree_lstat(path: Path, *, context: str) -> os.stat_result | None:
    """Return lstat metadata, distinguishing an absent path from collection failure."""

    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TreeIdentityCollectionError(
            f"{context} lstat failed for {path}: {exc}"
        ) from exc


def _dir_file_digests(root: Path) -> dict[str, str]:
    """Collect every included leaf below ``root`` or fail closed.

    Exclusions are evaluated against lexical paths relative to ``root``.  An
    absolute checkout ancestor named ``__pycache__`` therefore cannot suppress
    a product leaf, while enumeration, metadata, link, and content failures
    remain visible to the freshness finalizer.
    """

    root_stat = _tree_lstat(root, context="product tree")
    if root_stat is None:
        raise TreeIdentityCollectionError(f"product tree root missing: {root}")
    if not statmod.S_ISDIR(root_stat.st_mode):
        raise TreeIdentityCollectionError(f"product tree root is not a directory: {root}")

    digests: dict[str, str] = {}

    def visit(directory: Path, rel_parts: tuple[str, ...]) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        except OSError as exc:
            raise TreeIdentityCollectionError(
                f"product tree enumeration failed for {directory}: {exc}"
            ) from exc

        for entry in entries:
            child_parts = rel_parts + (entry.name,)
            if "__pycache__" in child_parts:
                continue
            if Path(entry.name).suffix in {".pyc", ".pyo"}:
                continue

            child = directory / entry.name
            try:
                child_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise TreeIdentityCollectionError(
                    f"product tree lstat failed for {child}: {exc}"
                ) from exc

            if statmod.S_ISDIR(child_stat.st_mode):
                visit(child, child_parts)
                continue
            if not (
                statmod.S_ISREG(child_stat.st_mode)
                or statmod.S_ISLNK(child_stat.st_mode)
            ):
                raise TreeIdentityCollectionError(
                    f"unsupported product tree entry type for {child}"
                )

            digest = tree_file_digest(child)
            if digest is None:
                raise TreeIdentityCollectionError(
                    f"product tree leaf could not be hashed: {child}"
                )
            digests["/".join(child_parts)] = digest

    visit(root, ())
    return digests


def _tree_root_identity(
    root: Path, *, root_stat: os.stat_result | None = None
) -> bytes | None:
    """Return byte-safe Git-relevant identity for an accepted tree root."""

    if root_stat is None:
        root_stat = _tree_lstat(root, context="product tree")
    if root_stat is None:
        return None
    if statmod.S_ISLNK(root_stat.st_mode):
        try:
            target = os.fsencode(os.readlink(root))
        except OSError as exc:
            raise TreeIdentityCollectionError(
                f"product tree root readlink failed for {root}: {exc}"
            ) from exc
        return _serialize_identity_fields((b"product-tree-root-v1", b"120000", target))
    if statmod.S_ISDIR(root_stat.st_mode):
        return _serialize_identity_fields((b"product-tree-root-v1", b"040000"))
    root_type = f"other:{statmod.S_IFMT(root_stat.st_mode):o}".encode("ascii")
    return _serialize_identity_fields((b"product-tree-root-v1", root_type))


def _tree_sha256_from_digests(
    digests: Mapping[str, str], *, root_identity: bytes | None = None
) -> str:
    """Hash a tree using length-framed raw path bytes and leaf identities."""

    import os

    if root_identity is None:
        root_identity = _serialize_identity_fields((b"product-tree-root-v1", b"040000"))
    entries = tuple(
        _serialize_identity_fields((os.fsencode(rel), digest.encode("ascii")))
        for rel, digest in sorted(digests.items(), key=lambda item: os.fsencode(item[0]))
    )
    material = _serialize_identity_fields(
        (b"product-tree-identity-v1", root_identity, *entries)
    )
    return hashlib.sha256(material).hexdigest()


def tree_content_sha256(root: Path) -> str | None:
    """Content-addressed digest of a Git-relevant product tree."""

    root_stat = _tree_lstat(root, context="product tree")
    root_identity = _tree_root_identity(root, root_stat=root_stat)
    if root_identity is None:
        return None
    if root_stat is not None and statmod.S_ISLNK(root_stat.st_mode):
        return _tree_sha256_from_digests({}, root_identity=root_identity)
    if root_stat is None or not statmod.S_ISDIR(root_stat.st_mode):
        return None
    return _tree_sha256_from_digests(
        _dir_file_digests(root), root_identity=root_identity
    )


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    if src.is_dir():
        shutil.copytree(src, dst)


def snapshot_staged_state(
    repo_root: Path,
    vehicle_id: str,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Snapshot perception/decision/memory activations + full staged bundle trees.

    When ``cache_dir`` is provided, copies ``autonomy/`` and ``implementations/``
    plus ``bundle-manifest.json`` into the cache so restore can recreate the prior
    staged configuration even after ``update perception/memory`` resyncs the bundle.
    """

    files: dict[str, Any] = {}
    ok = True
    errors: list[str] = []
    for name, path in activation_paths(repo_root, vehicle_id).items():
        snap = snapshot_activation(path)
        files[name] = snap
        if name == "perception" and not snapshot_is_restorable(snap):
            ok = False
            errors.append(str(snap.get("error") or f"{name} not restorable"))
        elif snap.get("existed") and not snapshot_is_restorable(snap):
            ok = False
            errors.append(str(snap.get("error") or f"{name} not restorable"))

    bundle = bundle_root(repo_root, vehicle_id)
    manifest_path = bundle / "bundle-manifest.json"
    manifest_snap = snapshot_activation(manifest_path)
    files["bundle_manifest"] = manifest_snap
    if manifest_snap.get("existed") and not snapshot_is_restorable(manifest_snap):
        ok = False
        errors.append("bundle-manifest not restorable")

    staged_trees: dict[str, Any] = {}
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for tree_name in ("autonomy", "implementations"):
            src = bundle / tree_name
            dst = cache_dir / tree_name
            if src.is_dir():
                try:
                    _copy_tree(src, dst)
                    digests = _dir_file_digests(dst)
                    tree_hash = _tree_sha256_from_digests(digests)
                except (OSError, TreeIdentityCollectionError) as exc:
                    ok = False
                    error = f"staged tree {tree_name} collection failed: {exc}"
                    errors.append(error)
                    staged_trees[tree_name] = {
                        "cache_relative": tree_name,
                        "file_count": 0,
                        "tree_sha256": None,
                        "file_digests": {},
                        "existed": True,
                        "collection_error": error,
                    }
                else:
                    staged_trees[tree_name] = {
                        "cache_relative": tree_name,
                        "file_count": len(digests),
                        "tree_sha256": tree_hash,
                        "file_digests": digests,
                        "existed": True,
                    }
            else:
                staged_trees[tree_name] = {
                    "cache_relative": tree_name,
                    "file_count": 0,
                    "tree_sha256": None,
                    "file_digests": {},
                    "existed": False,
                }
        if manifest_path.is_file():
            (cache_dir / "bundle-manifest.json").write_bytes(manifest_path.read_bytes())

    return {
        "ok": ok,
        "error": None if ok else "; ".join(errors),
        "files": files,
        "vehicle_id": vehicle_id,
        "staged_trees": staged_trees,
        "cache_dir": str(cache_dir) if cache_dir is not None else None,
        "schema": "continuity_staged_snapshot_v1",
    }


def snapshot_is_restorable(snapshot: Mapping[str, Any]) -> bool:
    if snapshot.get("ok") is not True and "files" not in snapshot:
        # single-file form
        if snapshot.get("existed") is False:
            return True  # absence is a valid restorable state
        return False
    if "files" in snapshot:
        if snapshot.get("ok") is not True:
            return False
        files = snapshot.get("files") or {}
        if not isinstance(files, dict) or not files:
            return False
        perception = files.get("perception") or {}
        return bool(perception.get("existed")) and isinstance(
            perception.get("restorable_bytes"), str
        ) and bool(perception.get("sha256"))
    body = snapshot.get("restorable_bytes")
    return isinstance(body, str) and len(body) > 0 and isinstance(snapshot.get("sha256"), str)


def restore_activation(snapshot: Mapping[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    """Restore one file, a full staged-state bundle, or remove trial-created files."""

    if "files" in snapshot:
        if not snapshot_is_restorable(snapshot):
            return {"ok": False, "error": "bundle snapshot not restorable"}
        results: dict[str, Any] = {}

        # Restore staged trees first when cache present.
        cache_dir_raw = snapshot.get("cache_dir")
        staged_trees = snapshot.get("staged_trees") or {}
        if cache_dir_raw and staged_trees:
            cache_dir = Path(str(cache_dir_raw))
            perc = (snapshot.get("files") or {}).get("perception") or {}
            perc_path = Path(str(perc.get("path") or ""))
            # .../runtime/vehicles/<id>/bundle/runtime/perception/active.json
            try:
                bundle = perc_path.parents[2]  # runtime -> bundle
            except IndexError:
                bundle = None
            if bundle is not None and bundle.name == "bundle":
                for tree_name, meta in staged_trees.items():
                    if not isinstance(meta, dict):
                        continue
                    dst = bundle / tree_name
                    if not meta.get("existed"):
                        # Absence is restorable: remove trial-created tree if present.
                        if dst.exists():
                            try:
                                if dst.is_dir():
                                    shutil.rmtree(dst)
                                else:
                                    dst.unlink()
                            except OSError as exc:
                                return {
                                    "ok": False,
                                    "error": f"failed to remove trial tree {tree_name}: {exc}",
                                    "results": results,
                                }
                            results[f"tree:{tree_name}"] = {
                                "ok": True,
                                "removed_trial_tree": True,
                                "path": str(dst),
                            }
                        else:
                            results[f"tree:{tree_name}"] = {
                                "ok": True,
                                "skipped": "did_not_exist",
                                "removed_trial_tree": False,
                            }
                        continue

                    expected_hash = meta.get("tree_sha256")
                    if not isinstance(expected_hash, str) or not expected_hash:
                        return {
                            "ok": False,
                            "error": f"staged tree {tree_name} missing tree_sha256",
                            "results": results,
                        }
                    src = cache_dir / tree_name
                    if not src.is_dir():
                        return {
                            "ok": False,
                            "error": f"staged tree cache missing: {tree_name}",
                            "results": results,
                        }
                    # Verify cache integrity before installing (fail closed on corruption).
                    try:
                        cache_hash = tree_content_sha256(src)
                    except TreeIdentityCollectionError as exc:
                        return {
                            "ok": False,
                            "error": f"staged tree cache collection failed for {tree_name}: {exc}",
                            "results": results,
                        }
                    if cache_hash != expected_hash:
                        return {
                            "ok": False,
                            "error": (
                                f"staged tree cache corrupted for {tree_name}: "
                                f"expected={expected_hash} cache={cache_hash}"
                            ),
                            "results": results,
                        }
                    try:
                        _copy_tree(src, dst)
                    except OSError as exc:
                        return {
                            "ok": False,
                            "error": f"restore tree {tree_name}: {exc}",
                            "results": results,
                        }
                    try:
                        actual_hash = tree_content_sha256(dst)
                    except TreeIdentityCollectionError as exc:
                        return {
                            "ok": False,
                            "error": f"restored tree collection failed for {tree_name}: {exc}",
                            "results": results,
                        }
                    if actual_hash != expected_hash:
                        return {
                            "ok": False,
                            "error": (
                                f"restored tree verification failed for {tree_name}: "
                                f"expected={expected_hash} actual={actual_hash}"
                            ),
                            "results": results,
                        }
                    results[f"tree:{tree_name}"] = {
                        "ok": True,
                        "path": str(dst),
                        "tree_sha256": actual_hash,
                        "verified": True,
                    }
                # restore bundle-manifest from cache if present and verify
                cached_manifest = cache_dir / "bundle-manifest.json"
                manifest_snap = (snapshot.get("files") or {}).get("bundle_manifest") or {}
                if cached_manifest.is_file():
                    target_manifest = bundle / "bundle-manifest.json"
                    body = cached_manifest.read_bytes()
                    target_manifest.write_bytes(body)
                    actual = hashlib.sha256(body).hexdigest()
                    expected = manifest_snap.get("sha256")
                    if isinstance(expected, str) and expected and actual != expected:
                        return {
                            "ok": False,
                            "error": "restored bundle-manifest hash mismatch",
                            "results": results,
                        }
                    results["tree:bundle_manifest"] = {
                        "ok": True,
                        "path": str(target_manifest),
                        "sha256": actual,
                        "verified": True,
                    }

        for name, file_snap in (snapshot.get("files") or {}).items():
            if not isinstance(file_snap, dict):
                continue
            if name in {"bundle_manifest"} and snapshot.get("cache_dir"):
                # already restored from cache when available
                if "tree:bundle_manifest" in results:
                    continue
            target = Path(str(file_snap.get("path") or ""))
            if file_snap.get("existed") is False:
                # Absence is restorable: remove trial-created file if present.
                if target and target.is_file():
                    try:
                        target.unlink()
                        results[name] = {
                            "ok": True,
                            "removed_trial_file": True,
                            "path": str(target),
                        }
                    except OSError as exc:
                        return {
                            "ok": False,
                            "error": f"{name}: failed to remove trial file: {exc}",
                            "results": results,
                        }
                else:
                    results[name] = {"ok": True, "skipped": "did_not_exist", "removed_trial_file": False}
                continue
            if not snapshot_is_restorable(file_snap):
                if name == "perception":
                    return {"ok": False, "error": f"{name} not restorable", "results": results}
                results[name] = {"ok": True, "skipped": "not_restorable_optional"}
                continue
            one = restore_activation(file_snap)
            results[name] = one
            if one.get("ok") is not True:
                return {"ok": False, "error": f"{name}: {one.get('error')}", "results": results}
        return {"ok": True, "error": None, "results": results}

    # Single-file restore
    if snapshot.get("existed") is False:
        target = Path(path or snapshot.get("path") or "")
        if target.is_file():
            target.unlink()
            return {"ok": True, "error": None, "path": str(target), "removed_trial_file": True}
        return {"ok": True, "error": None, "skipped": "did_not_exist", "removed_trial_file": False}

    if not snapshot_is_restorable(snapshot):
        return {"ok": False, "error": "snapshot is not restorable (need restorable_bytes)"}
    target = Path(path or snapshot["path"])
    body = str(snapshot["restorable_bytes"]).encode("utf-8")
    expected = str(snapshot["sha256"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        return {
            "ok": False,
            "error": "restore verification hash mismatch",
            "expected": expected,
            "actual": actual,
        }
    return {"ok": True, "error": None, "path": str(target), "sha256": actual}


def tree_file_digest(path: Path) -> str | None:
    """Digest one product file using Git-relevant type, mode, and material.

    Regular files bind executable mode plus bytes; symlinks bind mode ``120000``
    plus the target path bytes without following the link. Missing paths return
    ``None`` so required product entries fail closed in the finalizer.
    """

    return _git_relevant_file_digest(path)


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
    if product_paths is not None:
        paths = list(product_paths)
        include_default_trees = False
    else:
        paths = [repo_root / rel for rel in DEFAULT_PRODUCT_RELATIVE_PATHS]
        include_default_trees = True
    product: dict[str, str | None] = {}
    product_collection_errors: dict[str, str] = {}
    for path in paths:
        try:
            rel = str(path.resolve().relative_to(repo_root.resolve()))
        except ValueError:
            rel = str(path)
        digest = tree_file_digest(path)
        product[rel] = digest
        if digest is None:
            product_collection_errors[rel] = (
                f"product file identity unavailable: {path}"
            )
    if include_default_trees:
        for tree in DEFAULT_PRODUCT_TREE_ROOTS:
            root = repo_root / tree
            key = f"{tree}/"
            try:
                digest = tree_content_sha256(root)
            except TreeIdentityCollectionError as exc:
                digest = None
                product_collection_errors[key] = str(exc)
            else:
                if digest is None:
                    product_collection_errors[key] = (
                        f"product tree identity unavailable: {root}"
                    )
            product[key] = digest

    return {
        "schema": "continuity_identity_bundle_v0",
        "catalog_path": str(catalog_path),
        "catalog_sha256": tree_file_digest(catalog_path),
        "runner_sha256": tree_file_digest(runner),
        "continuity_contract_sha256": tree_file_digest(continuity),
        "product_sha256": product,
        "product_collection_errors": product_collection_errors,
        "metrics_ui": normalize_metrics_ui_identity(metrics_ui),
    }


def normalize_metrics_ui_identity(metrics_ui: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metrics_ui, dict) or not metrics_ui:
        return None
    return {
        "commit": metrics_ui.get("commit"),
        "worktree_state": metrics_ui.get("worktree_state"),
        "branch": metrics_ui.get("branch"),
        "diff_identity": metrics_ui.get("diff_identity"),
        "linked_pr": metrics_ui.get("linked_pr"),
        "named_diff": metrics_ui.get("named_diff"),
        "path": metrics_ui.get("path"),
    }


def metrics_ui_identity_acceptable(metrics_ui: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Dirty Metrics UI must carry a named reviewable identity (diff hash or linked PR)."""

    if not isinstance(metrics_ui, dict):
        return False, "metrics_ui identity missing"
    if not metrics_ui.get("commit"):
        return False, "metrics_ui commit missing"
    if metrics_ui.get("worktree_state") == "dirty":
        if not (
            metrics_ui.get("diff_identity")
            or metrics_ui.get("linked_pr")
            or metrics_ui.get("named_diff")
        ):
            return False, "dirty metrics_ui lacks named diff/linked_pr identity"
    return True, "ok"


def finalize_evidence_freshness(
    recorded: Mapping[str, Any],
    current: Mapping[str, Any],
) -> tuple[bool, str]:
    """Compare recorded identities to final tree identities; refuse pass on mismatch."""

    for label, bundle in (("recorded", recorded), ("current", current)):
        errors = bundle.get("product_collection_errors")
        if errors is None or errors == {}:
            continue
        if isinstance(errors, dict):
            detail = "; ".join(
                f"{path}: {message}" for path, message in sorted(errors.items())
            )
        else:
            detail = repr(errors)
        return False, f"{label} product collection failed: {detail}"

    for key in ("catalog_sha256", "runner_sha256", "continuity_contract_sha256"):
        rec = recorded.get(key)
        cur = current.get(key)
        if not isinstance(rec, str) or not rec:
            return False, f"recorded {key} missing"
        if not isinstance(cur, str) or not cur:
            return False, f"current {key} missing"
        if rec != cur:
            return False, f"mismatch {key}: recorded={rec!r} current={cur!r}"
    rec_prod = recorded.get("product_sha256")
    cur_prod = current.get("product_sha256")
    if not isinstance(rec_prod, dict) or not rec_prod:
        return False, "recorded product_sha256 missing or empty"
    if not isinstance(cur_prod, dict) or not cur_prod:
        return False, "current product_sha256 missing or empty"
    required = required_product_keys()
    rec_keys = set(rec_prod.keys())
    cur_keys = set(cur_prod.keys())
    missing_required = sorted(required - rec_keys)
    if missing_required:
        return False, f"recorded product_sha256 missing required keys: {missing_required}"
    if rec_keys != cur_keys:
        return (
            False,
            f"product key set mismatch recorded={sorted(rec_keys)} current={sorted(cur_keys)}",
        )
    for path, digest in rec_prod.items():
        if not isinstance(digest, str) or not digest:
            return False, f"recorded product digest missing for {path}"
        if cur_prod.get(path) != digest:
            return False, f"product mismatch {path}"
    if recorded.get("metrics_ui_required"):
        rec_mui = normalize_metrics_ui_identity(recorded.get("metrics_ui"))
        cur_mui = normalize_metrics_ui_identity(current.get("metrics_ui"))
        ok_rec, reason_rec = metrics_ui_identity_acceptable(rec_mui)
        if not ok_rec:
            return False, f"recorded {reason_rec}"
        ok_cur, reason_cur = metrics_ui_identity_acceptable(cur_mui)
        if not ok_cur:
            return False, f"current {reason_cur}"
        # Independent current identity must match recorded commit (+ dirty material).
        if rec_mui.get("commit") != cur_mui.get("commit"):
            return False, "metrics_ui commit mismatch"
        if rec_mui.get("worktree_state") != cur_mui.get("worktree_state"):
            return False, "metrics_ui worktree_state mismatch"
        if rec_mui.get("worktree_state") == "dirty":
            # Exact dirty material identity is required. linked_pr is additional
            # reviewability metadata, not a nullable escape hatch for mismatched diffs.
            rec_diff = rec_mui.get("diff_identity") or rec_mui.get("named_diff")
            cur_diff = cur_mui.get("diff_identity") or cur_mui.get("named_diff")
            if not isinstance(rec_diff, str) or not rec_diff:
                return False, "recorded dirty metrics_ui missing diff_identity/named_diff"
            if not isinstance(cur_diff, str) or not cur_diff:
                return False, "current dirty metrics_ui missing diff_identity/named_diff"
            if rec_diff != cur_diff:
                return False, "dirty metrics_ui diff_identity mismatch"
            rec_pr = rec_mui.get("linked_pr")
            cur_pr = cur_mui.get("linked_pr")
            if rec_pr is not None or cur_pr is not None:
                if rec_pr != cur_pr:
                    return False, "dirty metrics_ui linked_pr mismatch"
    return True, "evidence freshness finalizer ok"




def _serialize_identity_fields(fields: Sequence[bytes]) -> bytes:
    """Serialize identity fields without path/content delimiter ambiguity."""

    encoded = bytearray()
    encoded.extend(len(fields).to_bytes(8, "big"))
    for field in fields:
        encoded.extend(len(field).to_bytes(8, "big"))
        encoded.extend(field)
    return bytes(encoded)


def _git_relevant_file_digest(path: Path) -> str | None:
    """Return an unambiguous Git-material digest for a file-like product path."""

    import os
    import stat as statmod

    try:
        if path.is_symlink():
            mode = b"120000"
            material = os.fsencode(os.readlink(path))
        elif path.is_file():
            mode_bits = path.stat().st_mode
            mode = (
                b"100755"
                if mode_bits & (statmod.S_IXUSR | statmod.S_IXGRP | statmod.S_IXOTH)
                else b"100644"
            )
            material = path.read_bytes()
        elif path.exists():
            mode = f"other:{statmod.S_IFMT(path.stat().st_mode):o}".encode("ascii")
            material = b""
        else:
            return None
    except OSError:
        return None

    return hashlib.sha256(
        _serialize_identity_fields((b"product-file-identity-v1", mode, material))
    ).hexdigest()


def _untracked_path_identity(
    path: Path,
    rel: str,
    *,
    rel_bytes: bytes,
) -> tuple[str, str | None, bytes]:
    """Canonical untracked identity as Git-relevant mode + content digest.

    - Symlinks: mode ``120000`` and hash of the **target path bytes** (not the
      target's file contents; matches Git's symlink blob representation).
    - Regular files: mode ``100644`` / ``100755`` from the executable bit, plus
      content hash.
    - Other/missing: explicit sentinel so disappearance is identity-bearing.

    The third return value is the byte-safe, length-framed material used in the
    aggregate dirty identity. ``rel`` is display metadata only; Git path bytes
    are carried separately so quoted or delimiter-containing names cannot turn
    into a stable ``missing`` sentinel or collide during serialization.
    """

    import os
    import stat as statmod

    if path.is_symlink():
        try:
            target = os.readlink(path)
        except OSError:
            target = ""
        target_bytes = os.fsencode(target)
        digest = hashlib.sha256(target_bytes).hexdigest()
        material = _serialize_identity_fields(
            (b"untracked", b"120000", rel_bytes, target_bytes)
        )
        return f"untracked:120000:{rel}:{digest}", digest, material
    if path.is_file():
        try:
            mode_bits = path.stat().st_mode
        except OSError:
            material = _serialize_identity_fields((b"untracked", b"missing", rel_bytes))
            return f"untracked:missing:{rel}", None, material
        mode = "100755" if (mode_bits & (statmod.S_IXUSR | statmod.S_IXGRP | statmod.S_IXOTH)) else "100644"
        try:
            blob = path.read_bytes()
        except OSError:
            material = _serialize_identity_fields(
                (b"untracked", mode.encode("ascii"), rel_bytes, b"unreadable")
            )
            return f"untracked:{mode}:{rel}:unreadable", None, material
        digest = hashlib.sha256(blob).hexdigest()
        material = _serialize_identity_fields(
            (b"untracked", mode.encode("ascii"), rel_bytes, digest.encode("ascii"))
        )
        return f"untracked:{mode}:{rel}:{digest}", digest, material
    if path.exists():
        material = _serialize_identity_fields((b"untracked", b"other", rel_bytes))
        return f"untracked:other:{rel}", None, material
    material = _serialize_identity_fields((b"untracked", b"missing", rel_bytes))
    return f"untracked:missing:{rel}", None, material


def collect_git_identity(repo: Path) -> dict[str, Any] | None:
    """Shared Git identity algorithm for session recording and post-hoc finalization.

    Dirty material hashes status, tracked diffs, and untracked paths using a
    Git-relevant representation: symlink target bytes (mode 120000) and regular
    file content plus executable mode (100644/100755).
    """

    import os
    import subprocess

    repo = Path(repo)
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None

    def run_text(args: list[str]) -> str:
        try:
            done = subprocess.run(args, cwd=repo, check=False, capture_output=True, text=True)
        except OSError:
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    def run_bytes(args: list[str]) -> bytes:
        try:
            done = subprocess.run(args, cwd=repo, check=False, capture_output=True)
        except OSError:
            return b""
        return done.stdout if done.returncode == 0 else b""

    # ``-z`` makes Git emit raw path bytes terminated by NUL instead of its
    # human-oriented quoted representation. Decode only after splitting so
    # Unicode, control characters, and embedded newlines remain one path.
    status_raw = run_bytes(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "-z"]
    )
    status_lines = [os.fsdecode(part) for part in status_raw.split(b"\0") if part]
    commit = run_text(["git", "rev-parse", "HEAD"]) or None
    branch = run_text(["git", "branch", "--show-current"]) or None
    dirty = bool(status_raw)
    diff_identity = None
    untracked_names: list[str] = []
    untracked_hashes: dict[str, str] = {}
    if dirty:
        # Keep patch bytes intact; framing below prevents path/content bytes
        # from being reinterpreted as separators in the aggregate identity.
        patch = run_bytes(["git", "diff", "--binary", "--no-ext-diff", "HEAD"])
        cached = run_bytes(["git", "diff", "--binary", "--no-ext-diff", "--cached"])
        untracked_raw = run_bytes(
            ["git", "ls-files", "--others", "--exclude-standard", "--full-name", "-z"]
        )
        untracked_path_bytes = [part for part in untracked_raw.split(b"\0") if part]
        untracked_names = [os.fsdecode(part) for part in untracked_path_bytes]
        untracked_material: list[bytes] = []
        for rel_bytes, rel in zip(untracked_path_bytes, untracked_names):
            path = repo / rel
            entry, digest, material = _untracked_path_identity(
                path, rel, rel_bytes=rel_bytes
            )
            untracked_material.append(material)
            if digest is not None:
                untracked_hashes[rel] = digest
        material_fields = [b"git-identity-v2", status_raw, patch, cached]
        material_fields.extend(sorted(untracked_material))
        diff_identity = hashlib.sha256(_serialize_identity_fields(material_fields)).hexdigest()
    return {
        "path": repo.name,
        "commit": commit,
        "branch": branch,
        "worktree_state": "dirty" if dirty else "clean",
        "diff_identity": diff_identity,
        "status_porcelain": status_lines,
        "untracked_files": untracked_names,
        "untracked_sha256": untracked_hashes,
    }

def validate_session_against_tree(
    session_dir: Path,
    *,
    repo_root: Path,
    catalog_path: Path | None = None,
    metrics_ui: Mapping[str, Any] | None = None,
    metrics_ui_repo: Path | None = None,
) -> dict[str, Any]:
    """Post-hoc finalizer: load an existing session and recheck identity vs current tree.

    This is the deterministic entrypoint for session-A / tree-B stale-result checks.
    """

    session_dir = session_dir.resolve()
    repo_root = repo_root.resolve()
    result_path = session_dir / "result.json"
    identity_path = session_dir / "continuity-identity-at-start.json"
    if not result_path.is_file():
        return {"ok": False, "reason": f"missing {result_path}", "finalizer": None}

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"result.json unreadable: {exc}", "finalizer": None}

    recorded: dict[str, Any] | None = None
    # Prefer result.json continuity.identity_recorded (authoritative end-of-session
    # metrics_ui_required), then start-of-session snapshot file.
    cont = result.get("continuity") if isinstance(result, dict) else None
    if isinstance(cont, dict) and isinstance(cont.get("identity_recorded"), dict):
        recorded = dict(cont["identity_recorded"])
    if recorded is None and identity_path.is_file():
        try:
            recorded = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            recorded = None
    if not isinstance(recorded, dict):
        return {
            "ok": False,
            "reason": "no recorded identity bundle in session",
            "finalizer": None,
        }

    cat = catalog_path
    if cat is None:
        raw = recorded.get("catalog_path")
        if isinstance(raw, str) and raw:
            cat = Path(raw)
            if not cat.is_file():
                # try repo-relative
                rel_try = repo_root / Path(raw).name
                alt = (
                    repo_root
                    / "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs/m007-continuity.yaml"
                )
                cat = alt if alt.is_file() else cat
    if cat is None or not Path(cat).is_file():
        return {"ok": False, "reason": "catalog path for finalizer not found", "finalizer": None}

    # Never reuse recorded Metrics UI as "current" — that certifies stale UI identity.
    # Callers must supply metrics_ui or metrics_ui_repo for independent collection.
    current_mui = metrics_ui
    if current_mui is None and metrics_ui_repo is not None:
        current_mui = collect_git_identity(Path(metrics_ui_repo))
    if recorded.get("metrics_ui_required") and current_mui is None:
        return {
            "ok": False,
            "reason": (
                "metrics_ui_required but no current Metrics UI identity supplied "
                "(pass metrics_ui= or metrics_ui_repo= / --metrics-ui-repo)"
            ),
            "finalizer": None,
            "recorded": recorded,
            "current": None,
            "session_result": result.get("result") if isinstance(result, dict) else None,
        }

    current = collect_identity_bundle(
        repo_root=repo_root,
        catalog_path=Path(cat),
        metrics_ui=current_mui,
    )
    if recorded.get("metrics_ui_required"):
        current = dict(current)
        current["metrics_ui_required"] = True

    ok, reason = finalize_evidence_freshness(recorded, current)
    return {
        "ok": ok,
        "reason": reason,
        "finalizer": {"ok": ok, "reason": reason},
        "recorded": recorded,
        "current": current,
        "session_result": result.get("result") if isinstance(result, dict) else None,
    }


def derive_continuity_verdict(
    *,
    safety_preflight_ok: bool,
    family_aggregates: Mapping[str, str],
    restore_ok: bool | None,
    cleanup_ok: bool,
    finalizer_ok: bool,
    findings: Sequence[Mapping[str, Any]],
    hitl_complete: bool,
    operator: str | None = None,
) -> tuple[str, str | None]:
    """Single authoritative pass|findings|incomplete for continuity track."""

    if not safety_preflight_ok:
        return "incomplete", "continuity safety/family preflight failed"
    if not cleanup_ok:
        return "findings", "cleanup not proven"
    if restore_ok is False:
        return "findings", "US-04 staged-state restore failed"
    if not finalizer_ok:
        return "incomplete", "evidence freshness finalizer refused pass"
    for fid in REQUIRED_FAMILY_IDS:
        agg = family_aggregates.get(fid)
        if agg == "partial":
            return "incomplete", f"required family {fid} still partial (often HITL pending)"
        if agg != "passed":
            return "findings", f"required family {fid} aggregate is {agg!r}"
    blockers = [
        f
        for f in findings
        if isinstance(f, dict)
        and f.get("classification") in {"acceptance_blocker", "environment_blocker"}
    ]
    if blockers:
        return "findings", f"{len(blockers)} blocking finding(s) remain"
    if not hitl_complete:
        return "incomplete", "required visual HITL not completed"
    if not isinstance(operator, str) or not operator.strip():
        return "incomplete", "continuity pass requires a named --operator identity"
    return "pass", None


# ---------------------------------------------------------------------------
# Offline source lineage (content-bound)
# ---------------------------------------------------------------------------


def capture_source_lineage(src_dir: Path) -> dict[str, Any]:
    """Build content-bound lineage for a recorded perception-run directory.

    Hashes run.json plus ordered input frame image bytes. Fails closed if the
    capture receipt or ordered frames are missing.
    """

    src_dir = Path(src_dir)
    if not src_dir.is_dir():
        return {"ok": False, "error": f"src_dir not a directory: {src_dir}"}
    manifest = src_dir / "run.json"
    if not manifest.is_file():
        return {"ok": False, "error": f"missing capture receipt run.json under {src_dir}"}
    try:
        raw = manifest.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"run.json unreadable: {exc}"}
    frames = doc.get("frames")
    if not isinstance(frames, list) or not frames:
        return {"ok": False, "error": "run.json has no frames list"}
    frame_digests: list[dict[str, Any]] = []
    h = hashlib.sha256()
    h.update(raw)
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            return {"ok": False, "error": f"frame[{index}] not an object"}
        image_path = frame.get("image_path") or frame.get("path")
        if not isinstance(image_path, str) or not image_path:
            return {"ok": False, "error": f"frame[{index}] missing image_path"}
        path = Path(image_path)
        if not path.is_file():
            # try relative to src_dir
            alt = src_dir / "frames" / Path(image_path).name
            path = alt if alt.is_file() else path
        if not path.is_file():
            return {"ok": False, "error": f"frame[{index}] image missing: {image_path}"}
        blob = path.read_bytes()
        digest = hashlib.sha256(blob).hexdigest()
        h.update(digest.encode("ascii"))
        frame_digests.append(
            {
                "index": index,
                "frame_id": frame.get("frame_id"),
                "path_name": path.name,
                "sha256": digest,
                "size": len(blob),
            }
        )
    return {
        "ok": True,
        "error": None,
        "src_dir": str(src_dir),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "ordered_input_sha256": h.hexdigest(),
        "frame_count": len(frame_digests),
        "frames": frame_digests,
        "schema": "continuity_source_lineage_v1",
    }


def verify_source_lineage(src_dir: Path, expected: Mapping[str, Any]) -> tuple[bool, str]:
    """Recompute content lineage and compare to a previously recorded receipt."""

    if expected.get("ok") is not True:
        return False, "expected lineage not ok"
    current = capture_source_lineage(src_dir)
    if current.get("ok") is not True:
        return False, f"current lineage failed: {current.get('error')}"
    for key in ("manifest_sha256", "ordered_input_sha256", "frame_count"):
        if current.get(key) != expected.get(key):
            return False, f"lineage mismatch on {key}"
    return True, "lineage identity verified"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: post-hoc finalize a packaged continuity session against the current tree."""

    parser = argparse.ArgumentParser(description="Continuity evidence finalizer")
    sub = parser.add_subparsers(dest="cmd", required=True)
    fin = sub.add_parser("finalize-session", help="Validate session identities vs current tree")
    fin.add_argument("session_dir", type=Path)
    fin.add_argument("--repo-root", type=Path, default=Path.cwd())
    fin.add_argument("--catalog", type=Path, default=None)
    fin.add_argument(
        "--metrics-ui-repo",
        type=Path,
        default=None,
        help="Path to Metrics UI checkout for independent post-hoc identity collection",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.cmd == "finalize-session":
        out = validate_session_against_tree(
            args.session_dir,
            repo_root=args.repo_root,
            catalog_path=args.catalog,
            metrics_ui_repo=args.metrics_ui_repo,
        )
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out.get("ok") else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
