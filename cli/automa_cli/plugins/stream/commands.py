"""Registration, terminal writing, and exit status for ``plugins stream``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import TextIO

from autonomy.decision_cycle.activation import DECISION_STEPS

from ...shared.command_result import CommandResult
from .models import StreamEvent, StreamResult
from .render import render_stream
from .service import stream_host

STREAM_STEPS = ("perception", "memory", *DECISION_STEPS)


def register_stream(commands: argparse._SubParsersAction) -> None:
    stream = commands.add_parser(
        "stream", help="Show a step's latest output on a host, replacing the terminal view as it updates.",
        description=(
            "Poll a running host for one step's latest output. perception reads the host's latest "
            "observation; memory shows each applied plugin's health, epoch and record count and the "
            "evidence publisher; proposal, plan and action print the step's record from the latest fresh "
            "cycle whose generation matches the step selections, with the observation and memory a "
            "proposal read and the host's application of an action. A running terminal stream serves "
            "the local runtime view and shows its URLs: the perception view, memory map and action view."
        ),
        epilog="Streaming never starts automation or changes what a host has armed.",
    )
    stream.add_argument("--host-name", required=True, help="Host name from hosts status.")
    stream.add_argument("--step", required=True, choices=STREAM_STEPS, help="Cycle step to stream.")
    stream.add_argument("--refresh-s", type=float, default=0.5, help="Refresh cadence for the replacing terminal view.")
    stream.add_argument("--once", action="store_true",
                        help="Render once without a local view and exit; exit 2 unless the step is live.")
    stream.add_argument("--no-clear", action="store_true",
                        help="Print each render below the previous one, keeping earlier renders in scrollback.")
    stream.add_argument("--json", action="store_true", help=(
        "Print one JSON probe per refresh in place of the terminal and local views: host_perception_live, "
        "host_memory_live or host_<step>_stream, each with a status. A host that cannot be found prints an "
        "unavailable probe and exits 2."
    ))
    stream.set_defaults(handler=_handle_plugins_stream)


def write_stream_frame(output: TextIO | None, text: str, *, clear: bool) -> None:
    """Write and flush one formatted frame. The caller chooses the stream."""

    if output is None:
        return
    if clear:
        output.write("\033[2J\033[H")
    output.write(text + "\n")
    output.flush()


def stream_host_step(
    step: str,
    *,
    host_name: str,
    refresh_s: float = 0.5,
    once: bool = False,
    no_clear: bool = False,
    json_output: bool = False,
    output: TextIO | None = None,
    timeout_s: float = 3.0,
) -> CommandResult:
    """Stream ``step`` and return the exit code and any message still to print.

    JSON and ``--once`` do not serve a local view. Text, including one shot,
    reads the publication details that screen shows. Only a continuous text
    stream clears the terminal, and only unless ``--no-clear``.
    """

    return _present(
        lambda emit: stream_host(
            step, host_name=host_name, refresh_s=refresh_s, once=once, timeout_s=timeout_s,
            serve_view=not json_output and not once,
            include_publication=not json_output,
            emit=emit,
        ),
        output=output, json_output=json_output, clear=not (no_clear or once or json_output),
    )


def _handle_plugins_stream(args: argparse.Namespace) -> int:
    result = stream_host_step(
        args.step, host_name=args.host_name, refresh_s=args.refresh_s, once=args.once,
        no_clear=args.no_clear, json_output=args.json, output=sys.stdout,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _present(
    operation: Callable[[Callable[[StreamEvent], None]], StreamResult],
    *,
    output: TextIO | None,
    json_output: bool,
    clear: bool,
) -> CommandResult:
    delivered: dict[str, str | None] = {"line": None}

    def emit(event: StreamEvent) -> None:
        line = render_stream(event, json_output=json_output)
        delivered["line"] = line
        write_stream_frame(output, line, clear=clear)

    result = operation(emit)
    return _command_result(result, output=output, json_output=json_output, rendered_line=delivered["line"])


def _command_result(
    result: StreamResult, *, output: TextIO | None, json_output: bool, rendered_line: str | None,
) -> CommandResult:
    """Map a stream result to the message the CLI handler prints.

    Frames already written to ``output`` leave the message empty so the handler
    does not print them again. A one-shot call with no output returns the line.
    A text preflight returns its sentence. A JSON preflight prints one probe.
    """

    if result.exit_code == 130:
        return CommandResult(130, "")
    if result.preflight:
        if not json_output:
            return CommandResult(result.exit_code, result.message)
        line = json.dumps(result.live, sort_keys=True)
        if output is not None:
            print(line, file=output, flush=True)
        return CommandResult(result.exit_code, "" if output is not None else line)
    if output is not None:
        return CommandResult(result.exit_code, "")
    if result.exit_code == 0:
        return CommandResult(0, rendered_line or "")
    return CommandResult(result.exit_code, rendered_line or result.message)
