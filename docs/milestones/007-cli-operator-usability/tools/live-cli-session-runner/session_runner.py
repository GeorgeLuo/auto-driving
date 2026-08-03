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


def _git_identity(repo: Path) -> dict[str, Any]:
    def run(args: list[str]) -> str:
        try:
            completed = subprocess.run(
                args, cwd=repo, check=False, capture_output=True, text=True
            )
        except OSError:
            return ""
        return completed.stdout.strip() if completed.returncode == 0 else ""

    status = run(["git", "status", "--porcelain"])
    commit = run(["git", "rev-parse", "HEAD"]) or None
    branch = run(["git", "branch", "--show-current"]) or None
    dirty = bool(status)
    diff_identity = None
    if dirty:
        # Stable identity for dirty trees: hash of porcelain + shortstat.
        shortstat = run(["git", "diff", "--shortstat"])
        material = (status + "\n" + shortstat).encode("utf-8")
        diff_identity = hashlib.sha256(material).hexdigest()
    return {
        "path": str(repo),
        "commit": commit,
        "branch": branch,
        "worktree_state": "dirty" if dirty else "clean",
        "diff_identity": diff_identity,
        "status_porcelain": status.splitlines() if status else [],
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


def _layer_state(status: Mapping[str, Any], layer: str) -> str | None:
    layers = status.get("layers")
    if not isinstance(layers, dict):
        return None
    entry = layers.get(layer)
    if not isinstance(entry, dict):
        return None
    state = entry.get("state")
    return str(state) if state is not None else None


def _worker_details(status: Mapping[str, Any]) -> dict[str, Any]:
    layers = status.get("layers")
    if not isinstance(layers, dict):
        return {}
    worker = layers.get("automation_worker")
    if not isinstance(worker, dict):
        return {}
    details = worker.get("details")
    return details if isinstance(details, dict) else {}


def _authority(status: Mapping[str, Any]) -> dict[str, Any]:
    details = _worker_details(status)
    authority = details.get("authority")
    return authority if isinstance(authority, dict) else {}


def validate_initial_layers(status: Mapping[str, Any]) -> tuple[bool, str]:
    expected = {
        "simulator_server": "reachable",
        "simulator_frontend": "connected",
        "chase_game": "ready",
        "vehicle": "discoverable",
        "passive_capture": "available",
    }
    missing = []
    for layer, want in expected.items():
        got = _layer_state(status, layer)
        if got != want:
            missing.append(f"{layer}={got!r} (want {want!r})")
    if missing:
        return False, "; ".join(missing)
    return True, "initial layers healthy"


def validate_running_layers(status: Mapping[str, Any]) -> tuple[bool, str]:
    expected = {
        "automation_deployment": "deployed",
        "automation_worker": "running",
        "perception_view": "available",
        "passive_capture": "available",
    }
    missing = []
    for layer, want in expected.items():
        got = _layer_state(status, layer)
        if got != want:
            missing.append(f"{layer}={got!r} (want {want!r})")
    if missing:
        return False, "; ".join(missing)
    return True, "running layers healthy"


def validate_stopped_layers(status: Mapping[str, Any]) -> tuple[bool, str]:
    worker = _layer_state(status, "automation_worker")
    view = _layer_state(status, "perception_view")
    deployment = _layer_state(status, "automation_deployment")
    problems = []
    if worker != "stopped":
        problems.append(f"automation_worker={worker!r}")
    if view not in {"stale", "unavailable"}:
        problems.append(f"perception_view={view!r}")
    if deployment not in {"deployed", "invalid"}:  # staged remains deployed
        problems.append(f"automation_deployment={deployment!r}")
    if problems:
        return False, "; ".join(problems)
    return True, "stopped layers healthy"


def validate_authority(status: Mapping[str, Any]) -> tuple[bool, str]:
    authority = _authority(status)
    policy = authority.get("action_policy")
    application = authority.get("control_application")
    control = authority.get("last_frame")
    applied = None
    if isinstance(control, dict):
        ctrl = control.get("control")
        if isinstance(ctrl, dict):
            applied = ctrl.get("applied")
    # Prefer explicit authority fields; fall back to nested control.applied.
    if policy != "observe_only":
        return False, f"action_policy={policy!r}"
    if application != "not_applied":
        return False, f"control_application={application!r}"
    if applied is True:
        return False, "last_frame.control.applied is true"
    recording = authority.get("recording")
    if recording is True:
        return False, "authority.recording is true"
    return True, "observe_only / not_applied"


def validate_view_latest(payload: Mapping[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "view /api/latest missing or not an object"
    # Accept a few common shapes.
    frame_id = (
        payload.get("frame_id")
        or payload.get("latest_frame_id")
        or payload.get("camera_frame_id")
    )
    perception_frame_id = (
        payload.get("perception_frame_id")
        or payload.get("latest_perception_frame_id")
        or frame_id
    )
    if payload.get("error"):
        return False, f"view fetch error: {payload.get('error')}"
    # Nested publication styles
    if frame_id is None and isinstance(payload.get("frame"), dict):
        frame_id = payload["frame"].get("frame_id")
    if perception_frame_id is None and isinstance(payload.get("perception"), dict):
        perception_frame_id = payload["perception"].get("frame_id")
    if not frame_id or not perception_frame_id:
        # Some servers embed ids under health/status keys.
        status = payload.get("status")
        if isinstance(status, dict):
            frame_id = frame_id or status.get("frame_id") or status.get("latest_frame_id")
            perception_frame_id = (
                perception_frame_id
                or status.get("perception_frame_id")
                or status.get("latest_perception_frame_id")
            )
    if not frame_id or not perception_frame_id:
        return False, "view payload lacks camera/perception frame ids"
    if str(frame_id) != str(perception_frame_id):
        return False, f"frame ids disagree: camera={frame_id!r} perception={perception_frame_id!r}"
    if not str(frame_id).strip():
        return False, "frame id empty"
    return True, f"correlated frame_id={frame_id}"


def validate_recording_scan(
    before: Sequence[str], after: Sequence[str]
) -> tuple[bool, str]:
    new = sorted(set(after) - set(before))
    if new:
        return False, f"new automation run directories: {new}"
    return True, "no new automation run directories"


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
    elapsed_ms = int((time.monotonic() - started) * 1000)
    red_out = _redact(stdout, cwd)
    red_err = _redact(stderr, cwd)
    _write_text(stdout_path, red_out)
    _write_text(stderr_path, red_err)
    _append_text(
        transcript_path,
        f"$ {_format_command(argv)}\n{red_out}{red_err}exit={exit_code} elapsed_ms={elapsed_ms}\n",
    )
    return CommandOutcome(
        argv=list(argv),
        command=_format_command(argv),
        exit_code=exit_code,
        elapsed_ms=elapsed_ms,
        stdout_path=str(stdout_path.relative_to(session_dir)),
        stderr_path=str(stderr_path.relative_to(session_dir)),
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
    recording_before: list[str] = field(default_factory=list)
    recording_after: list[str] = field(default_factory=list)
    interactive_human_confirmation: bool = False
    dry_run: bool = False
    non_interactive: bool = False


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
    status_path: Path | None = None,
    view_path: Path | None = None,
    before_runs: Sequence[str] | None = None,
    after_runs: Sequence[str] | None = None,
) -> tuple[bool, str]:
    if name == "initial_layers":
        if status_path is None or not status_path.is_file():
            return False, "initial-status.json missing"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return validate_initial_layers(status)
    if name == "running_layers":
        if status_path is None or not status_path.is_file():
            return False, "running-status.json missing"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return validate_running_layers(status)
    if name == "stopped_layers":
        if status_path is None or not status_path.is_file():
            return False, "stopped-status.json missing"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return validate_stopped_layers(status)
    if name == "authority":
        if status_path is None or not status_path.is_file():
            return False, "status json missing for authority"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        return validate_authority(status)
    if name == "view_correlation":
        if view_path is None or not view_path.is_file():
            return False, "view-publication.json missing"
        try:
            payload = json.loads(view_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"view-publication.json is not JSON: {exc}"
        return validate_view_latest(payload)
    if name == "default_recording":
        return validate_recording_scan(before_runs or [], after_runs or [])
    return False, f"unknown validator {name!r}"


def _derive_verdict(state: SessionState) -> tuple[str, str | None]:
    track = state.catalog.get("track")
    if track == "acceptance":
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
            browser = baseline.get("browser") or {}
            if not browser.get("name") or not browser.get("version"):
                return "incomplete", "Acceptance pass requires browser name and version."
            repos = baseline.get("repositories") or {}
            metrics = repos.get("metrics_ui")
            if not isinstance(metrics, dict) or not metrics.get("commit"):
                return "incomplete", "Acceptance pass requires --metrics-ui-repo identity."
            auto = repos.get("auto_driving") or {}
            if auto.get("worktree_state") == "dirty" and not auto.get("diff_identity"):
                return "incomplete", "Dirty auto-driving worktree lacks diff_identity."
        # Screenshot optional path check if catalog requires it
        if not (state.session_dir / "browser-view.png").is_file():
            # Require explicit path or file for pass
            return "incomplete", "Acceptance pass requires browser-view.png in the session directory."

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
        "error": None,
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
        status_path = state.session_dir / "cleanup-status.json"
        raw = (step_dir / "cmd-01.stdout.txt").read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
            _write_json(status_path, payload)
            ok, summary = validate_stopped_layers(payload)
            cleanup["worker_stopped"] = ok
            cleanup["stopped_layers_summary"] = summary
            details = _worker_details(payload)
            pid = details.get("pid")
            if isinstance(pid, int):
                cleanup["pid"] = pid
                cleanup["pid_alive"] = _pid_alive(pid)
        except json.JSONDecodeError as exc:
            cleanup["error"] = f"cleanup status not JSON: {exc}"
            cleanup["worker_stopped"] = False
    except Exception as exc:  # noqa: BLE001
        cleanup["error"] = f"{type(exc).__name__}: {exc}"
        cleanup["worker_stopped"] = False

    if cleanup.get("worker_stopped") is not True or cleanup.get("pid_alive") is True:
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
) -> dict[str, Any]:
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

    state = SessionState(
        catalog=catalog,
        session_dir=session_dir,
        repo_root=repo_root,
        variables=variables,
        execution_mode=execution_mode,
        session_id=session_id,
        dry_run=dry_run,
        non_interactive=non_interactive,
    )

    auto_driving = _git_identity(repo_root)
    metrics_ui = _git_identity(metrics_ui_repo) if metrics_ui_repo else None
    state.recording_before = _list_run_directories(repo_root, variables["vehicle_id"])

    baseline = {
        "recorded_at_utc": _iso(started),
        "operating_system": platform.platform(),
        "python": sys.version.split()[0],
        "browser": {"name": browser_name, "version": browser_version},
        "metrics_ui_origin": variables["metrics_ui_origin"],
        "repositories": {"auto_driving": auto_driving, "metrics_ui": metrics_ui},
        "vehicle_id": variables["vehicle_id"],
        "execution_mode": execution_mode,
        "recording_before": state.recording_before,
    }
    _write_json(session_dir / "baseline.json", baseline)
    if browser_view_path is not None and browser_view_path.is_file():
        target = session_dir / "browser-view.png"
        target.write_bytes(browser_view_path.read_bytes())

    notes_lines = [
        f"# Session notes — {catalog.get('id')}",
        "",
        f"- started_at_utc: `{_iso(started)}`",
        f"- execution_mode: `{execution_mode}`",
        f"- track: `{catalog.get('track')}`",
        "",
    ]

    cleanup_info: dict[str, Any] = {"attempted": False, "needed": False}

    try:
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
                    }
                    command_outcomes.append(payload)
                    # Print a short excerpt for the operator only.
                    excerpt = (step_dir / f"cmd-{index:02d}.stdout.txt").read_text(encoding="utf-8")[:1200]
                    print(excerpt)
                    print(f"exit={outcome.exit_code} elapsed_ms={outcome.elapsed_ms}")
                    if "automation" in rendered and "run" in rendered and outcome.exit_code == 0:
                        state.worker_may_exist = True
                    if "automation" in rendered and "run" in rendered and outcome.exit_code != 0:
                        # partial startup still needs cleanup attempt
                        state.worker_may_exist = True
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
                    command_outcomes.append(
                        {
                            "argv": outcome.argv,
                            "command": outcome.command,
                            "exit_code": outcome.exit_code,
                            "elapsed_ms": outcome.elapsed_ms,
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
                        details = _worker_details(parsed)
                        pid = details.get("pid")
                        if isinstance(pid, int):
                            state.last_worker_pid = pid
                            if _layer_state(parsed, "automation_worker") == "running":
                                state.worker_may_exist = True
                    except json.JSONDecodeError as exc:
                        step_status = "fail"
                        machine_ok = False
                        validator_notes.append(f"JSON capture invalid: {exc}")
                        _write_text(json_out.with_suffix(json_out.suffix + ".raw.txt"), raw)

                    if step.get("capture_view_latest") and json_out.is_file():
                        view_meta = _capture_view_latest(session_dir, json_out)
                        if not view_meta or view_meta.get("error"):
                            machine_ok = False
                            step_status = "fail"
                            validator_notes.append(
                                f"view latest failed: {view_meta}"
                            )
                        else:
                            validator_notes.append(f"view_latest={view_meta}")

                # Machine validators declared on the step
                for validator in step.get("machine_validators") or []:
                    name = str(validator)
                    status_path = None
                    view_path = session_dir / "view-publication.json"
                    if name in {"initial_layers"}:
                        status_path = session_dir / "initial-status.json"
                    elif name in {"running_layers", "authority"}:
                        status_path = session_dir / "running-status.json"
                    elif name in {"stopped_layers"}:
                        status_path = session_dir / "stopped-status.json"
                    if name == "default_recording":
                        state.recording_after = _list_run_directories(
                            repo_root, variables["vehicle_id"]
                        )
                    ok, summary = _run_machine_validator(
                        name,
                        status_path=status_path,
                        view_path=view_path if view_path.is_file() else None,
                        before_runs=state.recording_before,
                        after_runs=state.recording_after,
                    )
                    validator_notes.append(f"{name}: {summary}")
                    if not ok:
                        machine_ok = False
                        step_status = "fail"

            if validator_notes:
                machine_summary = "; ".join(validator_notes) or machine_summary

            judgment = _prompt_judgment(
                step=step,
                prompt=prompt,
                non_interactive=non_interactive,
                auto_visual=auto_visual,
            )
            if judgment.interactive and judgment.visual in {"pass", "fail"}:
                state.interactive_human_confirmation = True

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


def _capture_view_latest(session_dir: Path, running_status: Path) -> dict[str, Any] | None:
    try:
        status = json.loads(running_status.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"error": "running status unreadable"}
    details = ((_worker := (status.get("layers") or {}).get("perception_view") or {}).get("details") or {})
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
    ok, summary = validate_view_latest(payload)
    if not ok:
        return {"url": latest_url, "path": out_path.name, "error": summary}
    return {"url": latest_url, "path": out_path.name, "http_status": 200, "summary": summary}


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
        "--browser-view",
        type=Path,
        default=None,
        help="Path to a cropped browser-view.png to copy into the session (required for acceptance pass).",
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

    _write_text(session_dir / "catalog-source.txt", str(catalog_path))
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
    )
    if result.get("result") in {"pass", "complete"}:
        return 0
    if result.get("result") == "findings":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
