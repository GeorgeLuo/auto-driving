"""Background entry point for ``hosts run``.

The process runs the host session and records a failure that happens before
the run has its own state. It formats data events for its standard output. Stop recognizes this module and the
two entry points it replaced.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, TextIO

from ...shared.paths import display_path
from ..step_formatting import format_activation_problems
from .models import WORKER_MODULE, RunEvent
from .render import frame_line, render_run_result, reports_frame, startup_lines
from .service import run_host_automation
from .store import record_host_automation_terminal_result


class RunOutput:
    """The transcript a run monitor updates. Live lines go to ``output``; the closing result does not."""

    def __init__(self, output: TextIO | None = None, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self._output = output

    def startup(self, state: dict[str, Any]) -> None:
        self._write(startup_lines(state))

    def frame(
        self, frame_id: str, *, signals: int, things: int, action: Any, count: int, verbose: bool,
    ) -> None:
        if reports_frame(count, verbose=verbose):
            self._write([frame_line(frame_id, signals=signals, things=things, action=action)])

    def _write(self, lines: list[str]) -> None:
        if self._output is None:
            return
        for line in lines:
            print(line, file=self._output, flush=True)

    def __call__(self, event: RunEvent) -> None:
        if event.kind == "startup":
            self.startup(event.data)
        elif event.kind == "frame":
            self.frame(**event.data, verbose=self.verbose)


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
        emit=RunOutput(sys.stdout, verbose=args.verbose),
    )
    message = render_run_result(result.state, state_path=display_path(result.state_path)) if result.state is not None else result.message
    if result.problems:
        message = format_activation_problems(result.problems)
    if message:
        print(message)
    if args.host_name:
        record_host_automation_terminal_result(host_name=args.host_name, result=result)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(_run_worker(sys.argv[1:]))
