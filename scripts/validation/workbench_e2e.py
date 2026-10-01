#!/usr/bin/env python3
"""End-to-end workbench replay check.

Replays the bright high-rate capture through the workbench with packaged
perception plugins that extract signals. The run is reduced to a fingerprint of per-frame
statuses, selected proposals, memory changes, and per-plugin signal and thing
counts. Timing fields are left out. The same replay at a git ref, run in a
temporary worktree, is the reference, so no recorded output is kept in the
repository. A second pass serves the workbench and changes the plugin selection
through its API.

    scripts/validation/workbench_e2e.py                    # compare against the branch point
    scripts/validation/workbench_e2e.py --against main     # compare against another ref
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "lab/runs/cv-synthesis-20260921/experiment-3/bright-motion-20s-20260921-133121"
PLUGINS = ("multi_obstruction_tracks", "floor_continuity", "classical_regions")


def run_replay(checkout: Path = ROOT) -> dict:
    """Replay the capture with the CLI and plugins of ``checkout``."""

    command = [
        sys.executable, str(checkout / "cli/automa"), "vehicles", "workbench", "replay", str(SOURCE),
        "--cadence-ms", "0", "--json",
    ]
    for plugin_id in PLUGINS:
        command += ["--plugin", plugin_id]
    completed = subprocess.run(command, cwd=checkout, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(f"workbench replay in {checkout} exited {completed.returncode}")
    return json.loads(completed.stdout)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()


def replay_at(ref: str) -> dict:
    """Replay the capture in a temporary worktree checked out at ``ref``."""

    commit = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    with tempfile.TemporaryDirectory(prefix="workbench-e2e-") as scratch:
        checkout = Path(scratch) / "checkout"
        git("worktree", "add", "--detach", str(checkout), commit)
        try:
            return run_replay(checkout)
        finally:
            git("worktree", "remove", "--force", str(checkout))


def check_selector() -> list[str]:
    """Serve the workbench and change the selection the way the page does.

    A paused selection reprocesses the displayed frame, seeking shows earlier
    frames with the current selection, seeking past the recorded frames lands
    there, a running selection reaches the next frame, and a loop pass runs
    with the current selection.
    """

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    command = [
        sys.executable, str(ROOT / "cli/automa"), "vehicles", "workbench", "replay", str(SOURCE),
        "--serve", "--port", str(port), "--cadence-ms", "0",
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
                "plugin_id", "status", "error", "signal_count", "thing_count",
            )}
            for run in perception.get("plugin_runs") or []
        ],
        "signals": [
            {key: signal.get(key) for key in ("signal_id", "source_plugin_id", "value")}
            for signal in perception.get("signals") or []
        ],
        "memory": {key: memory.get(key) for key in (
            "plugin_id", "health", "record_count", "error",
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
    parser.add_argument(
        "--against", metavar="REF",
        help="Git ref to compare against (default: where HEAD branched from origin/main).",
    )
    args = parser.parse_args()

    ref = args.against or git("merge-base", "HEAD", "origin/main")
    expected = fingerprint(replay_at(ref))
    actual = fingerprint(run_replay())
    differences = compare(expected, actual) + check_selector()
    if differences:
        print(f"{len(differences)} difference(s) from {ref}:")
        print("\n".join(differences[:40]))
        return 1
    print(
        f"matches {ref}: {len(actual['frames'])} frames, {len(actual['plugin_runs'])} plugins; "
        "plugin selection reprocesses frames"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
