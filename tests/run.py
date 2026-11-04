#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
for path in (ROOT / "cli", ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from automa_cli.hosts.deployment.picar_deploy import inspect_picar_autonomy_runtime
from implementations.vehicle.picar.defaults import DEFAULT_LOCAL_CAR_BASE_URL, LOCAL_CAR_BASE_URL_ENV

SYSTEMS_GROUPS = ("commands", "journeys", "frontend", "packaging", "qca", "live")
# Audit events that start another program, mapped to the index of their argv.
SPAWN_EVENTS = {
    "subprocess.Popen": 1,
    "os.system": 0,
    "os.exec": 1,
    "os.posix_spawn": 1,
    "os.spawn": 2,
    "os.fork": None,
    "os.forkpty": None,
    "pty.spawn": 0,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tests/run.py",
        description="Run the in-process deterministic suite, or the systems suite.",
    )
    parser.add_argument(
        "--systems",
        nargs="*",
        choices=SYSTEMS_GROUPS,
        metavar="GROUP",
        help=(
            "Run tests/systems, the tests that start another program. "
            f"Name groups to run only those: {', '.join(SYSTEMS_GROUPS)}."
        ),
    )
    parser.add_argument(
        "-k",
        dest="patterns",
        action="append",
        metavar="PATTERN",
        help=(
            "Run only the tests whose ID (module.Class.method) matches PATTERN, as "
            "unittest's -k does: a substring, or a glob when it contains *. Repeatable."
        ),
    )
    parser.add_argument(
        "--durations",
        type=int,
        default=10,
        help="Report the slowest N tests, including setup and cleanup (default: 10).",
    )
    parser.add_argument(
        "--live-sim",
        action="store_true",
        help=(
            "Run the systems suite, including the live simulator smoke test against the running "
            "Chase simulator at $CHASE_UI_WS_URL (default: ws://localhost:5050/ws/control)."
        ),
    )
    parser.add_argument(
        "--live-pi",
        action="store_true",
        help="Run the systems suite, including the read-only Pi connectivity and autonomy activation smoke test.",
    )
    parser.add_argument(
        "--picar-url",
        default=os.environ.get(LOCAL_CAR_BASE_URL_ENV) or DEFAULT_LOCAL_CAR_BASE_URL,
        help=(
            f"Pi Donkey server base URL for --live-pi (default: ${LOCAL_CAR_BASE_URL_ENV}, "
            f"which the CLI's discovery also reads, else {DEFAULT_LOCAL_CAR_BASE_URL})."
        ),
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
        status = inspect_picar_autonomy_runtime(
            base_url=normalized_url,
            timeout_s=timeout_s,
        )
    except RuntimeError as exc:
        _print_pi_unavailable(
            endpoint=f"{normalized_url}/host/status",
            reason=str(exc),
        )
        return False

    if status["mode"] != "manual":
        _print_pi_unavailable(
            endpoint=str(status["status_url"]),
            reason=f"mode is {status['mode']!r}; expected 'manual'",
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
                f"mode: {status['mode']}",
                *(
                    f"{step}: {', '.join(plugins) if plugins is not None else '(not loaded)'}"
                    for step, plugins in status["steps"].items()
                ),
                "side effects: read-only status requests only; vehicle movement is disabled",
            ]
        ),
        flush=True,
    )
    return True


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []

    def startTest(self, test):
        super().startTest(test)
        self.started = time.perf_counter()

    def stopTest(self, test):
        self.timings.append((time.perf_counter() - self.started, test.id()))
        super().stopTest(test)


def forbid_spawns() -> None:
    """Fail a deterministic test that starts another program."""

    def hook(event: str, args: tuple) -> None:
        if event not in SPAWN_EVENTS:
            return
        index = SPAWN_EVENTS[event]
        argv = "" if index is None else f" {args[index]!r}"
        raise RuntimeError(
            f"{event}{argv}: deterministic tests run in this process; "
            "a test that starts a program belongs in tests/systems/"
        )

    sys.addaudithook(hook)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.live_pi and args.pi_timeout_s <= 0:
        parser.error("--pi-timeout-s must be greater than zero")
    if args.live_sim:
        os.environ["AUTOMA_TEST_LIVE_SIM"] = "1"
    if args.live_pi and not prepare_live_pi(
        base_url=args.picar_url,
        timeout_s=args.pi_timeout_s,
    ):
        return 2

    deterministic = args.systems is None and not (args.live_sim or args.live_pi)
    if deterministic:
        directories = [TESTS_DIR / "deterministic"]
    elif args.systems:
        directories = [TESTS_DIR / "systems" / group for group in dict.fromkeys(args.systems)]
    else:
        directories = [TESTS_DIR / "systems"]
    loader = unittest.TestLoader()
    if args.patterns:
        loader.testNamePatterns = [
            pattern if "*" in pattern else f"*{pattern}*" for pattern in args.patterns
        ]
    suite = unittest.TestSuite(
        loader.discover(str(directory), pattern="test_*.py", top_level_dir=str(ROOT))
        for directory in directories
    )
    runner = unittest.TextTestRunner(verbosity=2, resultclass=TimedResult)
    if deterministic:
        forbid_spawns()
    result = runner.run(suite)
    if args.patterns and not result.testsRun:
        print(f"No tests match -k {' -k '.join(args.patterns)}.", file=sys.stderr)
        return 1
    if args.durations > 0:
        print("\nSlowest tests (including setup and cleanup):", file=sys.stderr)
        for seconds, name in sorted(result.timings, reverse=True)[: args.durations]:
            print(f"{seconds:7.3f}s {name}", file=sys.stderr)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
