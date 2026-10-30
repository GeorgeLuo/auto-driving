"""Stand in for Metrics UI's headless launcher: serve the fake Metrics UI on ``--port``.

Like ``scripts/play-chase-headless.mjs``, it prints its control URL once
ready and serves until terminated.
"""
from __future__ import annotations

import argparse
import shlex
import sys
import threading
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.support.fake_metrics_ui import fake_metrics_ui


def fake_headless_sim_command() -> str:
    """The ``AUTOMA_HEADLESS_SIM_COMMAND`` that runs this launcher."""

    return shlex.join([sys.executable, str(Path(__file__).resolve())])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    with fake_metrics_ui(args.port) as server:
        print(f"CHASE_UI_WS_URL={server.url}", flush=True)
        threading.Event().wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
