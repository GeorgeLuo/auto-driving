"""Background entry point for ``hosts run``.

The process runs the host session and records a failure that happens before
the run has its own state. It prints the accepted run and its ending to its
standard output. Stop recognizes a worker only by this module.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any, TextIO

from ...shared.paths import display_path
from ..step_formatting import format_activation_problems
from .models import WORKER_MODULE
from .render import render_run_result, startup_lines
from .service import run_host_automation
from .store import record_host_automation_terminal_result


def startup_printer(output: TextIO) -> Callable[[dict[str, Any]], None]:
    """Print the accepted run's lines to ``output``; the closing result is printed separately."""

    def print_startup(state: dict[str, Any]) -> None:
        for line in startup_lines(state):
            print(line, file=output, flush=True)

    return print_startup


def _run_worker(argv: list[str]) -> int:
    """Run in the foreground for hosts run, reporting a failure before the run starts."""

    parser = argparse.ArgumentParser(prog=WORKER_MODULE)
    # ``--name`` is the run's host; ``--host-url`` names that host's existing runtime when it was given.
    parser.add_argument("--name", dest="host_name", required=True)
    parser.add_argument("--host-url")
    parser.add_argument("--timeout-s", type=float, required=True)
    parser.add_argument("--num-decisions", type=int)
    parser.add_argument("--unbounded", action="store_true")
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args(argv)
    result = run_host_automation(
        host_name=None if args.host_url else args.host_name,
        host_url=args.host_url,
        timeout_s=args.timeout_s,
        num_decisions=args.num_decisions,
        unbounded=args.unbounded,
        take_control=not args.observe_only,
        record=args.record,
        emit=startup_printer(sys.stdout),
    )
    message = render_run_result(result.state, state_path=display_path(result.state_path)) if result.state is not None else result.message
    if result.problems:
        message = format_activation_problems(result.problems)
    if message:
        print(message)
    if args.host_url is None:
        record_host_automation_terminal_result(host_name=args.host_name, result=result)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(_run_worker(sys.argv[1:]))
