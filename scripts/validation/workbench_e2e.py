#!/usr/bin/env python3
"""End-to-end workbench check over its HTTP API.

Serves the workbench on the bright high-rate capture with packaged perception
plugins that extract signals, and runs it to the end. Every frame is then
shown by seeking, as the page does, and compared with
``automa plugins inspect perception`` over the same directory and plugins: the
workbench must show the frame, status, and per-plugin signal and thing counts
the CLI reports. Memory and decision state must be present for every frame.
Both are given the ``multi_obstruction`` perception preset. A second pass
changes the plugin selection through the API while paused, running, seeking,
and looping. A third selects memory's default plugins, as
``automa plugins list --step memory`` reports them, and compares what runs.

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
SOURCE = ROOT / "lab/runs/cv-synthesis-20260921/experiment-3/bright-motion-20s-20260921-133121/frames"
PERCEPTION_PRESET = "multi_obstruction"
RUN_KEYS = ("plugin_id", "status", "error", "signal_count", "thing_count")


def automa(*args: str) -> list[str]:
    return [sys.executable, str(ROOT / "cli/automa"), *args]


def inspect_report() -> dict:
    """The CLI's report for the same source, with the perception preset."""

    completed = subprocess.run(
        [sys.executable, str(ROOT / "cli/automa"), "plugins", "inspect", "perception",
         str(SOURCE), "--preset", PERCEPTION_PRESET, "--json"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(f"perception inspect exited {completed.returncode}")
    return json.loads(completed.stdout)


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

    def pause(self, run_id: str) -> dict:
        paused = self.act(action="pause", run_id=run_id)
        # An in-flight frame may finish after pause responds. An unchanged
        # selection waits for that boundary without processing another frame.
        return self.act(
            action="select_plugins", run_id=run_id, step="perception",
            active_plugin_ids=paused["active_perception_plugin_ids"],
        )

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
def serve():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    command = automa(
        "workbench", str(SOURCE), "--serve", "--port", str(port), "--cadence-ms", "0",
        "--perception-preset", PERCEPTION_PRESET,
    )
    server = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL)
    try:
        yield Workbench(f"http://127.0.0.1:{port}/workbench")
    finally:
        server.terminate()
        server.wait(timeout=10)


def runs(current: dict) -> list[str]:
    perception = current["perception"] or {}
    return [run["plugin_id"] for run in perception.get("plugin_runs") or []]


def check_parity() -> list[str]:
    """Show every frame of a finished run and compare it with ``inspect``."""

    report = inspect_report()
    expected = report["frames"]
    if not expected:
        return ["inspect returned no frames"]
    problems: list[str] = []
    inspected = report["selections"]["perception"]
    try:
        with serve() as workbench:
            started = workbench.wait_for(lambda s: s["position"] >= 1)
            run_id = started["run_id"]
            served = started["machine_detail"]["pipeline"]["perception_preset"]
            plugins = started["active_perception_plugin_ids"]
            if (served, plugins) != (inspected["preset"], inspected["config"]["plugins"]):
                problems.append(
                    f"workbench runs {served} {plugins}, "
                    f"inspect runs {inspected['preset']} {inspected['config']['plugins']}"
                )
            done = workbench.pause(run_id)
            # Seeking ahead processes the unseen frames; chunks keep each request short.
            for start in range(0, len(expected), 50):
                done = workbench.act(action="seek", run_id=run_id, position=min(start + 49, len(expected) - 1))
            if done.get("failure"):
                problems.append(f"run failed: {done['failure']!r}")
            completed = done["latest_processed_position"] + 1
            if completed != len(expected) or done["progress"]["total"] != len(expected):
                problems.append(f"frames: inspect {len(expected)}, workbench {completed} of {done['progress']['total']}")
            for index, want in enumerate(expected):
                shown = workbench.act(action="seek", run_id=run_id, position=index)
                label = f"frame {index}"
                if shown["position"] != index + 1:
                    problems.append(f"{label}: seek landed at {shown['position'] - 1}")
                if shown["current_frame"]["frame_id"] != want["frame_id"]:
                    problems.append(f"{label}: frame identity differs from inspect")
                record = want["record"]
                perception = shown["perception"]
                if record is None:
                    if perception is not None:
                        problems.append(f"{label}: absent frame has a perception record")
                else:
                    if (perception or {}).get("status") != record["status"]:
                        problems.append(f"{label}: status {(perception or {}).get('status')!r}, inspect {record['status']!r}")
                    got_runs = [
                        {key: run.get(key) for key in RUN_KEYS}
                        for run in (perception or {}).get("plugin_runs") or []
                    ]
                    want_runs = [{key: run.get(key) for key in RUN_KEYS} for run in record["plugin_runs"]]
                    if got_runs != want_runs:
                        problems.append(f"{label}: plugin runs {got_runs}, inspect {want_runs}")
                cycle = shown["cycle"]
                if not (cycle["action"] or {}).get("status"):
                    problems.append(f"{label}: no decision status")
                plugins = (cycle["memory"] or {}).get("plugins") or []
                if not plugins or any((item.get("state") or {}).get("record_count") is None for item in plugins):
                    problems.append(f"{label}: no memory record count")
                if len(problems) > 20:
                    break
    except (TimeoutError, OSError, KeyError) as exc:
        problems.append(f"parity check failed: {type(exc).__name__}: {exc}")
    return problems


def check_selector() -> list[str]:
    """Change the selection the way the page does.

    A paused selection refreshes only the displayed frame. Earlier frames keep
    the selection they were computed with. Seeking past those frames processes
    the new ones with the current selection. A running selection applies on a
    later frame, and a loop pass runs with the current selection.
    """

    problems: list[str] = []
    try:
        with serve() as workbench:
            act = workbench.act
            wait_for = workbench.wait_for
            current = wait_for(lambda s: s["position"] >= 3)
            run_id = current["run_id"]
            original = list(current["active_perception_plugin_ids"])
            paused = workbench.pause(run_id)
            frame_id = paused["current_frame"]["frame_id"]
            selected = act(
                action="select_plugins", run_id=run_id, step="perception",
                active_plugin_ids=["floor_continuity"],
            )
            if selected["current_frame"]["frame_id"] != frame_id or runs(selected) != ["floor_continuity"]:
                problems.append(f"paused selection did not refresh {frame_id}: {runs(selected)}")
            if selected["latest_processed_position"] + 1 != paused["position"]:
                problems.append("selection changed the processed frame count")
            sought = act(action="seek", run_id=run_id, position=0)
            if runs(sought) != original:
                problems.append(f"earlier frame was rewritten to {runs(sought)}")
            if "select_plugins" in sought["controls"]["allowed_actions"]:
                problems.append("historical frame still allows plugin edits")
            latest = act(action="seek", run_id=run_id, position=selected["latest_processed_position"])
            if latest["phase"] != "paused" or runs(latest) != ["floor_continuity"]:
                problems.append(f"return to latest showed {runs(latest)} in phase {latest['phase']}")
            target = latest["position"] + 10
            ahead = act(action="seek", run_id=run_id, position=target)
            if ahead["position"] != target + 1 or runs(ahead) != ["floor_continuity"]:
                problems.append(f"seek past recorded frames to {target} landed at {ahead['position'] - 1}")
            act(action="resume", run_id=run_id)
            running = act(
                action="select_plugins", run_id=run_id, step="perception",
                active_plugin_ids=["classical_regions"],
            )
            if running["active_perception_plugin_ids"] != ["classical_regions"]:
                problems.append(f"running selection stayed {running['active_perception_plugin_ids']}")
            if running["phase"] != "running":
                problems.append(f"running selection left phase {running['phase']}")
            act(action="set_loop", run_id=run_id, loop=True)
            wait_for(lambda s: s["position"] > 200 and s["latest_processed_position"] == s["position"] - 1)
            looped = wait_for(lambda s: 0 < s["position"] < 20)
            if runs(looped) != ["classical_regions"] or looped["latest_processed_position"] + 1 != looped["position"]:
                problems.append(
                    f"loop pass did not reprocess: position {looped['position']}, "
                    f"processed {looped['latest_processed_position'] + 1}, runs {runs(looped)}"
                )
    except (TimeoutError, OSError, KeyError) as exc:
        problems.append(f"selector check failed: {type(exc).__name__}: {exc}")
    return problems


def check_memory_selector() -> list[str]:
    """Select memory plugins the way the page does and compare with ``plugins list``'s default.

    A selection while paused keeps the displayed frame and applies on that frame.
    """

    problems: list[str] = []
    completed = subprocess.run(
        [sys.executable, str(ROOT / "cli/automa"), "plugins", "list", "--step", "memory", "--json"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        return [f"plugins list exited {completed.returncode}"]
    expected = json.loads(completed.stdout)["defaults"]["memory"]
    label = "default memory"
    try:
        with serve() as workbench:
            current = workbench.wait_for(lambda s: s["position"] >= 5)
            run_id = current["run_id"]
            paused = workbench.pause(run_id)
            position, frame_id = paused["position"], paused["current_frame"]["frame_id"]
            selected = workbench.act(
                action="select_plugins", run_id=run_id, step="memory", active_plugin_ids=expected,
            )
            applied = selected["machine_detail"]["pipeline"]["memory_plugin_report"]["applied_plugin_ids"]
            if applied != expected or selected["active_memory_plugin_ids"] != expected:
                problems.append(f"{label}: workbench memory {applied}, plugins list {expected}")
            if selected["position"] != position or selected["current_frame"]["frame_id"] != frame_id:
                problems.append(f"{label}: displayed frame moved to {selected['current_frame']['frame_id']}")
            if selected["latest_processed_position"] + 1 != position:
                problems.append(f"{label}: selection changed the processed frame count")
            shown = [
                entry["plugin_id"]
                for entry in (selected["cycle"]["memory"] or {}).get("plugins", [])
            ]
            if shown != expected:
                problems.append(f"{label}: memory step shows {shown}, expected {expected}")
    except (TimeoutError, OSError, KeyError) as exc:
        problems.append(f"memory selector check failed: {type(exc).__name__}: {exc}")
    return problems


def main() -> int:
    problems = check_parity() + check_selector() + check_memory_selector()
    if problems:
        print(f"{len(problems)} problem(s):")
        print("\n".join(problems[:40]))
        return 1
    print(
        "workbench matches inspect frame by frame; plugin selection reprocesses frames; "
        "memory selection matches the default plugins list reports"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
