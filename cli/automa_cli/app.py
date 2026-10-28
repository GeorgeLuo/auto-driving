from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from autonomy.plugins import DuplicatePluginIdError

from .hosts.automation import get_hosts_status
from .hosts.commands import _add_selection_args, register_hosts
from .step_inspect import INSPECT_STEPS, inspect_step
from .hosts.workbench_source import WORKBENCH_DEFAULT_MAX_FRAMES
from .simulators import (
    DEFAULT_SCENARIO_ID, create_simulator, delete_simulator, ensure_simulator, get_simulator_status,
)
from .runtime_view import serve_viewer
from .vehicles.commands import register_vehicles
from .vehicles.status import format_vehicles_status, get_vehicles_status


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
    _register_plugins(subcommands)
    _register_simulators(subcommands)
    return parser


def _register_plugins(subcommands: argparse._SubParsersAction) -> None:
    step_plugins = subcommands.add_parser(
        "plugins",
        help="Inspect what a step's plugins report, from images or a recorded run.",
    )
    step_plugins.set_defaults(handler=_handle_plugins_help)
    step_plugin_commands = step_plugins.add_subparsers(dest="plugin_command")
    step_plugin_commands.add_parser(
        "help",
        help="Show plugin inspection commands.",
    ).set_defaults(handler=_handle_plugins_help)

    inspect_control = step_plugin_commands.add_parser(
        "inspect", help="Replay images or a recorded run and report a step's plugins.",
    )
    inspect_control.set_defaults(handler=_handle_plugins_inspect_help)
    inspect_commands = inspect_control.add_subparsers(dest="step")
    inspect_commands.add_parser("help", help="Show the inspect steps.").set_defaults(
        handler=_handle_plugins_inspect_help,
    )
    for step_name in INSPECT_STEPS:
        record_description = {
            "perception": "what perception detects",
            "memory": "what memory retains",
        }.get(step_name, f"the {step_name} record")
        step_inspect = inspect_commands.add_parser(
            step_name,
            help=f"Show {record_description} for every frame.",
            description=(
                f"Replay an image, a directory of images, or a recorded run through the whole "
                f"cycle and report {record_description} after each frame. A recording restores "
                f"every step's selection and restages it per frame; --preset or --plugin overrides "
                f"{step_name}. Otherwise each step uses its default. Absent frames have a null "
                f"record and reset temporal state. Unmanifested images use filename order and "
                f"times 0, 1000, 2000, ... ms. Recorded frames replay with their recorded drive mode."
            ),
        )
        step_inspect.add_argument(
            "source",
            type=Path,
            help="Image file, directory of images, or recorded run.",
        )
        _add_selection_args(step_inspect, step_name, verb="inspect", fallback="the recorded selection, else ")
        step_inspect.add_argument(
            "--frame",
            type=int,
            default=None,
            metavar="N",
            help="Report only the source's Nth frame (0-based); the frames before it still replay.",
        )
        step_inspect.add_argument(
            "--record",
            action="store_true",
            help=f"Persist executed frames and inputs in a replay manifest, with the selected report under runtime/{step_name}-inspections/ for replay.",
        )
        step_inspect.add_argument(
            "--json",
            action="store_true",
            help="Print the machine-readable report.",
        )
        step_inspect.set_defaults(handler=_handle_plugins_inspect, step=step_name)
        step_inspect.add_argument(
            "--shared-memory",
            type=Path,
            dest="shared_memory_file",
            default=None,
            metavar="FILE",
            help="Start inspect from a shared_memory_v1 JSON file; recorded as provenance.",
        )
        step_inspect.add_argument(
            "--max-frames",
            type=int,
            default=WORKBENCH_DEFAULT_MAX_FRAMES,
            help=(
                "Reject image directories or recordings above this frame count; "
                f"increase for larger sources (default: {WORKBENCH_DEFAULT_MAX_FRAMES})."
            ),
        )


def _register_simulators(subcommands: argparse._SubParsersAction) -> None:
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


def _handle_plugins_help(args: argparse.Namespace) -> int:
    print("\n".join([
        "automa plugins commands", "",
        "- inspect  report a step's plugins from images or a recorded run",
        "- help     show this summary", "",
        "Upload, arm, disarm and catalog status: ./cli/automa hosts plugins", "",
        "Detailed help: ./cli/automa plugins inspect <step> --help",
    ]))
    return 0


def _handle_plugins_inspect_help(args: argparse.Namespace) -> int:
    print("\n".join([
        "automa plugins inspect steps", "",
        "- perception  show what a selection detects",
        "- memory      show what a selection retains",
        "- proposal    show the proposal record",
        "- plan        show the plan record",
        "- action      show the action record", "",
        "Source: an image, a directory of images, or a recorded run.",
        "Detailed help: ./cli/automa plugins inspect <step> --help",
    ]))
    return 0


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
                "- plugins    Inspect what each step's plugins report.",
                "- simulators Create headless simulators, or prepare and inspect SimEval environments.",
                "",
                "Open the runtime viewer. It does not require a host:",
                "- ./cli/automa",
                "Or on a host:",
                "- ./cli/automa hosts viewer --name <host name>",
                "",
                "Detailed help:",
                "- ./cli/automa --help",
                "- ./cli/automa <command> help",
            ]
        )
    )
    return 0


def _handle_open_viewer(args: argparse.Namespace) -> int:
    return serve_viewer()


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


def _handle_plugins_inspect(args: argparse.Namespace) -> int:
    result = inspect_step(
        args.step,
        source=str(args.source),
        preset=args.preset,
        plugins=args.plugins,
        frame=args.frame,
        record=args.record,
        json_output=args.json,
        max_frames=args.max_frames,
        shared_memory_file=args.shared_memory_file,
    )
    if result.message:
        print(result.message)
    return result.exit_code


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
