#!/usr/bin/env python3
"""Prompt-driven recorder for the replay-workbench POC acceptance session.

Launches the same CLI the operator would run, waits for human page
observations, snapshots loopback state as corroboration, and writes the
evidence packet. It never infers a visual pass from machine state. It does
ask the operator to type first/second/failed/recovered run IDs and to supply
the inspect screenshot during that step.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO
from urllib.parse import urljoin

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[4]
AUTOMA = REPO_ROOT / "cli" / "automa"
RECORD = HERE / "result.json"
README = HERE / "README.md"
TRANSCRIPT = HERE / "cli-transcript.txt"
SCREENSHOT = HERE / "browser-view.png"
SCHEMA = "m008_replay_workbench_acceptance_v1"

PromptFn = Callable[[str], str]
FetchJsonFn = Callable[..., dict[str, Any]]

DETERMINISTIC_CITATIONS = (
    "tests/cli/test_workbench.py::test_explicit_catalog_allows_raw_capture_and_live_replacement",
    "tests/cli/test_workbench.py::test_loopback_api_exposes_and_applies_plugin_selection",
    "tests/cli/test_workbench.py::test_loopback_api_persists_after_terminal_state_and_rejects_raw_argv",
    "tests/cli/test_workbench.py::test_cli_replay_machine_readable_boundary",
    "tests/cli/test_workbench.py::test_cli_replay_accepts_realtime_pace",
)

STEPS: tuple[dict[str, str], ...] = (
    {
        "id": "page_open",
        "required": (
            "Page shows source identity, plugin catalog, and declared next actions."
        ),
        "do": (
            "In the page: confirm source identity, plugin catalog, and next "
            "actions are visible without using the shell as the display. "
            "Turn loop off so this run can reach a terminal state."
        ),
        "ask": (
            "Does the page show source identity, plugin catalog, and next "
            "actions (loop off)?"
        ),
    },
    {
        "id": "inspect_replay",
        "required": (
            "Ready-plugin replay shows capture, server overlays, progress, "
            "and memory on a processed frame."
        ),
        "do": (
            "Replay should already be running. Wait for a processed frame. "
            "Inspect capture, overlays, progress, and the memory ledger. "
            "Crop a screenshot of that still now (viewer, overlays or "
            "raw-capture, progress, memory). Exclude the Setup sidebar "
            "directory paths; local absolute paths must not appear."
        ),
        "ask": (
            "On a processed frame, are capture, overlays, progress, and "
            "memory visible?"
        ),
    },
    {
        "id": "paused_toggle",
        "required": (
            "Paused toggle including empty raw-capture updates the held still "
            "from the server; invalid IDs are refused."
        ),
        "do": (
            "Pause. Toggle to another ready set, then empty raw-capture, then "
            "back to a non-empty ready set. After each change, check that the "
            "held still updates from the server. Leave a non-empty set selected."
        ),
        "ask": (
            "Did each paused toggle (including empty) update the held still "
            "from the server?"
        ),
    },
    {
        "id": "running_toggle",
        "required": (
            "Running toggle including empty keeps the current still until the "
            "next processed frame."
        ),
        "do": (
            "Set cadence to 1000 ms fixed. Resume. While running, toggle to "
            "empty (or another ready set). The current still must stay as "
            "processed until the next frame, which should show the new set. "
            "Restore realtime before the first run completes."
        ),
        "ask": (
            "Did the running toggle keep the current still until the next "
            "processed frame?"
        ),
    },
    {
        "id": "second_run",
        "required": (
            "Reset and a second run without restarting the server; prior run "
            "identity is not current success."
        ),
        "do": (
            "Wait until the first run is terminal. Reset isolated memory, "
            "reselect a ready non-empty set if needed, and start a second run "
            "without restarting the server. Keep loop off. Press Enter only "
            "after the second run has actually started. You will be asked to "
            "type the new run id; do not reuse the first run id."
        ),
        "ask": (
            "Did a second run start on the same page/server with a new run id?"
        ),
    },
    {
        "id": "source_failure",
        "required": (
            "Empty, missing, or unsupported source names the failure and next "
            "action; recovery is an operator-chosen directory."
        ),
        "do": (
            "When the source field is editable, this step has two pauses. "
            "Part 1: point it at an empty, missing, or unsupported directory "
            "and press Start. Open Failure boundary. Press Enter while that "
            "failure is still visible, before recovering. Part 2: point it at "
            "a directory you choose and Start again. Press Enter after the "
            "recovered run has started."
        ),
        "ask": (
            "Was the bad source a named failure, and did recovery use a "
            "directory you chose (no silent substitute)?"
        ),
    },
    {
        "id": "cleanup",
        "required": (
            "Cancel or reset with no worker, simulator, Metrics operation, "
            "movement, or recording; isolated state is reset."
        ),
        "do": (
            "After the recovered run completes or is cancelled, reset. Confirm "
            "no vehicle, worker, simulator, Metrics operation, movement, or "
            "recording was started."
        ),
        "ask": "Did cleanup reset isolated state with no movement/worker/recording?",
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _redact(text: str, repo_root: Path) -> str:
    root = str(repo_root.resolve())
    home = str(Path.home())
    return text.replace(root, "<repo>").replace(home, "<home>")


def _redact_path(path: str | Path, repo_root: Path) -> str:
    text = _redact(str(path), repo_root)
    if text.startswith("/") or (len(text) > 2 and text[1:3] == ":\\"):
        return "<path>/" + Path(path).name
    return text


def _git_identity(repo_root: Path) -> dict[str, str]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "commit": commit,
        "branch": branch or "HEAD",
        "worktree_state": "dirty" if porcelain.strip() else "clean",
    }


def _compact_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    source = state.get("source") if isinstance(state.get("source"), dict) else {}
    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    perception = (
        state.get("perception") if isinstance(state.get("perception"), dict) else {}
    )
    memory = state.get("memory") if isinstance(state.get("memory"), dict) else {}
    controls = state.get("controls") if isinstance(state.get("controls"), dict) else {}
    plugin_runs = perception.get("plugin_runs") or ()
    records = memory.get("records") or ()
    failure = state.get("failure")
    failure_message = None
    failure_boundary = state.get("failure_boundary")
    if isinstance(failure, dict):
        failure_message = failure.get("message")
        failure_boundary = failure.get("boundary") or failure_boundary
    return {
        "server_identity": state.get("server_identity"),
        "run_id": state.get("run_id"),
        "phase": state.get("phase"),
        "source_identity": state.get("source_identity") or source.get("source_id"),
        "active_plugin_ids": state.get("active_plugin_ids"),
        "run_active_plugin_ids": state.get("run_active_plugin_ids"),
        "progress": {
            "completed": progress.get("completed"),
            "total": progress.get("total"),
        },
        "perception_status": perception.get("status"),
        "plugin_run_ids": [
            item.get("plugin_id")
            for item in plugin_runs
            if isinstance(item, dict)
        ],
        "memory_record_count": len(records) if isinstance(records, (list, tuple)) else None,
        "failure": failure,
        "failure_boundary": failure_boundary,
        "failure_message": failure_message,
        "recovery_action": state.get("recovery_action"),
        "cleanup": state.get("cleanup"),
        "pace": controls.get("pace"),
        "loop": controls.get("loop"),
    }


def _failure_payload(machine: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(machine, dict):
        return None
    failure = machine.get("failure")
    if isinstance(failure, dict) and (failure.get("message") or failure.get("boundary")):
        return failure
    if machine.get("failure_message") or machine.get("failure_boundary"):
        return {
            "boundary": machine.get("failure_boundary"),
            "message": machine.get("failure_message"),
        }
    return None


def _format_machine(machine: dict[str, Any] | None) -> str:
    if not machine:
        return "(no /api/state snapshot)"
    progress = machine.get("progress") if isinstance(machine.get("progress"), dict) else {}
    failure = _failure_payload(machine)
    lines = [
        f"  server_identity: {machine.get('server_identity')}",
        f"  run_id: {machine.get('run_id')}",
        f"  phase: {machine.get('phase')}",
        f"  progress: {progress.get('completed')}/{progress.get('total')}",
        f"  source_identity: {machine.get('source_identity')}",
        f"  plugins: {machine.get('active_plugin_ids')}",
        f"  pace: {machine.get('pace')} loop: {machine.get('loop')}",
        f"  recovery_action: {machine.get('recovery_action')}",
    ]
    if failure:
        lines.append(f"  failure.boundary: {failure.get('boundary')}")
        lines.append(f"  failure.message: {failure.get('message')}")
    else:
        lines.append("  failure: null")
    return "\n".join(lines)


def _get_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"non-object JSON from {url}")
    return payload


def _ask(prompt: str, reader: PromptFn) -> str:
    return reader(prompt).strip()


def _ask_yes_no(question: str, reader: PromptFn) -> tuple[str, str]:
    while True:
        answer = _ask(f"{question} [y/n/u]: ", reader).lower()
        if answer in {"y", "yes"}:
            return "observed_pass", answer
        if answer in {"n", "no"}:
            return "observed_fail", answer
        if answer in {"u", "unsure", "skip", ""}:
            return "pending", answer or "unsure"
        print("Please answer y, n, or u (unsure).")


def _ask_verdict(reader: PromptFn) -> str:
    while True:
        answer = _ask("Operator verdict [accepted/blocked/incomplete]: ", reader).lower()
        if answer in {"accepted", "blocked", "incomplete"}:
            return answer
        print("Please answer accepted, blocked, or incomplete.")


def _ask_run_id(label: str, reader: PromptFn, output: TextIO) -> str | None:
    entered = _ask(
        f"Enter the {label} (required; copy from the snapshot above or /api/state): ",
        reader,
    )
    if not entered:
        print(f"No {label} entered.", file=output, flush=True)
        return None
    return entered


def _step_by_id(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any]:
    for item in steps:
        if item.get("id") == step_id:
            return item
    return {}


def identity_gaps(
    identities: dict[str, Any],
    steps: list[dict[str, Any]],
    screenshot: dict[str, Any] | None,
) -> list[str]:
    """Return reasons this packet cannot be accepted."""

    gaps: list[str] = []
    first = identities.get("first_run_id") or None
    second = identities.get("second_run_id") or None
    failed = identities.get("failed_run_id") or None
    recovered = identities.get("recovered_run_id") or None
    if not first:
        gaps.append("missing first run id")
    if not second:
        gaps.append("missing second run id")
    elif first and second == first:
        gaps.append("second run id is not distinct from first")
    if not failed:
        gaps.append("missing failed run id")
    if not recovered:
        gaps.append("missing recovered run id")
    elif failed and recovered == failed:
        gaps.append("recovered run id is not distinct from failed")

    second_machine = _step_by_id(steps, "second_run").get("machine") or {}
    if not second_machine.get("run_id"):
        gaps.append("second_run snapshot has no run_id")
    elif first and second_machine.get("run_id") == first:
        gaps.append("second_run snapshot still shows the first run id")
    if second and second_machine.get("run_id") and second_machine.get("run_id") != second:
        gaps.append("typed second run id does not match the second_run snapshot")

    failure_step = _step_by_id(steps, "source_failure")
    failed_machine = failure_step.get("machine") or {}
    if not _failure_payload(failed_machine):
        gaps.append("source_failure snapshot has no failure payload")
    recovered_machine = failure_step.get("machine_recovery") or {}
    if not recovered_machine.get("run_id"):
        gaps.append("recovered snapshot has no run_id")
    elif failed and recovered_machine.get("run_id") == failed:
        gaps.append("recovered snapshot still shows the failed run id")
    if recovered and recovered_machine.get("run_id") and recovered_machine.get("run_id") != recovered:
        gaps.append("typed recovered run id does not match the recovery snapshot")

    servers = [
        identities.get("server_identity"),
        second_machine.get("server_identity"),
        failed_machine.get("server_identity"),
        recovered_machine.get("server_identity"),
    ]
    named = [item for item in servers if item]
    if len(set(named)) > 1:
        gaps.append("server identity changed during the session")

    shot = screenshot if isinstance(screenshot, dict) else {}
    if not shot.get("captured"):
        gaps.append("inspect screenshot was not captured during inspect_replay")
    if shot.get("path_redaction") != "observed_pass":
        gaps.append("screenshot path redaction was not confirmed")
    return gaps


def finalize_status(
    *,
    verdict: str,
    steps: list[dict[str, Any]],
    observation_only: dict[str, Any],
    gaps: list[str],
) -> tuple[str, str | None]:
    failed_steps = any(item.get("status") == "observed_fail" for item in steps)
    pending_steps = any(item.get("status") == "pending" for item in steps)
    safety_fail = any(
        isinstance(item, dict) and item.get("occurred")
        for item in observation_only.values()
    )
    if verdict == "accepted" and (failed_steps or safety_fail):
        return (
            "blocked",
            "Operator said accepted, but a required step or safety observation failed.",
        )
    if verdict == "accepted" and pending_steps:
        return (
            "incomplete",
            "Operator said accepted, but at least one required step was unsure.",
        )
    if verdict == "accepted" and gaps:
        return (
            "incomplete",
            "Operator said accepted, but required identity or screenshot "
            "evidence is missing: " + "; ".join(gaps),
        )
    if verdict == "accepted":
        return "accepted", None
    if verdict == "blocked":
        return "blocked", None
    return "incomplete", "Operator recorded incomplete."


class WorkbenchProcess:
    def __init__(self, command: list[str], cwd: Path, *, url: str) -> None:
        self.command = command
        self.cwd = cwd
        self.expected_url = url.rstrip("/") + "/"
        self.proc: subprocess.Popen[str] | None = None
        self.lines: list[str] = []
        self.url: str | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            text = line.rstrip("\n")
            with self._lock:
                self.lines.append(text)
                if self.url is None and "workbench:" in text:
                    self.url = text.split("workbench:", 1)[1].strip()
            print(text, flush=True)

    def wait_until_ready(
        self,
        *,
        timeout_s: float,
        reader: PromptFn,
        output: TextIO,
    ) -> tuple[str, dict[str, Any]]:
        health_url = urljoin(self.expected_url, "api/health")
        deadline = time.time() + timeout_s
        last_error = "not contacted yet"
        print(
            "Waiting for the workbench to load the source and print its URL.\n"
            f"Expect {self.expected_url}  (long captures can take a minute.)",
            file=output,
            flush=True,
        )
        while time.time() < deadline:
            with self._lock:
                found = self.url
            if found:
                health_url = urljoin(
                    found if found.endswith("/") else found + "/",
                    "api/health",
                )
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(
                    "workbench process exited before becoming ready\n"
                    + self.transcript()
                )
            try:
                health = _get_json(health_url, timeout=1.0)
                if health.get("available"):
                    url = str(health.get("url") or found or self.expected_url)
                    return url, health
            except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
                last_error = str(exc)
            time.sleep(0.25)
        print("\nTimed out waiting for the server.", file=output, flush=True)
        print("Captured CLI output follows:\n", file=output, flush=True)
        print(self.transcript() or "(no stdout yet)", file=output, flush=True)
        pasted = _ask(
            "If the page is open, paste its URL (empty to abort): ",
            reader,
        )
        if not pasted:
            raise RuntimeError(
                "timed out waiting for workbench URL "
                f"(last health error: {last_error})"
            )
        url = pasted.rstrip("/") + "/"
        health = _get_json(urljoin(url, "api/health"))
        return url, health

    def transcript(self) -> str:
        with self._lock:
            return "\n".join(self.lines) + ("\n" if self.lines else "")

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _write_readme(payload: dict[str, Any]) -> None:
    steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    checklist = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        mark = "x" if step.get("status") == "observed_pass" else " "
        checklist.append(
            f"- [{mark}] `{step.get('id')}` — {step.get('required')}"
        )
    findings = payload.get("findings") or []
    findings_text = (
        "None recorded."
        if not findings
        else json.dumps(findings, indent=2)
    )
    env = payload.get("environment") if isinstance(payload.get("environment"), dict) else {}
    browser = env.get("browser") if isinstance(env.get("browser"), dict) else {}
    repo = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    times = payload.get("timestamps") if isinstance(payload.get("timestamps"), dict) else {}
    identities = (
        payload.get("identities") if isinstance(payload.get("identities"), dict) else {}
    )
    shot = payload.get("screenshot") if isinstance(payload.get("screenshot"), dict) else {}
    status = payload.get("status")
    citations = "\n".join(f"- `{name}`" for name in DETERMINISTIC_CITATIONS)

    def _show(value: object) -> str:
        if value is None or value == "":
            return "none"
        return str(value)

    browser_label = " ".join(
        part for part in (_show(browser.get("name")), _show(browser.get("version")))
        if part != "none"
    ) or "none"
    README.write_text(
        f"""# Replay workbench POC acceptance evidence

Status: **{status}**

Accepted contract:
[Replay workbench POC acceptance](../../proposals/replay-workbench-acceptance.md)
([PR #190](https://github.com/GeorgeLuo/auto-driving/pull/190)).

## Verdict

`{_show(payload.get("verdict"))}` — {payload.get("incomplete_reason") or payload.get("status")}

Operator: `{_show(payload.get("operator"))}`

## Environment receipt

| Field | Value |
| --- | --- |
| Operator | `{_show(payload.get("operator"))}` |
| Started (UTC) | `{_show(times.get("started_at_utc"))}` |
| Ended (UTC) | `{_show(times.get("ended_at_utc"))}` |
| OS | `{_show(env.get("operating_system"))}` |
| Browser | `{browser_label}` |
| auto-driving commit | `{_show(repo.get("commit"))}` |
| Worktree | `{_show(repo.get("worktree_state"))}` |
| Image source (redacted) | `{_show(source.get("path_redacted"))}` |
| Plugin root | `{_show(source.get("plugin_root"))}` |
| Loopback URL | `{_show(source.get("loopback_url"))}` |
| Server identity | `{_show(identities.get("server_identity"))}` |
| First run id | `{_show(identities.get("first_run_id"))}` |
| Second run id | `{_show(identities.get("second_run_id"))}` |
| Failed run id | `{_show(identities.get("failed_run_id"))}` |
| Recovered run id | `{_show(identities.get("recovered_run_id"))}` |

## Session checklist

Recorded by `record_session.py`. The operator drove the page; the script
launched the CLI and wrote artifacts. Compact `/api/state` snapshots are
corroboration. The operator types run IDs; the script does not fill them.

{os.linesep.join(checklist)}

Observation-only checks are in `result.json` `observation_only`.

Inspect screenshot asked during `inspect_replay` (not at session end):
captured=`{shot.get("captured")}`, path_redaction=`{shot.get("path_redaction")}`.

## Findings

{findings_text}

## Limitations

- The workbench page does not display `run_id`. That is an
  `enhancement_candidate` residual, not a blocker. After identity steps the
  recorder prints a compact `/api/state` snapshot and asks for the run id
  from that snapshot (or `{source.get("loopback_url") or ""}api/state`).
  Surfacing current run identity on the page would avoid that side channel;
  it is out of this evidence PR.
- `accepted` requires distinct first, second, failed, and recovered run IDs
  on one server identity, a failure payload while the invalid source is
  visible, a recovered run snapshot, and a cropped inspect screenshot whose
  local paths were confirmed excluded.
- Worktree `dirty` at record time is the in-progress evidence packet.

## Deterministic boundary citations

{citations}

## Artifacts

See `result.json` `artifacts` and derived [result.html](result.html).
Regenerate HTML with `python3 render_result.py`.
""",
        encoding="utf-8",
    )


def _refresh_html() -> None:
    spec = HERE / "render_result.py"
    subprocess.run([sys.executable, str(spec)], check=True, cwd=HERE)


def _compact_health(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "server_identity": payload.get("server_identity"),
        "available": payload.get("available"),
        "url": payload.get("url"),
        "phase": payload.get("phase"),
        "run_id": payload.get("run_id"),
        "observation_only": payload.get("observation_only"),
        "persistent_across_terminal_state": payload.get(
            "persistent_across_terminal_state"
        ),
    }


def _snapshot_state(
    state_url: str,
    fetch_json: FetchJsonFn,
    output: TextIO,
) -> dict[str, Any] | None:
    try:
        machine = _compact_state(fetch_json(state_url))
    except (urllib.error.URLError, TimeoutError, RuntimeError, TypeError) as exc:
        print(f"(could not snapshot /api/state: {exc})", file=output, flush=True)
        return None
    print("Machine snapshot:", file=output, flush=True)
    print(_format_machine(machine), file=output, flush=True)
    print(
        "The page does not show run_id. Copy it from this snapshot or open "
        f"{state_url}.",
        file=output,
        flush=True,
    )
    return machine


def _record_visual(
    spec: dict[str, str],
    *,
    reader: PromptFn,
    machine: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status, raw = _ask_yes_no(spec["ask"], reader)
    notes = _ask("Notes (optional): ", reader)
    item: dict[str, Any] = {
        "id": spec["id"],
        "status": status,
        "required": spec["required"],
        "observation": raw,
        "notes": notes or None,
        "machine": machine,
    }
    if extra:
        item.update(extra)
    return item


def run_session(
    *,
    source_dir: Path,
    plugin_dir: Path | None,
    plugin: str | None,
    operator: str,
    browser_name: str,
    browser_version: str,
    screenshot: Path | None,
    pace: str,
    max_frames: int,
    reader: PromptFn,
    output: TextIO,
    fetch_json: FetchJsonFn | None = None,
    launcher: Callable[..., Any] | None = None,
    artifact_dir: Path | None = None,
) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise SystemExit(f"source directory does not exist: {source_dir}")
    fetch = fetch_json or _get_json
    make_workbench = launcher or WorkbenchProcess
    artifacts = artifact_dir or HERE
    screenshot_dest = artifacts / "browser-view.png"
    transcript_path = artifacts / "cli-transcript.txt"
    port = _free_loopback_port()
    expected_url = f"http://127.0.0.1:{port}/"
    command = [
        sys.executable,
        "-u",
        str(AUTOMA),
        "vehicles",
        "workbench",
        "replay",
        str(source_dir),
        "--pace",
        pace,
        "--max-frames",
        str(max_frames),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--open",
    ]
    if plugin_dir is not None:
        command.extend(["--plugin-dir", str(plugin_dir.resolve())])
        if plugin:
            command.extend(["--plugin", plugin])
    started = _utc_now()
    identity = _git_identity(REPO_ROOT)
    workbench = make_workbench(command, REPO_ROOT, url=expected_url)
    print("Launching:", " ".join(command), file=output, flush=True)
    print(
        "The browser should open. Keep this terminal; after each page action "
        "you will be asked what you saw.\n"
        "Run ids are not shown on the page. After identity steps this script "
        "prints a compact /api/state snapshot and asks you to type the run id. "
        "The inspect screenshot is asked during inspect_replay, not at the end.\n",
        file=output,
        flush=True,
    )
    workbench.start()
    payload: dict[str, Any]
    try:
        url, health = workbench.wait_until_ready(
            timeout_s=180,
            reader=reader,
            output=output,
        )
        state_url = urljoin(url if url.endswith("/") else url + "/", "api/state")
        print(f"\nWorkbench URL: {url}", file=output, flush=True)
        print(f"State URL: {state_url}", file=output, flush=True)
        identities: dict[str, Any] = {
            "server_identity": health.get("server_identity"),
            "first_run_id": None,
            "second_run_id": None,
            "failed_run_id": None,
            "recovered_run_id": None,
        }
        recorded_steps: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        screenshot_meta: dict[str, Any] = {
            "asked_during": "inspect_replay",
            "captured": False,
            "path_redaction": None,
        }

        def _add_finding(step_id: str, observed: str, index: int) -> None:
            findings.append(
                {
                    "id": f"M008-POC-{index:03d}",
                    "step": step_id,
                    "classification": "acceptance_blocker",
                    "observed": observed,
                }
            )

        for index, spec in enumerate(STEPS, start=1):
            print(
                f"\n=== Step {index}/{len(STEPS)}: {spec['id']} ===",
                file=output,
                flush=True,
            )
            print(spec["do"], file=output, flush=True)
            extra: dict[str, Any] = {}
            machine: dict[str, Any] | None

            if spec["id"] == "source_failure":
                print(
                    "\nPART 1/2 — invalid source. Press Enter while Failure "
                    "boundary is visible, before you recover.",
                    file=output,
                    flush=True,
                )
                _ask("Press Enter when the failure is visible. ", reader)
                machine = _snapshot_state(state_url, fetch, output)
                if not _failure_payload(machine):
                    print(
                        "WARNING: snapshot has no failure payload. A later "
                        "recovery snapshot cannot stand in for this.",
                        file=output,
                        flush=True,
                    )
                failed_id = _ask_run_id("failed run id", reader, output)
                identities["failed_run_id"] = failed_id
                extra["operator_failure"] = {
                    "boundary": _ask("Enter the failure boundary shown: ", reader) or None,
                    "message": _ask("Enter the failure message shown: ", reader) or None,
                    "recovery_action": _ask(
                        "Enter the next action shown: ",
                        reader,
                    )
                    or None,
                }
                extra["operator_run_id"] = failed_id
                print(
                    "\nPART 2/2 — recover with a directory you choose. Same "
                    "server. Press Enter after the recovered run has started.",
                    file=output,
                    flush=True,
                )
                _ask("Press Enter when the recovered run has started. ", reader)
                recovered_machine = _snapshot_state(state_url, fetch, output)
                extra["machine_recovery"] = recovered_machine
                recovered_id = _ask_run_id("recovered run id", reader, output)
                identities["recovered_run_id"] = recovered_id
                extra["operator_recovered_run_id"] = recovered_id
                if failed_id and recovered_id == failed_id:
                    print(
                        "WARNING: recovered run id matches the failed run id.",
                        file=output,
                        flush=True,
                    )
                recorded = _record_visual(
                    spec, reader=reader, machine=machine, extra=extra
                )
                if recorded["status"] == "observed_fail":
                    _add_finding(spec["id"], recorded["notes"] or "operator reported fail", index)
                recorded_steps.append(recorded)
                continue

            if spec["id"] == "second_run":
                print(
                    f"First run id (do not reuse): {identities.get('first_run_id')}",
                    file=output,
                    flush=True,
                )
            if spec["id"] == "inspect_replay":
                print(
                    "You will be asked for the cropped PNG path in this step, "
                    "not at the end of the session.",
                    file=output,
                    flush=True,
                )
            _ask("Press Enter when you have done that on the page. ", reader)
            machine = _snapshot_state(state_url, fetch, output)

            if spec["id"] == "page_open":
                first_id = _ask_run_id("first run id", reader, output)
                identities["first_run_id"] = first_id
                extra["operator_run_id"] = first_id
            elif spec["id"] == "second_run":
                second_id = _ask_run_id("second run id", reader, output)
                identities["second_run_id"] = second_id
                extra["operator_run_id"] = second_id
                if second_id and second_id == identities.get("first_run_id"):
                    print(
                        "WARNING: second run id matches the first run id.",
                        file=output,
                        flush=True,
                    )
                if (
                    identities.get("first_run_id")
                    and machine
                    and machine.get("run_id") == identities.get("first_run_id")
                ):
                    print(
                        "WARNING: snapshot still shows the first run id. A "
                        "second run has not been captured.",
                        file=output,
                        flush=True,
                    )
            elif spec["id"] == "inspect_replay":
                default_shot = str(screenshot) if screenshot is not None else ""
                hint = f" [{default_shot}]" if default_shot else ""
                entered = _ask(
                    "Path to cropped browser-view.png"
                    f"{hint} (empty to skip; ask is now, not at the end): ",
                    reader,
                )
                screenshot_path = None
                if entered:
                    screenshot_path = Path(entered).expanduser()
                elif default_shot:
                    screenshot_path = Path(default_shot).expanduser()
                redaction_status, redaction_raw = _ask_yes_no(
                    "Does the crop exclude local filesystem paths "
                    "(no /Users/... in the sidebar)?",
                    reader,
                )
                screenshot_meta["path_redaction"] = redaction_status
                extra["screenshot_path_redaction"] = redaction_raw
                if screenshot_path is not None:
                    if not screenshot_path.is_file():
                        raise SystemExit(f"screenshot not found: {screenshot_path}")
                    shutil.copyfile(screenshot_path, screenshot_dest)
                    screenshot_meta["captured"] = True

            recorded = _record_visual(
                spec, reader=reader, machine=machine, extra=extra
            )
            if recorded["status"] == "observed_fail":
                _add_finding(
                    spec["id"],
                    recorded["notes"] or "operator reported fail",
                    index,
                )
            recorded_steps.append(recorded)

        print("=== Observation-only / cleanup ===", file=output)
        observation_only: dict[str, Any] = {}
        for key, label in (
            ("vehicle", "vehicle start or mutation"),
            ("worker", "Automa worker"),
            ("simulator", "simulator reconfiguration"),
            ("metrics_operation", "Metrics UI operation"),
            ("movement", "movement or control"),
            ("recording", "recording"),
        ):
            status, _raw = _ask_yes_no(f"Confirm no {label} occurred?", reader)
            occurred = status == "observed_fail"
            observation_only[key] = {
                "occurred": occurred,
                "operator_status": status,
            }
            if occurred:
                findings.append(
                    {
                        "id": f"M008-POC-S-{key}",
                        "step": "cleanup",
                        "classification": "acceptance_blocker",
                        "observed": f"operator reported {label}",
                    }
                )
        gaps = identity_gaps(identities, recorded_steps, screenshot_meta)
        if gaps:
            print("Identity/screenshot gaps:", file=output, flush=True)
            for gap in gaps:
                print(f"  - {gap}", file=output, flush=True)
        verdict = _ask_verdict(reader)
        status_value, incomplete_reason = finalize_status(
            verdict=verdict,
            steps=recorded_steps,
            observation_only=observation_only,
            gaps=gaps,
        )
        plugin_root = (
            _redact_path(plugin_dir, REPO_ROOT) if plugin_dir is not None else "packaged"
        )
        payload = {
            "schema": SCHEMA,
            "status": status_value,
            "incomplete_reason": incomplete_reason,
            "operator": operator,
            "verdict": verdict,
            "timestamps": {"started_at_utc": started, "ended_at_utc": _utc_now()},
            "environment": {
                "operating_system": platform.platform(),
                "browser": {"name": browser_name, "version": browser_version},
            },
            "repository": identity,
            "source": {
                "kind": "image_directory",
                "path_redacted": _redact_path(source_dir, REPO_ROOT),
                "plugin_root": plugin_root,
                "loopback_url": url,
                "health": _compact_health(health),
            },
            "identities": identities,
            "screenshot": screenshot_meta,
            "launch_command": [_redact(item, REPO_ROOT) for item in command],
            "steps": recorded_steps,
            "observation_only": observation_only,
            "findings": findings,
            "artifacts": [],
        }
        return payload
    finally:
        workbench.stop()
        redacted = _redact(workbench.transcript(), REPO_ROOT)
        transcript_path.write_text(
            redacted if redacted.endswith("\n") else redacted + "\n"
        )


def _prompt_path(label: str, default: str, reader: PromptFn) -> Path:
    entered = _ask(f"{label} [{default}]: ", reader)
    return Path(entered or default).expanduser()


def _prompt_text(label: str, default: str, reader: PromptFn) -> str:
    entered = _ask(f"{label} [{default}]: ", reader)
    return entered or default


def main(argv: list[str] | None = None, reader: PromptFn | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Launch the workbench, prompt for page observations, and write "
            "the POC acceptance evidence packet."
        )
    )
    parser.add_argument("--source-dir", type=Path, default=None)
    parser.add_argument(
        "--plugin-dir",
        type=Path,
        default=REPO_ROOT / "lab" / "plugins" / "perception",
    )
    parser.add_argument(
        "--packaged",
        action="store_true",
        help="Use the packaged catalog instead of --plugin-dir.",
    )
    parser.add_argument("--plugin", default="classical_regions")
    parser.add_argument("--operator", default=None)
    parser.add_argument("--browser-name", default=None)
    parser.add_argument("--browser-version", default=None)
    parser.add_argument("--screenshot", type=Path, default=None)
    parser.add_argument("--pace", default="realtime")
    parser.add_argument("--max-frames", type=int, default=1024)
    args = parser.parse_args(argv)
    prompt = reader or input
    print(
        "This recorder asks you what you see on the page.\n"
        "It launches the workbench, then waits for your y/n/u answers.\n"
        "It asks you to type run ids and the inspect screenshot path.\n"
        "It does not fill those answers for you.\n",
        flush=True,
    )
    default_source = (
        "/Users/gluo/Projects/auto-driving/runtime/vehicles/chase-sim-chaser/"
        "bundle/runtime/automation/captures/"
        "chase-stream-decision-model-default-45s-20260901-230833"
    )
    source_dir = args.source_dir or _prompt_path(
        "Image directory",
        default_source,
        prompt,
    )
    operator = args.operator or _prompt_text(
        "Operator name",
        os.environ.get("USER") or "operator",
        prompt,
    )
    browser_name = args.browser_name or _prompt_text(
        "Browser name",
        "Chrome",
        prompt,
    )
    browser_version = args.browser_version or _prompt_text(
        "Browser version (chrome://version)",
        "",
        prompt,
    )
    if not browser_version:
        raise SystemExit("browser version is required")
    plugin_dir = None if args.packaged else args.plugin_dir
    payload = run_session(
        source_dir=source_dir,
        plugin_dir=plugin_dir,
        plugin=None if plugin_dir is None else args.plugin,
        operator=operator,
        browser_name=browser_name,
        browser_version=browser_version,
        screenshot=args.screenshot,
        pace=args.pace,
        max_frames=args.max_frames,
        reader=prompt,
        output=sys.stdout,
    )
    RECORD.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _write_readme(payload)
    _refresh_html()
    print(f"Wrote {RECORD}")
    print(f"Status: {payload['status']}")
    return 0 if payload["status"] in {"accepted", "blocked", "incomplete"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
