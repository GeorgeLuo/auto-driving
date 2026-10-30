"""``automa hosts`` run, stop, restart, and status."""

from __future__ import annotations

import argparse
import sys

from ...providers.models import DEFAULT_READINESS_TIMEOUT_S
from ...shared.cli.timeout_input import validate_timeout_input
from ...shared.command_result import CommandResult
from .render import render_hosts_status, render_lifecycle_result
from .service import (
    restart_host_automation,
    start_host_automation_background,
    stop_host_automation,
)
from .status import hosts_status

_HOST_NAME_HELP = "Name of a host created with hosts create."


def register_host_runs(host_commands: argparse._SubParsersAction) -> None:
    host_run = host_commands.add_parser(
        "run",
        help="Start a run on a host and verify one correlated camera/perception publication.",
        description=(
            "Start autonomous movement using the staged plugins. Success requires one camera frame, its "
            "completed perception result, and a healthy current-generation loopback view. "
            "--host-url runs an existing host instead of starting one; that host reports its host name."
        ),
    )
    run_target = host_run.add_mutually_exclusive_group(required=True)
    run_target.add_argument(
        "--name",
        dest="host_name",
        help=_HOST_NAME_HELP,
    )
    run_target.add_argument(
        "--host-url",
        dest="host_url",
        help=(
            "Base URL of a runtime host that is already running. "
            "Run that host instead of starting one. The host reports its host name."
        ),
    )
    host_run.set_defaults(handler=_handle_hosts_run)

    host_stop = host_commands.add_parser(
        "stop",
        help="Stop the host's run; the host stays attached until hosts delete.",
        description=(
            "Stop autonomous movement and its publication monitor. The host stays attached, with its "
            "deployment staged, until hosts delete; its former view is no longer current-generation "
            "available."
        ),
    )
    host_stop.add_argument(
        "--name",
        required=True,
        dest="host_name",
        help=_HOST_NAME_HELP,
    )
    host_stop.set_defaults(handler=_handle_hosts_stop)

    host_status = host_commands.add_parser(
        "status",
        help="Show each host's state and attachment.",
        description=(
            "Show each host's state and whether its simulator lists it; --long adds "
            "its deployment, release, run and view, and each step's staged selection "
            "beside the one the running host applied with its runs and failures. "
            "Exits 1 when the --name host is missing, stopped, unanswering, or errored."
        ),
    )
    host_status.add_argument(
        "--name",
        dest="host_name",
        default=None,
        help="Host name. Omit to list every host.",
    )
    host_status.add_argument(
        "--long",
        action="store_true",
        help="Add each host's deployment, release, staged and applied steps, run and view under its block.",
    )
    host_status.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            f"One wall-clock deadline for host and simulator reads (default: {DEFAULT_READINESS_TIMEOUT_S:g}s)."
        ),
    )
    host_status.add_argument(
        "--json",
        action="store_true",
        help="Print automa_hosts_status JSON.",
    )
    host_status.set_defaults(handler=_handle_hosts_status)

    host_restart = host_commands.add_parser(
        "restart",
        help="Restart the host's run and verify its correlated runtime view.",
        description="Restart the host's run and verify its correlated runtime view.",
    )
    host_restart.add_argument(
        "--name",
        required=True,
        dest="host_name",
        help=_HOST_NAME_HELP,
    )
    host_restart.set_defaults(handler=_handle_hosts_restart)

    for command in (host_run, host_restart):
        command.add_argument(
            "--timeout-s",
            type=float,
            default=DEFAULT_READINESS_TIMEOUT_S,
            help=(
                "Runtime readiness deadline in seconds "
                f"(default: {DEFAULT_READINESS_TIMEOUT_S:g})."
            ),
        )
        command.add_argument(
            "--num-decisions",
            type=int,
            help=(
                "Number of decision cycles to complete (N > 0). Movement requires a count "
                "unless --unbounded is set; --observe-only without one runs until stopped."
            ),
        )
        command.add_argument(
            "--unbounded",
            action="store_true",
            help="Run until stopped, overriding --num-decisions. Stop with hosts stop.",
        )
        command.add_argument(
            "--observe-only",
            action="store_true",
            help="Run plugins without applying their output. The same mode works on every vehicle.",
        )
        command.add_argument(
            "--open-view",
            action="store_true",
            help=(
                "Open the local Automa runtime views after the first correlated "
                "camera/perception publication is healthy."
            ),
        )
        command.add_argument(
            "--record",
            action="store_true",
            help="Save per-frame images and cycle records under a timestamped run directory.",
        )
        command.add_argument(
            "--verbose",
            action="store_true",
            help="Print every frame's detail when output is connected.",
        )
        command.add_argument(
            "--log",
            action="store_true",
            dest="log_to_disk",
            help="Persist background run output to automation.log.",
        )
    for command in (host_stop, host_restart):
        command.add_argument(
            "--wait-s",
            type=float,
            default=3.0,
            help="Seconds to wait for graceful stop before forcing termination.",
        )


def get_hosts_status(
    *,
    host_name: str | None = None,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    long: bool = False,
    json_output: bool = False,
) -> CommandResult:
    """Collect host status and render it.

    JSON and ``--long`` both include each host's run. The exit code follows
    the named host's state; listing every host does not.
    """

    exit_code, hosts = hosts_status(
        host_name=host_name,
        timeout_s=timeout_s,
        include_runs=long or json_output,
    )
    return CommandResult(
        exit_code,
        render_hosts_status(hosts, host_name=host_name, json_output=json_output, long=long),
    )


def _handle_hosts_run(args: argparse.Namespace) -> int:
    if not validate_timeout_input(
        args.timeout_s,
        command="automa hosts run",
        json_output=False,
        human_stream=sys.stdout,
    ):
        return 2
    result = start_host_automation_background(
        host_name=args.host_name,
        host_url=args.host_url,
        timeout_s=args.timeout_s,
        num_decisions=args.num_decisions,
        unbounded=args.unbounded,
        take_control=not args.observe_only,
        record=args.record,
        verbose=args.verbose,
        log_to_disk=args.log_to_disk,
        open_view=args.open_view,
    )
    message = render_lifecycle_result(result)
    if message:
        print(message)
    return result.exit_code


def _handle_hosts_stop(args: argparse.Namespace) -> int:
    result = stop_host_automation(
        host_name=args.host_name,
        wait_s=args.wait_s,
    )
    message = render_lifecycle_result(result)
    if message:
        print(message)
    return result.exit_code


def _handle_hosts_status(args: argparse.Namespace) -> int:
    if not validate_timeout_input(
        args.timeout_s,
        command="automa hosts status",
        json_output=args.json,
        human_stream=sys.stderr,
    ):
        return 2
    result = get_hosts_status(
        host_name=args.host_name,
        timeout_s=args.timeout_s,
        long=args.long,
        json_output=args.json,
    )
    message = result.message
    if message:
        print(message)
    return result.exit_code


def _handle_hosts_restart(args: argparse.Namespace) -> int:
    result = restart_host_automation(
        host_name=args.host_name,
        timeout_s=args.timeout_s,
        num_decisions=args.num_decisions,
        unbounded=args.unbounded,
        take_control=not args.observe_only,
        record=args.record,
        verbose=args.verbose,
        log_to_disk=args.log_to_disk,
        open_view=args.open_view,
        wait_s=args.wait_s,
    )
    message = render_lifecycle_result(result)
    if message:
        print(message)
    return result.exit_code
