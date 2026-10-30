"""The ``automa vehicles`` parser and its handlers."""

from __future__ import annotations

import argparse
import json
import sys

from ..shared.timeout_input import validate_timeout_input
from .discovery import discover_vehicle, remove_vehicle
from ..providers.catalog import PROVIDERS
from ..providers.probes import DEFAULT_READINESS_TIMEOUT_S
from .render import format_vehicles_status
from .status import get_vehicles_status


def register_vehicles(subcommands: argparse._SubParsersAction) -> None:
    vehicles = subcommands.add_parser(
        "vehicles",
        help="Discover and inspect vehicle environments.",
    )
    vehicles.set_defaults(handler=_handle_vehicles_help)
    vehicle_commands = vehicles.add_subparsers(dest="vehicle_command")

    vehicles_help = vehicle_commands.add_parser(
        "help",
        help="Show vehicle-level commands.",
    )
    vehicles_help.set_defaults(handler=_handle_vehicles_help)

    status = vehicle_commands.add_parser(
        "status",
        help="Show discovered vehicle environments.",
        description=(
            "Show the default simulator (CHASE_UI_WS_URL, else http://localhost:5050), "
            "and each vehicle that vehicles discover listed. "
            "Exits 1 when no vehicle is reachable."
        ),
    )
    status.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            f"One wall-clock deadline for vehicle probes (default: {DEFAULT_READINESS_TIMEOUT_S:g}s)."
        ),
    )
    status.add_argument(
        "--json",
        action="store_true",
        help="Print automa_vehicles_status JSON.",
    )
    status.set_defaults(handler=_handle_vehicles_status)

    discover = vehicle_commands.add_parser(
        "discover",
        help="List a vehicle in vehicles status.",
        description=(
            "List a vehicle in vehicles status once it can take a host: a simulator "
            "with its frontend connected and the Chase game loaded, or a PiCar whose "
            "machine answers. Exits 2 otherwise, unless --force."
        ),
    )
    discover.add_argument("--provider", required=True, choices=PROVIDERS,
                          help="What the vehicle is: a simulator (chase-sim) or a PiCar (picar).")
    discover.add_argument("--url", required=True,
                          help="The simulator's HTTP(S) origin or WS(S) control URL, or the PiCar's HTTP(S) URL.")
    discover.add_argument("--force", action="store_true", help="List the vehicle even when it cannot take a host now.")
    discover.set_defaults(handler=_handle_vehicles_discover)
    remove = vehicle_commands.add_parser(
        "remove",
        help="Stop listing a discovered vehicle in vehicles status.",
        description="Stop listing a discovered vehicle. The default simulator is always listed.",
    )
    remove.add_argument("--url", required=True, help="The URL the vehicle was discovered at, in either form.")
    remove.set_defaults(handler=_handle_vehicles_remove)


def _handle_vehicles_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles commands",
                "",
                "- discover     list a vehicle in status",
                "- remove       stop listing a discovered vehicle",
                "- status       show discovered vehicle environments",
                "- help         show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_status(args: argparse.Namespace) -> int:
    if not validate_timeout_input(
        args.timeout_s,
        command="automa vehicles status",
        json_output=args.json,
        human_stream=sys.stderr,
    ):
        return 2
    try:
        payload = get_vehicles_status(timeout_s=args.timeout_s)
    except ValueError as exc:
        print(f"automa vehicles status: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_vehicles_status(payload))
    if all(vehicle["state"] == "unreachable" for vehicle in payload["vehicles"]):
        return 1
    return 0


def _handle_vehicles_discover(args: argparse.Namespace) -> int:
    result = discover_vehicle(provider=args.provider, url=args.url, force=args.force)
    print(result.message)
    return result.exit_code


def _handle_vehicles_remove(args: argparse.Namespace) -> int:
    result = remove_vehicle(url=args.url)
    print(result.message)
    return result.exit_code
