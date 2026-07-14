#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


SUPPORTED_MODES = {
    "launch_fails",
    "offline_then_launch",
    "online",
    "online_frontend_drops",
    "online_frontend_stale_until_open",
    "online_no_frontend_then_open",
}


def fake_simeval_environment(root: Path, mode: str) -> dict[str, str]:
    """Point Automa at this executable and isolate its state under ``root``."""
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported fake simeval mode: {mode}")
    return {
        "AUTOMA_SIMEVAL_BIN": str(Path(__file__).resolve()),
        "FAKE_SIMEVAL_MODE": mode,
        "FAKE_SIMEVAL_STATE": str(root / "state.json"),
        "FAKE_SIMEVAL_TRACE": str(root / "trace.jsonl"),
    }


def read_simeval_calls(root: Path) -> list[list[str]]:
    trace = root / "trace.jsonl"
    if not trace.exists():
        return []
    return [
        json.loads(line)
        for line in trace.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _status_payload(online: bool) -> dict:
    return {
        "status": "success" if online else "failed",
        "local": {
            "deployments": [
                {
                    "key": "3000",
                    "server": "http://127.0.0.1:3000/api",
                    "workspace": "/tmp/fake-sim",
                    "overall": "running" if online else "stopped",
                    "processAlive": online,
                    "health": {"ok": online, "error": None if online else "fetch failed"},
                    "status": {
                        "ok": online,
                        "error": None if online else "Skipped status check (health failed).",
                    },
                }
            ]
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = os.environ["FAKE_SIMEVAL_MODE"]
    state_path = Path(os.environ["FAKE_SIMEVAL_STATE"])
    trace_path = Path(os.environ["FAKE_SIMEVAL_TRACE"])

    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\n")

    if args[:1] == ["status"]:
        state = _read_state(state_path)
        online = mode == "online" or (
            mode == "offline_then_launch" and state.get("launched") is True
        )
        online = online or mode in (
            "online_no_frontend_then_open",
            "online_frontend_stale_until_open",
        )
        print(json.dumps(_status_payload(online)))
        return 0 if online else 1

    if args[:2] == ["ui", "verify"]:
        state = _read_state(state_path)
        frontend_connected = mode in (
            "online",
            "offline_then_launch",
            "online_frontend_drops",
        )
        frontend_connected = frontend_connected or bool(state.get("frontend_connected"))
        frontend_connected = frontend_connected or mode == "online_frontend_stale_until_open"
        print(
            json.dumps(
                {
                    "status": "success" if frontend_connected else "failed",
                    "server": {
                        "url": "http://127.0.0.1:5050",
                        "autoServed": "--auto-serve" in args,
                    },
                    "samples": [
                        {
                            "frontendConnected": frontend_connected,
                            "stateSource": "live",
                        }
                    ],
                    "failures": []
                    if frontend_connected
                    else [
                        {
                            "type": "frontend-not-connected",
                            "message": "Frontend not connected.",
                        }
                    ],
                }
            )
        )
        return 0

    if args[:2] == ["deploy", "start"]:
        if mode == "launch_fails":
            print("launch failed", file=sys.stderr)
            return 1
        state = _read_state(state_path)
        state["launched"] = True
        _write_state(state_path, state)
        print(json.dumps({"status": "success", "action": "deploy-start"}))
        return 0

    if args[:2] == ["ui", "subapp"]:
        print(
            json.dumps(
                {
                    "status": "success",
                    "action": "subapp",
                    "app": "play",
                    "uiUrl": "ws://127.0.0.1:5050/ws/control",
                }
            )
        )
        return 0

    if args[:2] == ["ui", "play-game-action"]:
        state = _read_state(state_path)
        if mode == "online_frontend_stale_until_open" and not state.get("play_ready"):
            print("Timed out waiting for UI ack.", file=sys.stderr)
            return 1
        print(json.dumps({"status": "success", "action": "play-game-action"}))
        return 0

    if args[:2] == ["ui", "play-debug"]:
        state = _read_state(state_path)
        frontend_connected = mode in (
            "online",
            "offline_then_launch",
            "online_frontend_drops",
        )
        frontend_connected = frontend_connected or bool(state.get("frontend_connected"))
        if mode == "online_frontend_stale_until_open" and not state.get("play_ready"):
            print("Timed out waiting for Play debug.", file=sys.stderr)
            return 1
        if not frontend_connected:
            print("Frontend not connected", file=sys.stderr)
            return 1
        if mode == "online_frontend_drops":
            debug_calls = int(state.get("debug_calls", 0))
            state["debug_calls"] = debug_calls + 1
            _write_state(state_path, state)
            if debug_calls > 0:
                print("Frontend disconnected after setup", file=sys.stderr)
                return 1
        print(json.dumps({"gameId": "chase", "frameIndex": 7}))
        return 0

    if args[:2] == ["deploy", "list"]:
        print(json.dumps({"deployments": []}))
        return 0

    print(f"unexpected fake simeval args: {args}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
