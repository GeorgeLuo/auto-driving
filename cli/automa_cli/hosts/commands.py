"""The ``automa hosts`` parser and its handlers."""

from __future__ import annotations

import argparse
import sys

from ..shared.timeout_input import validate_timeout_input
from ..vehicles.probes import DEFAULT_READINESS_TIMEOUT_S
from ..vehicles.providers import PROVIDERS
from .automation import (
    restart_host_automation,
    start_host_automation_background,
)
from .automation_status import get_hosts_status
from .automation_stop import stop_host_automation
from .deploy import create_host, update_host, delete_host
from .viewer import serve_host_viewer


def register_hosts(subcommands: argparse._SubParsersAction) -> None:
    hosts = subcommands.add_parser("hosts", help="Create, configure, and operate vehicle controller hosts.")
    hosts.set_defaults(handler=_handle_hosts_help)
    host_commands = hosts.add_subparsers(dest="host_command")
    host_help = host_commands.add_parser("help", help="Show host-level commands.")
    host_help.set_defaults(handler=_handle_hosts_help)

    _register_hosts_viewer(host_commands)
    _register_hosts_lifecycle(host_commands)


def _register_hosts_viewer(host_commands: argparse._SubParsersAction) -> None:
    viewer = host_commands.add_parser(
        "viewer",
        help="Open the runtime viewer on a host.",
        description=(
            "Serve the runtime viewer fed from the host named by --name, or at --host-url "
            "(the URL hosts status shows under the host), until interrupted. Exits 2 when "
            "no such host exists, it is not running, or it does not answer."
        ),
    )
    viewer_target = viewer.add_mutually_exclusive_group(required=True)
    viewer_target.add_argument("--name", dest="host_name", help=_HOST_NAME_HELP)
    viewer_target.add_argument("--host-url", help="The host's URL from hosts status.")
    viewer.set_defaults(handler=_handle_hosts_viewer)


def _register_hosts_lifecycle(host_commands: argparse._SubParsersAction) -> None:
    host_create = host_commands.add_parser("create", help="Create and start a host.")
    host_update = host_commands.add_parser("update", help="Package and deploy a host's release.")
    host_delete = host_commands.add_parser("delete", help="Stop the host and its run, and remove its local files.")
    for command in (host_create, host_update, host_delete):
        command.add_argument("--name", dest="host_name", required=True, help="Host name.")
        command.add_argument("--verbose", action="store_true")
    for command in (host_create, host_update):
        command.add_argument("--dry-run", action="store_true", help="Print the deployment plan without writing or connecting.")
        command.add_argument("--json", action="store_true")
        command.add_argument("--drive-args", help="Persist PiCar drive arguments; update requires --restart.")
    host_create.add_argument("--provider", required=True, choices=PROVIDERS,
                             help="What the host controls: the simulator's chaser (chase-sim) or a PiCar (picar).")
    host_create.add_argument("--url", required=True,
                             help="The vehicle's URL: the simulator's HTTP(S) origin or WS(S) control URL, "
                                  "or the PiCar's HTTP(S) URL. vehicles status lists both.")
    host_create.add_argument("--ssh-target", help="PiCar SSH target (default: PI_HOST or the URL host).")
    host_create.add_argument("--pi-home", help="PiCar home (default: PI_HOME or /home/piracer).")
    host_update.add_argument("--restart", action="store_true", help="Restart the host onto this release.")
    host_create.set_defaults(handler=_handle_hosts_create)
    host_update.set_defaults(handler=_handle_hosts_update)
    host_delete.set_defaults(handler=_handle_hosts_delete)

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


def _handle_hosts_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa hosts commands",
                "",
                "- create      create and start a host",
                "- update      package and deploy a host's release",
                "- delete      stop the host and its run, and remove its local files",
                "- run         start a run and verify camera + perception + current view",
                "- stop        stop the run; the host stays attached until hosts delete",
                "- restart     stop and start the run",
                "- status      show each host's state and attachment",
                "- viewer      open a host's runtime viewer",
                "- help        show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa hosts <command> --help",
            ]
        )
    )
    return 0


_HOST_NAME_HELP = (
    "Name of a host created with hosts create."
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
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_hosts_stop(args: argparse.Namespace) -> int:
    result = stop_host_automation(
        host_name=args.host_name,
        wait_s=args.wait_s,
    )
    if result.message:
        print(result.message)
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
    if result.message:
        print(result.message)
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
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_hosts_create(args: argparse.Namespace) -> int:
    result = create_host(
        host_name=args.host_name, provider=args.provider, url=args.url,
        ssh_target=args.ssh_target, pi_home=args.pi_home, drive_args=args.drive_args,
        dry_run=args.dry_run, json_output=args.json, verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
    )
    print(result.message)
    return result.exit_code


def _handle_hosts_viewer(args: argparse.Namespace) -> int:
    result = serve_host_viewer(host_name=args.host_name, host_url=args.host_url)
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_hosts_update(args: argparse.Namespace) -> int:
    result = update_host(
        host_name=args.host_name, dry_run=args.dry_run, restart=args.restart,
        drive_args=args.drive_args, json_output=args.json, verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
    )
    print(result.message)
    return result.exit_code


def _handle_hosts_delete(args: argparse.Namespace) -> int:
    result = delete_host(host_name=args.host_name, verbose=args.verbose, output=sys.stdout)
    print(result.message)
    return result.exit_code
