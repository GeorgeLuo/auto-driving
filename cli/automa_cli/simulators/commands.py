"""The ``automa simulators`` parser and its handlers."""

from __future__ import annotations

import argparse
import sys

from ..providers.models import DEFAULT_READINESS_TIMEOUT_S
from ..providers.urls import normalize_chase_url
from ..shared.cli.timeout_input import validate_timeout_input
from .render import render_simulator_result, render_simulators_status
from .service import create_simulator, delete_simulator
from .status import simulators_status


def register_simulators(subcommands: argparse._SubParsersAction) -> None:
    simulators = subcommands.add_parser("simulators", help="Create, show, and delete headless simulators.")
    simulators.set_defaults(handler=_handle_simulators_help)
    simulator_commands = simulators.add_subparsers(dest="simulator_command")

    simulators_help = simulator_commands.add_parser(
        "help",
        help="Show simulator-level commands.",
    )
    simulators_help.set_defaults(handler=_handle_simulators_help)

    simulators_create = simulator_commands.add_parser(
        "create",
        help="Start a headless Chase simulator that vehicles status lists.",
        description=(
            "Start a headless Chase simulator with Metrics UI's default headless configuration, "
            "from the Metrics UI checkout at AUTOMA_METRICS_UI_DIR, on the first free port from 5051. "
            "It runs until simulators delete. Exits 2 when it is not ready within 45 s."
        ),
    )
    simulators_create.set_defaults(handler=_handle_simulators_create)

    simulators_delete = simulator_commands.add_parser(
        "delete",
        help="End a simulator that simulators create started.",
        description=(
            "End the simulator simulators create started at --url, or remove its record when "
            "it has exited. Exits 2 when no created simulator has that URL or a host was created at it."
        ),
    )
    simulators_delete.add_argument("--url", required=True, help="The simulator's URL from simulators create.")
    simulators_delete.set_defaults(handler=_handle_simulators_delete)

    simulators_status_command = simulator_commands.add_parser(
        "status",
        help="Show each simulator simulators create started.",
        description=(
            "Show each simulator simulators create started, whether it answers, its frontend, "
            "and the hosts created at it; --long adds its Metrics UI checkout, configuration and log. "
            "Exits 1 when the --url simulator is missing, exited, or unreachable."
        ),
    )
    simulators_status_command.add_argument(
        "--url",
        help="The simulator's URL from simulators create. Omit to list every one.",
    )
    simulators_status_command.add_argument(
        "--long",
        action="store_true",
        help="Add the launcher's details under each block.",
    )
    simulators_status_command.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=f"One wall-clock deadline for the simulator probes (default: {DEFAULT_READINESS_TIMEOUT_S:g}s).",
    )
    simulators_status_command.add_argument(
        "--json",
        action="store_true",
        help="Print automa_simulators_status JSON.",
    )
    simulators_status_command.set_defaults(handler=_handle_simulators_status)


def _handle_simulators_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa simulators commands",
                "",
                "- create   start a headless Chase simulator that vehicles status lists",
                "- delete   end a simulator that create started",
                "- status   show each simulator create started",
                "- help     show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa simulators <command> --help",
            ]
        )
    )
    return 0


def _handle_simulators_create(args: argparse.Namespace) -> int:
    result = create_simulator()
    print(render_simulator_result(result))
    return result.exit_code


def _handle_simulators_delete(args: argparse.Namespace) -> int:
    result = delete_simulator(url=args.url)
    print(render_simulator_result(result))
    return result.exit_code


def _handle_simulators_status(args: argparse.Namespace) -> int:
    if not validate_timeout_input(
        args.timeout_s,
        command="automa simulators status",
        json_output=args.json,
        human_stream=sys.stderr,
    ):
        return 2
    try:
        ws_url = normalize_chase_url(args.url) if args.url is not None else None
    except ValueError as exc:
        print(f"automa simulators status: {exc}", file=sys.stderr)
        return 2
    exit_code, status = simulators_status(url=ws_url, timeout_s=args.timeout_s)
    print(render_simulators_status(status, json_output=args.json, long=args.long))
    return exit_code
