#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def fake_browser_environment(root: Path) -> dict[str, str]:
    """Point Automa at this executable and share fake simulator state."""
    return {
        "AUTOMA_BROWSER_OPEN_COMMAND": str(Path(__file__).resolve()),
        "FAKE_BROWSER_STATE": str(root / "state.json"),
        "FAKE_BROWSER_TRACE": str(root / "browser_trace.txt"),
    }


def read_browser_calls(root: Path) -> list[str]:
    trace = root / "browser_trace.txt"
    if not trace.exists():
        return []
    return [line for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    state_path = Path(os.environ["FAKE_BROWSER_STATE"])
    trace_path = Path(os.environ["FAKE_BROWSER_TRACE"])
    url = args[-1]

    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    state["frontend_connected"] = True
    state["play_ready"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")

    previous = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""
    trace_path.write_text(previous + url + "\n", encoding="utf-8")
    print(f"opened {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
