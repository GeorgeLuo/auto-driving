#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "cli" / "tests"
for path in (ROOT / "cli", ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from automa_cli.simulators import ensure_simulator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_tests.py",
        description="Run the Automa CLI scenario harness.",
    )
    parser.add_argument(
        "--live-sim",
        action="store_true",
        help="Ensure the Chase simulator is usable, then include live simulator smoke tests.",
    )
    parser.add_argument(
        "--sim-timeout-ms",
        type=int,
        default=2000,
        help="Simulator readiness probe timeout in milliseconds for --live-sim.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.live_sim:
        print("Ensuring simulator is ready for live tests...", flush=True)
        ensure_result = ensure_simulator(timeout_ms=args.sim_timeout_ms, json_output=False)
        if ensure_result.message:
            print(ensure_result.message, flush=True)
        if ensure_result.exit_code != 0:
            return ensure_result.exit_code
        os.environ["AUTOMA_TEST_LIVE_SIM"] = "1"

    suite = unittest.defaultTestLoader.discover(str(TESTS_DIR), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
