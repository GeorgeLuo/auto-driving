"""The ``automa simulators`` parser and its handlers."""

from __future__ import annotations

import argparse
from .render import render_simulator_result

from .service import create_simulator, delete_simulator


def register_simulators(subcommands: argparse._SubParsersAction) -> None:
    simulators = subcommands.add_parser("simulators", help="Create and delete headless simulators.")
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
            "End the simulator simulators create started at --url. Exits 2 when no created "
            "simulator has that URL or a host was created at it."
        ),
    )
    simulators_delete.add_argument("--url", required=True, help="The simulator's URL from simulators create.")
    simulators_delete.set_defaults(handler=_handle_simulators_delete)


def _handle_simulators_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa simulators commands",
                "",
                "- create   start a headless Chase simulator that vehicles status lists",
                "- delete   end a simulator that create started",
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
