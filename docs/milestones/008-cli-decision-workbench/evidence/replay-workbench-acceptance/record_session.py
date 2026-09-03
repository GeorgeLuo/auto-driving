#!/usr/bin/env python3
"""Prompt-driven recorder for the replay-workbench POC acceptance session.

Launches the same CLI the operator would run, waits for human page
observations, snapshots loopback state as corroboration, and writes the
evidence packet. It never infers a visual pass from machine state.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
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
            "Inspect capture, overlays, progress, and the memory ledger."
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
            "After the first run is terminal, reset isolated memory, reselect "
            "a ready non-empty set if needed, and start a second run without "
            "restarting the server. Keep loop off."
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
            "When the source field is editable, point it at an empty, missing, "
            "or unsupported directory and press Start. Record the failure "
            "boundary. Then point it at a valid directory and Start again."
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
        "failure": state.get("failure"),
        "recovery_action": state.get("recovery_action"),
        "cleanup": state.get("cleanup"),
        "pace": controls.get("pace"),
        "loop": controls.get("loop"),
    }


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


class WorkbenchProcess:
    def __init__(self, command: list[str], cwd: Path) -> None:
        self.command = command
        self.cwd = cwd
        self.proc: subprocess.Popen[str] | None = None
        self.lines: list[str] = []
        self.url: str | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.proc = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
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
            print(text)

    def wait_for_url(self, timeout_s: float = 30.0) -> str:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            with self._lock:
                if self.url:
                    return self.url
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError("workbench process exited before printing a URL")
            time.sleep(0.1)
        raise RuntimeError("timed out waiting for workbench URL")

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
    status = payload.get("status")
    README.write_text(
        f"""# Replay workbench POC acceptance evidence

Status: **{status}**

Accepted contract:
[Replay workbench POC acceptance](../../proposals/replay-workbench-acceptance.md)
([PR #190](https://github.com/GeorgeLuo/auto-driving/pull/190)).

## Verdict

`{payload.get("verdict")}` — {payload.get("incomplete_reason") or payload.get("status")}

Operator: `{payload.get("operator")}`

## Environment receipt

| Field | Value |
| --- | --- |
| Operator | `{payload.get("operator")}` |
| Started (UTC) | `{times.get("started_at_utc")}` |
| Ended (UTC) | `{times.get("ended_at_utc")}` |
| OS | `{env.get("operating_system")}` |
| Browser | `{browser.get("name")} {browser.get("version")}` |
| auto-driving commit | `{repo.get("commit")}` |
| Worktree | `{repo.get("worktree_state")}` |
| Image source (redacted) | `{source.get("path_redacted")}` |
| Plugin root | `{source.get("plugin_root")}` |
| Loopback URL | `{source.get("loopback_url")}` |

## Session checklist

Recorded by `record_session.py`. The operator drove the page; the script
launched the CLI and wrote artifacts.

{os.linesep.join(checklist)}

## Findings

{findings_text}

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
) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    if not source_dir.is_dir():
        raise SystemExit(f"source directory does not exist: {source_dir}")
    command = [
        sys.executable,
        str(AUTOMA),
        "vehicles",
        "workbench",
        "replay",
        str(source_dir),
        "--pace",
        pace,
        "--max-frames",
        str(max_frames),
        "--open",
    ]
    if plugin_dir is not None:
        command.extend(["--plugin-dir", str(plugin_dir.resolve())])
        if plugin:
            command.extend(["--plugin", plugin])
    started = _utc_now()
    identity = _git_identity(REPO_ROOT)
    workbench = WorkbenchProcess(command, REPO_ROOT)
    print("Launching:", " ".join(command), file=output)
    workbench.start()
    payload: dict[str, Any]
    try:
        url = workbench.wait_for_url()
        health_url = urljoin(url if url.endswith("/") else url + "/", "api/health")
        state_url = urljoin(url if url.endswith("/") else url + "/", "api/state")
        deadline = time.time() + 20
        health: dict[str, Any] | None = None
        while time.time() < deadline:
            try:
                health = _get_json(health_url)
                if health.get("available"):
                    break
            except (urllib.error.URLError, TimeoutError, RuntimeError):
                time.sleep(0.25)
        if health is None or not health.get("available"):
            raise RuntimeError(f"workbench health not available at {health_url}")
        print(f"\nWorkbench URL: {url}", file=output)
        print(
            "Do the page work yourself. After each step this script snapshots "
            "/api/state and records your observation.\n",
            file=output,
        )
        recorded_steps: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        for index, spec in enumerate(STEPS, start=1):
            print(f"=== Step {index}/{len(STEPS)}: {spec['id']} ===", file=output)
            print(spec["do"], file=output)
            _ask("Press Enter when you have done that on the page. ", reader)
            machine = None
            try:
                machine = _compact_state(_get_json(state_url))
            except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
                print(f"(could not snapshot /api/state: {exc})", file=output)
            status, raw = _ask_yes_no(spec["ask"], reader)
            notes = _ask("Notes (optional): ", reader)
            if status == "observed_fail":
                findings.append(
                    {
                        "id": f"M008-POC-{index:03d}",
                        "step": spec["id"],
                        "classification": "acceptance_blocker",
                        "observed": notes or "operator reported fail",
                    }
                )
            recorded_steps.append(
                {
                    "id": spec["id"],
                    "status": status,
                    "required": spec["required"],
                    "observation": raw,
                    "notes": notes or None,
                    "machine": machine,
                }
            )
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
        screenshot_path = screenshot
        if screenshot_path is None:
            entered = _ask(
                "Path to cropped browser-view.png (empty to skip): ",
                reader,
            )
            if entered:
                screenshot_path = Path(entered).expanduser()
        if screenshot_path is not None:
            if not screenshot_path.is_file():
                raise SystemExit(f"screenshot not found: {screenshot_path}")
            shutil.copyfile(screenshot_path, SCREENSHOT)
        verdict = _ask_verdict(reader)
        failed_steps = any(
            item.get("status") == "observed_fail" for item in recorded_steps
        )
        pending_steps = any(
            item.get("status") == "pending" for item in recorded_steps
        )
        safety_fail = any(
            item.get("occurred") for item in observation_only.values()
        )
        if verdict == "accepted" and (failed_steps or safety_fail):
            status_value = "blocked"
            incomplete_reason = (
                "Operator said accepted, but a required step or safety "
                "observation failed."
            )
        elif verdict == "accepted" and pending_steps:
            status_value = "incomplete"
            incomplete_reason = (
                "Operator said accepted, but at least one required step was unsure."
            )
        elif verdict == "accepted":
            status_value = "accepted"
            incomplete_reason = None
        elif verdict == "blocked":
            status_value = "blocked"
            incomplete_reason = None
        else:
            status_value = "incomplete"
            incomplete_reason = "Operator recorded incomplete."
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
        TRANSCRIPT.write_text(redacted if redacted.endswith("\n") else redacted + "\n")


def main(argv: list[str] | None = None, reader: PromptFn | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Launch the workbench, prompt for page observations, and write "
            "the POC acceptance evidence packet."
        )
    )
    parser.add_argument("--source-dir", required=True, type=Path)
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
    parser.add_argument("--operator", required=True)
    parser.add_argument("--browser-name", required=True)
    parser.add_argument("--browser-version", required=True)
    parser.add_argument("--screenshot", type=Path, default=None)
    parser.add_argument("--pace", default="realtime")
    parser.add_argument("--max-frames", type=int, default=1024)
    args = parser.parse_args(argv)
    prompt = reader or input
    plugin_dir = None if args.packaged else args.plugin_dir
    payload = run_session(
        source_dir=args.source_dir,
        plugin_dir=plugin_dir,
        plugin=None if plugin_dir is None else args.plugin,
        operator=args.operator,
        browser_name=args.browser_name,
        browser_version=args.browser_version,
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
