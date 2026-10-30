"""Background entry point for ``hosts run``.

The process runs the host session and records a failure that happens before
the run has its own state. It chooses the transcript for its own standard
output and passes that to ``service``. Stop recognizes this module and the
two entry points it replaced.
"""

from __future__ import annotations

import argparse
import sys

from .models import WORKER_MODULE
from .render import RunOutput
from .service import record_host_automation_terminal_result, run_host_automation


def _run_worker(argv: list[str]) -> int:
    """Run in the foreground for hosts run, reporting a failure before the run starts."""

    parser = argparse.ArgumentParser(prog=WORKER_MODULE)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", dest="host_name")
    target.add_argument("--host-url")
    parser.add_argument("--timeout-s", type=float, required=True)
    parser.add_argument("--num-decisions", type=int)
    parser.add_argument("--unbounded", action="store_true")
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    result = run_host_automation(
        host_name=args.host_name,
        host_url=args.host_url,
        timeout_s=args.timeout_s,
        num_decisions=args.num_decisions,
        unbounded=args.unbounded,
        take_control=not args.observe_only,
        record=args.record,
        verbose=args.verbose,
        transcript=RunOutput(sys.stdout),
    )
    if result.message:
        print(result.message)
    if args.host_name:
        record_host_automation_terminal_result(host_name=args.host_name, result=result)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(_run_worker(sys.argv[1:]))
