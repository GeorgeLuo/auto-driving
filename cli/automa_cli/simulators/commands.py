"""The ``automa simulators`` parser and its handlers."""

from __future__ import annotations

import argparse

from .created import create_simulator, delete_simulator
from .ensure import ensure_simulator
from .simeval import DEFAULT_SCENARIO_ID
from .status import get_simulator_status


def register_simulators(subcommands: argparse._SubParsersAction) -> None:
    simulators = subcommands.add_parser("simulators", help="Create, prepare and inspect simulators.")
    simulators.set_defaults(handler=_handle_simulators_help)
    simulator_commands = simulators.add_subparsers(dest="simulator_command")

    simulators_help = simulator_commands.add_parser(
        "help",
        help="Show simulator-level commands.",
    )
    simulators_help.set_defaults(handler=_handle_simulators_help)

    simulators_status = simulator_commands.add_parser(
        "status",
        help="Show whether SimEval has an online simulator deployment.",
        description="Show whether SimEval has an online simulator deployment.",
    )
    simulators_status.add_argument(
        "--timeout-ms",
        type=int,
        default=2000,
        help="Probe timeout passed to `simeval status` in milliseconds.",
    )
    simulators_status.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable simulator status payload.",
    )
    simulators_status.set_defaults(handler=_handle_simulators_status)

    simulators_ensure = simulator_commands.add_parser(
        "ensure",
        help="Explicitly launch/configure a known simulator environment.",
        description=(
            "Explicitly prepare SimEval and select a Chase scenario. This command "
            "changes simulator configuration; passive status and observation never invoke it."
        ),
    )
    simulators_ensure.add_argument(
        "--timeout-ms",
        type=int,
        default=2000,
        help="Probe timeout passed to `simeval status` in milliseconds.",
    )
    simulators_ensure.add_argument(
        "--scenario",
        default=DEFAULT_SCENARIO_ID,
        help=f"Chase scenario id to select after the simulator is ready (default: {DEFAULT_SCENARIO_ID}).",
    )
    simulators_ensure.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable simulator ensure payload.",
    )
    simulators_ensure.set_defaults(handler=_handle_simulators_ensure)

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
                "- status   show simulator deployment availability",
                "- ensure   explicitly launch/configure a known Chase environment",
                "- help     show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa simulators <command> --help",
            ]
        )
    )
    return 0


def _handle_simulators_status(args: argparse.Namespace) -> int:
    result = get_simulator_status(
        timeout_ms=args.timeout_ms,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_simulators_create(args: argparse.Namespace) -> int:
    result = create_simulator()
    print(result.message)
    return result.exit_code


def _handle_simulators_delete(args: argparse.Namespace) -> int:
    result = delete_simulator(url=args.url)
    print(result.message)
    return result.exit_code


def _handle_simulators_ensure(args: argparse.Namespace) -> int:
    result = ensure_simulator(
        timeout_ms=args.timeout_ms,
        scenario_id=args.scenario,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code
