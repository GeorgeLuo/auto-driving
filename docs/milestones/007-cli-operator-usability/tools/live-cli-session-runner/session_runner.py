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
    "df6cd2ba50241371de0558a00d7f00aa11d9d6b7a49ac419d7b812fed880092c"
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


# Continuous Chase publishes new camera frames while perception (~80ms+) finishes.
# Product overlay.status is "current" only on exact frame-id match; otherwise
# "stale" with frame_lag. Acceptance treats bounded lag as a real gate (not
# poll-until-green): pipeline lag is expected; unbounded lag is not.
DEFAULT_VIEW_MAX_FRAME_LAG = 24


def validate_view_latest(
    payload: Mapping[str, Any] | None,
    *,
    vehicle_id: str,
    max_frame_lag: int = DEFAULT_VIEW_MAX_FRAME_LAG,
) -> tuple[bool, str]:
    """Validate real Automa perception-view /api/latest publication.

    Gate is lag-bounded correlation, not a single-shot poll for status=current:

    - ``current``: overlay.source_frame_id must equal frame.frame_id (lag 0).
    - ``stale``: require a non-empty source_frame_id and integer frame_lag in
      ``[1, max_frame_lag]`` (observed live flicker often 12–17 under Chase).
    - ``pending`` / missing perception / lag above threshold: fail closed.

    Polling can diagnose Live green/red; the pass criterion is this lag bound.
    """

    if not isinstance(payload, dict):
        return False, "view /api/latest missing or not an object"
    if payload.get("error"):
        return False, f"view fetch error: {payload.get('error')}"
    if payload.get("schema") != PUBLICATION_SCHEMA:
        return False, f"schema={payload.get('schema')!r} (want {PUBLICATION_SCHEMA!r})"
    if str(payload.get("vehicle_id")) != vehicle_id:
        return False, f"vehicle_id={payload.get('vehicle_id')!r} (want {vehicle_id!r})"

    frame = payload.get("frame")
    overlay = payload.get("overlay")
    perception = payload.get("perception")
    cycle = payload.get("cycle")
    control = payload.get("control")

    if not isinstance(frame, dict) or not frame.get("frame_id"):
        return False, "frame.frame_id missing"
    if not isinstance(overlay, dict):
        return False, "overlay object missing"
    status = overlay.get("status")
    source_frame_id = overlay.get("source_frame_id")
    frame_lag = overlay.get("frame_lag")
    if status == "current":
        if source_frame_id != frame.get("frame_id"):
            return False, (
                f"overlay.status=current but source_frame_id={source_frame_id!r} "
                f"!= frame.frame_id={frame.get('frame_id')!r}"
            )
        correlation = f"current (exact match) for {frame.get('frame_id')}"
    elif status == "stale":
        # Real continuous-chase gate: perception trails camera by a few frames.
        if not source_frame_id:
            return False, "overlay.status=stale but source_frame_id missing"
        if source_frame_id == frame.get("frame_id"):
            return False, (
                "overlay.status=stale but source_frame_id matches frame "
                "(inconsistent publication)"
            )
        if not isinstance(frame_lag, int) or isinstance(frame_lag, bool):
            return False, f"overlay.frame_lag={frame_lag!r} (want int lag for stale)"
        if frame_lag < 1:
            return False, f"overlay.frame_lag={frame_lag} (want >= 1 when stale)"
        if frame_lag > max_frame_lag:
            return False, (
                f"overlay.frame_lag={frame_lag} exceeds max_frame_lag={max_frame_lag} "
                f"(camera={frame.get('frame_id')!r} overlay_source={source_frame_id!r})"
            )
        correlation = (
            f"stale within lag budget ({frame_lag}<={max_frame_lag}) "
            f"camera={frame.get('frame_id')} source={source_frame_id}"
        )
    else:
        return False, (
            f"overlay.status={status!r} (want current, or stale with "
            f"frame_lag in 1..{max_frame_lag})"
        )
    if not isinstance(perception, dict) or not perception:
        return False, "perception result absent"
    if not isinstance(cycle, dict):
        return False, "cycle object missing"
    if cycle.get("action_policy") != "observe_only":
        return False, f"cycle.action_policy={cycle.get('action_policy')!r}"
    if cycle.get("control_application") != "not_applied":
        return False, f"cycle.control_application={cycle.get('control_application')!r}"
    if not isinstance(control, dict):
        return False, "control object missing"
    if control.get("applied") is not False:
        return False, f"control.applied={control.get('applied')!r} (want false)"
    return True, correlation


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
) -> tuple[bool, str]:
    if name == "initial_layers":
        if status_path is None or not status_path.is_file():
            return False, "initial-status.json missing"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return validate_initial_layers(status, vehicle_id=vehicle_id)
    if name == "staged_layers":
        if status_path is None or not status_path.is_file():
            return False, "staged-status.json missing"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return validate_staged_layers(
            status,
            vehicle_id=vehicle_id,
            perception_algorithm=perception_algorithm,
        )
    if name == "running_layers":
        if status_path is None or not status_path.is_file():
            return False, "running-status.json missing"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return validate_running_layers(status, vehicle_id=vehicle_id)
    if name == "stopped_layers":
        if status_path is None or not status_path.is_file():
            return False, "stopped-status.json missing"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return validate_stopped_layers(status, vehicle_id=vehicle_id)
    if name == "authority":
        if status_path is None or not status_path.is_file():
            return False, "status json missing for authority"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return validate_authority(status, vehicle_id=vehicle_id)
    if name == "view_correlation":
        if view_path is None or not view_path.is_file():
            return False, "view-publication.json missing"
        try:
            payload = json.loads(view_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"view-publication.json is not JSON: {exc}"
        return validate_view_latest(payload, vehicle_id=vehicle_id)
    if name == "default_recording":
        return validate_recording_scan(before_runs or [], after_runs or [])
    if name == "preservation":
        return validate_preservation(baseline_fingerprint, current_fingerprint)
    return False, f"unknown validator {name!r}"


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
    """Write digests.json for all files except digests.json itself."""
    artifacts: list[dict[str, str]] = []
    for path in sorted(session_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "digests.json":
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
    """Acceptance live mutations require precondition + initial + staging pass."""

    if state.catalog.get("track") != "acceptance":
        return True, "non-acceptance track"
    if state.dry_run:
        return True, "dry-run"
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
) -> dict[str, Any]:
    started = _utc_now()
    session_id = started.strftime("%Y%m%d%H%M%S")
    if dry_run:
        execution_mode = "dry_run"
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

    # Formal acceptance: never execute any CLI from a noncanonical catalog.
    # Verdict already fails closed; this is the safety owner (execution order).
    refuse_acceptance_execution = (
        catalog.get("track") == "acceptance" and not canonical and not dry_run
    )

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
        else:
            # Acceptance: stop any pre-existing worker before the catalog baseline.
            if catalog.get("track") == "acceptance" and not dry_run:
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
                        summary="Precondition cleanup failed before acceptance baseline",
                        human_notes=str(state.precondition_cleanup.get("error")),
                        evidence=[
                            "steps/_precondition_cleanup/",
                            "precondition-cleanup.json",
                        ],
                    )

            for step in catalog.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                step_id = str(step.get("id") or f"step-{len(state.steps)+1}")
                print()
                print("=" * 72)
                print(f"STEP {step_id}  ({step.get('kind') or 'command'})")
                print(f"Question: {step.get('question') or ''}")
                print(f"Safety: {step.get('safety') or 'unspecified'}")
                if step.get("note"):
                    print(f"Note: {step['note']}")
                print("=" * 72)

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

                # Hard safety: never execute live_mutation when prerequisites failed.
                if (
                    not dry_run
                    and step.get("safety") == "live_mutation"
                    and step.get("kind") != "baseline"
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
                        ok, summary = _run_machine_validator(
                            name,
                            vehicle_id=variables["vehicle_id"],
                            status_path=status_path,
                            view_path=view_path if view_path.is_file() else None,
                            before_runs=state.recording_before,
                            after_runs=state.recording_after,
                            baseline_fingerprint=state.baseline_fingerprint,
                            current_fingerprint=state.latest_fingerprint,
                            perception_algorithm=variables["perception_algorithm"],
                        )
                        validator_notes.append(f"{name}: {summary}")
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

                if judgment.visual == "fail":
                    step_status = "fail"
                elif judgment.visual == "skip" and step.get("required_for_verdict"):
                    step_status = "skip"
                elif step_status == "ok" and judgment.visual in {"pass", "n/a"} and machine_ok:
                    step_status = "pass"
                elif not machine_ok:
                    step_status = "fail"

                evidence_refs = [f"steps/{step_id}/envelope.json"]
                for outcome in command_outcomes:
                    if outcome.get("stdout_path"):
                        evidence_refs.append(str(outcome["stdout_path"]))

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

                envelope = {
                    "id": step_id,
                    "kind": step.get("kind"),
                    "question": step.get("question"),
                    "safety": step.get("safety"),
                    "primary_cue": step.get("primary_cue"),
                    "status": step_status,
                    "machine_summary": machine_summary,
                    "machine_ok": machine_ok,
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
        cleanup_info = _enforce_cleanup(
            state,
            command_timeout_s=command_timeout_s,
            transcript_path=transcript_path,
        )
        state.recording_after = _list_run_directories(repo_root, variables["vehicle_id"])

    ended = _utc_now()
    result_status, incomplete_reason = _derive_verdict(state)

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
        "interactive_human_confirmation": state.interactive_human_confirmation,
        "catalog": {
            "id": catalog.get("id"),
            "track": catalog.get("track"),
            "title": catalog.get("title"),
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
        "artifact_manifest": "digests.json",
        "variables": {k: v for k, v in state.variables.items() if k != "src_dir" or v},
    }
    # Write immutable result first, then detached digests that include it.
    _write_json(session_dir / "result.json", result)
    digests = _write_digests(session_dir)

    print()
    print("=" * 72)
    print(f"SESSION COMPLETE: {result_status}")
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
    ok, summary = validate_view_latest(payload, vehicle_id=expected_vehicle)
    if not ok:
        return {"url": latest_url, "path": out_path.name, "error": summary}
    return {
        "url": latest_url,
        "path": out_path.name,
        "http_status": 200,
        "summary": summary,
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
        "--auto-visual",
        choices=["pass", "fail", "skip", "n/a"],
        default="skip",
        help="Judgment used with --non-interactive (cannot produce acceptance pass).",
    )
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-catalogs", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
        non_interactive=bool(args.non_interactive or args.dry_run),
        auto_visual=args.auto_visual if args.non_interactive or args.dry_run else None,
        command_timeout_s=args.timeout_s,
        dry_run=bool(args.dry_run),
        browser_view_path=args.browser_view.expanduser().resolve() if args.browser_view else None,
        operator=args.operator,
        catalog_path=catalog_path,
        auto_driving_linked_pr=args.auto_driving_linked_pr,
        metrics_ui_linked_pr=args.metrics_ui_linked_pr,
    )
    if result.get("result") in {"pass", "complete"}:
        return 0
    if result.get("result") == "findings":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
