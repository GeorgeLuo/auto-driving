"""The ``automa plugins`` parser and its handlers."""

from __future__ import annotations

import argparse
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS

from ..hosts.workbench_source import WORKBENCH_DEFAULT_MAX_FRAMES
from ..shared.selection_args import add_selection_args
from .catalog import list_plugins
from .step_inspect import INSPECT_STEPS, inspect_step


def register_plugins(subcommands: argparse._SubParsersAction) -> None:
    step_plugins = subcommands.add_parser(
        "plugins",
        help="List a plugin catalog or what a host has armed, or inspect plugins from images and recordings.",
    )
    step_plugins.set_defaults(handler=_handle_plugins_help)
    step_plugin_commands = step_plugins.add_subparsers(dest="plugin_command")
    step_plugin_commands.add_parser(
        "help",
        help="Show plugin listing and inspection commands.",
    ).set_defaults(handler=_handle_plugins_help)

    listing = step_plugin_commands.add_parser(
        "list", help="Show the packaged catalog, or what a host has armed, in cycle order.",
        description=(
            "Without a host, list the packaged catalog in implementations/decision_cycle/catalog.py "
            "and mark each step's defaults. With --host-name or --url, list the plugins the host has "
            "armed; --long lists the host's whole catalog, packaged and uploaded, and marks the armed "
            "plugins. Exits 2 while the host's last arming failed. "
            "This command does not start a host or change what it has armed."
        ),
    )
    listing_target = listing.add_mutually_exclusive_group()
    listing_target.add_argument("--host-name", help="Host name from hosts status; omit to list the packaged catalog.")
    listing_target.add_argument("--url", help="Runtime or workbench URL; bypass host lookup. A workbench catalog arms nothing, so list it with --long.")
    listing.add_argument("--long", action="store_true", help="List the host's whole catalog and its version, marking armed plugins.")
    listing.add_argument("--step", choices=STEPS, help="Show only this cycle step.")
    listing.add_argument("--json", action="store_true", help="Print the catalog and each step's defaults, or the host's armed plugins, as JSON.")
    listing.set_defaults(handler=_handle_plugins_list)

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
        add_selection_args(step_inspect, step_name, verb="inspect", fallback="the recorded selection, else ")
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


def _handle_plugins_help(args: argparse.Namespace) -> int:
    print("\n".join([
        "automa plugins commands", "",
        "- list     show the packaged catalog or what a host has armed",
        "- inspect  report a step's plugins from images or a recorded run",
        "- help     show this summary", "",
        "Upload, arm and disarm: ./cli/automa hosts plugins", "",
        "Packaged catalog: ./cli/automa plugins list",
        "Armed on a host: ./cli/automa plugins list --host-name <host>",
        "A host's catalog: ./cli/automa plugins list --host-name <host> --long",
        "Detailed help: ./cli/automa plugins <command> --help",
    ]))
    return 0


def _handle_plugins_list(args: argparse.Namespace) -> int:
    result = list_plugins(host_name=args.host_name, url=args.url, step=args.step, long=args.long,
                          json_output=args.json)
    print(result.message)
    return result.exit_code


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
