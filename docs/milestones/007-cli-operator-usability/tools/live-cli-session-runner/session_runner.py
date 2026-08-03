#!/usr/bin/env python3
"""Human-in-the-loop live CLI session runner.

Runs a YAML catalog of operator steps, captures machine evidence, prompts for
human visual judgment and notes, and writes a structured session artifact that
agents and reviewers can consume.

This is evidence tooling for M007 live acceptance and exploratory discovery.
It does not change Automa product behavior.
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


# ---------------------------------------------------------------------------
# Small helpers
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


def _redact(text: str, repo_root: Path) -> str:
    """Redact absolute repo prefixes for reviewable transcripts."""
    root = str(repo_root.resolve())
    home = str(Path.home())
    out = text.replace(root, "<repo>")
    out = out.replace(home, "<home>")
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised when PyYAML missing
        raise SystemExit(
            "PyYAML is required for the session runner "
            f"({exc}). Install pyyaml or use a catalog already converted to JSON."
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
    schema = data.get("schema")
    if schema != CATALOG_SCHEMA:
        raise SystemExit(f"Unsupported catalog schema {schema!r}; expected {CATALOG_SCHEMA}")
    return data


def _format_command(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def _substitute(value: str, variables: Mapping[str, str]) -> str:
    out = value
    for key, replacement in variables.items():
        out = out.replace("{" + key + "}", replacement)
    return out


def _substitute_argv(argv: Sequence[str], variables: Mapping[str, str]) -> list[str]:
    return [_substitute(part, variables) for part in argv]


def _git_identity(repo: Path) -> dict[str, Any]:
    def run(args: list[str]) -> str:
        try:
            completed = subprocess.run(
                args,
                cwd=repo,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return ""
        return completed.stdout.strip() if completed.returncode == 0 else ""

    status = run(["git", "status", "--porcelain"])
    return {
        "path": str(repo),
        "commit": run(["git", "rev-parse", "HEAD"]) or None,
        "branch": run(["git", "branch", "--show-current"]) or None,
        "worktree_state": "dirty" if status else "clean",
        "status_porcelain": status.splitlines() if status else [],
    }


def _extract_http_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s\"']+", text)


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------


PromptFn = Callable[[str], str]


def _default_prompt(message: str) -> str:
    try:
        return input(message)
    except EOFError:
        return ""


@dataclass
class HumanJudgment:
    visual: str  # pass | fail | skip | n/a
    notes: str
    finding: bool
    finding_severity: str | None = None
    finding_summary: str | None = None


def _prompt_judgment(
    *,
    step: Mapping[str, Any],
    prompt: PromptFn,
    non_interactive: bool,
    auto_visual: str | None,
) -> HumanJudgment:
    visual_required = bool(step.get("visual_required"))
    if non_interactive:
        # Machine-only steps auto-pass; visual gates honor --auto-visual (default skip).
        if visual_required:
            visual = auto_visual or "skip"
        else:
            visual = "pass" if (auto_visual in {None, "skip", "n/a", "pass"}) else auto_visual
        return HumanJudgment(visual=visual, notes="non-interactive session", finding=False)

    print()
    print(f"Primary cue: {step.get('primary_cue') or '(none stated)'}")
    if step.get("visual_prompt"):
        print(f"Visual check: {step['visual_prompt']}")
    if visual_required:
        raw = prompt("Visual result [p]ass / [f]ail / [s]kip: ").strip().lower()
        visual = {"p": "pass", "pass": "pass", "f": "fail", "fail": "fail", "s": "skip", "skip": "skip"}.get(
            raw, "skip"
        )
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
    )


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


@dataclass
class CommandOutcome:
    argv: list[str]
    command: str
    exit_code: int
    elapsed_ms: int
    stdout_path: str
    stderr_path: str
    stdout_excerpt: str
    stderr_excerpt: str


def _run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    session_dir: Path,
    step_dir: Path,
    index: int,
    timeout_s: float | None,
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
        stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "" if isinstance(exc.stderr, str) else "") + f"\nTIMEOUT after {timeout_s}s\n"
    except OSError as exc:
        exit_code = 127
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}\n"
    elapsed_ms = int((time.monotonic() - started) * 1000)
    _write_text(stdout_path, _redact(stdout, cwd))
    _write_text(stderr_path, _redact(stderr, cwd))
    return CommandOutcome(
        argv=list(argv),
        command=_format_command(argv),
        exit_code=exit_code,
        elapsed_ms=elapsed_ms,
        stdout_path=str(stdout_path.relative_to(session_dir)),
        stderr_path=str(stderr_path.relative_to(session_dir)),
        stdout_excerpt=_redact(stdout, cwd)[:1200],
        stderr_excerpt=_redact(stderr, cwd)[:800],
    )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass
class SessionState:
    catalog: dict[str, Any]
    session_dir: Path
    repo_root: Path
    variables: dict[str, str]
    findings: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    gate_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    finding_counter: int = 0


def _next_finding_id(state: SessionState) -> str:
    state.finding_counter += 1
    return f"M007-LIVE-{state.finding_counter:03d}"


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


def _derive_verdict(state: SessionState) -> tuple[str, str | None]:
    track = state.catalog.get("track")
    if track != "acceptance":
        if any(f.get("classification") in {"acceptance_blocker", "usability_defect"} for f in state.findings):
            return "findings", "Exploratory session recorded findings."
        if any(step.get("human", {}).get("visual") == "fail" for step in state.steps):
            return "findings", "Exploratory session recorded human fail judgments."
        if any(step.get("status") == "blocked" for step in state.steps):
            return "incomplete", "Exploratory session had blocked steps."
        return "complete", None

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
    if any(
        f.get("classification") == "usability_defect" and f.get("severity") in {"P1", "P2"}
        for f in state.findings
    ):
        return "findings", "Blocking usability defect recorded."
    return "pass", None


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
        # Fail sticks; pass can upgrade skip/incomplete absence only.
        if previous and previous.get("status") == "fail":
            continue
        if previous and previous.get("status") == "pass" and status != "fail":
            continue
        state.gate_results[gate_id] = {
            "id": gate_id,
            "status": status,
            "summary": summary,
            "evidence": list(evidence),
        }


def _capture_view_latest(session_dir: Path, running_status: Path) -> dict[str, Any] | None:
    try:
        status = json.loads(running_status.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    layers = status.get("layers") if isinstance(status, dict) else None
    view = (layers or {}).get("perception_view") if isinstance(layers, dict) else None
    details = (view or {}).get("details") if isinstance(view, dict) else {}
    url = details.get("url") if isinstance(details, dict) else None
    if not isinstance(url, str) or not url:
        return None
    latest_url = url.rstrip("/") + "/api/latest"
    try:
        import urllib.request

        with urllib.request.urlopen(latest_url, timeout=3) as response:  # noqa: S310 - local loopback
            body = response.read()
            payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - capture any local view failure
        error_path = session_dir / "view-latest-error.txt"
        _write_text(error_path, f"{type(exc).__name__}: {exc}")
        return {"url": latest_url, "error": str(exc), "error_path": error_path.name}
    out_path = session_dir / "view-publication.json"
    _write_json(out_path, payload)
    return {"url": latest_url, "path": out_path.name, "http_status": 200}


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
) -> dict[str, Any]:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "steps").mkdir(exist_ok=True)
    (session_dir / "transcripts").mkdir(exist_ok=True)

    started = _utc_now()
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
    )

    # Baseline identity
    auto_driving = _git_identity(repo_root)
    metrics_ui = _git_identity(metrics_ui_repo) if metrics_ui_repo else None
    baseline = {
        "recorded_at_utc": _iso(started),
        "operating_system": platform.platform(),
        "python": sys.version.split()[0],
        "browser": {"name": browser_name, "version": browser_version},
        "metrics_ui_origin": variables["metrics_ui_origin"],
        "repositories": {
            "auto_driving": auto_driving,
            "metrics_ui": metrics_ui,
        },
        "vehicle_id": variables["vehicle_id"],
    }
    _write_json(session_dir / "baseline.json", baseline)

    notes_lines = [
        f"# Session notes — {catalog.get('id')}",
        "",
        f"- started_at_utc: `{_iso(started)}`",
        f"- track: `{catalog.get('track')}`",
        f"- catalog: `{catalog.get('id')}`",
        "",
    ]

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

        # Optional variable prompt
        requires = step.get("requires_prompt")
        if isinstance(requires, str) and requires:
            if non_interactive and state.variables.get(requires):
                pass
            elif non_interactive:
                raise SystemExit(f"Non-interactive session missing required variable {requires!r}")
            else:
                help_text = step.get("requires_prompt_help") or requires
                value = prompt(f"{help_text}: ").strip()
                state.variables[requires] = value

        step_dir = session_dir / "steps" / step_id
        step_dir.mkdir(parents=True, exist_ok=True)
        command_outcomes: list[dict[str, Any]] = []
        combined_stdout: list[str] = []
        step_status = "ok"
        machine_summary = ""

        if dry_run:
            machine_summary = "dry-run: commands not executed"
            for index, argv in enumerate(step.get("commands") or []):
                if not isinstance(argv, list):
                    continue
                rendered = _substitute_argv([str(x) for x in argv], state.variables)
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
                rendered = _substitute_argv([str(x) for x in argv], state.variables)
                print(f"\n$ {_format_command(rendered)}")
                outcome = _run_command(
                    rendered,
                    cwd=repo_root,
                    session_dir=session_dir,
                    step_dir=step_dir,
                    index=index,
                    timeout_s=command_timeout_s,
                )
                payload = {
                    "argv": outcome.argv,
                    "command": outcome.command,
                    "exit_code": outcome.exit_code,
                    "elapsed_ms": outcome.elapsed_ms,
                    "stdout_path": outcome.stdout_path,
                    "stderr_path": outcome.stderr_path,
                    "stdout_excerpt": outcome.stdout_excerpt,
                    "stderr_excerpt": outcome.stderr_excerpt,
                }
                command_outcomes.append(payload)
                combined_stdout.append(outcome.stdout_excerpt)
                print(outcome.stdout_excerpt)
                if outcome.stderr_excerpt.strip():
                    print(outcome.stderr_excerpt, file=sys.stderr)
                print(f"exit={outcome.exit_code} elapsed_ms={outcome.elapsed_ms}")
                if expect_exit is not None and outcome.exit_code != int(expect_exit) and not allow_nonzero:
                    step_status = "fail"
                elif outcome.exit_code != 0 and not allow_nonzero and expect_exit is None:
                    step_status = "fail"

            # Optional JSON capture
            capture = step.get("capture_json")
            if isinstance(capture, dict) and isinstance(capture.get("command"), list):
                rendered = _substitute_argv([str(x) for x in capture["command"]], state.variables)
                json_out = session_dir / str(capture.get("path") or f"{step_id}.json")
                print(f"\n$ {_format_command(rendered)}  > {json_out.name}")
                try:
                    completed = subprocess.run(
                        rendered,
                        cwd=repo_root,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=command_timeout_s,
                    )
                    raw = completed.stdout or ""
                    # Ensure pure JSON on disk when possible.
                    try:
                        parsed = json.loads(raw)
                        _write_json(json_out, parsed)
                    except json.JSONDecodeError:
                        _write_text(json_out.with_suffix(json_out.suffix + ".raw.txt"), raw)
                        step_status = "fail"
                        machine_summary = "JSON capture was not valid JSON"
                    else:
                        if step.get("capture_view_latest"):
                            view_meta = _capture_view_latest(session_dir, json_out)
                            if view_meta:
                                machine_summary = f"view_latest={view_meta}"
                except (OSError, subprocess.TimeoutExpired) as exc:
                    step_status = "fail"
                    machine_summary = f"JSON capture failed: {exc}"

        # Human judgment
        judgment = _prompt_judgment(
            step=step,
            prompt=prompt,
            non_interactive=non_interactive,
            auto_visual=auto_visual,
        )

        if judgment.visual == "fail":
            step_status = "fail"
        elif judgment.visual == "skip" and step.get("required_for_verdict"):
            step_status = "skip"
        elif step_status == "ok" and judgment.visual in {"pass", "n/a"}:
            step_status = "pass"

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
                    if state.catalog.get("track") == "acceptance" and judgment.visual == "fail"
                    else "usability_defect"
                ),
                severity=judgment.finding_severity or "P3",
                summary=judgment.finding_summary or "operator-reported finding",
                human_notes=judgment.notes,
                evidence=evidence_refs,
                repro=[o.get("command", "") for o in command_outcomes if o.get("command")],
            )

        # Gate updates
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

        envelope = {
            "id": step_id,
            "kind": step.get("kind"),
            "question": step.get("question"),
            "safety": step.get("safety"),
            "primary_cue": step.get("primary_cue"),
            "status": step_status,
            "machine_summary": machine_summary,
            "commands": command_outcomes,
            "human": {
                "visual": judgment.visual,
                "notes": judgment.notes,
                "finding_requested": judgment.finding,
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
                "",
            ]
        )

        # Combined transcript append
        transcript_path = session_dir / "transcripts" / "cli-transcript.txt"
        with transcript_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n##### {step_id}\n")
            for outcome in command_outcomes:
                handle.write(f"$ {outcome.get('command')}\n")
                handle.write(f"{outcome.get('stdout_excerpt') or ''}\n")
                handle.write(f"exit={outcome.get('exit_code')}\n")

    ended = _utc_now()
    result_status, incomplete_reason = _derive_verdict(state)
    gates_list = []
    declared = state.catalog.get("gates") or []
    if declared:
        for gate in declared:
            if not isinstance(gate, dict):
                continue
            gate_id = str(gate.get("id"))
            gates_list.append(state.gate_results.get(gate_id) or {
                "id": gate_id,
                "status": "incomplete",
                "summary": "not evaluated",
                "evidence": [],
            })
    else:
        gates_list = list(state.gate_results.values())

    # Digests for stable files
    artifacts: list[dict[str, Any]] = []
    for path in sorted(session_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "result.json":
            continue
        rel = str(path.relative_to(session_dir))
        artifacts.append({"path": rel, "sha256": _sha256_file(path)})

    result = {
        "schema": SCHEMA,
        "result": result_status,
        "incomplete_reason": incomplete_reason,
        "catalog": {
            "id": catalog.get("id"),
            "track": catalog.get("track"),
            "title": catalog.get("title"),
        },
        "timestamps": {
            "started_at_utc": _iso(started),
            "ended_at_utc": _iso(ended),
            "local_timezone": time.tzname,
        },
        "baseline": baseline,
        "gates": gates_list,
        "ordered_step_outcomes": state.steps,
        "findings": state.findings,
        "artifacts": artifacts,
        "variables": {k: v for k, v in state.variables.items() if k != "src_dir" or v},
    }
    _write_json(session_dir / "result.json", result)
    _write_json(session_dir / "findings.json", state.findings)
    findings_path = session_dir / "findings.jsonl"
    with findings_path.open("w", encoding="utf-8") as handle:
        for finding in state.findings:
            handle.write(json.dumps(finding, sort_keys=True) + "\n")
    notes_lines.extend(
        [
            "## Verdict",
            "",
            f"- result: `{result_status}`",
            f"- reason: {incomplete_reason or '(none)'}",
            f"- findings: {len(state.findings)}",
            "",
        ]
    )
    _write_text(session_dir / "human-notes.md", "\n".join(notes_lines))

    # Refresh digests including result-adjacent files written after first scan
    artifacts = []
    for path in sorted(session_dir.rglob("*")):
        if not path.is_file() or path.name == "digests.json":
            continue
        artifacts.append(
            {"path": str(path.relative_to(session_dir)), "sha256": _sha256_file(path)}
        )
    _write_json(session_dir / "digests.json", {"artifacts": artifacts})
    result["artifacts"] = artifacts
    _write_json(session_dir / "result.json", result)

    print()
    print("=" * 72)
    print(f"SESSION COMPLETE: {result_status}")
    if incomplete_reason:
        print(incomplete_reason)
    print(f"Session directory: {session_dir}")
    print(f"Findings: {len(state.findings)}")
    print("=" * 72)
    return result


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
        help="Path to a session catalog YAML/JSON (default: m007-acceptance).",
    )
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=None,
        help="Directory for session artifacts (default: ./live-cli-sessions/<id>-<timestamp>).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT_DEFAULT,
        help="Automa repository root containing ./cli/automa.",
    )
    parser.add_argument(
        "--metrics-ui-origin",
        default=None,
        help="Metrics UI HTTP origin (default: from catalog).",
    )
    parser.add_argument(
        "--metrics-ui-repo",
        type=Path,
        default=None,
        help="Optional Metrics UI checkout path for baseline identity.",
    )
    parser.add_argument("--browser-name", default=None, help="Browser name for the environment receipt.")
    parser.add_argument("--browser-version", default=None, help="Browser version for the environment receipt.")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt; use --auto-visual for judgments (for tests/dry automation).",
    )
    parser.add_argument(
        "--auto-visual",
        choices=["pass", "fail", "skip", "n/a"],
        default="skip",
        help="Judgment used with --non-interactive (default: skip).",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=120.0,
        help="Per-command timeout in seconds (default: 120).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them.",
    )
    parser.add_argument(
        "--list-catalogs",
        action="store_true",
        help="List bundled catalogs and exit.",
    )
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

    # Copy catalog into session for provenance.
    session_dir.mkdir(parents=True, exist_ok=True)
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
    )
    if result.get("result") == "pass":
        return 0
    if result.get("result") == "complete":
        return 0
    if result.get("result") == "findings":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
