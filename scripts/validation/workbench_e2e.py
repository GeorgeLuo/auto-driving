#!/usr/bin/env python3
"""End-to-end workbench replay check.

Replays the bright high-rate capture through the workbench with lab plugins
that extract signals, including one that names the camera by its legacy spec
and brings its own lab memory. The run is reduced to a fingerprint of per-frame
statuses, selected proposals, memory changes, and per-plugin signal and thing
counts. Timing fields are left out. A second pass serves the workbench and
changes the plugin selection through its API.

    scripts/validation/workbench_e2e.py --write-baseline   # record current behavior
    scripts/validation/workbench_e2e.py                    # compare against it
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "lab/runs/cv-synthesis-20260921/experiment-3/bright-motion-20s-20260921-133121"
PLUGIN_DIR = ROOT / "lab/plugins/perception"
PLUGINS = ("multi_obstruction_tracks", "floor_continuity", "classical_regions")
BASELINE = Path(__file__).with_name("workbench_e2e_baseline.json")


def run_replay() -> dict:
    command = [
        sys.executable, str(ROOT / "cli/automa"), "vehicles", "workbench", "replay", str(SOURCE),
        "--plugin-dir", str(PLUGIN_DIR), "--cadence-ms", "0", "--json",
    ]
    for plugin_id in PLUGINS:
        command += ["--plugin", plugin_id]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(f"workbench replay exited {completed.returncode}")
    return json.loads(completed.stdout)


def check_selector() -> list[str]:
    """Serve the workbench and change the selection the way the page does.

    A paused selection reprocesses the displayed frame, a running one reaches
    the next frame, and a loop pass runs with the current selection.
    """

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    command = [
        sys.executable, str(ROOT / "cli/automa"), "vehicles", "workbench", "replay", str(SOURCE),
        "--plugin-dir", str(PLUGIN_DIR), "--serve", "--port", str(port), "--cadence-ms", "0",
    ]
    for plugin_id in PLUGINS:
        command += ["--plugin", plugin_id]
    server = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def state() -> dict:
        return json.load(urllib.request.urlopen(base + "/api/state", timeout=5))

    def act(**payload) -> dict:
        request = urllib.request.Request(
            base + "/api/action",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.load(urllib.request.urlopen(request, timeout=60))["state"]

    def runs(current: dict) -> list[str]:
        perception = current.get("perception") or {}
        return [run["plugin_id"] for run in perception.get("plugin_runs") or []]

    def wait_for(condition, timeout: float = 120.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                current = state()
                if condition(current):
                    return current
            except OSError:
                pass
            time.sleep(0.2)
        raise TimeoutError("workbench did not reach the expected state")

    problems: list[str] = []
    try:
        current = wait_for(lambda s: s["position"] >= 3)
        run_id = current["run_id"]
        paused = act(action="pause", run_id=run_id)
        frame_id = paused["current_frame"]["frame_id"]
        selected = act(action="select_plugins", run_id=run_id, active_plugin_ids=["floor_continuity"])
        if selected["current_frame"]["frame_id"] != frame_id or runs(selected) != ["floor_continuity"]:
            problems.append(f"paused selection did not reprocess {frame_id}: {runs(selected)}")
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
    finally:
        server.terminate()
        server.wait(timeout=10)
    return problems


def _digest(values: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(values)).encode()).hexdigest()[:16]


def fingerprint(state: dict) -> dict:
    frames = []
    for entry in state["timeline"]:
        decision = entry.get("decision") or {}
        effect = entry.get("memory_effect") or {}
        frames.append({
            "frame_id": entry["frame"]["frame_id"],
            "perception_status": entry.get("perception_status"),
            "decision_status": decision.get("status"),
            "selected_proposal_id": decision.get("selected_proposal_id"),
            "proposed_steering": decision.get("proposed_steering"),
            "memory_record_count": entry.get("memory_record_count"),
            "memory_added": sorted(effect.get("added") or []),
            "memory_removed": sorted(effect.get("removed") or []),
            "memory_retained_digest": _digest(effect.get("retained") or []),
        })
    perception = state.get("perception") or {}
    memory = state.get("memory") or {}
    return {
        "phase": state.get("phase"),
        "failure": state.get("failure"),
        "progress": state.get("progress"),
        "plugin_runs": [
            {key: run.get(key) for key in (
                "plugin_id", "implementation_id", "status", "error", "signal_count", "thing_count",
            )}
            for run in perception.get("plugin_runs") or []
        ],
        "signals": [
            {key: signal.get(key) for key in ("signal_id", "source_plugin_id", "value")}
            for signal in perception.get("signals") or []
        ],
        "memory": {key: memory.get(key) for key in (
            "implementation_id", "health", "record_count", "error",
        )},
        "frames": frames,
    }


def compare(expected: dict, actual: dict) -> list[str]:
    differences = []
    for key in expected:
        if key == "frames":
            continue
        if expected[key] != actual.get(key):
            differences.append(f"{key}: expected {expected[key]!r}, got {actual.get(key)!r}")
    expected_frames, actual_frames = expected["frames"], actual.get("frames", [])
    if len(expected_frames) != len(actual_frames):
        differences.append(f"frames: expected {len(expected_frames)}, got {len(actual_frames)}")
    for want, got in zip(expected_frames, actual_frames):
        for key in want:
            if want[key] != got.get(key):
                differences.append(
                    f"{want['frame_id']} {key}: expected {want[key]!r}, got {got.get(key)!r}"
                )
    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-baseline", action="store_true", help="Record the current run as the baseline.")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    args = parser.parse_args()

    actual = fingerprint(run_replay())
    if args.write_baseline:
        args.baseline.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written: {args.baseline} ({len(actual['frames'])} frames)")
        return 0
    expected = json.loads(args.baseline.read_text(encoding="utf-8"))
    differences = compare(expected, actual) + check_selector()
    if differences:
        print(f"{len(differences)} difference(s) from {args.baseline}:")
        print("\n".join(differences[:40]))
        return 1
    print(
        f"matches baseline: {len(actual['frames'])} frames, {len(actual['plugin_runs'])} plugins; "
        "plugin selection reprocesses frames"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
