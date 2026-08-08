#!/usr/bin/env python3
"""Human-in-the-loop live CLI session runner.

Runs a YAML/JSON catalog of operator steps, captures machine evidence, prompts
for human visual judgment and notes, and writes a structured session artifact.

Acceptance catalogs cannot pass under dry-run or non-interactive auto-visual
modes. Machine gates evaluate status/view JSON against the M007-05 contract.
Cleanup runs in a finally block when startup may have created a worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import importlib.util

_CONTINUITY_PATH = Path(__file__).resolve().parent / "continuity_contract.py"
_CONTINUITY_SPEC = importlib.util.spec_from_file_location(
    "live_cli_continuity_contract",
    _CONTINUITY_PATH,
)
assert _CONTINUITY_SPEC is not None and _CONTINUITY_SPEC.loader is not None
_continuity = importlib.util.module_from_spec(_CONTINUITY_SPEC)
_CONTINUITY_SPEC.loader.exec_module(_continuity)
CONTINUITY_TRACK = _continuity.CONTINUITY_TRACK
REQUIRED_FAMILY_IDS = _continuity.REQUIRED_FAMILY_IDS
aggregate_family_status = _continuity.aggregate_family_status
collect_identity_bundle = _continuity.collect_identity_bundle
finalize_evidence_freshness = _continuity.finalize_evidence_freshness
overall_pass_allowed = _continuity.overall_pass_allowed
validate_continuity_families = _continuity.validate_continuity_families
validate_continuity_safety_preflight = _continuity.validate_continuity_safety_preflight
snapshot_activation = _continuity.snapshot_activation
snapshot_staged_state = _continuity.snapshot_staged_state
restore_activation = _continuity.restore_activation
snapshot_is_restorable = _continuity.snapshot_is_restorable
derive_continuity_verdict = _continuity.derive_continuity_verdict
capture_source_lineage = _continuity.capture_source_lineage
verify_source_lineage = _continuity.verify_source_lineage
validate_session_against_tree = _continuity.validate_session_against_tree


SCHEMA = "live_cli_session_result_v0"
FINDING_SCHEMA = "live_cli_session_finding_v0"
CATALOG_SCHEMA = "live_cli_session_catalog_v0"

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[5]
TOOL_DIR = Path(__file__).resolve().parent
CATALOGS_DIR = TOOL_DIR / "catalogs"

PromptFn = Callable[[str], str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text if text.endswith("\n") else text + "\n")


def _redact(text: str, repo_root: Path) -> str:
    root = str(repo_root.resolve())
    home = str(Path.home())
    return text.replace(root, "<repo>").replace(home, "<home>")


def _redact_path(path: str | Path, repo_root: Path) -> str:
    """Redact absolute local paths from reviewable artifacts."""

    text = _redact(str(path), repo_root)
    # Collapse remaining absolute paths (outside repo/home) to basename.
    if text.startswith("/") or (len(text) > 2 and text[1] == ":"):
        return f"<path>/{Path(text).name}"
    return text


# Canonical acceptance catalog: only the pinned bundled content may pass.
CANONICAL_ACCEPTANCE_ID = "m007-acceptance"
CANONICAL_ACCEPTANCE_PATH = CATALOGS_DIR / "m007-acceptance.yaml"
CANONICAL_ACCEPTANCE_GATES = (
    "help_discoverability",
    "initial_layers",
    "staging",
    "startup",
    "running_layers",
    "human_view",
    "authority",
    "correlation",
    "default_recording",
    "cleanup",
)
CANONICAL_ACCEPTANCE_STEP_VALIDATORS = {
    "status-initial": ("initial_layers",),
    "update-perception": ("staged_layers",),
    "status-running": ("running_layers", "authority", "view_correlation", "preservation"),
    "status-stopped": ("stopped_layers", "default_recording", "preservation"),
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _catalog_bytes_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# Reviewed digest of catalogs/m007-acceptance.yaml. Update this constant in the
# same commit that intentionally changes the bundled acceptance catalog.
# Independent of runtime file reads: an on-disk edit before process start must
# not become the new expected pin.
PINNED_ACCEPTANCE_CATALOG_DIGEST = (
    "c1cbc12e95c211c9e473077babac7a9ac448061d398deff783e1d33216a634f4"
)

_GITHUB_PR_URL_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/pull/(?P<number>\d{1,7})/?$"
)
_GITHUB_PR_NUMBER_RE = re.compile(r"^#?(?P<number>\d{1,7})$")
_GITHUB_REMOTE_RE = re.compile(
    r"(?:github\.com[:/])(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)


def _parse_github_pr_ref(value: str | None) -> dict[str, Any] | None:
    """Parse a GitHub PR URL or bare number. Bare numbers need a repo remote."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    match = _GITHUB_PR_URL_RE.fullmatch(text)
    if match:
        return {
            "owner": match.group("owner"),
            "repo": match.group("repo"),
            "number": int(match.group("number")),
            "url": text.rstrip("/"),
            "kind": "url",
        }
    match = _GITHUB_PR_NUMBER_RE.fullmatch(text)
    if match:
        return {
            "owner": None,
            "repo": None,
            "number": int(match.group("number")),
            "url": None,
            "kind": "number",
        }
    return None


def _valid_linked_pr(value: str | None) -> bool:
    """Syntax check only; provenance binding is in ``_repo_reviewable``."""

    return _parse_github_pr_ref(value) is not None


def _github_remote_identity(repo: Path | None) -> dict[str, str] | None:
    if repo is None or not repo.is_dir():
        return None
    try:
        completed = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    url = (completed.stdout or "").strip()
    match = _GITHUB_REMOTE_RE.search(url)
    if not match:
        return None
    return {"owner": match.group("owner"), "repo": match.group("repo"), "remote": url}


def _linked_pr_bound_to_checkout(
    linked_pr: str | None,
    *,
    repo: Path | None,
    identity: Mapping[str, Any],
) -> tuple[bool, str, dict[str, Any] | None]:
    """Require linked PR to name the same GitHub owner/repo as the checkout origin."""

    parsed = _parse_github_pr_ref(linked_pr)
    if parsed is None:
        return False, f"linked_pr={linked_pr!r} is not a valid GitHub PR reference", None
    remote = _github_remote_identity(repo)
    if remote is None:
        # Fall back to identity fields if present.
        owner = identity.get("github_owner")
        name = identity.get("github_repo") or identity.get("path")
        if isinstance(owner, str) and isinstance(name, str):
            remote = {"owner": owner, "repo": name, "remote": ""}
        else:
            return (
                False,
                "cannot bind linked PR without a GitHub origin remote on the checkout",
                None,
            )
    if parsed["kind"] == "number":
        parsed = {
            **parsed,
            "owner": remote["owner"],
            "repo": remote["repo"],
            "url": (
                f"https://github.com/{remote['owner']}/{remote['repo']}"
                f"/pull/{parsed['number']}"
            ),
        }
    if parsed["owner"] != remote["owner"] or parsed["repo"] != remote["repo"]:
        return (
            False,
            (
                f"linked PR {parsed['owner']}/{parsed['repo']}#{parsed['number']} "
                f"does not match checkout origin {remote['owner']}/{remote['repo']}"
            ),
            None,
        )
    bound = {
        "owner": parsed["owner"],
        "repo": parsed["repo"],
        "number": parsed["number"],
        "url": parsed["url"],
        "checkout_commit": identity.get("commit"),
        "checkout_branch": identity.get("branch"),
        "origin": remote.get("remote"),
    }
    return True, "linked PR bound to checkout origin", bound


def _load_catalog_bytes_if_pinned(path: Path | None = None) -> bytes | None:
    """Return catalog file bytes only when they match the reviewed digest constant."""

    target = path if path is not None else CANONICAL_ACCEPTANCE_PATH
    try:
        raw = target.read_bytes()
    except OSError:
        return None
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PINNED_ACCEPTANCE_CATALOG_DIGEST:
        return None
    return raw


def _load_pinned_acceptance_catalog(path: Path | None = None) -> dict[str, Any] | None:
    """Re-parse catalog bytes only after they match the independent digest pin."""

    raw = _load_catalog_bytes_if_pinned(path)
    if raw is None:
        return None
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _catalog_max_frame_lag(catalog: Mapping[str, Any]) -> int | None:
    """Read the reviewed correlation bound from the acceptance surface."""

    contract = catalog.get("acceptance_contract")
    correlation = contract.get("correlation") if isinstance(contract, Mapping) else None
    value = correlation.get("max_frame_lag") if isinstance(correlation, Mapping) else None
    if type(value) is not int or value < 1:  # bool is intentionally rejected
        return None
    return value


def _is_canonical_acceptance_catalog(
    path: Path | None, catalog: Mapping[str, Any]
) -> tuple[bool, str]:
    """Formal acceptance requires executed mapping == reviewed pinned catalog.

    The reviewed digest is a source constant. On-disk edits before process start
    change the file digest and fail closed unless the constant is intentionally
    updated in the same reviewable change.
    """

    if not PINNED_ACCEPTANCE_CATALOG_DIGEST:
        return False, "pinned acceptance catalog digest constant is empty"
    # Always evaluate the bundled path against the independent constant.
    bundled_digest = _catalog_bytes_digest(CANONICAL_ACCEPTANCE_PATH)
    if bundled_digest != PINNED_ACCEPTANCE_CATALOG_DIGEST:
        return (
            False,
            "bundled m007-acceptance.yaml digest does not match the reviewed "
            f"PINNED_ACCEPTANCE_CATALOG_DIGEST constant ({bundled_digest})",
        )
    if path is not None:
        path_digest = _catalog_bytes_digest(path)
        if path_digest != PINNED_ACCEPTANCE_CATALOG_DIGEST:
            return (
                False,
                "catalog file digest does not match the reviewed "
                "PINNED_ACCEPTANCE_CATALOG_DIGEST constant",
            )
    pinned = _load_pinned_acceptance_catalog(CANONICAL_ACCEPTANCE_PATH)
    if pinned is None:
        return False, "pinned acceptance catalog could not be parsed after digest check"
    # Bind the mapping that run_session will execute, not only the path on disk.
    if _stable_json(catalog) != _stable_json(pinned):
        return (
            False,
            "parsed catalog mapping does not match the pinned acceptance catalog content",
        )
    if catalog.get("id") != CANONICAL_ACCEPTANCE_ID:
        return False, f"catalog id {catalog.get('id')!r} is not {CANONICAL_ACCEPTANCE_ID!r}"
    if catalog.get("track") != "acceptance":
        return False, f"catalog track {catalog.get('track')!r} is not 'acceptance'"
    if str(catalog.get("vehicle_id") or "") != "chase-sim-chaser":
        return False, f"vehicle_id {catalog.get('vehicle_id')!r} is not chase-sim-chaser"
    if _catalog_max_frame_lag(catalog) is None:
        return False, "acceptance_contract.correlation.max_frame_lag must be a positive integer"
    gates = [
        str(g.get("id"))
        for g in (catalog.get("gates") or [])
        if isinstance(g, dict) and g.get("id")
    ]
    if tuple(gates) != CANONICAL_ACCEPTANCE_GATES:
        return False, f"acceptance gates mismatch: {gates}"
    steps_by_id = {
        str(s.get("id")): s
        for s in (catalog.get("steps") or [])
        if isinstance(s, dict) and s.get("id")
    }
    for step_id, validators in CANONICAL_ACCEPTANCE_STEP_VALIDATORS.items():
        step = steps_by_id.get(step_id)
        if step is None:
            return False, f"missing required acceptance step {step_id!r}"
        declared = tuple(str(v) for v in (step.get("machine_validators") or []))
        if declared != validators:
            return False, f"step {step_id!r} validators {declared} != {validators}"
    run_step = steps_by_id.get("automation-run") or {}
    run_cmds = run_step.get("commands") or []
    if not run_cmds or not isinstance(run_cmds[0], list):
        return False, "automation-run missing primary command"
    run_argv = [str(p) for p in run_cmds[0]]
    if "--observe-only" not in run_argv:
        return False, "automation-run must include --observe-only"
    if "run" not in run_argv:
        return False, "automation-run must invoke automation run"
    stop_step = steps_by_id.get("automation-stop") or {}
    stop_cmds = stop_step.get("commands") or []
    if not stop_cmds or "stop" not in [str(p) for p in stop_cmds[0]]:
        return False, "automation-stop must invoke automation stop"
    initial = steps_by_id.get("status-initial") or {}
    commands = initial.get("commands") or []
    if not commands or not isinstance(commands[0], list):
        return False, "status-initial missing primary command"
    primary = [str(p) for p in commands[0]]
    if "--id" in primary:
        return False, "status-initial primary command must be aggregate (no --id)"
    if primary[:3] != ["./cli/automa", "vehicles", "status"]:
        return False, "status-initial primary command must be vehicles status"
    if "--chase-url" not in primary:
        return False, "status-initial primary command must include --chase-url"
    return True, "canonical acceptance catalog"


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "PyYAML is required for YAML catalogs. Install with: pip install pyyaml"
        ) from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Catalog must be a mapping: {path}")
    return data


def _load_catalog(path: Path) -> dict[str, Any]:
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = _load_yaml(path)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit(f"Catalog must be a mapping: {path}")
    if data.get("schema") != CATALOG_SCHEMA:
        raise SystemExit(
            f"Unsupported catalog schema {data.get('schema')!r}; expected {CATALOG_SCHEMA}"
        )
    return data


def _format_command(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def _substitute(value: str, variables: Mapping[str, str]) -> str:
    out = value
    for key, replacement in variables.items():
        out = out.replace("{" + key + "}", replacement)
    return out


def _substitute_argv(argv: Sequence[str], variables: Mapping[str, str]) -> list[str]:
    return [_substitute(str(part), variables) for part in argv]


def _path_under(path: Path, root: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return None


def _git_identity(repo: Path) -> dict[str, Any]:
    """Record pre-session repository identity (before any session artifacts)."""

    def run(args: list[str]) -> str:
        try:
            completed = subprocess.run(
                args, cwd=repo, check=False, capture_output=True, text=True
            )
        except OSError:
            return ""
        return completed.stdout.strip() if completed.returncode == 0 else ""

    status_lines = [
        line
        for line in (run(["git", "status", "--porcelain"]) or "").splitlines()
        if line
    ]
    status = "\n".join(status_lines)
    commit = run(["git", "rev-parse", "HEAD"]) or None
    branch = run(["git", "branch", "--show-current"]) or None
    dirty = bool(status)
    diff_identity = None
    untracked_names: list[str] = []
    untracked_hashes: dict[str, str] = {}
    if dirty:
        # Tracked patch for identity material; untracked listed but never copied.
        patch = run(["git", "diff", "HEAD"])
        cached = run(["git", "diff", "--cached"])
        untracked_raw = run(["git", "ls-files", "--others", "--exclude-standard"])
        untracked_names = [line for line in untracked_raw.splitlines() if line]
        parts = [status, patch, cached]
        for rel in untracked_names:
            path = repo / rel
            # Never follow symlinks for identity hashing.
            if path.is_symlink():
                parts.append(f"untracked-symlink:{rel}")
                continue
            if path.is_file():
                digest = _sha256_file(path)
                untracked_hashes[rel] = digest
                parts.append(f"untracked:{rel}:{digest}")
            else:
                parts.append(f"untracked:{rel}:missing")
        material = "\n".join(parts).encode("utf-8")
        diff_identity = hashlib.sha256(material).hexdigest()
    return {
        # Never store absolute local paths in reviewable artifacts.
        "path": repo.name,
        "commit": commit,
        "branch": branch,
        "worktree_state": "dirty" if dirty else "clean",
        "diff_identity": diff_identity,
        "status_porcelain": status_lines,
        "untracked_files": untracked_names,
        "untracked_sha256": untracked_hashes,
    }


def _default_prompt(message: str) -> str:
    try:
        return input(message)
    except EOFError:
        return ""


def _pid_alive(pid: int | None) -> bool | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _bind_src_dir_from_capture_stdout(
    stdout_text: str, *, repo_root: Path
) -> Path | None:
    """Parse exact recorded run path from perception run human/json output."""

    candidates: list[Path] = []
    for line in stdout_text.splitlines():
        line = line.strip()
        for prefix in ("run:", "run_dir:", "record:", "Record:"):
            if line.lower().startswith(prefix.lower()):
                raw = line.split(":", 1)[1].strip()
                path = Path(raw)
                if not path.is_absolute():
                    path = (repo_root / path).resolve()
                if path.is_dir():
                    candidates.append(path)
        # bare path line containing perception-runs
        if "perception-runs" in line and not line.startswith("$"):
            # may be JSON "run_dir": "..."
            m = re.search(r'(/[^\s"]*perception-runs/[^\s"]+)', line)
            if m:
                path = Path(m.group(1))
                if path.is_dir():
                    candidates.append(path)
            else:
                raw = line.strip().strip(",\"'")
                path = Path(raw)
                if not path.is_absolute():
                    path = (repo_root / path).resolve()
                if path.is_dir() and "perception-runs" in str(path):
                    candidates.append(path)
    return candidates[-1] if candidates else None


def _latest_perception_run_dir(repo_root: Path, vehicle_id: str) -> Path | None:
    """Newest recorded perception-run directory for continuity src_dir binding."""

    root = (
        repo_root
        / "runtime"
        / "vehicles"
        / vehicle_id
        / "bundle"
        / "runtime"
        / "perception-runs"
    )
    if not root.is_dir():
        # Fallback global applies/runs layouts used by some configs
        alt = repo_root / "runtime" / "perception-runs"
        root = alt if alt.is_dir() else root
    if not root.is_dir():
        return None
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def _perception_activation_path(repo_root: Path, vehicle_id: str) -> Path:
    return (
        repo_root
        / "runtime"
        / "vehicles"
        / vehicle_id
        / "bundle"
        / "runtime"
        / "perception"
        / "active.json"
    )


def _list_run_directories(repo_root: Path, vehicle_id: str) -> list[str]:
    runs = (
        repo_root
        / "runtime"
        / "vehicles"
        / vehicle_id
        / "bundle"
        / "runtime"
        / "automation"
        / "runs"
    )
    if not runs.is_dir():
        return []
    return sorted(path.name for path in runs.iterdir() if path.is_dir())


# ---------------------------------------------------------------------------
# Machine validators (M007-05)
# ---------------------------------------------------------------------------


STATUS_SCHEMA = "automa_vehicle_status_v1"
PUBLICATION_SCHEMA = "automa_perception_publication_v1"

# Within one capture receipt, every protected field must match before/after.
_SESSION_FIELDS = (
    "game_id",
    "scenario_id",
    "simulation_epoch",
    "playback",
    "control_source",
    "control_input",
)

# Across commands, natural frameIndex advancement is allowed; mode/authority is not.
# Mirrors observe-only continuous-run stable identity while treating control_input
# as operator-visible protected state (proposal: distinguish sim time from mutation).
_CROSS_COMMAND_STABLE_SCALAR_FIELDS = (
    "game_id",
    "scenario_id",
    "simulation_epoch",
    "control_source",
    "control_input",
)


def extract_vehicle_status(
    status: Mapping[str, Any],
    vehicle_id: str,
) -> dict[str, Any] | None:
    """Return one automa_vehicle_status_v1 card from targeted or aggregate JSON.

    Requires exact schema and vehicle identity. Never substitutes a differently
    id'd card when the requested id is absent.
    """

    vehicles = status.get("vehicles")
    if isinstance(vehicles, list):
        matches = [
            item
            for item in vehicles
            if isinstance(item, dict)
            and item.get("schema") == STATUS_SCHEMA
            and str(item.get("vehicle_id")) == vehicle_id
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    layers = status.get("layers")
    if isinstance(layers, dict) and layers:
        if status.get("schema") != STATUS_SCHEMA:
            return None
        if str(status.get("vehicle_id")) != vehicle_id:
            return None
        return dict(status)
    return None


def _layer_state(status: Mapping[str, Any], layer: str, vehicle_id: str | None = None) -> str | None:
    card: Mapping[str, Any] = status
    if vehicle_id is not None:
        extracted = extract_vehicle_status(status, vehicle_id)
        if extracted is None:
            return None
        card = extracted
    layers = card.get("layers")
    if not isinstance(layers, dict):
        return None
    entry = layers.get(layer)
    if not isinstance(entry, dict):
        return None
    state = entry.get("state")
    return str(state) if state is not None else None


def _worker_details(status: Mapping[str, Any], vehicle_id: str | None = None) -> dict[str, Any]:
    card: Mapping[str, Any] = status
    if vehicle_id is not None:
        extracted = extract_vehicle_status(status, vehicle_id)
        if extracted is None:
            return {}
        card = extracted
    layers = card.get("layers")
    if not isinstance(layers, dict):
        return {}
    worker = layers.get("automation_worker")
    if not isinstance(worker, dict):
        return {}
    details = worker.get("details")
    return details if isinstance(details, dict) else {}


def _authority(status: Mapping[str, Any], vehicle_id: str | None = None) -> dict[str, Any]:
    details = _worker_details(status, vehicle_id)
    authority = details.get("authority")
    return authority if isinstance(authority, dict) else {}


def _playback_mode(playback: Any) -> dict[str, Any] | None:
    """Stable playback projection: mode/authority, not elapsed frameIndex."""

    if not isinstance(playback, dict):
        return None
    return {
        "phase": playback.get("phase"),
        "pendingAction": playback.get("pendingAction"),
    }


def stable_session_projection(fingerprint: Mapping[str, Any]) -> dict[str, Any] | None:
    """Cross-command stable projection of a within-receipt fingerprint."""

    if not isinstance(fingerprint, Mapping):
        return None
    projection: dict[str, Any] = {}
    for field_name in _CROSS_COMMAND_STABLE_SCALAR_FIELDS:
        if field_name not in fingerprint:
            return None
        projection[field_name] = fingerprint[field_name]
    mode = _playback_mode(fingerprint.get("playback"))
    if mode is None:
        return None
    projection["playback_mode"] = mode
    return projection


def extract_session_fingerprint(
    status: Mapping[str, Any],
    vehicle_id: str,
) -> dict[str, Any] | None:
    """Fingerprint from layers.passive_capture (mirrors live-smoke predicate).

    Within one receipt, all six session fields must match before/after exactly.
    Cross-command comparison uses :func:`stable_session_projection`.
    """

    card = extract_vehicle_status(status, vehicle_id)
    if card is None:
        return None
    layers = card.get("layers")
    if not isinstance(layers, dict):
        return None
    passive = layers.get("passive_capture")
    if not isinstance(passive, dict):
        return None
    if passive.get("state") != "available":
        return None
    if passive.get("mutation_attempted") is not False:
        return None
    preservation = passive.get("session_preservation")
    if not isinstance(preservation, dict):
        return None
    if preservation.get("preserved") is not True:
        return None
    # Fail closed: keys must be present and exactly empty lists.
    if preservation.get("changed_fields") != []:
        return None
    if preservation.get("unknown_fields") != []:
        return None
    before = preservation.get("before")
    after = preservation.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    fingerprint: dict[str, Any] = {}
    for field_name in _SESSION_FIELDS:
        if field_name not in before or field_name not in after:
            return None
        if field_name != "control_input" and before[field_name] is None:
            return None
        if before[field_name] != after[field_name]:
            return None
        fingerprint[field_name] = before[field_name]
    for field_name in ("game_id", "scenario_id", "simulation_epoch", "control_source"):
        if fingerprint.get(field_name) in (None, ""):
            return None
    if not isinstance(fingerprint.get("playback"), dict):
        return None
    fingerprint["preserved"] = True
    fingerprint["changed_fields"] = []
    fingerprint["unknown_fields"] = []
    fingerprint["mutation_attempted"] = False
    fingerprint["stable_projection"] = stable_session_projection(fingerprint)
    return fingerprint


def validate_initial_layers(
    status: Mapping[str, Any],
    *,
    vehicle_id: str,
) -> tuple[bool, str]:
    card = extract_vehicle_status(status, vehicle_id)
    if card is None:
        return False, f"no vehicle status card for {vehicle_id!r} (aggregate or targeted)"
    expected = {
        "simulator_server": "reachable",
        "simulator_frontend": "connected",
        "chase_game": "ready",
        "vehicle": "discoverable",
        "passive_capture": "available",
    }
    missing = []
    for layer, want in expected.items():
        got = _layer_state(card, layer)
        if got != want:
            missing.append(f"{layer}={got!r} (want {want!r})")
    # Baseline requires an explicit stopped worker (missing layer is not proof).
    worker = _layer_state(card, "automation_worker")
    if worker != "stopped":
        missing.append(f"automation_worker={worker!r} (want stopped)")
    if missing:
        return False, "; ".join(missing)
    return True, "initial layers healthy"


def validate_staged_layers(
    status: Mapping[str, Any],
    *,
    vehicle_id: str,
    perception_algorithm: str = "lightweight_observer",
) -> tuple[bool, str]:
    """Post-update staging: worker remains stopped; deployment is staged."""

    card = extract_vehicle_status(status, vehicle_id)
    if card is None:
        return False, f"no vehicle status card for {vehicle_id!r}"
    problems = []
    worker = _layer_state(card, "automation_worker")
    if worker != "stopped":
        problems.append(f"automation_worker={worker!r} (want stopped after staging)")
    deployment = _layer_state(card, "automation_deployment")
    if deployment != "deployed":
        problems.append(f"automation_deployment={deployment!r} (want deployed)")
    # Prefer explicit packaged algorithm when status exposes it.
    layers = card.get("layers") if isinstance(card.get("layers"), dict) else {}
    deployment_entry = layers.get("automation_deployment") if isinstance(layers, dict) else {}
    details = (
        deployment_entry.get("details")
        if isinstance(deployment_entry, dict)
        else {}
    )
    if isinstance(details, dict):
        algorithm = details.get("algorithm") or details.get("perception_algorithm")
        if algorithm is not None and str(algorithm) != perception_algorithm:
            problems.append(
                f"staged algorithm={algorithm!r} (want {perception_algorithm!r})"
            )
    if problems:
        return False, "; ".join(problems)
    return True, "staging left worker stopped with deployed perception"


def validate_running_layers(
    status: Mapping[str, Any],
    *,
    vehicle_id: str,
) -> tuple[bool, str]:
    card = extract_vehicle_status(status, vehicle_id)
    if card is None:
        return False, f"no vehicle status card for {vehicle_id!r}"
    expected = {
        "automation_deployment": "deployed",
        "automation_worker": "running",
        "perception_view": "available",
        "passive_capture": "available",
    }
    missing = []
    for layer, want in expected.items():
        got = _layer_state(card, layer)
        if got != want:
            missing.append(f"{layer}={got!r} (want {want!r})")
    if missing:
        return False, "; ".join(missing)
    return True, "running layers healthy"


def validate_stopped_layers(
    status: Mapping[str, Any],
    *,
    vehicle_id: str,
) -> tuple[bool, str]:
    card = extract_vehicle_status(status, vehicle_id)
    if card is None:
        return False, f"no vehicle status card for {vehicle_id!r}"
    worker = _layer_state(card, "automation_worker")
    view = _layer_state(card, "perception_view")
    deployment = _layer_state(card, "automation_deployment")
    problems = []
    if worker != "stopped":
        problems.append(f"automation_worker={worker!r}")
    if view not in {"stale", "unavailable"}:
        problems.append(f"perception_view={view!r}")
    if deployment != "deployed":
        problems.append(f"automation_deployment={deployment!r} (want deployed)")
    if problems:
        return False, "; ".join(problems)
    return True, "stopped layers healthy"


def validate_authority(
    status: Mapping[str, Any],
    *,
    vehicle_id: str,
) -> tuple[bool, str]:
    card = extract_vehicle_status(status, vehicle_id)
    if card is None:
        return False, f"no vehicle status card for {vehicle_id!r}"
    if card.get("schema") != STATUS_SCHEMA:
        return False, f"unexpected status schema {card.get('schema')!r}"
    authority = _authority(status, vehicle_id)
    if not authority:
        return False, "authority object missing"
    policy = authority.get("action_policy")
    application = authority.get("control_application")
    last_frame = authority.get("last_frame")
    if not isinstance(last_frame, dict):
        return False, "authority.last_frame missing"
    ctrl = last_frame.get("control")
    if not isinstance(ctrl, dict):
        return False, "authority.last_frame.control missing"
    applied = ctrl.get("applied")
    if policy != "observe_only":
        return False, f"action_policy={policy!r}"
    if application != "not_applied":
        return False, f"control_application={application!r}"
    if applied is not False:
        return False, f"last_frame.control.applied={applied!r} (want false)"
    if authority.get("recording") is not False:
        return False, f"recording={authority.get('recording')!r} (want false)"
    return True, "observe_only / not_applied / recording=false"


def _view_correlation_evidence(
    payload: Mapping[str, Any] | None,
    *,
    vehicle_id: str,
    max_frame_lag: int,
) -> dict[str, Any]:
    """Return a fail-closed, reviewable verdict for one captured publication."""

    frame = payload.get("frame") if isinstance(payload, Mapping) else None
    overlay = payload.get("overlay") if isinstance(payload, Mapping) else None
    frame_map = frame if isinstance(frame, Mapping) else {}
    overlay_map = overlay if isinstance(overlay, Mapping) else {}
    current_id = frame_map.get("frame_id")
    source_id = overlay_map.get("source_frame_id")
    current_index = frame_map.get("frame_index")
    source_index = overlay_map.get("source_frame_index")
    claimed_lag = overlay_map.get("frame_lag")
    status = overlay_map.get("status")
    frame_lag_ms = overlay_map.get("frame_lag_ms")
    result_age_ms = overlay_map.get("result_age_ms")

    evidence: dict[str, Any] = {
        "current_frame_id": current_id,
        "source_frame_id": source_id,
        "current_frame_index": current_index,
        "source_frame_index": source_index,
        "claimed_frame_lag": claimed_lag,
        "derived_frame_lag": None,
        "max_frame_lag": max_frame_lag,
        "frame_lag_ms": frame_lag_ms,
        "result_age_ms": result_age_ms,
        "mode": "invalid",
        "verdict": "fail",
        "reason": "not evaluated",
        "diagnostic_findings": [],
    }

    diagnostic_findings: list[str] = []
    for name, value in (
        ("overlay.frame_lag_ms", frame_lag_ms),
        ("overlay.result_age_ms", result_age_ms),
    ):
        if type(value) is int:
            valid_timing = value >= 0
        elif type(value) is float:
            valid_timing = math.isfinite(value) and value >= 0
        else:
            valid_timing = value is None
        if not valid_timing:
            diagnostic_findings.append(
                f"{name}={value!r} is not a finite nonnegative number"
            )
    evidence["diagnostic_findings"] = diagnostic_findings

    def finish(ok: bool, reason: str, *, mode: str | None = None) -> dict[str, Any]:
        if mode is not None:
            evidence["mode"] = mode
        evidence["verdict"] = "pass" if ok else "fail"
        evidence["reason"] = reason
        lag = evidence["derived_frame_lag"]
        lag_text = "unknown" if lag is None else str(lag)
        evidence["summary"] = (
            f"mode={evidence['mode']} derived_lag={lag_text} "
            f"bound={max_frame_lag}: {reason}"
        )
        return evidence

    if type(max_frame_lag) is not int or max_frame_lag < 1:
        return finish(False, f"max_frame_lag={max_frame_lag!r} is not a positive integer")
    if not isinstance(payload, dict):
        return finish(False, "view /api/latest missing or not an object")
    if payload.get("error"):
        return finish(False, f"view fetch error: {payload.get('error')}")
    if payload.get("schema") != PUBLICATION_SCHEMA:
        return finish(
            False,
            f"schema={payload.get('schema')!r} (want {PUBLICATION_SCHEMA!r})",
        )
    if str(payload.get("vehicle_id")) != vehicle_id:
        return finish(
            False,
            f"vehicle_id={payload.get('vehicle_id')!r} (want {vehicle_id!r})",
        )
    if not isinstance(frame, dict):
        return finish(False, "frame object missing")
    if not isinstance(current_id, str) or not current_id.strip():
        return finish(False, f"frame.frame_id={current_id!r} is not a nonempty string")
    if not isinstance(overlay, dict):
        return finish(False, "overlay object missing")
    if not isinstance(source_id, str) or not source_id.strip():
        return finish(
            False,
            f"overlay.source_frame_id={source_id!r} is not a nonempty string",
        )

    if status == "current":
        evidence["derived_frame_lag"] = 0
        if source_id != current_id:
            return finish(
                False,
                f"current ids conflict: source={source_id!r} current={current_id!r}",
                mode="current",
            )
        if "frame_lag" in overlay and (type(claimed_lag) is not int or claimed_lag != 0):
            return finish(
                False,
                f"overlay.frame_lag={claimed_lag!r} must be integer 0 for current",
                mode="current",
            )
        mode = "current"
    elif status == "stale":
        mode = "bounded_stale"
        evidence["mode"] = mode
        for name, value in (
            ("frame.frame_index", current_index),
            ("overlay.source_frame_index", source_index),
            ("overlay.frame_lag", claimed_lag),
        ):
            if type(value) is not int:
                return finish(
                    False,
                    f"{name}={value!r} must be an integer, got {type(value).__name__}",
                    mode=mode,
                )
        derived_lag = current_index - source_index
        evidence["derived_frame_lag"] = derived_lag
        if derived_lag <= 0:
            return finish(
                False,
                f"reverse or zero lineage: current_index={current_index} "
                f"source_index={source_index} derived_lag={derived_lag}",
                mode=mode,
            )
        if claimed_lag != derived_lag:
            return finish(
                False,
                f"claimed_lag={claimed_lag} != derived_lag={derived_lag}",
                mode=mode,
            )
        if derived_lag > max_frame_lag:
            return finish(
                False,
                f"derived_lag={derived_lag} > max_frame_lag={max_frame_lag}",
                mode=mode,
            )
    else:
        return finish(
            False,
            f"overlay.status={status!r} (want current or stale)",
        )

    perception = payload.get("perception")
    cycle = payload.get("cycle")
    control = payload.get("control")
    if not isinstance(perception, dict) or not perception:
        return finish(False, "perception result absent", mode=mode)
    if not isinstance(cycle, dict):
        return finish(False, "cycle object missing", mode=mode)
    if cycle.get("action_policy") != "observe_only":
        return finish(
            False,
            f"cycle.action_policy={cycle.get('action_policy')!r}",
            mode=mode,
        )
    if cycle.get("control_application") != "not_applied":
        return finish(
            False,
            f"cycle.control_application={cycle.get('control_application')!r}",
            mode=mode,
        )
    if not isinstance(control, dict):
        return finish(False, "control object missing", mode=mode)
    if control.get("applied") is not False:
        return finish(
            False,
            f"control.applied={control.get('applied')!r} (want false)",
            mode=mode,
        )
    return finish(True, "correlation proven", mode=mode)


def validate_view_latest(
    payload: Mapping[str, Any] | None,
    *,
    vehicle_id: str,
    max_frame_lag: int,
) -> tuple[bool, str]:
    """Validate one real Automa perception-view /api/latest publication."""

    evidence = _view_correlation_evidence(
        payload,
        vehicle_id=vehicle_id,
        max_frame_lag=max_frame_lag,
    )
    return evidence["verdict"] == "pass", str(evidence["summary"])


def validate_recording_scan(
    before: Sequence[str], after: Sequence[str]
) -> tuple[bool, str]:
    new = sorted(set(after) - set(before))
    if new:
        return False, f"new automation run directories: {new}"
    return True, "no new automation run directories"


def validate_preservation(
    baseline: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """Cross-command stability via stable projection (not full playback equality).

    Each argument must already be a successful within-receipt extraction
    (exact before/after for all six fields). Comparing a missing/current-None
    extraction to a prior fingerprint must fail closed.
    """

    if not isinstance(baseline, dict) or not baseline:
        return False, "baseline fingerprint missing"
    if not isinstance(current, dict) or not current:
        return False, "current fingerprint missing"
    if baseline.get("preserved") is not True or current.get("preserved") is not True:
        return False, "preserved is not True on baseline/current fingerprints"
    if baseline.get("changed_fields") != [] or current.get("changed_fields") != []:
        return False, "changed_fields must be exactly [] on both fingerprints"
    if baseline.get("unknown_fields") != [] or current.get("unknown_fields") != []:
        return False, "unknown_fields must be exactly [] on both fingerprints"
    base_proj = baseline.get("stable_projection") or stable_session_projection(baseline)
    cur_proj = current.get("stable_projection") or stable_session_projection(current)
    if not isinstance(base_proj, dict) or not isinstance(cur_proj, dict):
        return False, "stable projection missing"
    for key in list(_CROSS_COMMAND_STABLE_SCALAR_FIELDS) + ["playback_mode"]:
        if key not in base_proj or key not in cur_proj:
            return False, f"stable projection missing {key!r}"
        if base_proj.get(key) != cur_proj.get(key):
            return False, f"{key} changed: {base_proj.get(key)!r} -> {cur_proj.get(key)!r}"
    for key in ("game_id", "scenario_id", "simulation_epoch", "control_source"):
        if baseline.get(key) in (None, ""):
            return False, f"baseline {key} is empty"
    return True, "protected session fields preserved (stable projection)"


def validate_browser_view_image(
    path: Path,
    *,
    not_before_unix: float | None = None,
    require_not_before: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    """Validate a browser screenshot file.

    ``not_before_unix`` is the earliest acceptable source mtime (typically when
    the view became machine-healthy). When ``require_not_before`` is true, a
    missing floor fails closed so pre-session or unbound images cannot pass.
    """

    meta: dict[str, Any] = {}
    if not path.is_file():
        return False, f"browser-view.png missing at {path}", meta
    size = path.stat().st_size
    mtime = path.stat().st_mtime
    meta["source_size_bytes"] = size
    meta["source_mtime_unix"] = mtime
    meta["source_sha256"] = _sha256_file(path)
    if size <= 0:
        return False, "browser-view.png is empty", meta
    if require_not_before and not_before_unix is None:
        return False, "browser-view.png rejected: view-health floor not established", meta
    if not_before_unix is not None and mtime + 1.0 < not_before_unix:
        return (
            False,
            f"browser-view.png mtime {mtime} predates view-health time {not_before_unix}",
            meta,
        )
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.load()
            width, height = image.size
    except Exception as exc:  # noqa: BLE001
        return False, f"browser-view.png is not a decodable image: {exc}", meta
    if width < 1 or height < 1:
        return False, "browser-view.png has invalid dimensions", meta
    meta["width_px"] = width
    meta["height_px"] = height
    return True, f"browser-view.png ok ({width}x{height}, {size} bytes)", meta


def _bind_browser_view_image(
    import_path: Path,
    target: Path,
    *,
    not_before_unix: float | None,
) -> tuple[bool, str, dict[str, Any]]:
    """Validate source image against the health floor and copy preserving mtime.

    The copy keeps the source modification time so later acceptance checks cannot
    treat a replayed pre-session PNG as freshly captured after ``write_bytes``.
    """

    ok, summary, meta = validate_browser_view_image(
        import_path,
        not_before_unix=not_before_unix,
        require_not_before=True,
    )
    if not ok:
        if target.is_file():
            try:
                target.unlink()
            except OSError:
                pass
        return ok, summary, meta
    target.write_bytes(import_path.read_bytes())
    source_mtime = float(meta["source_mtime_unix"])
    os.utime(target, (source_mtime, source_mtime))
    meta["bound_path"] = target.name
    meta["bound_mtime_unix"] = target.stat().st_mtime
    return True, summary, meta


# ---------------------------------------------------------------------------
# Prompting / commands
# ---------------------------------------------------------------------------


@dataclass
class HumanJudgment:
    visual: str
    notes: str
    finding: bool
    finding_severity: str | None = None
    finding_summary: str | None = None
    interactive: bool = True


def _finalize_step_status(
    step_status: str,
    *,
    machine_ok: bool,
    visual: str,
    required_for_verdict: bool,
) -> str:
    """Combine evidence without allowing a visual skip to hide machine failure."""

    if visual == "fail" or not machine_ok:
        return "fail"
    if visual == "skip" and required_for_verdict:
        return "skip"
    if step_status == "ok" and visual in {"pass", "n/a"}:
        return "pass"
    return step_status


def _prompt_judgment(
    *,
    step: Mapping[str, Any],
    prompt: PromptFn,
    non_interactive: bool,
    auto_visual: str | None,
) -> HumanJudgment:
    visual_required = bool(step.get("visual_required"))
    if non_interactive:
        if visual_required:
            visual = auto_visual or "skip"
        else:
            visual = "pass"
        return HumanJudgment(
            visual=visual,
            notes="non-interactive session",
            finding=False,
            interactive=False,
        )

    print()
    print(f"Primary cue: {step.get('primary_cue') or '(none stated)'}")
    if step.get("visual_prompt"):
        print(f"Visual check: {step['visual_prompt']}")
    if visual_required:
        raw = prompt("Visual result [p]ass / [f]ail / [s]kip: ").strip().lower()
        visual = {
            "p": "pass",
            "pass": "pass",
            "f": "fail",
            "fail": "fail",
            "s": "skip",
            "skip": "skip",
        }.get(raw, "skip")
    else:
        raw = prompt("Step result [p]ass / [f]ail / [s]kip / [Enter]=pass: ").strip().lower()
        visual = {
            "": "pass",
            "p": "pass",
            "pass": "pass",
            "f": "fail",
            "fail": "fail",
            "s": "skip",
            "skip": "skip",
            "n": "n/a",
            "n/a": "n/a",
        }.get(raw, "pass")

    notes = prompt("Notes (optional): ").strip()
    finding_raw = prompt("Record a finding? [y/N]: ").strip().lower()
    finding = finding_raw in {"y", "yes"}
    severity = None
    summary = None
    if finding:
        severity = prompt("Finding severity [P1/P2/P3] (default P3): ").strip().upper() or "P3"
        summary = prompt("Finding summary: ").strip() or notes or "operator-reported finding"
    return HumanJudgment(
        visual=visual,
        notes=notes,
        finding=finding,
        finding_severity=severity,
        finding_summary=summary,
        interactive=True,
    )


@dataclass
class CommandOutcome:
    argv: list[str]
    command: str
    exit_code: int
    elapsed_ms: int
    stdout_path: str
    stderr_path: str
    started_at_utc: str
    ended_at_utc: str


def _run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    session_dir: Path,
    step_dir: Path,
    index: int,
    timeout_s: float | None,
    transcript_path: Path,
) -> CommandOutcome:
    step_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = step_dir / f"cmd-{index:02d}.stdout.txt"
    stderr_path = step_dir / f"cmd-{index:02d}.stderr.txt"
    wall_started = _utc_now()
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr if isinstance(exc.stderr, str) else "") + f"\nTIMEOUT after {timeout_s}s\n"
    except OSError as exc:
        exit_code = 127
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}\n"
    wall_ended = _utc_now()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    red_out = _redact(stdout, cwd)
    red_err = _redact(stderr, cwd)
    _write_text(stdout_path, red_out)
    _write_text(stderr_path, red_err)
    _append_text(
        transcript_path,
        f"$ {_format_command(argv)}\n{red_out}{red_err}"
        f"exit={exit_code} elapsed_ms={elapsed_ms} "
        f"started={_iso(wall_started)} ended={_iso(wall_ended)}\n",
    )
    return CommandOutcome(
        argv=list(argv),
        command=_format_command(argv),
        exit_code=exit_code,
        elapsed_ms=elapsed_ms,
        stdout_path=str(stdout_path.relative_to(session_dir)),
        stderr_path=str(stderr_path.relative_to(session_dir)),
        started_at_utc=_iso(wall_started),
        ended_at_utc=_iso(wall_ended),
    )


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    catalog: dict[str, Any]
    session_dir: Path
    repo_root: Path
    variables: dict[str, str]
    execution_mode: str
    session_id: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    gate_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    finding_counter: int = 0
    worker_may_exist: bool = False
    last_worker_pid: int | None = None
    observed_worker_pids: set[int] = field(default_factory=set)
    pre_existing_worker_pids: set[int] = field(default_factory=set)
    session_owned_worker_pids: set[int] = field(default_factory=set)
    automation_run_seen: bool = False
    recording_before: list[str] = field(default_factory=list)
    recording_after: list[str] = field(default_factory=list)
    baseline_fingerprint: dict[str, Any] | None = None
    latest_fingerprint: dict[str, Any] | None = None
    capture_fingerprints: dict[str, Any] = field(default_factory=dict)
    browser_view_meta: dict[str, Any] | None = None
    view_correlation: dict[str, Any] | None = None
    view_healthy_at_unix: float | None = None
    interactive_human_confirmation: bool = False
    dry_run: bool = False
    non_interactive: bool = False
    operator: str | None = None
    precondition_cleanup: dict[str, Any] | None = None
    canonical_acceptance: bool = False
    auto_driving_linked_pr: str | None = None
    metrics_ui_linked_pr: str | None = None
    catalog_source_path: str | None = None
    safety_blocked: bool = False
    safety_block_reason: str | None = None
    executed_commands: list[list[str]] = field(default_factory=list)
    metrics_ui_repo_path: Path | None = None


def _note_worker_pid(state: SessionState, pid: Any, *, source: str) -> None:
    if not isinstance(pid, int) or pid <= 0:
        return
    state.observed_worker_pids.add(pid)
    state.last_worker_pid = pid
    if state.automation_run_seen:
        state.session_owned_worker_pids.add(pid)
    else:
        state.pre_existing_worker_pids.add(pid)


def _next_finding_id(state: SessionState) -> str:
    state.finding_counter += 1
    return f"M007-LIVE-{state.session_id}-{state.finding_counter:03d}"


def _record_finding(
    state: SessionState,
    *,
    step_id: str,
    classification: str,
    severity: str,
    summary: str,
    human_notes: str,
    evidence: Sequence[str],
    repro: Sequence[str] | None = None,
) -> dict[str, Any]:
    finding = {
        "schema": FINDING_SCHEMA,
        "id": _next_finding_id(state),
        "track": state.catalog.get("track"),
        "step_id": step_id,
        "classification": classification,
        "severity": severity,
        "summary": summary,
        "human_notes": human_notes,
        "evidence": list(evidence),
        "repro": list(repro or []),
    }
    state.findings.append(finding)
    return finding


def _apply_gate(
    state: SessionState,
    gate_ids: Sequence[str],
    *,
    status: str,
    summary: str,
    evidence: Sequence[str],
) -> None:
    for gate_id in gate_ids:
        previous = state.gate_results.get(gate_id)
        if previous and previous.get("status") == "fail" and status != "fail":
            continue
        if previous and previous.get("status") == "pass" and status not in {"fail"}:
            # keep first pass unless failing later
            if status == "pass":
                continue
        state.gate_results[gate_id] = {
            "id": gate_id,
            "status": status,
            "summary": summary,
            "evidence": list(evidence),
        }


def _run_machine_validator(
    name: str,
    *,
    vehicle_id: str,
    status_path: Path | None = None,
    view_path: Path | None = None,
    before_runs: Sequence[str] | None = None,
    after_runs: Sequence[str] | None = None,
    baseline_fingerprint: Mapping[str, Any] | None = None,
    current_fingerprint: Mapping[str, Any] | None = None,
    perception_algorithm: str = "lightweight_observer",
    max_frame_lag: int | None = None,
) -> tuple[bool, str, dict[str, Any] | None]:
    def plain(result: tuple[bool, str]) -> tuple[bool, str, None]:
        return result[0], result[1], None

    if name == "initial_layers":
        if status_path is None or not status_path.is_file():
            return False, "initial-status.json missing", None
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return plain(validate_initial_layers(status, vehicle_id=vehicle_id))
    if name == "staged_layers":
        if status_path is None or not status_path.is_file():
            return False, "staged-status.json missing", None
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return plain(
            validate_staged_layers(
                status,
                vehicle_id=vehicle_id,
                perception_algorithm=perception_algorithm,
            )
        )
    if name == "running_layers":
        if status_path is None or not status_path.is_file():
            return False, "running-status.json missing", None
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return plain(validate_running_layers(status, vehicle_id=vehicle_id))
    if name == "stopped_layers":
        if status_path is None or not status_path.is_file():
            return False, "stopped-status.json missing", None
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return plain(validate_stopped_layers(status, vehicle_id=vehicle_id))
    if name == "authority":
        if status_path is None or not status_path.is_file():
            return False, "status json missing for authority", None
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return plain(validate_authority(status, vehicle_id=vehicle_id))
    if name == "view_correlation":
        if view_path is None or not view_path.is_file():
            return False, "view-publication.json missing", None
        try:
            payload = json.loads(view_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"view-publication.json is not JSON: {exc}", None
        evidence = _view_correlation_evidence(
            payload,
            vehicle_id=vehicle_id,
            max_frame_lag=max_frame_lag if max_frame_lag is not None else 0,
        )
        return evidence["verdict"] == "pass", str(evidence["summary"]), evidence
    if name == "default_recording":
        return plain(validate_recording_scan(before_runs or [], after_runs or []))
    if name == "preservation":
        return plain(validate_preservation(baseline_fingerprint, current_fingerprint))
    return False, f"unknown validator {name!r}", None


def _repo_reviewable(
    identity: Mapping[str, Any] | None,
    *,
    session_dir: Path,
    label: str,
    linked_pr: str | None,
    repo: Path | None = None,
) -> tuple[bool, str]:
    """Dirty checkouts need a reviewable tracked patch and/or a bound linked PR.

    Untracked content is never auto-copied and never accepted via linked PR alone:
    formal acceptance requires a clean tree or an exact tracked-diff artifact for
    the dirty bytes. A linked PR must name the same GitHub owner/repo as the
    checkout origin; free-text or unrelated-repo URLs fail closed.
    """

    if not isinstance(identity, Mapping):
        return False, f"{label} identity missing"
    if identity.get("worktree_state") != "dirty":
        return True, f"{label} clean"

    untracked = list(identity.get("untracked_files") or [])
    if untracked:
        return (
            False,
            f"Dirty {label} has untracked files; formal acceptance requires a "
            f"clean checkout (untracked content is not auto-copied and cannot "
            f"be blessed by a linked PR alone): {untracked[:5]}",
        )

    pr_value = linked_pr or identity.get("linked_pr")
    if pr_value is not None:
        pr_text = str(pr_value)
        ok_bound, bound_msg, bound = _linked_pr_bound_to_checkout(
            pr_text, repo=repo, identity=identity
        )
        if not ok_bound:
            return False, f"Dirty {label}: {bound_msg}"
        # Bound PR may accompany tracked dirty trees only when a reviewable
        # tracked patch is also present (exact local dirty bytes).
        diff_path = session_dir / f"{label}-worktree.diff"
        if not diff_path.is_file():
            return (
                False,
                f"Dirty {label} has a bound linked PR but no captured tracked "
                "diff artifact for the local dirty bytes",
            )
        patch = diff_path.read_text(encoding="utf-8", errors="replace")
        if not patch.strip():
            return (
                False,
                f"Dirty {label} has a bound linked PR but empty tracked diff; "
                "use a clean checkout on the PR head instead",
            )
        return (
            True,
            f"{label} dirty with bound linked PR and reviewable tracked patch "
            f"({bound.get('url') if bound else pr_text})",
        )

    if not identity.get("diff_identity"):
        return False, f"Dirty {label} worktree lacks diff_identity"
    diff_path = session_dir / f"{label}-worktree.diff"
    if not diff_path.is_file():
        return False, f"Dirty {label} worktree lacks captured diff artifact"
    patch = diff_path.read_text(encoding="utf-8", errors="replace")
    if not patch.strip():
        return False, f"Dirty {label} has empty reviewable tracked diff"
    return True, f"{label} dirty with reviewable tracked patch"


def _derive_machine_preflight(
    state: SessionState,
    cleanup: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarize deterministic sequence health independently of human judgment."""

    if state.dry_run:
        return {
            "verdict": "not_run",
            "summary": "dry-run did not execute the machine sequence",
            "failures": [],
            "evaluated_steps": [],
        }

    evaluated_steps: list[str] = []
    failures: list[dict[str, str]] = []
    for step in state.steps:
        if not step.get("required_for_verdict"):
            continue
        step_id = str(step.get("id") or "unknown")
        evaluated_steps.append(step_id)
        if step.get("machine_ok") is not True:
            reason = str(step.get("machine_summary") or "")
            if not reason:
                failed_commands = [
                    command
                    for command in (step.get("commands") or [])
                    if command.get("exit_code") != 0
                ]
                reason = "; ".join(
                    f"exit={command.get('exit_code')} {command.get('command') or ''}".strip()
                    for command in failed_commands
                )
            failures.append(
                {
                    "step_id": step_id,
                    "reason": reason or f"machine status={step.get('status')!r}",
                }
            )

    if cleanup.get("worker_stopped") is not True:
        failures.append(
            {
                "step_id": "_cleanup",
                "reason": str(cleanup.get("error") or "cleanup not proven"),
            }
        )
    if state.safety_blocked and not failures:
        failures.append(
            {
                "step_id": "_catalog_bind" if not state.steps else "_safety",
                "reason": str(state.safety_block_reason or "safety prerequisite blocked"),
            }
        )

    if failures:
        return {
            "verdict": "fail",
            "summary": f"{len(failures)} machine failure(s)",
            "failures": failures,
            "evaluated_steps": evaluated_steps,
        }
    if not evaluated_steps:
        return {
            "verdict": "not_run",
            "summary": "no required machine steps were evaluated",
            "failures": [],
            "evaluated_steps": [],
        }
    return {
        "verdict": "pass",
        "summary": f"{len(evaluated_steps)} required step(s) machine-green",
        "failures": [],
        "evaluated_steps": evaluated_steps,
    }


def _derive_verdict(state: SessionState) -> tuple[str, str | None]:
    track = state.catalog.get("track")
    if track == "acceptance":
        if not state.canonical_acceptance:
            return (
                "incomplete",
                "Formal acceptance pass requires the byte-identical bundled "
                "m007-acceptance catalog with the frozen gate/validator matrix.",
            )
        if state.dry_run:
            return "incomplete", "Dry-run cannot produce an acceptance pass."
        if state.non_interactive:
            return "incomplete", "Non-interactive mode cannot produce an acceptance pass."
        if not state.interactive_human_confirmation:
            return "incomplete", "Acceptance pass requires interactive human confirmation."
        # Environment identity
        baseline_path = state.session_dir / "baseline.json"
        if baseline_path.is_file():
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            if not baseline.get("operator"):
                return "incomplete", "Acceptance pass requires --operator identity."
            browser = baseline.get("browser") or {}
            if not browser.get("name") or not browser.get("version"):
                return "incomplete", "Acceptance pass requires browser name and version."
            repos = baseline.get("repositories") or {}
            metrics = repos.get("metrics_ui")
            if not isinstance(metrics, dict) or not metrics.get("commit"):
                return "incomplete", "Acceptance pass requires --metrics-ui-repo identity."
            auto = repos.get("auto_driving") or {}
            ok_auto, auto_msg = _repo_reviewable(
                auto,
                session_dir=state.session_dir,
                label="auto-driving",
                linked_pr=state.auto_driving_linked_pr,
                repo=state.repo_root,
            )
            if not ok_auto:
                return "incomplete", auto_msg
            metrics_repo_path = None
            if isinstance(metrics, dict) and metrics.get("path"):
                # Path is basename only in identity; use optional absolute from state.
                metrics_repo_path = getattr(state, "metrics_ui_repo_path", None)
            ok_metrics, metrics_msg = _repo_reviewable(
                metrics,
                session_dir=state.session_dir,
                label="metrics-ui",
                linked_pr=state.metrics_ui_linked_pr,
                repo=metrics_repo_path,
            )
            if not ok_metrics:
                return "incomplete", metrics_msg
            if not baseline.get("session_visible"):
                return (
                    "incomplete",
                    "Acceptance pass requires recorded session-visible fingerprint values.",
                )
            pre = baseline.get("precondition_cleanup") or {}
            if pre.get("error") or pre.get("ok") is not True:
                return (
                    "findings" if pre.get("error") else "incomplete",
                    "Precondition cleanup did not prove a stopped baseline worker.",
                )
        view_path = state.session_dir / "browser-view.png"
        # Prefer provenance recorded at the human-view gate; fall back to file
        # mtime only when meta is absent (still require a view-health floor).
        meta_path = state.session_dir / "browser-view-meta.json"
        floor = state.view_healthy_at_unix
        if meta_path.is_file():
            try:
                recorded = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                recorded = {}
            if recorded.get("ok") is not True:
                return (
                    "incomplete",
                    "Acceptance pass requires browser-view.png bound after view health.",
                )
            source_mtime = recorded.get("source_mtime_unix")
            if floor is None:
                return (
                    "incomplete",
                    "Acceptance pass requires view-health floor before screenshot binding.",
                )
            if not isinstance(source_mtime, (int, float)) or source_mtime + 1.0 < floor:
                return (
                    "incomplete",
                    "Acceptance pass requires browser-view source mtime after view health.",
                )
        ok_img, img_summary, _meta = validate_browser_view_image(
            view_path,
            not_before_unix=floor,
            require_not_before=True,
        )
        if not ok_img:
            return "incomplete", f"Acceptance pass requires valid browser-view.png ({img_summary})."

        required_gates = {
            str(gate["id"]): gate
            for gate in state.catalog.get("gates") or []
            if isinstance(gate, dict) and gate.get("required")
        }
        for gate_id in required_gates:
            result = state.gate_results.get(gate_id)
            if result is None:
                return "incomplete", f"Required gate {gate_id!r} was not evaluated."
            if result.get("status") == "skip":
                return "incomplete", f"Required gate {gate_id!r} was skipped."
            if result.get("status") != "pass":
                return "findings", f"Required gate {gate_id!r} did not pass."

        if any(f.get("classification") == "acceptance_blocker" for f in state.findings):
            return "findings", "Acceptance blocker recorded."
        return "pass", None

    # exploratory
    if any(f.get("classification") in {"acceptance_blocker", "usability_defect"} for f in state.findings):
        return "findings", "Exploratory session recorded findings."
    if any(step.get("status") == "fail" for step in state.steps):
        return "findings", "Exploratory session recorded failed steps."
    if any(step.get("status") in {"blocked", "skip"} for step in state.steps):
        return "incomplete", "Exploratory session had blocked or skipped steps."
    return "complete", None


def _write_digests(session_dir: Path) -> list[dict[str, str]]:
    """Write digests.json for all files except digests.json itself.

    Excludes the US-04 staged cache tree (local restorable bytes, not review evidence).
    """
    artifacts: list[dict[str, str]] = []
    for path in sorted(session_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "digests.json":
            continue
        rel = path.relative_to(session_dir).as_posix()
        if rel == "us04-staged-cache" or rel.startswith("us04-staged-cache/"):
            continue
        artifacts.append(
            {"path": str(path.relative_to(session_dir)), "sha256": _sha256_file(path)}
        )
    _write_json(session_dir / "digests.json", {"schema": "live_cli_session_digests_v0", "artifacts": artifacts})
    return artifacts


def _enforce_cleanup(
    state: SessionState,
    *,
    command_timeout_s: float | None,
    transcript_path: Path,
) -> dict[str, Any]:
    cleanup: dict[str, Any] = {
        "attempted": False,
        "needed": state.worker_may_exist,
        "stop_exit_code": None,
        "final_status_exit_code": None,
        "worker_stopped": None,
        "pid_alive": None,
        "pids": [],
        "pid_liveness": {},
        "error": None,
        "preservation": None,
    }
    if not state.worker_may_exist or state.dry_run:
        cleanup["worker_stopped"] = True
        return cleanup

    cleanup["attempted"] = True
    vehicle = state.variables["vehicle_id"]
    step_dir = state.session_dir / "steps" / "_cleanup"
    try:
        stop = _run_command(
            ["./cli/automa", "vehicles", "automation", "stop", "--id", vehicle],
            cwd=state.repo_root,
            session_dir=state.session_dir,
            step_dir=step_dir,
            index=0,
            timeout_s=command_timeout_s,
            transcript_path=transcript_path,
        )
        cleanup["stop_exit_code"] = stop.exit_code
        status_cmd = [
            "./cli/automa",
            "vehicles",
            "status",
            "--id",
            vehicle,
            "--json",
        ]
        status_out = _run_command(
            status_cmd,
            cwd=state.repo_root,
            session_dir=state.session_dir,
            step_dir=step_dir,
            index=1,
            timeout_s=command_timeout_s,
            transcript_path=transcript_path,
        )
        cleanup["final_status_exit_code"] = status_out.exit_code
        if stop.exit_code != 0 or status_out.exit_code != 0:
            cleanup["error"] = (
                f"nonzero cleanup exits stop={stop.exit_code} status={status_out.exit_code}"
            )
            cleanup["worker_stopped"] = False
        status_path = state.session_dir / "cleanup-status.json"
        raw = (step_dir / "cmd-01.stdout.txt").read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
            _write_json(status_path, payload)
            ok, summary = validate_stopped_layers(payload, vehicle_id=vehicle)
            cleanup["stopped_layers_summary"] = summary
            if cleanup.get("worker_stopped") is not False:
                cleanup["worker_stopped"] = ok
            details = _worker_details(payload, vehicle)
            final_pid = details.get("pid")
            _note_worker_pid(state, final_pid, source="cleanup-status")
            # Bind fingerprint for this exact capture (including None).
            fingerprint = extract_session_fingerprint(payload, vehicle)
            state.latest_fingerprint = fingerprint
            state.capture_fingerprints["cleanup-status"] = fingerprint
            if fingerprint is not None:
                _write_json(
                    state.session_dir / "session-fingerprint-cleanup.json",
                    fingerprint,
                )
            else:
                _write_json(
                    state.session_dir / "session-fingerprint-cleanup.json",
                    {"error": "extraction_failed", "capture": "cleanup-status"},
                )
            if state.baseline_fingerprint is None and state.catalog.get("track") == CONTINUITY_TRACK:
                # Continuity catalogs may not capture an initial status fingerprint.
                cleanup["preservation"] = {
                    "ok": True,
                    "summary": "continuity track: baseline fingerprint not required",
                }
            else:
                ok_pres, pres_summary = validate_preservation(
                    state.baseline_fingerprint, fingerprint
                )
                cleanup["preservation"] = {"ok": ok_pres, "summary": pres_summary}
                if not ok_pres:
                    cleanup["error"] = cleanup.get("error") or f"cleanup preservation: {pres_summary}"
                    # Do not claim full cleanup success when terminal receipt drifts.
                    cleanup["worker_stopped"] = False
        except json.JSONDecodeError as exc:
            cleanup["error"] = f"cleanup status not JSON: {exc}"
            cleanup["worker_stopped"] = False
    except Exception as exc:  # noqa: BLE001
        cleanup["error"] = f"{type(exc).__name__}: {exc}"
        cleanup["worker_stopped"] = False

    # Every distinct PID observed in the session must be proven dead.
    if state.worker_may_exist and not state.dry_run:
        if cleanup.get("stop_exit_code") != 0 or cleanup.get("final_status_exit_code") != 0:
            cleanup["worker_stopped"] = False
        pids = sorted(state.observed_worker_pids)
        cleanup["pids"] = pids
        liveness = {str(pid): _pid_alive(pid) for pid in pids}
        cleanup["pid_liveness"] = liveness
        cleanup["pid"] = state.last_worker_pid
        if not pids:
            cleanup["worker_stopped"] = False
            cleanup["pid_alive"] = None
            cleanup["error"] = (
                cleanup.get("error")
                or "cleanup cannot prove process liveness without a known PID"
            )
        else:
            # True or unknown liveness fails; only explicit False passes.
            alive_flags = list(liveness.values())
            cleanup["pid_alive"] = any(flag is True for flag in alive_flags) or (
                None if any(flag is None for flag in alive_flags) else False
            )
            if any(flag is not False for flag in alive_flags):
                cleanup["worker_stopped"] = False
                cleanup["error"] = (
                    cleanup.get("error")
                    or f"one or more observed worker PIDs still live/unknown: {liveness}"
                )

    if cleanup.get("worker_stopped") is not True:
        _record_finding(
            state,
            step_id="_cleanup",
            classification="acceptance_blocker",
            severity="P1",
            summary="Session cleanup failed to prove worker stopped",
            human_notes=str(cleanup.get("error") or cleanup.get("stopped_layers_summary") or ""),
            evidence=["steps/_cleanup/"],
            repro=[f"./cli/automa vehicles automation stop --id {vehicle}"],
        )
        _apply_gate(
            state,
            ["cleanup"],
            status="fail",
            summary="cleanup failed",
            evidence=["steps/_cleanup/"],
        )
    return cleanup


def _pids_all_dead(pids: Sequence[int]) -> tuple[bool, dict[str, bool | None]]:
    liveness = {str(pid): _pid_alive(pid) for pid in pids}
    ok = bool(pids) and all(flag is False for flag in liveness.values())
    if not pids:
        # No known PID is fine when worker is stopped and never observed.
        return True, {}
    return ok, liveness


def _run_precondition_cleanup(
    state: SessionState,
    *,
    command_timeout_s: float | None,
    transcript_path: Path,
    metrics_ui_origin: str,
) -> dict[str, Any]:
    """Stop any pre-existing worker before the acceptance baseline.

    Does not count as the acceptance stop. Records receipts under
    steps/_precondition_cleanup/ and pre-baseline-status.json.

    Requires zero exit, exact targeted status identity, and explicit
    ``automation_worker=stopped`` before the acceptance sequence proceeds.
    """

    vehicle = state.variables["vehicle_id"]
    step_dir = state.session_dir / "steps" / "_precondition_cleanup"
    record: dict[str, Any] = {
        "attempted": False,
        "needed": False,
        "ok": False,
        "worker_state_before": None,
        "stop_exit_code": None,
        "status_exit_code": None,
        "worker_state_after": None,
        "pids_before": [],
        "pid_liveness": {},
        "error": None,
    }
    if state.dry_run:
        record["skipped"] = "dry_run"
        record["ok"] = True
        return record

    def _fail(message: str) -> dict[str, Any]:
        record["error"] = message
        record["ok"] = False
        return record

    try:
        status_out = _run_command(
            [
                "./cli/automa",
                "vehicles",
                "status",
                "--id",
                vehicle,
                "--chase-url",
                metrics_ui_origin,
                "--json",
            ],
            cwd=state.repo_root,
            session_dir=state.session_dir,
            step_dir=step_dir,
            index=0,
            timeout_s=command_timeout_s,
            transcript_path=transcript_path,
        )
        record["status_exit_code"] = status_out.exit_code
        if status_out.exit_code != 0:
            return _fail(f"precondition status exit={status_out.exit_code}")
        raw = (step_dir / "cmd-00.stdout.txt").read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return _fail(f"precondition status not JSON: {exc}")
        _write_json(state.session_dir / "pre-baseline-status.json", payload)
        card = extract_vehicle_status(payload, vehicle)
        if card is None:
            return _fail(
                "precondition status missing exact automa_vehicle_status_v1 card "
                f"for {vehicle!r}"
            )
        worker_state = _layer_state(card, "automation_worker")
        record["worker_state_before"] = worker_state
        details = _worker_details(payload, vehicle)
        pre_pid = details.get("pid")
        _note_worker_pid(state, pre_pid, source="pre-baseline")
        pids_before = sorted(state.pre_existing_worker_pids)
        record["pids_before"] = pids_before
        deployment = _layer_state(card, "automation_deployment")
        view = _layer_state(card, "perception_view")
        record["deployment_before"] = deployment
        record["view_before"] = view

        if worker_state == "stopped":
            record["needed"] = False
            record["worker_state_after"] = "stopped"
            # Pre-existing PID from a prior run must be dead if known.
            if pids_before:
                ok_dead, liveness = _pids_all_dead(pids_before)
                record["pid_liveness"] = liveness
                if not ok_dead:
                    return _fail(
                        f"pre-existing worker PID still live/unknown before baseline: {liveness}"
                    )
            record["ok"] = True
            return record

        if worker_state != "running":
            return _fail(
                f"precondition automation_worker={worker_state!r} "
                "(want explicit stopped or running)"
            )

        record["needed"] = True
        record["attempted"] = True
        state.worker_may_exist = True
        stop = _run_command(
            ["./cli/automa", "vehicles", "automation", "stop", "--id", vehicle],
            cwd=state.repo_root,
            session_dir=state.session_dir,
            step_dir=step_dir,
            index=1,
            timeout_s=command_timeout_s,
            transcript_path=transcript_path,
        )
        record["stop_exit_code"] = stop.exit_code
        if stop.exit_code != 0:
            return _fail(f"precondition stop exit={stop.exit_code}")
        after_out = _run_command(
            [
                "./cli/automa",
                "vehicles",
                "status",
                "--id",
                vehicle,
                "--chase-url",
                metrics_ui_origin,
                "--json",
            ],
            cwd=state.repo_root,
            session_dir=state.session_dir,
            step_dir=step_dir,
            index=2,
            timeout_s=command_timeout_s,
            transcript_path=transcript_path,
        )
        record["status_after_exit_code"] = after_out.exit_code
        if after_out.exit_code != 0:
            return _fail(f"precondition post-stop status exit={after_out.exit_code}")
        after_raw = (step_dir / "cmd-02.stdout.txt").read_text(encoding="utf-8")
        try:
            after_payload = json.loads(after_raw)
        except json.JSONDecodeError as exc:
            return _fail(f"precondition post-stop status not JSON: {exc}")
        _write_json(state.session_dir / "pre-baseline-status-after-stop.json", after_payload)
        after_card = extract_vehicle_status(after_payload, vehicle)
        if after_card is None:
            return _fail("precondition post-stop status missing exact vehicle card")
        after_state = _layer_state(after_card, "automation_worker")
        record["worker_state_after"] = after_state
        _note_worker_pid(
            state,
            _worker_details(after_payload, vehicle).get("pid"),
            source="pre-baseline-after-stop",
        )
        if after_state != "stopped":
            return _fail(
                f"precondition stop left automation_worker={after_state!r} (want stopped)"
            )
        all_pids = sorted(state.pre_existing_worker_pids | state.observed_worker_pids)
        if all_pids:
            ok_dead, liveness = _pids_all_dead(all_pids)
            record["pid_liveness"] = liveness
            if not ok_dead:
                return _fail(
                    f"precondition PIDs still live/unknown after stop: {liveness}"
                )
        record["ok"] = True
        return record
    except Exception as exc:  # noqa: BLE001
        return _fail(f"{type(exc).__name__}: {exc}")


def _capture_worktree_reviewables(
    repo: Path,
    session_dir: Path,
    *,
    label: str,
) -> dict[str, Any]:
    """Write reviewable *tracked* dirty-tree patch for a repository checkout.

    Untracked files are listed by name only and never copied into the session.
    """

    info: dict[str, Any] = {
        "label": label,
        "diff_path": None,
        "untracked_listed": [],
        "untracked_copied": False,
        "policy": "tracked_diff_only_no_untracked_copy",
    }
    try:
        patch = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        cached = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        combined = ""
        if patch:
            combined += patch
        if cached:
            combined += ("\n" if combined else "") + "# staged\n" + cached
        diff_name = f"{label}-worktree.diff"
        _write_text(session_dir / diff_name, combined)
        info["diff_path"] = diff_name
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        ).stdout
        info["untracked_listed"] = [line for line in untracked.splitlines() if line]
    except OSError as exc:
        info["error"] = str(exc)
    _write_json(session_dir / f"{label}-worktree-review.json", info)
    return info


def _live_mutation_prerequisites_met(state: SessionState) -> tuple[bool, str]:
    """Live mutations require proven precondition; acceptance also needs staging gates."""

    track = state.catalog.get("track")
    if state.dry_run:
        return True, "dry-run"
    if track == CONTINUITY_TRACK:
        pre = state.precondition_cleanup
        if not isinstance(pre, dict) or pre.get("ok") is not True:
            return False, "continuity precondition_cleanup not proven ok"
        if state.safety_blocked:
            return False, state.safety_block_reason or "continuity safety blocked"
        return True, "continuity precondition ok"
    if track != "acceptance":
        return True, "non-acceptance track"
    if not state.canonical_acceptance:
        return False, "noncanonical acceptance catalog cannot execute live mutation"
    pre = state.precondition_cleanup
    if not isinstance(pre, dict) or pre.get("ok") is not True:
        return False, "precondition_cleanup not proven ok"
    initial = state.gate_results.get("initial_layers") or {}
    if initial.get("status") != "pass":
        return False, "initial_layers gate has not passed"
    staging = state.gate_results.get("staging") or {}
    if staging.get("status") != "pass":
        return False, "staging gate has not passed"
    if state.safety_blocked:
        return False, state.safety_block_reason or "safety blocked"
    return True, "prerequisites met"


def run_session(
    *,
    catalog: dict[str, Any],
    session_dir: Path,
    repo_root: Path,
    metrics_ui_origin: str | None,
    metrics_ui_repo: Path | None,
    browser_name: str | None,
    browser_version: str | None,
    prompt: PromptFn,
    non_interactive: bool,
    auto_visual: str | None,
    command_timeout_s: float | None,
    dry_run: bool,
    browser_view_path: Path | None,
    operator: str | None = None,
    catalog_path: Path | None = None,
    auto_driving_linked_pr: str | None = None,
    metrics_ui_linked_pr: str | None = None,
    machine_only: bool = False,
) -> dict[str, Any]:
    started = _utc_now()
    session_id = started.strftime("%Y%m%d%H%M%S")
    if dry_run:
        execution_mode = "dry_run"
    elif machine_only:
        execution_mode = "machine_only_live"
    elif non_interactive:
        execution_mode = "non_interactive_live"
    else:
        execution_mode = "interactive_live"

    variables = {
        "vehicle_id": str(catalog.get("vehicle_id") or "chase-sim-chaser"),
        "metrics_ui_origin": metrics_ui_origin
        or str(catalog.get("metrics_ui_origin") or "http://localhost:5050"),
        "perception_algorithm": str(catalog.get("perception_algorithm") or "lightweight_observer"),
        "src_dir": "",
    }

    # Capture repository identities BEFORE any session artifacts exist so a
    # session_dir under the checkout cannot manufacture dirty state.
    # Do not exclude the future session directory: identity is pre-session, so
    # any pre-existing sibling dirt must still be reported.
    auto_driving = _git_identity(repo_root)
    remote_ad = _github_remote_identity(repo_root)
    if remote_ad:
        auto_driving = dict(auto_driving)
        auto_driving["github_owner"] = remote_ad["owner"]
        auto_driving["github_repo"] = remote_ad["repo"]
    if auto_driving_linked_pr:
        auto_driving = dict(auto_driving)
        auto_driving["linked_pr"] = auto_driving_linked_pr
    metrics_ui = _git_identity(metrics_ui_repo) if metrics_ui_repo else None
    if metrics_ui is not None and metrics_ui_repo is not None:
        remote_mu = _github_remote_identity(metrics_ui_repo)
        if remote_mu:
            metrics_ui = dict(metrics_ui)
            metrics_ui["github_owner"] = remote_mu["owner"]
            metrics_ui["github_repo"] = remote_mu["repo"]
    if metrics_ui is not None and metrics_ui_linked_pr:
        metrics_ui = dict(metrics_ui)
        metrics_ui["linked_pr"] = metrics_ui_linked_pr
    recording_before = _list_run_directories(repo_root, variables["vehicle_id"])

    # Bind executed catalog: for acceptance, re-parse pinned bytes so in-memory
    # mutations of a loaded mapping cannot change what runs.
    canonical = False
    canonical_reason = "catalog path not provided"
    executed_catalog = catalog
    if catalog.get("track") == "acceptance":
        pinned = _load_pinned_acceptance_catalog(CANONICAL_ACCEPTANCE_PATH)
        if pinned is not None and catalog_path is not None:
            # Prefer path+mapping equality against pin; if equal, execute pin.
            ok_bind, bind_reason = _is_canonical_acceptance_catalog(
                catalog_path, catalog
            )
            if ok_bind:
                executed_catalog = pinned
                catalog = pinned
                canonical = True
                canonical_reason = bind_reason
            else:
                canonical = False
                canonical_reason = bind_reason
        else:
            canonical, canonical_reason = _is_canonical_acceptance_catalog(
                catalog_path, catalog
            )
    elif catalog_path is not None:
        canonical, canonical_reason = _is_canonical_acceptance_catalog(
            catalog_path, catalog
        )

    session_dir.mkdir(parents=True, exist_ok=True)
    if (session_dir / "result.json").exists() or (session_dir / "steps").exists() and any(
        (session_dir / "steps").iterdir()
    ):
        raise SystemExit(
            f"Session directory already has results (refusing to mix evidence): {session_dir}"
        )
    (session_dir / "steps").mkdir(exist_ok=True)
    (session_dir / "transcripts").mkdir(exist_ok=True)
    transcript_path = session_dir / "transcripts" / "cli-transcript.txt"
    _write_text(transcript_path, "")

    state = SessionState(
        catalog=executed_catalog,
        session_dir=session_dir,
        repo_root=repo_root,
        variables=variables,
        execution_mode=execution_mode,
        session_id=session_id,
        dry_run=dry_run,
        non_interactive=non_interactive,
        operator=operator,
        canonical_acceptance=canonical,
        auto_driving_linked_pr=auto_driving_linked_pr,
        metrics_ui_linked_pr=metrics_ui_linked_pr,
        catalog_source_path=_redact_path(catalog_path, repo_root)
        if catalog_path is not None
        else None,
        recording_before=recording_before,
        metrics_ui_repo_path=metrics_ui_repo,
    )

    worktree_meta: dict[str, Any] = {}
    if auto_driving.get("worktree_state") == "dirty":
        worktree_meta["auto-driving"] = _capture_worktree_reviewables(
            repo_root,
            session_dir,
            label="auto-driving",
        )
    if metrics_ui_repo is not None and isinstance(metrics_ui, dict):
        if metrics_ui.get("worktree_state") == "dirty":
            worktree_meta["metrics-ui"] = _capture_worktree_reviewables(
                metrics_ui_repo,
                session_dir,
                label="metrics-ui",
            )

    baseline = {
        "recorded_at_utc": _iso(started),
        "operating_system": platform.platform(),
        "python": sys.version.split()[0],
        "operator": operator,
        "browser": {"name": browser_name, "version": browser_version},
        "metrics_ui_origin": variables["metrics_ui_origin"],
        "repositories": {"auto_driving": auto_driving, "metrics_ui": metrics_ui},
        "vehicle_id": variables["vehicle_id"],
        "execution_mode": execution_mode,
        "recording_before": state.recording_before,
        "session_visible": None,
        "precondition_cleanup": None,
        "canonical_acceptance": canonical,
        "canonical_acceptance_reason": canonical_reason,
        "catalog_source": state.catalog_source_path,
        "worktree_reviewables": worktree_meta,
        "worktree_diff_path": (
            "auto-driving-worktree.diff"
            if auto_driving.get("worktree_state") == "dirty"
            else None
        ),
    }
    _write_json(session_dir / "baseline.json", baseline)
    notes_lines = [
        f"# Session notes — {catalog.get('id')}",
        "",
        f"- started_at_utc: `{_iso(started)}`",
        f"- execution_mode: `{execution_mode}`",
        f"- track: `{catalog.get('track')}`",
        f"- operator: `{operator or '(unset)'}`",
        "",
    ]

    cleanup_info: dict[str, Any] = {"attempted": False, "needed": False}
    continuity_restore: dict[str, Any] | None = None
    continuity_restore_done = False
    continuity_identity_at_start: dict[str, Any] | None = None
    last_swap_step_id: str | None = None

    # Formal acceptance: never execute any CLI from a noncanonical catalog.
    # Verdict already fails closed; this is the safety owner (execution order).
    refuse_acceptance_execution = (
        catalog.get("track") == "acceptance" and not canonical and not dry_run
    )

    # Continuity track: argv-derived safety + required families before any CLI.
    continuity_preflight: dict[str, Any] | None = None
    refuse_continuity_execution = False
    if catalog.get("track") == CONTINUITY_TRACK and not dry_run:
        safety_ok, safety_reason, safety_findings = validate_continuity_safety_preflight(
            catalog
        )
        family_ok, family_reason, family_meta = validate_continuity_families(catalog)
        continuity_preflight = {
            "schema": "continuity_preflight_v0",
            "safety_ok": safety_ok,
            "safety_reason": safety_reason,
            "family_ok": family_ok,
            "family_reason": family_reason,
            "family_meta": family_meta,
            "safety_findings": safety_findings,
            "required_family_ids": list(REQUIRED_FAMILY_IDS),
        }
        _write_json(session_dir / "continuity-preflight.json", continuity_preflight)
        if not safety_ok or not family_ok:
            refuse_continuity_execution = True
            reason = safety_reason if not safety_ok else family_reason
            state.safety_blocked = True
            state.safety_block_reason = f"continuity preflight refused: {reason}"
            state.precondition_cleanup = {
                "ok": False,
                "attempted": False,
                "needed": False,
                "skipped": "continuity_preflight",
                "error": state.safety_block_reason,
            }
            baseline["precondition_cleanup"] = state.precondition_cleanup
            baseline["continuity_preflight"] = continuity_preflight
            _write_json(session_dir / "baseline.json", baseline)
            _write_json(
                session_dir / "precondition-cleanup.json",
                state.precondition_cleanup,
            )
            _record_finding(
                state,
                step_id="_continuity_preflight",
                classification="acceptance_blocker",
                severity="P1",
                summary="Continuity catalog refused before any command",
                human_notes=reason,
                evidence=["continuity-preflight.json", "baseline.json"],
            )
            notes_lines.extend(
                [
                    "## continuity preflight failed",
                    "",
                    f"- reason: {reason}",
                    "- action: no catalog commands executed",
                    "",
                ]
            )
            print()
            print("=" * 72)
            print("REFUSED: continuity preflight — no commands executed")
            print(reason)
            print("=" * 72)

    try:
        if refuse_acceptance_execution:
            state.safety_blocked = True
            state.safety_block_reason = (
                f"noncanonical acceptance catalog: {canonical_reason}"
            )
            state.precondition_cleanup = {
                "ok": False,
                "attempted": False,
                "needed": False,
                "skipped": "noncanonical_acceptance_catalog",
                "error": state.safety_block_reason,
            }
            baseline["precondition_cleanup"] = state.precondition_cleanup
            _write_json(session_dir / "baseline.json", baseline)
            _write_json(
                session_dir / "precondition-cleanup.json",
                state.precondition_cleanup,
            )
            _record_finding(
                state,
                step_id="_catalog_bind",
                classification="acceptance_blocker",
                severity="P1",
                summary="Noncanonical acceptance catalog refused before any command",
                human_notes=canonical_reason,
                evidence=["baseline.json", "precondition-cleanup.json"],
            )
            notes_lines.extend(
                [
                    "## catalog bind failed",
                    "",
                    f"- reason: {canonical_reason}",
                    "- action: no catalog commands executed",
                    "",
                ]
            )
            print()
            print("=" * 72)
            print("REFUSED: noncanonical acceptance catalog — no commands executed")
            print(canonical_reason)
            print("=" * 72)
        elif refuse_continuity_execution:
            pass  # findings and notes already recorded above
        else:
            # Stop any pre-existing worker before acceptance or continuity catalogs.
            if catalog.get("track") in {"acceptance", CONTINUITY_TRACK} and not dry_run:
                print()
                print("=" * 72)
                print("PRECONDITION: ensure no pre-existing automation worker")
                print("=" * 72)
                state.precondition_cleanup = _run_precondition_cleanup(
                    state,
                    command_timeout_s=command_timeout_s,
                    transcript_path=transcript_path,
                    metrics_ui_origin=variables["metrics_ui_origin"],
                )
                baseline["precondition_cleanup"] = state.precondition_cleanup
                # Keep disk and in-memory baseline identical for final result.json.
                _write_json(session_dir / "baseline.json", baseline)
                _write_json(
                    session_dir / "precondition-cleanup.json",
                    state.precondition_cleanup,
                )
                if state.precondition_cleanup.get("ok") is not True:
                    state.safety_blocked = True
                    state.safety_block_reason = (
                        f"precondition failed: {state.precondition_cleanup.get('error')}"
                    )
                    _record_finding(
                        state,
                        step_id="_precondition_cleanup",
                        classification="acceptance_blocker",
                        severity="P1",
                        summary="Precondition cleanup failed before catalog baseline",
                        human_notes=str(state.precondition_cleanup.get("error")),
                        evidence=[
                            "steps/_precondition_cleanup/",
                            "precondition-cleanup.json",
                        ],
                    )
                    notes_lines.extend(
                        [
                            "## precondition failed",
                            "",
                            f"- error: `{state.precondition_cleanup.get('error')}`",
                            "- action: catalog steps not executed",
                            "",
                        ]
                    )
                    print()
                    print("=" * 72)
                    print("REFUSED: precondition cleanup failed — no catalog commands")
                    print(state.precondition_cleanup.get("error"))
                    print("=" * 72)

            continuity_swap_family = "continuity.live_config_swap"
            precondition_blocks_catalog = (
                state.safety_blocked
                and state.precondition_cleanup is not None
                and state.precondition_cleanup.get("ok") is not True
            )
            if catalog.get("track") == CONTINUITY_TRACK and catalog_path is not None:
                continuity_identity_at_start = collect_identity_bundle(
                    repo_root=repo_root,
                    catalog_path=catalog_path,
                    metrics_ui={
                        "commit": (metrics_ui or {}).get("commit") if metrics_ui else None,
                        "worktree_state": (metrics_ui or {}).get("worktree_state")
                        if metrics_ui
                        else None,
                        "branch": (metrics_ui or {}).get("branch") if metrics_ui else None,
                        "diff_identity": (metrics_ui or {}).get("diff_identity")
                        if metrics_ui
                        else None,
                        "linked_pr": (metrics_ui or {}).get("linked_pr") if metrics_ui else None,
                        "path": (metrics_ui or {}).get("path") if metrics_ui else None,
                    },
                )
                # metrics_ui_required is decided at finalizer time (only when visual HITL complete).
                if continuity_identity_at_start is not None:
                    continuity_identity_at_start["metrics_ui_required"] = False
                    _write_json(
                        session_dir / "continuity-identity-at-start.json",
                        continuity_identity_at_start,
                    )
            catalog_step_list = [
                s for s in (catalog.get("steps") or []) if isinstance(s, dict)
            ]
            for s in catalog_step_list:
                if s.get("family_id") == continuity_swap_family:
                    last_swap_step_id = str(s.get("id") or "")

            if precondition_blocks_catalog:
                catalog_step_list = []  # fail-stop: do not mutate after failed precondition

            for step in catalog_step_list:
                if not isinstance(step, dict):
                    continue
                step_id = str(step.get("id") or f"step-{len(state.steps)+1}")
                family_id = step.get("family_id")
                print()
                print("=" * 72)
                print(f"STEP {step_id}  ({step.get('kind') or 'command'})")
                print(f"Question: {step.get('question') or ''}")
                print(f"Safety: {step.get('safety') or 'unspecified'}")
                if family_id:
                    print(f"Family: {family_id}")
                if step.get("note"):
                    print(f"Note: {step['note']}")
                print("=" * 72)

                # US-04: snapshot full staged activations before first swap mutation.
                if (
                    catalog.get("track") == CONTINUITY_TRACK
                    and family_id == continuity_swap_family
                    and continuity_restore is None
                    and not dry_run
                ):
                    us04_cache = session_dir / "us04-staged-cache"
                    snap = snapshot_staged_state(
                        repo_root,
                        variables["vehicle_id"],
                        cache_dir=us04_cache,
                    )
                    if not snapshot_is_restorable(snap):
                        step_status = "fail"
                        machine_ok = False
                        machine_summary = (
                            f"US-04 snapshot not restorable: {snap.get('error')}"
                        )
                        _record_finding(
                            state,
                            step_id=step_id,
                            classification="acceptance_blocker",
                            severity="P1",
                            summary=machine_summary,
                            human_notes=str(snap.get("error")),
                            evidence=[],
                        )
                        print(f"  FAIL: {machine_summary}")
                        envelope = {
                            "id": step_id,
                            "family_id": family_id,
                            "kind": step.get("kind"),
                            "status": "fail",
                            "machine_ok": False,
                            "machine_summary": machine_summary,
                            "commands": [],
                            "human": {
                                "visual": "skip",
                                "notes": "",
                                "finding_requested": False,
                                "interactive": False,
                            },
                            "required_for_verdict": bool(step.get("required_for_verdict")),
                        }
                        step_dir = session_dir / "steps" / step_id
                        step_dir.mkdir(parents=True, exist_ok=True)
                        _write_json(step_dir / "envelope.json", envelope)
                        state.steps.append(envelope)
                        continue
                    continuity_restore = snap
                    meta_files = {}
                    for name, file_snap in (snap.get("files") or {}).items():
                        if not isinstance(file_snap, dict):
                            continue
                        meta_files[name] = {
                            "path": _redact_path(str(file_snap.get("path") or ""), repo_root),
                            "sha256": file_snap.get("sha256"),
                            "restorable": snapshot_is_restorable(file_snap),
                            "existed": file_snap.get("existed"),
                        }
                    _write_json(
                        session_dir / "us04-activation-snapshot-meta.json",
                        {"restorable": True, "files": meta_files},
                    )
                    perc = (snap.get("files") or {}).get("perception") or {}
                    print(
                        f"  US-04 staged snapshot ok perception="
                        f"{str(perc.get('sha256') or '')[:12]}…"
                    )

                requires = step.get("requires_prompt")
                if isinstance(requires, str) and requires:
                    if non_interactive and state.variables.get(requires):
                        pass
                    elif non_interactive:
                        raise SystemExit(f"Non-interactive session missing required variable {requires!r}")
                    else:
                        help_text = step.get("requires_prompt_help") or requires
                        state.variables[requires] = prompt(f"{help_text}: ").strip()

                step_dir = session_dir / "steps" / step_id
                step_dir.mkdir(parents=True, exist_ok=True)
                command_outcomes: list[dict[str, Any]] = []
                step_status = "ok"
                machine_summary = ""
                machine_ok = True
                validator_notes: list[str] = []

                # Hard safety: acceptance live_mutation requires precondition/staging.
                # Continuity live_mutation also requires proven precondition cleanup.
                if (
                    not dry_run
                    and step.get("safety") == "live_mutation"
                    and step.get("kind") != "baseline"
                    and catalog.get("track") in {"acceptance", CONTINUITY_TRACK}
                ):
                    prereq_ok, prereq_reason = _live_mutation_prerequisites_met(state)
                    if not prereq_ok:
                        step_status = "blocked"
                        machine_ok = False
                        machine_summary = f"blocked before live mutation: {prereq_reason}"
                        validator_notes.append(machine_summary)
                        print(f"  BLOCKED: {prereq_reason}")
                        state.safety_blocked = True
                        state.safety_block_reason = prereq_reason
                        judgment = _prompt_judgment(
                            step=step,
                            prompt=prompt,
                            non_interactive=True,
                            auto_visual="skip",
                        )
                        gate_ids = [str(g) for g in (step.get("gate_ids") or [])]
                        if gate_ids:
                            _apply_gate(
                                state,
                                gate_ids,
                                status="fail",
                                summary=machine_summary,
                                evidence=[f"steps/{step_id}/envelope.json"],
                            )
                        if step.get("required_for_verdict"):
                            _record_finding(
                                state,
                                step_id=step_id,
                                classification="acceptance_blocker"
                                if catalog.get("track") == "acceptance"
                                else "environment_blocker",
                                severity="P1",
                                summary=machine_summary,
                                human_notes=prereq_reason,
                                evidence=[f"steps/{step_id}/envelope.json"],
                            )
                        envelope = {
                            "id": step_id,
                            "kind": step.get("kind"),
                            "question": step.get("question"),
                            "safety": step.get("safety"),
                            "primary_cue": step.get("primary_cue"),
                            "status": step_status,
                            "machine_summary": machine_summary,
                            "machine_ok": False,
                            "commands": [],
                            "human": {
                                "visual": judgment.visual,
                                "notes": judgment.notes,
                                "finding_requested": False,
                                "interactive": False,
                            },
                            "gate_ids": gate_ids,
                            "required_for_verdict": bool(step.get("required_for_verdict")),
                            "blocked_reason": prereq_reason,
                        }
                        _write_json(step_dir / "envelope.json", envelope)
                        state.steps.append(envelope)
                        notes_lines.extend(
                            [
                                f"## {step_id}",
                                "",
                                f"- status: `{step_status}`",
                                f"- blocked: {prereq_reason}",
                                "",
                            ]
                        )
                        continue

                if dry_run:
                    machine_summary = "dry-run: commands not executed"
                    for index, argv in enumerate(step.get("commands") or []):
                        if not isinstance(argv, list):
                            continue
                        rendered = _substitute_argv(argv, state.variables)
                        print(f"  dry-run: {_format_command(rendered)}")
                        command_outcomes.append(
                            {
                                "argv": rendered,
                                "command": _format_command(rendered),
                                "exit_code": 0,
                                "elapsed_ms": 0,
                                "dry_run": True,
                            }
                        )
                elif step.get("kind") == "baseline":
                    machine_summary = (
                        f"origin={variables['metrics_ui_origin']}; "
                        f"auto_driving={auto_driving.get('commit')}; "
                        f"worktree={auto_driving.get('worktree_state')}"
                    )
                    print(machine_summary)
                else:
                    allow_nonzero = bool(step.get("allow_nonzero_exit"))
                    expect_exit = step.get("expect_exit")
                    for index, argv in enumerate(step.get("commands") or []):
                        if not isinstance(argv, list):
                            continue
                        rendered = _substitute_argv(argv, state.variables)
                        state.executed_commands.append(list(rendered))
                        print(f"\n$ {_format_command(rendered)}")
                        outcome = _run_command(
                            rendered,
                            cwd=repo_root,
                            session_dir=session_dir,
                            step_dir=step_dir,
                            index=index,
                            timeout_s=command_timeout_s,
                            transcript_path=transcript_path,
                        )
                        payload = {
                            "argv": outcome.argv,
                            "command": outcome.command,
                            "exit_code": outcome.exit_code,
                            "elapsed_ms": outcome.elapsed_ms,
                            "stdout_path": outcome.stdout_path,
                            "stderr_path": outcome.stderr_path,
                            "started_at_utc": outcome.started_at_utc,
                            "ended_at_utc": outcome.ended_at_utc,
                        }
                        command_outcomes.append(payload)
                        # Print a short excerpt for the operator only.
                        excerpt = (step_dir / f"cmd-{index:02d}.stdout.txt").read_text(encoding="utf-8")[:1200]
                        print(excerpt)
                        print(f"exit={outcome.exit_code} elapsed_ms={outcome.elapsed_ms}")
                        if "automation" in rendered and "run" in rendered:
                            # partial or full startup still needs cleanup attempt
                            state.worker_may_exist = True
                            state.automation_run_seen = True
                        if expect_exit is not None and outcome.exit_code != int(expect_exit) and not allow_nonzero:
                            step_status = "fail"
                            machine_ok = False
                        elif outcome.exit_code != 0 and not allow_nonzero and expect_exit is None:
                            step_status = "fail"
                            machine_ok = False
                        # Continuity: bind exact capture run from stdout; content lineage required.
                        if (
                            catalog.get("track") == CONTINUITY_TRACK
                            and outcome.exit_code == 0
                            and "perception" in rendered
                            and "run" in rendered
                            and "--record" in rendered
                        ):
                            stdout_text = (
                                step_dir / f"cmd-{index:02d}.stdout.txt"
                            ).read_text(encoding="utf-8")
                            bound = _bind_src_dir_from_capture_stdout(
                                stdout_text, repo_root=repo_root
                            )
                            if bound is None:
                                step_status = "fail"
                                machine_ok = False
                                validator_notes.append(
                                    "offline capture: exact run path missing from stdout "
                                    "(mtime fallback disabled)"
                                )
                                print(
                                    "  FAIL: continuity requires exact capture path in stdout"
                                )
                            else:
                                lineage = capture_source_lineage(bound)
                                if lineage.get("ok") is not True:
                                    step_status = "fail"
                                    machine_ok = False
                                    validator_notes.append(
                                        f"offline capture lineage failed: {lineage.get('error')}"
                                    )
                                    print(
                                        f"  FAIL: content lineage: {lineage.get('error')}"
                                    )
                                else:
                                    state.variables["src_dir"] = str(bound)
                                    lineage_out = {
                                        "ok": True,
                                        "src_dir": str(bound),
                                        "src_dir_redacted": _redact_path(bound, repo_root),
                                        "manifest_sha256": lineage.get("manifest_sha256"),
                                        "ordered_input_sha256": lineage.get(
                                            "ordered_input_sha256"
                                        ),
                                        "frame_count": lineage.get("frame_count"),
                                        "frames": lineage.get("frames"),
                                        "schema": lineage.get("schema"),
                                    }
                                    _write_json(
                                        session_dir / "offline-source-lineage.json",
                                        lineage_out,
                                    )
                                    print(
                                        f"  continuity: src_dir={bound} "
                                        f"ordered_input={str(lineage.get('ordered_input_sha256') or '')[:12]}…"
                                    )
                        # Continuity: re-verify content lineage before/after apply.
                        if (
                            catalog.get("track") == CONTINUITY_TRACK
                            and outcome.exit_code == 0
                            and "perception" in rendered
                            and "apply" in rendered
                        ):
                            lineage_path = session_dir / "offline-source-lineage.json"
                            src = state.variables.get("src_dir")
                            if not lineage_path.is_file() or not src:
                                step_status = "fail"
                                machine_ok = False
                                validator_notes.append(
                                    "offline apply: missing bound content lineage"
                                )
                                print("  FAIL: apply without content-bound lineage")
                            else:
                                try:
                                    expected = json.loads(
                                        lineage_path.read_text(encoding="utf-8")
                                    )
                                except (OSError, json.JSONDecodeError) as exc:
                                    expected = {"ok": False, "error": str(exc)}
                                ok_lin, lin_reason = verify_source_lineage(
                                    Path(str(src)), expected
                                )
                                if not ok_lin:
                                    step_status = "fail"
                                    machine_ok = False
                                    validator_notes.append(
                                        f"offline apply lineage mismatch: {lin_reason}"
                                    )
                                    print(f"  FAIL: lineage: {lin_reason}")
                                else:
                                    print(f"  continuity: lineage verified ({lin_reason})")

                    # JSON capture as a first-class command outcome
                    capture = step.get("capture_json")
                    if isinstance(capture, dict) and isinstance(capture.get("command"), list):
                        rendered = _substitute_argv(capture["command"], state.variables)
                        state.executed_commands.append(list(rendered))
                        json_name = str(capture.get("path") or f"{step_id}.json")
                        json_out = session_dir / json_name
                        print(f"\n$ {_format_command(rendered)}  > {json_name}")
                        outcome = _run_command(
                            rendered,
                            cwd=repo_root,
                            session_dir=session_dir,
                            step_dir=step_dir,
                            index=len(command_outcomes),
                            timeout_s=command_timeout_s,
                            transcript_path=transcript_path,
                        )
                        if outcome.exit_code != 0:
                            step_status = "fail"
                            machine_ok = False
                            validator_notes.append(
                                f"JSON capture exit={outcome.exit_code}"
                            )
                        command_outcomes.append(
                            {
                                "argv": outcome.argv,
                                "command": outcome.command,
                                "exit_code": outcome.exit_code,
                                "elapsed_ms": outcome.elapsed_ms,
                                "started_at_utc": outcome.started_at_utc,
                                "ended_at_utc": outcome.ended_at_utc,
                                "stdout_path": outcome.stdout_path,
                                "stderr_path": outcome.stderr_path,
                                "captures_to": json_name,
                            }
                        )
                        raw = (session_dir / outcome.stdout_path).read_text(encoding="utf-8")
                        try:
                            parsed = json.loads(raw)
                            _write_json(json_out, parsed)
                            # Extract worker pid if present
                            details = _worker_details(parsed, variables["vehicle_id"])
                            pid = details.get("pid")
                            _note_worker_pid(state, pid, source=step_id)
                            if (
                                isinstance(pid, int)
                                and _layer_state(
                                    parsed, "automation_worker", variables["vehicle_id"]
                                )
                                == "running"
                            ):
                                state.worker_may_exist = True
                            # Bind fingerprint to this exact capture, including None.
                            fingerprint = extract_session_fingerprint(
                                parsed, variables["vehicle_id"]
                            )
                            state.latest_fingerprint = fingerprint
                            state.capture_fingerprints[json_name] = fingerprint
                            if fingerprint is not None:
                                if state.baseline_fingerprint is None and step_id.startswith(
                                    "status-initial"
                                ):
                                    state.baseline_fingerprint = fingerprint
                                    _write_json(
                                        session_dir / "session-fingerprint-baseline.json",
                                        fingerprint,
                                    )
                                    # Record proposal-required visible session values.
                                    baseline_path = session_dir / "baseline.json"
                                    if baseline_path.is_file():
                                        baseline_doc = json.loads(
                                            baseline_path.read_text(encoding="utf-8")
                                        )
                                        session_visible = {
                                            "game_id": fingerprint.get("game_id"),
                                            "scenario_id": fingerprint.get("scenario_id"),
                                            "simulation_epoch": fingerprint.get(
                                                "simulation_epoch"
                                            ),
                                            "playback": fingerprint.get("playback"),
                                            "control_source": fingerprint.get(
                                                "control_source"
                                            ),
                                            "control_input": fingerprint.get(
                                                "control_input"
                                            ),
                                        }
                                        baseline_doc["session_visible"] = session_visible
                                        # Keep in-memory baseline (and final result) in sync.
                                        baseline["session_visible"] = session_visible
                                        _write_json(baseline_path, baseline_doc)
                                _write_json(
                                    session_dir / "session-fingerprint-latest.json",
                                    fingerprint,
                                )
                            else:
                                _write_json(
                                    session_dir / "session-fingerprint-latest.json",
                                    {
                                        "error": "extraction_failed",
                                        "capture": json_name,
                                        "step_id": step_id,
                                    },
                                )
                        except json.JSONDecodeError as exc:
                            step_status = "fail"
                            machine_ok = False
                            validator_notes.append(f"JSON capture invalid: {exc}")
                            _write_text(json_out.with_suffix(json_out.suffix + ".raw.txt"), raw)

                        if step.get("capture_view_latest") and json_out.is_file():
                            try:
                                view_meta = _capture_view_latest(
                                    session_dir,
                                    json_out,
                                    vehicle_id=variables["vehicle_id"],
                                    max_frame_lag=(
                                        _catalog_max_frame_lag(catalog) or 0
                                    ),
                                )
                            except Exception as exc:  # noqa: BLE001
                                view_meta = {
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                            if not view_meta or view_meta.get("error"):
                                machine_ok = False
                                step_status = "fail"
                                validator_notes.append(
                                    f"view_latest: failed ({view_meta})"
                                )
                            else:
                                validator_notes.append(
                                    f"view_latest: {view_meta.get('summary') or 'ok'}"
                                )

                    # Machine validators declared on the step
                    for validator in step.get("machine_validators") or []:
                        name = str(validator)
                        status_path = None
                        view_path = session_dir / "view-publication.json"
                        if name in {"initial_layers"}:
                            status_path = session_dir / "initial-status.json"
                        elif name in {"staged_layers"}:
                            status_path = session_dir / "staged-status.json"
                        elif name in {"running_layers", "authority"}:
                            status_path = session_dir / "running-status.json"
                        elif name in {"stopped_layers"}:
                            status_path = session_dir / "stopped-status.json"
                        elif name == "preservation":
                            # Fingerprints already updated from the latest JSON capture.
                            status_path = None
                        if name == "default_recording":
                            state.recording_after = _list_run_directories(
                                repo_root, variables["vehicle_id"]
                            )
                        ok, summary, machine_evidence = _run_machine_validator(
                            name,
                            vehicle_id=variables["vehicle_id"],
                            status_path=status_path,
                            view_path=view_path if view_path.is_file() else None,
                            before_runs=state.recording_before,
                            after_runs=state.recording_after,
                            baseline_fingerprint=state.baseline_fingerprint,
                            current_fingerprint=state.latest_fingerprint,
                            perception_algorithm=variables["perception_algorithm"],
                            max_frame_lag=_catalog_max_frame_lag(catalog),
                        )
                        validator_notes.append(f"{name}: {summary}")
                        if name == "view_correlation" and machine_evidence is not None:
                            state.view_correlation = machine_evidence
                            for issue in machine_evidence.get("diagnostic_findings") or []:
                                _record_finding(
                                    state,
                                    step_id=step_id,
                                    classification="usability_defect",
                                    severity="P3",
                                    summary=f"Malformed view timing diagnostic: {issue}",
                                    human_notes=(
                                        "Timing is diagnostic only for M007; the frame-count "
                                        "correlation verdict remains authoritative."
                                    ),
                                    evidence=["view-publication.json"],
                                )
                        if not ok:
                            machine_ok = False
                            step_status = "fail"
                        elif name == "view_correlation":
                            state.view_healthy_at_unix = time.time()
                        elif name == "running_layers" and ok:
                            # Startup must not reuse a pre-existing worker PID.
                            running_pid = state.last_worker_pid
                            if (
                                isinstance(running_pid, int)
                                and running_pid in state.pre_existing_worker_pids
                            ):
                                machine_ok = False
                                step_status = "fail"
                                validator_notes.append(
                                    f"running_layers: worker pid {running_pid} is "
                                    "pre-existing (not created by this session's run)"
                                )

                judgment = _prompt_judgment(
                    step=step,
                    prompt=prompt,
                    non_interactive=non_interactive,
                    auto_visual=auto_visual,
                )
                if judgment.interactive and judgment.visual in {"pass", "fail"}:
                    state.interactive_human_confirmation = True

                # Bind browser screenshot to the human-view gate after inspection.
                if (
                    step.get("visual_required")
                    and "human_view" in (step.get("gate_ids") or [])
                    and not dry_run
                ):
                    import_path = browser_view_path
                    if not non_interactive:
                        offered = prompt(
                            "Path to cropped browser-view.png for this inspection "
                            "(Enter to use --browser-view if provided): "
                        ).strip()
                        if offered:
                            import_path = Path(offered).expanduser()
                    if import_path is not None and import_path.is_file():
                        target = session_dir / "browser-view.png"
                        # Bind only after view_correlation established the floor;
                        # early human_view steps may still collect notes without
                        # labeling a pre-session PNG as acceptance evidence.
                        if state.view_healthy_at_unix is None:
                            ok_img = False
                            img_summary = (
                                "browser-view.png not bound yet: view health not proven"
                            )
                            source_meta: dict[str, Any] = {
                                "source_sha256": _sha256_file(import_path),
                                "source_mtime_unix": import_path.stat().st_mtime,
                            }
                            if target.is_file():
                                try:
                                    target.unlink()
                                except OSError:
                                    pass
                        else:
                            ok_img, img_summary, source_meta = _bind_browser_view_image(
                                import_path,
                                target,
                                not_before_unix=state.view_healthy_at_unix,
                            )
                        state.browser_view_meta = {
                            # Redact absolute paths from reviewable evidence.
                            "imported_from": _redact_path(import_path, repo_root),
                            "imported_at_utc": _iso(_utc_now()),
                            "source_mtime_unix": source_meta.get("source_mtime_unix"),
                            "source_sha256": source_meta.get("source_sha256"),
                            "view_healthy_at_unix": state.view_healthy_at_unix,
                            "step_id": step_id,
                            "validation": img_summary,
                            "ok": ok_img,
                        }
                        _write_json(session_dir / "browser-view-meta.json", state.browser_view_meta)
                        # Only fail the step when this gate already proved view
                        # health (or judgment claims pass without any bound image).
                        if not ok_img and state.view_healthy_at_unix is not None:
                            machine_ok = False
                            step_status = "fail"
                            validator_notes.append(f"browser_view: {img_summary}")
                    elif judgment.visual == "pass" and state.view_healthy_at_unix is not None:
                        machine_ok = False
                        step_status = "fail"
                        validator_notes.append(
                            "browser_view: browser-view.png not provided at human-view gate"
                        )

                # Single join of validator notes after all machine + screenshot checks.
                if validator_notes:
                    machine_summary = "; ".join(validator_notes)

                step_status = _finalize_step_status(
                    step_status,
                    machine_ok=machine_ok,
                    visual=judgment.visual,
                    required_for_verdict=bool(step.get("required_for_verdict")),
                )

                evidence_refs = [f"steps/{step_id}/envelope.json"]
                for outcome in command_outcomes:
                    if outcome.get("stdout_path"):
                        evidence_refs.append(str(outcome["stdout_path"]))
                if step.get("capture_view_latest") and (
                    session_dir / "view-publication.json"
                ).is_file():
                    evidence_refs.append("view-publication.json")

                if judgment.finding:
                    _record_finding(
                        state,
                        step_id=step_id,
                        classification=(
                            "acceptance_blocker"
                            if catalog.get("track") == "acceptance" and judgment.visual == "fail"
                            else "usability_defect"
                        ),
                        severity=judgment.finding_severity or "P3",
                        summary=judgment.finding_summary or "operator-reported finding",
                        human_notes=judgment.notes,
                        evidence=evidence_refs,
                        repro=[o.get("command", "") for o in command_outcomes if o.get("command")],
                    )

                gate_ids = [str(g) for g in (step.get("gate_ids") or [])]
                if gate_ids:
                    if step_status == "pass":
                        _apply_gate(
                            state,
                            gate_ids,
                            status="pass",
                            summary=judgment.notes or machine_summary or step.get("primary_cue") or step_id,
                            evidence=evidence_refs,
                        )
                    elif step_status == "skip":
                        _apply_gate(
                            state,
                            gate_ids,
                            status="skip",
                            summary=judgment.notes or "operator skipped",
                            evidence=evidence_refs,
                        )
                    else:
                        _apply_gate(
                            state,
                            gate_ids,
                            status="fail",
                            summary=judgment.notes or machine_summary or "step failed",
                            evidence=evidence_refs,
                        )
                        # Durable finding for required gate failure
                        if step.get("required_for_verdict"):
                            _record_finding(
                                state,
                                step_id=step_id,
                                classification="acceptance_blocker"
                                if catalog.get("track") == "acceptance"
                                else "usability_defect",
                                severity="P1" if catalog.get("track") == "acceptance" else "P2",
                                summary=machine_summary or f"Required step {step_id} failed",
                                human_notes=judgment.notes,
                                evidence=evidence_refs,
                                repro=[o.get("command", "") for o in command_outcomes if o.get("command")],
                            )
                    if "correlation" in gate_ids and state.view_correlation is not None:
                        correlation_gate = state.gate_results.get("correlation")
                        if correlation_gate is not None:
                            correlation_gate["details"] = state.view_correlation

                envelope = {
                    "id": step_id,
                    "family_id": family_id,
                    "kind": step.get("kind"),
                    "question": step.get("question"),
                    "safety": step.get("safety"),
                    "primary_cue": step.get("primary_cue"),
                    "status": step_status,
                    "machine_summary": machine_summary,
                    "machine_ok": machine_ok,
                    "machine_evidence": (
                        {"view_correlation": state.view_correlation}
                        if "view_correlation" in (step.get("machine_validators") or [])
                        else {}
                    ),
                    "commands": command_outcomes,
                    "human": {
                        "visual": judgment.visual,
                        "notes": judgment.notes,
                        "finding_requested": judgment.finding,
                        "interactive": judgment.interactive,
                    },
                    "gate_ids": gate_ids,
                    "required_for_verdict": bool(step.get("required_for_verdict")),
                    "browser_view": state.browser_view_meta
                    if step.get("visual_required") and "human_view" in gate_ids
                    else None,
                }
                _write_json(step_dir / "envelope.json", envelope)
                state.steps.append(envelope)
                notes_lines.extend(
                    [
                        f"## {step_id}",
                        "",
                        f"- status: `{step_status}`",
                        f"- visual: `{judgment.visual}`",
                        f"- notes: {judgment.notes or '(none)'}",
                        f"- machine: {machine_summary or '(none)'}",
                        "",
                    ]
                )

                if step_id == "automation-run" and step_status in {"pass", "fail", "ok"}:
                    state.worker_may_exist = True

    except KeyboardInterrupt:
        _record_finding(
            state,
            step_id="_session",
            classification="environment_blocker",
            severity="P2",
            summary="Session interrupted by KeyboardInterrupt",
            human_notes="operator cancelled",
            evidence=[],
        )
        notes_lines.append("## interrupted\n\n- KeyboardInterrupt\n")
    except Exception as exc:  # noqa: BLE001
        # Convert unexpected failures into durable incomplete/findings evidence.
        # Cleanup still runs in finally; result.json is always written below.
        _record_finding(
            state,
            step_id="_session",
            classification="acceptance_blocker"
            if catalog.get("track") == "acceptance"
            else "environment_blocker",
            severity="P1",
            summary=f"Session aborted: {type(exc).__name__}: {exc}",
            human_notes=str(exc),
            evidence=[],
        )
        notes_lines.append(
            f"## aborted\n\n- {type(exc).__name__}: {exc}\n"
        )
    finally:
        # US-04: always restore staged state if a snapshot was taken (any exit path).
        if (
            catalog.get("track") == CONTINUITY_TRACK
            and continuity_restore is not None
            and not continuity_restore_done
            and not dry_run
        ):
            try:
                restore_result = restore_activation(continuity_restore)
                continuity_restore_done = True
                redacted_results = restore_result.get("results") or {}
                if isinstance(redacted_results, dict):
                    cleaned = {}
                    for k, v in redacted_results.items():
                        if isinstance(v, dict):
                            vv = dict(v)
                            if "path" in vv:
                                vv["path"] = _redact_path(str(vv.get("path") or ""), repo_root)
                            cleaned[k] = vv
                        else:
                            cleaned[k] = v
                    redacted_results = cleaned
                _write_json(
                    session_dir / "us04-activation-restore.json",
                    {
                        "ok": restore_result.get("ok"),
                        "error": restore_result.get("error"),
                        "results": redacted_results,
                        "path": _redact_path(
                            str(
                                ((restore_result.get("results") or {}).get("perception") or {}).get(
                                    "path"
                                )
                                or ""
                            ),
                            repo_root,
                        ),
                    },
                )
                if restore_result.get("ok") is not True:
                    _record_finding(
                        state,
                        step_id="_us04_restore",
                        classification="acceptance_blocker",
                        severity="P1",
                        summary=f"US-04 restore failed: {restore_result.get('error')}",
                        human_notes=str(restore_result.get("error")),
                        evidence=["us04-activation-restore.json"],
                    )
                    print(f"  FAIL: US-04 restore failed: {restore_result.get('error')}")
                else:
                    print("  US-04 staged-state restore ok (finally)")
            except Exception as restore_exc:  # noqa: BLE001
                _record_finding(
                    state,
                    step_id="_us04_restore",
                    classification="acceptance_blocker",
                    severity="P1",
                    summary=f"US-04 restore exception: {type(restore_exc).__name__}: {restore_exc}",
                    human_notes=str(restore_exc),
                    evidence=[],
                )
        cleanup_info = _enforce_cleanup(
            state,
            command_timeout_s=command_timeout_s,
            transcript_path=transcript_path,
        )
        state.recording_after = _list_run_directories(repo_root, variables["vehicle_id"])

    ended = _utc_now()

    gates_list = []
    declared = state.catalog.get("gates") or []
    if declared:
        for gate in declared:
            if not isinstance(gate, dict):
                continue
            gate_id = str(gate.get("id"))
            gates_list.append(
                state.gate_results.get(gate_id)
                or {
                    "id": gate_id,
                    "status": "incomplete",
                    "summary": "not evaluated",
                    "evidence": [],
                }
            )
    else:
        gates_list = list(state.gate_results.values())

    machine_preflight = _derive_machine_preflight(state, cleanup_info)

    # Continuity track: compute the single authoritative verdict BEFORE any human
    # or machine representation is written (prevents complete vs pass leakage).
    continuity_block: dict[str, Any] | None = None
    if catalog.get("track") == CONTINUITY_TRACK:
        sequences = []
        catalog_steps = {
            str(s.get("id")): s
            for s in (catalog.get("steps") or [])
            if isinstance(s, dict) and s.get("id") is not None
        }
        hitl_needed = False
        hitl_done = True
        for step in state.steps:
            if not isinstance(step, dict):
                continue
            cat_step = catalog_steps.get(str(step.get("id"))) or {}
            visual_required = bool(
                step.get("visual_required")
                if step.get("visual_required") is not None
                else cat_step.get("visual_required")
            )
            sequences.append(
                {
                    "family_id": step.get("family_id")
                    or cat_step.get("family_id")
                    or (step.get("machine_evidence") or {}).get("family_id"),
                    "status": step.get("status"),
                    "id": step.get("id"),
                    "visual_required": visual_required,
                    "machine_ok": step.get("machine_ok"),
                }
            )
            if visual_required and step.get("required_for_verdict"):
                hitl_needed = True
                human = step.get("human") or {}
                if not (
                    human.get("interactive")
                    and human.get("visual") in {"pass", "fail"}
                ):
                    hitl_done = False
        family_aggregates = aggregate_family_status(sequences)
        identity_recorded = continuity_identity_at_start or collect_identity_bundle(
            repo_root=repo_root,
            catalog_path=catalog_path
            if catalog_path is not None
            else CATALOGS_DIR / "m007-continuity.yaml",
            metrics_ui={
                "commit": (metrics_ui or {}).get("commit") if metrics_ui else None,
                "worktree_state": (metrics_ui or {}).get("worktree_state")
                if metrics_ui
                else None,
                "branch": (metrics_ui or {}).get("branch") if metrics_ui else None,
                "diff_identity": (metrics_ui or {}).get("diff_identity")
                if metrics_ui
                else None,
                "linked_pr": (metrics_ui or {}).get("linked_pr") if metrics_ui else None,
                "path": (metrics_ui or {}).get("path") if metrics_ui else None,
            },
        )
        metrics_required = bool(hitl_needed and hitl_done)
        if isinstance(identity_recorded, dict):
            identity_recorded = dict(identity_recorded)
            identity_recorded["metrics_ui_required"] = metrics_required
            if metrics_ui is not None:
                identity_recorded["metrics_ui"] = {
                    "commit": metrics_ui.get("commit"),
                    "worktree_state": metrics_ui.get("worktree_state"),
                    "branch": metrics_ui.get("branch"),
                    "path": metrics_ui.get("path"),
                    "diff_identity": metrics_ui.get("diff_identity"),
                    "linked_pr": metrics_ui.get("linked_pr"),
                    "named_diff": metrics_ui.get("named_diff"),
                }
        identity_current = collect_identity_bundle(
            repo_root=repo_root,
            catalog_path=catalog_path
            if catalog_path is not None
            else CATALOGS_DIR / "m007-continuity.yaml",
            metrics_ui={
                "commit": (metrics_ui or {}).get("commit") if metrics_ui else None,
                "worktree_state": (metrics_ui or {}).get("worktree_state")
                if metrics_ui
                else None,
                "branch": (metrics_ui or {}).get("branch") if metrics_ui else None,
                "path": (metrics_ui or {}).get("path") if metrics_ui else None,
                "diff_identity": (metrics_ui or {}).get("diff_identity")
                if metrics_ui
                else None,
                "linked_pr": (metrics_ui or {}).get("linked_pr") if metrics_ui else None,
                "named_diff": (metrics_ui or {}).get("named_diff") if metrics_ui else None,
            },
        )
        if isinstance(identity_current, dict):
            identity_current = dict(identity_current)
            identity_current["metrics_ui_required"] = metrics_required
        finalizer_ok, finalizer_reason = finalize_evidence_freshness(
            identity_recorded,
            identity_current,
        )
        safety_ok = bool(
            continuity_preflight
            and continuity_preflight.get("safety_ok")
            and continuity_preflight.get("family_ok")
        )
        if refuse_continuity_execution:
            safety_ok = False
        restore_ok: bool | None = None
        restore_path = session_dir / "us04-activation-restore.json"
        if restore_path.is_file():
            try:
                restore_doc = json.loads(restore_path.read_text(encoding="utf-8"))
                restore_ok = bool(restore_doc.get("ok"))
            except (OSError, json.JSONDecodeError):
                restore_ok = False
        elif continuity_restore is not None:
            restore_ok = False
        cleanup_ok = cleanup_info.get("worker_stopped") is True
        hitl_complete = (not hitl_needed) or hitl_done
        verdict, verdict_reason = derive_continuity_verdict(
            safety_preflight_ok=safety_ok,
            family_aggregates=family_aggregates,
            restore_ok=restore_ok,
            cleanup_ok=cleanup_ok,
            finalizer_ok=finalizer_ok,
            findings=state.findings,
            hitl_complete=hitl_complete,
        )
        result_status = verdict
        incomplete_reason = verdict_reason
        continuity_block = {
            "preflight": continuity_preflight,
            "family_aggregates": family_aggregates,
            "required_family_ids": list(REQUIRED_FAMILY_IDS),
            "identity_recorded": identity_recorded,
            "identity_current": identity_current,
            "finalizer": {"ok": finalizer_ok, "reason": finalizer_reason},
            "restore_ok": restore_ok,
            "hitl_complete": hitl_complete,
            "verdict": verdict,
            "verdict_reason": verdict_reason,
        }
    else:
        result_status, incomplete_reason = _derive_verdict(state)

    notes_lines.extend(
        [
            "## Verdict",
            "",
            f"- result: `{result_status}`",
            f"- reason: {incomplete_reason or '(none)'}",
            f"- findings: {len(state.findings)}",
            f"- cleanup: {cleanup_info}",
            "",
        ]
    )
    _write_text(session_dir / "human-notes.md", "\n".join(notes_lines))
    _write_json(session_dir / "findings.json", state.findings)
    with (session_dir / "findings.jsonl").open("w", encoding="utf-8") as handle:
        for finding in state.findings:
            handle.write(json.dumps(finding, sort_keys=True) + "\n")

    result = {
        "schema": SCHEMA,
        "result": result_status,
        "incomplete_reason": incomplete_reason,
        "execution_mode": execution_mode,
        "machine_preflight": machine_preflight,
        "interactive_human_confirmation": state.interactive_human_confirmation,
        "catalog": {
            "id": catalog.get("id"),
            "track": catalog.get("track"),
            "title": catalog.get("title"),
            "max_frame_lag": _catalog_max_frame_lag(catalog),
        },
        "timestamps": {
            "started_at_utc": _iso(started),
            "ended_at_utc": _iso(ended),
            "local_timezone": list(time.tzname),
        },
        "baseline": baseline,
        "gates": gates_list,
        "ordered_step_outcomes": state.steps,
        "findings": state.findings,
        "cleanup": cleanup_info,
        "recording": {
            "before": state.recording_before,
            "after": state.recording_after,
            "new": sorted(set(state.recording_after) - set(state.recording_before)),
        },
        "session_fingerprint": {
            "baseline": state.baseline_fingerprint,
            "latest": state.latest_fingerprint,
        },
        "browser_view": state.browser_view_meta,
        "view_correlation": state.view_correlation,
        "artifact_manifest": "digests.json",
        "variables": {k: v for k, v in state.variables.items() if k != "src_dir" or v},
    }
    if continuity_block is not None:
        result["continuity"] = continuity_block

    # Write immutable result first, then detached digests that include it.
    _write_json(session_dir / "result.json", result)
    digests = _write_digests(session_dir)

    print()
    print("=" * 72)
    print(f"SESSION COMPLETE: {result_status}")
    print(f"MACHINE PREFLIGHT: {str(machine_preflight['verdict']).upper()}")
    for failure in machine_preflight["failures"]:
        print(f"- {failure['step_id']}: {failure['reason']}")
    if incomplete_reason:
        print(incomplete_reason)
    print(f"Session directory: {session_dir}")
    print(f"Findings: {len(state.findings)}")
    print(f"Digest entries: {len(digests)}")
    print("=" * 72)
    return result


def _capture_view_latest(
    session_dir: Path,
    running_status: Path,
    *,
    vehicle_id: str,
    max_frame_lag: int,
) -> dict[str, Any]:
    try:
        status = json.loads(running_status.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"error": "running status unreadable"}
    expected_vehicle = vehicle_id
    card = extract_vehicle_status(status, expected_vehicle)
    if card is None:
        return {
            "error": (
                f"running status missing exact {STATUS_SCHEMA} card for "
                f"{expected_vehicle!r}"
            )
        }
    layers = card.get("layers") if isinstance(card, dict) else {}
    view = (layers or {}).get("perception_view") if isinstance(layers, dict) else {}
    details = (view or {}).get("details") if isinstance(view, dict) else {}
    if not isinstance(details, dict):
        details = {}
    url = details.get("url")
    if not isinstance(url, str) or not url:
        return {"error": "perception_view.url missing"}
    latest_url = url.rstrip("/") + "/api/latest"
    try:
        import urllib.request

        with urllib.request.urlopen(latest_url, timeout=3) as response:  # noqa: S310
            body = response.read()
            payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        _write_text(session_dir / "view-latest-error.txt", f"{type(exc).__name__}: {exc}")
        return {"url": latest_url, "error": str(exc)}
    out_path = session_dir / "view-publication.json"
    _write_json(out_path, payload)
    evidence = _view_correlation_evidence(
        payload,
        vehicle_id=expected_vehicle,
        max_frame_lag=max_frame_lag,
    )
    ok = evidence["verdict"] == "pass"
    summary = str(evidence["summary"])
    if not ok:
        return {
            "url": latest_url,
            "path": out_path.name,
            "error": summary,
            "correlation": evidence,
        }
    return {
        "url": latest_url,
        "path": out_path.name,
        "http_status": 200,
        "summary": summary,
        "correlation": evidence,
        "vehicle_id": expected_vehicle,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Human-in-the-loop live CLI session runner for M007 evidence.",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=CATALOGS_DIR / "m007-acceptance.yaml",
        help="Path to a session catalog YAML/JSON.",
    )
    parser.add_argument("--session-dir", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    parser.add_argument("--metrics-ui-origin", default=None)
    parser.add_argument("--metrics-ui-repo", type=Path, default=None)
    parser.add_argument("--browser-name", default=None)
    parser.add_argument("--browser-version", default=None)
    parser.add_argument(
        "--operator",
        default=None,
        help="Named operator for the session (required for acceptance pass).",
    )
    parser.add_argument(
        "--auto-driving-linked-pr",
        default=None,
        help="Linked PR for a dirty auto-driving worktree (alternative to captured patch).",
    )
    parser.add_argument(
        "--metrics-ui-linked-pr",
        default=None,
        help="Linked PR for a dirty Metrics UI worktree (alternative to captured patch).",
    )
    parser.add_argument(
        "--browser-view",
        type=Path,
        default=None,
        help=(
            "Path to a cropped browser-view.png. Bound only after view_correlation "
            "establishes the health floor; source mtime must postdate that floor."
        ),
    )
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument(
        "--machine-only",
        action="store_true",
        help=(
            "Execute the live sequence without human prompts; exit 0 only when "
            "machine_preflight passes (formal acceptance remains incomplete)."
        ),
    )
    parser.add_argument(
        "--auto-visual",
        choices=["pass", "fail", "skip", "n/a"],
        default="skip",
        help="Judgment used with --non-interactive (cannot produce acceptance pass).",
    )
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-catalogs", action="store_true")
    return parser


def _result_exit_code(result: Mapping[str, Any], *, machine_only: bool) -> int:
    if machine_only:
        machine_verdict = (result.get("machine_preflight") or {}).get("verdict")
        if machine_verdict == "pass":
            return 0
        if machine_verdict == "fail":
            return 1
        return 2
    if result.get("result") in {"pass", "complete"}:
        return 0
    if result.get("result") == "findings":
        return 1
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.machine_only and args.dry_run:
        parser.error("--machine-only executes the live sequence and cannot use --dry-run")

    if args.list_catalogs:
        for path in sorted(CATALOGS_DIR.glob("*")):
            if path.suffix.lower() in {".yaml", ".yml", ".json"}:
                print(path.name)
        return 0

    catalog_path = args.catalog.expanduser().resolve()
    if not catalog_path.is_file():
        print(f"Catalog not found: {catalog_path}", file=sys.stderr)
        return 2
    catalog = _load_catalog(catalog_path)

    repo_root = args.repo_root.expanduser().resolve()
    if not (repo_root / "cli" / "automa").exists():
        print(f"Repo root does not look like auto-driving: {repo_root}", file=sys.stderr)
        return 2

    if args.session_dir is None:
        stamp = _utc_now().strftime("%Y%m%d-%H%M%S")
        session_dir = Path.cwd() / "live-cli-sessions" / f"{catalog.get('id', 'session')}-{stamp}"
    else:
        session_dir = args.session_dir.expanduser().resolve()

    session_dir.mkdir(parents=True, exist_ok=True)
    # Refuse non-empty after mkdir only if pre-existing files besides none
    existing = [p for p in session_dir.iterdir()]
    if existing:
        print(f"Session directory is not empty: {session_dir}", file=sys.stderr)
        return 2

    _write_text(session_dir / "catalog-source.txt", _redact_path(catalog_path, repo_root))
    _write_json(session_dir / "catalog.json", catalog)

    result = run_session(
        catalog=catalog,
        session_dir=session_dir,
        repo_root=repo_root,
        metrics_ui_origin=args.metrics_ui_origin,
        metrics_ui_repo=args.metrics_ui_repo.expanduser().resolve() if args.metrics_ui_repo else None,
        browser_name=args.browser_name,
        browser_version=args.browser_version,
        prompt=_default_prompt,
        non_interactive=bool(args.machine_only or args.non_interactive or args.dry_run),
        auto_visual=(
            "skip"
            if args.machine_only
            else args.auto_visual if args.non_interactive or args.dry_run else None
        ),
        command_timeout_s=args.timeout_s,
        dry_run=bool(args.dry_run),
        browser_view_path=args.browser_view.expanduser().resolve() if args.browser_view else None,
        operator=args.operator,
        catalog_path=catalog_path,
        auto_driving_linked_pr=args.auto_driving_linked_pr,
        metrics_ui_linked_pr=args.metrics_ui_linked_pr,
        machine_only=bool(args.machine_only),
    )
    return _result_exit_code(result, machine_only=bool(args.machine_only))


if __name__ == "__main__":
    raise SystemExit(main())
