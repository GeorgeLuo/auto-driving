#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
for path in (ROOT / "cli", ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from automa_cli.deploy import inspect_physical_autonomy_runtime
from automa_cli.simulators import ensure_simulator
from implementations.vehicle.picar.defaults import DEFAULT_LOCAL_CAR_BASE_URL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tests/run.py",
        description="Run the repository-owned deterministic test suite.",
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
    parser.add_argument(
        "--live-pi",
        action="store_true",
        help="Include the read-only Pi connectivity and autonomy activation smoke test.",
    )
    parser.add_argument(
        "--picar-url",
        default=DEFAULT_LOCAL_CAR_BASE_URL,
        help=f"Pi Donkey server base URL for --live-pi (default: {DEFAULT_LOCAL_CAR_BASE_URL}).",
    )
    parser.add_argument(
        "--pi-timeout-s",
        type=float,
        default=3.0,
        help="Per-request Pi readiness timeout in seconds for --live-pi (default: 3.0).",
    )
    return parser


def _print_pi_unavailable(*, endpoint: str, reason: str) -> None:
    print(
        "\n".join(
            [
                "Pi live validation",
                "------------------",
                "result: unavailable",
                f"endpoint: {endpoint}",
                f"reason: {reason}",
                "side effects: none; only a read-only status request was attempted",
                "No drive, mode-change, restart, or SSH command was sent.",
            ]
        ),
        flush=True,
    )


def prepare_live_pi(*, base_url: str, timeout_s: float) -> bool:
    normalized_url = base_url.strip().rstrip("/")
    print("Checking read-only Pi runtime readiness...", flush=True)
    try:
        status = inspect_physical_autonomy_runtime(
            base_url=normalized_url,
            timeout_s=timeout_s,
        )
    except RuntimeError as exc:
        _print_pi_unavailable(
            endpoint=f"{normalized_url}/autonomy/status",
            reason=str(exc),
        )
        return False

    if status["drive_mode"] != "user":
        _print_pi_unavailable(
            endpoint=str(status["status_url"]),
            reason=f"drive mode is {status['drive_mode']!r}; expected 'user'",
        )
        return False

    os.environ["AUTOMA_TEST_LIVE_PI"] = "1"
    os.environ["AUTOMA_TEST_PICAR_URL"] = normalized_url
    os.environ["AUTOMA_TEST_PICAR_TIMEOUT_S"] = str(timeout_s)
    print(
        "\n".join(
            [
                "Pi live validation",
                "------------------",
                "result: ready",
                f"endpoint: {status['status_url']}",
                f"drive mode: {status['drive_mode']}",
                f"decision engine: {status['engine']}",
                f"perception: {status['perception_algorithm']}",
                "side effects: read-only status requests only; vehicle movement is disabled",
            ]
        ),
        flush=True,
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.live_pi and args.pi_timeout_s <= 0:
        parser.error("--pi-timeout-s must be greater than zero")
    if args.live_sim:
        print("Ensuring simulator is ready for live tests...", flush=True)
        ensure_result = ensure_simulator(timeout_ms=args.sim_timeout_ms, json_output=False)
        if ensure_result.message:
            print(ensure_result.message, flush=True)
        if ensure_result.exit_code != 0:
            return ensure_result.exit_code
        os.environ["AUTOMA_TEST_LIVE_SIM"] = "1"
    if args.live_pi and not prepare_live_pi(
        base_url=args.picar_url,
        timeout_s=args.pi_timeout_s,
    ):
        return 2

    suite = unittest.defaultTestLoader.discover(
        str(TESTS_DIR),
        pattern="test_*.py",
        top_level_dir=str(ROOT),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
