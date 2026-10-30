"""The ``automa plugins`` parser and its handlers."""

from __future__ import annotations

import argparse
import sys
from functools import partial
from pathlib import Path

from autonomy.decision_cycle.activation import DECISION_STEPS

from .catalog.commands import register_catalog
from .decision_steps import stream_host_step
from .streaming import stream_host_memory, stream_host_perception
from ..shared.replay.source import WORKBENCH_DEFAULT_MAX_FRAMES
from ..shared.selection_args import add_selection_args
from .step_inspect import INSPECT_STEPS, inspect_step

# Each step a host streams, in cycle order.
_STREAMS = {
    "perception": stream_host_perception,
    "memory": stream_host_memory,
    **{step: partial(stream_host_step, step) for step in DECISION_STEPS},
}


def register_plugins(subcommands: argparse._SubParsersAction) -> None:
    step_plugins = subcommands.add_parser(
        "plugins",
        help="List a plugin catalog or what a host has armed, upload, arm, disarm and save a host's plugins, stream a step on a host, or inspect plugins from images and recordings.",
    )
    step_plugins.set_defaults(handler=_handle_plugins_help)
    step_plugin_commands = step_plugins.add_subparsers(dest="plugin_command")
    step_plugin_commands.add_parser(
        "help",
        help="Show plugin listing and inspection commands.",
    ).set_defaults(handler=_handle_plugins_help)

    register_catalog(step_plugin_commands)

    stream = step_plugin_commands.add_parser(
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
    stream.add_argument("--step", required=True, choices=tuple(_STREAMS), help="Cycle step to stream.")
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
            help="Start inspect from a shared_memory JSON file; recorded as provenance.",
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
        "- upload   register a plugin file in a host's or workbench's catalog",
        "- arm      arm catalog plugins on a host by step/plugin",
        "- disarm   deselect a host's plugins by step/plugin, or clear a step or every step",
        "- save     make what a host has armed the plugins it starts with",
        "- stream   show a step's latest output on a host as it updates",
        "- inspect  report a step's plugins from images or a recorded run",
        "- help     show this summary", "",
        "Packaged catalog: ./cli/automa plugins list",
        "Armed on a host: ./cli/automa plugins list --host-name <host>",
        "A host's catalog: ./cli/automa plugins list --host-name <host> --long",
        "Upload to a host: ./cli/automa plugins upload --host-name <host> --file <file> --name <step>/<plugin> --entrypoint <module>:<Class>",
        "Arm on a host: ./cli/automa plugins arm --host-name <host> --name <step>/<plugin>",
        "Disarm on a host: ./cli/automa plugins disarm --host-name <host> --name <step>/<plugin>",
        "Start a host with what it has armed: ./cli/automa plugins save --host-name <host>",
        "Stream on a host: ./cli/automa plugins stream --host-name <host> --step <step>",
        "Detailed help: ./cli/automa plugins <command> --help",
    ]))
    return 0


def _handle_plugins_stream(args: argparse.Namespace) -> int:
    result = _STREAMS[args.step](host_name=args.host_name, refresh_s=args.refresh_s, once=args.once,
                                 no_clear=args.no_clear, json_output=args.json, output=sys.stdout)
    if result.message:
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
