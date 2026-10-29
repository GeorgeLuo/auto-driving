from __future__ import annotations

import argparse
import sys
from typing import Any

from autonomy.plugins import DuplicatePluginIdError

from .hosts.automation_status import get_hosts_status
from .hosts.commands import register_hosts
from .plugins.commands import register_plugins
from .simulators.commands import register_simulators
from .runtime_view import serve_viewer
from .vehicles.commands import register_vehicles
from .vehicles.status import format_vehicles_status, get_vehicles_status
from .workbench.commands import register_workbench


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="automa",
        description="Single access point for local and vehicle-facing automation commands.",
    )
    # With no command, automa opens the runtime viewer.
    subcommands = parser.add_subparsers(dest="command", required=False)

    help_command = subcommands.add_parser(
        "help",
        help="Show top-level commands and common examples.",
    )
    help_command.set_defaults(handler=_handle_top_level_help)

    status = subcommands.add_parser(
        "status",
        help="Show hosts status and vehicles status.",
        description="Print the regular text of hosts status, then vehicles status.",
    )
    status.set_defaults(handler=_handle_status)

    register_vehicles(subcommands)
    register_hosts(subcommands)
    register_workbench(subcommands)
    register_plugins(subcommands)
    register_simulators(subcommands)
    return parser


def _handle_top_level_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "Automa is the control desk for the vehicles and simulators in this workspace.",
                "It helps you find what is reachable, stage controller choices locally, and deploy code to a PiCar.",
                "It can create hosts and start and stop their runs without making you remember where the runtime files live.",
                "It also lets you inspect the perception output and controller behavior of each host.",
                "Use it when you want to move from editing local code to running that code against a real or simulated vehicle.",
                "The sections below only show the next command level; each command has its own help for details.",
                "",
                "- help       Show this command summary.",
                "- status     Show hosts status and vehicles status.",
                "- vehicles   Discover and inspect vehicle environments.",
                "- hosts      Create, configure, and operate vehicle controller hosts.",
                "- workbench  Replay a local image directory through the controller steps.",
                "- plugins    List a plugin catalog or what a host has armed, upload, arm and disarm a host's plugins, stream what each step outputs on a host, and inspect what each step reports.",
                "- simulators Create and delete headless simulators.",
                "",
                "Open the runtime viewer. It does not require a host:",
                "- ./cli/automa",
                "Or on a host:",
                "- ./cli/automa hosts viewer --name <host name>",
                "",
                "Detailed help:",
                "- ./cli/automa --help",
                "- ./cli/automa <command> help",
                "- ./cli/automa workbench --help",
            ]
        )
    )
    return 0


def _handle_open_viewer(args: argparse.Namespace) -> int:
    return serve_viewer()


def _handle_status(args: argparse.Namespace) -> int:
    result = get_hosts_status(long=False, json_output=False)
    if result.message:
        print(result.message)
    try:
        payload = get_vehicles_status()
    except ValueError as exc:
        print(f"automa vehicles status: {exc}", file=sys.stderr)
        return 2
    if result.message:
        print()
    print(format_vehicles_status(payload))
    if all(vehicle["state"] == "unreachable" for vehicle in payload["vehicles"]):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Any = getattr(args, "handler", None)
    if handler is None and args.command is None:
        handler = _handle_open_viewer
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args))
    except DuplicatePluginIdError as exc:
        # Plugins declare their own IDs; the implementations owner must resolve a clash.
        print(f"error: {exc}", file=sys.stderr)
        return 2
