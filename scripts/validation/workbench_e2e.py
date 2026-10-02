#!/usr/bin/env python3
"""End-to-end workbench check over its HTTP API.

Serves the workbench on the bright high-rate capture with packaged perception
plugins that extract signals, and runs it to the end. Every frame is then
shown by seeking, as the page does, and compared with
``automa vehicles perception inspect`` over the same directory and plugins: the
workbench must show the frame, status, and per-plugin signal and thing counts
the CLI reports. Memory and decision state must be present for every frame.
A second pass changes the plugin selection through the API while paused,
running, seeking, and looping.

    scripts/validation/workbench_e2e.py
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "lab/runs/cv-synthesis-20260921/experiment-3/bright-motion-20s-20260921-133121"
PLUGINS = ("multi_obstruction_tracks", "floor_continuity", "classical_regions")
RUN_KEYS = ("plugin_id", "status", "error", "signal_count", "thing_count")


def automa(*args: str) -> list[str]:
    return [sys.executable, str(ROOT / "cli/automa"), "vehicles", *args]


def plugin_options() -> list[str]:
    options: list[str] = []
    for plugin_id in PLUGINS:
        options += ["--plugin", plugin_id]
    return options


def inspect_frames() -> list[dict]:
    """Frames as the CLI reports them for the same source and plugins."""

    completed = subprocess.run(
        automa("perception", "inspect", str(SOURCE), *plugin_options(), "--json"),
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(f"perception inspect exited {completed.returncode}")
    return json.loads(completed.stdout)["frames"]


class Workbench:
    """A served workbench and the two calls the page makes."""

    def __init__(self, base: str) -> None:
        self.base = base

    def state(self) -> dict:
        return json.load(urllib.request.urlopen(self.base + "/api/state", timeout=5))

    def act(self, **payload) -> dict:
        request = urllib.request.Request(
            self.base + "/api/action",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.load(urllib.request.urlopen(request, timeout=60))["state"]

    def wait_for(self, condition, timeout: float = 120.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                current = self.state()
                if condition(current):
                    return current
            except OSError:
                pass
            time.sleep(0.2)
        raise TimeoutError("workbench did not reach the expected state")


@contextmanager
def serve(plugins: bool = True):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    command = automa(
        "workbench", "replay", str(SOURCE), "--serve", "--port", str(port), "--cadence-ms", "0",
    )
    if plugins:
        command += plugin_options()
    server = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        yield Workbench(f"http://127.0.0.1:{port}")
    finally:
        server.terminate()
        server.wait(timeout=10)


def runs(current: dict) -> list[str]:
    perception = current["steps"]["perception"] or {}
    return [run["plugin_id"] for run in perception.get("plugin_runs") or []]


def check_parity() -> list[str]:
    """Show every frame of a finished run and compare it with ``inspect``."""

    expected = inspect_frames()
    problems: list[str] = []
    try:
        with serve() as workbench:
            started = workbench.wait_for(lambda s: s["position"] >= 1)
            run_id = started["run_id"]
            workbench.act(action="pause", run_id=run_id)
            # Seeking ahead processes the unseen frames; chunks keep each request short.
            for ahead in range(50, len(expected) + 49, 50):
                done = workbench.act(action="seek", run_id=run_id, position=min(ahead, len(expected)) - 1)
            if done.get("failure"):
                problems.append(f"run failed: {done['failure']!r}")
            if len(done["timeline"]) != len(expected):
                problems.append(f"frames: inspect {len(expected)}, workbench {len(done['timeline'])}")
            for index, want in enumerate(expected[: len(done["timeline"])]):
                shown = workbench.act(action="seek", run_id=run_id, position=index)
                label = f"frame {index}"
                entry = shown["timeline"][index]
                if shown["position"] != index + 1:
                    problems.append(f"{label}: seek landed at {shown['position'] - 1}")
                if entry.get("perception_status") != want["status"]:
                    problems.append(
                        f"{label}: status {entry.get('perception_status')!r}, inspect {want['status']!r}"
                    )
                got_runs = [
                    {key: run.get(key) for key in RUN_KEYS}
                    for run in (shown["steps"]["perception"] or {}).get("plugin_runs") or []
                ]
                want_runs = [{key: run.get(key) for key in RUN_KEYS} for run in want["plugin_runs"]]
                if got_runs != want_runs:
                    problems.append(f"{label}: plugin runs {got_runs}, inspect {want_runs}")
                if not (entry.get("decision") or {}).get("status"):
                    problems.append(f"{label}: no decision status")
                if entry.get("memory_record_count") is None:
                    problems.append(f"{label}: no memory record count")
                if len(problems) > 20:
                    break
    except (TimeoutError, OSError, KeyError) as exc:
        problems.append(f"parity check failed: {type(exc).__name__}: {exc}")
    return problems


def check_selector() -> list[str]:
    """Change the selection the way the page does.

    A paused selection reprocesses the displayed frame, seeking shows earlier
    frames with the current selection, seeking past the recorded frames lands
    there, a running selection reaches the next frame, and a loop pass runs
    with the current selection.
    """

    problems: list[str] = []
    try:
        with serve() as workbench:
            act = workbench.act
            wait_for = workbench.wait_for
            current = wait_for(lambda s: s["position"] >= 3)
            run_id = current["run_id"]
            paused = act(action="pause", run_id=run_id)
            frame_id = paused["current_frame"]["frame_id"]
            selected = act(action="select_plugins", run_id=run_id, active_plugin_ids=["floor_continuity"])
            if selected["current_frame"]["frame_id"] != frame_id or runs(selected) != ["floor_continuity"]:
                problems.append(f"paused selection did not reprocess {frame_id}: {runs(selected)}")
            sought = act(action="seek", run_id=run_id, position=0)
            if runs(sought) != ["floor_continuity"]:
                problems.append(f"seek to a frame recorded earlier showed {runs(sought)}")
            target = len(sought["timeline"]) + 10
            ahead = act(action="seek", run_id=run_id, position=target)
            if ahead["position"] != target + 1 or runs(ahead) != ["floor_continuity"]:
                problems.append(f"seek past recorded frames to {target} landed at {ahead['position'] - 1}")
            act(action="resume", run_id=run_id)
            act(action="select_plugins", run_id=run_id, active_plugin_ids=["classical_regions"])
            running = wait_for(lambda s: runs(s) == ["classical_regions"], timeout=30)
            if running["phase"] != "running":
                problems.append(f"running selection left phase {running['phase']}")
            act(action="set_loop", run_id=run_id, loop=True)
            wait_for(lambda s: s["position"] > len(s["timeline"]) - 1 and s["position"] > 200)
            looped = wait_for(lambda s: 0 < s["position"] < 20)
            if runs(looped) != ["classical_regions"] or len(looped["timeline"]) != looped["position"]:
                problems.append(
                    f"loop pass did not reprocess: position {looped['position']}, "
                    f"timeline {len(looped['timeline'])}, runs {runs(looped)}"
                )
    except (TimeoutError, OSError, KeyError) as exc:
        problems.append(f"selector check failed: {type(exc).__name__}: {exc}")
    return problems


def main() -> int:
    problems = check_parity() + check_selector()
    if problems:
        print(f"{len(problems)} problem(s):")
        print("\n".join(problems[:40]))
        return 1
    print("workbench matches inspect frame by frame; plugin selection reprocesses frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
