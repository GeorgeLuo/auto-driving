"""The ``automa plugins`` parser and its handlers."""

from __future__ import annotations

import argparse
import sys
from functools import partial
from pathlib import Path

from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS
from autonomy.decision_cycle.steps import CLEARABLE_STEPS, ONE_PLUGIN_STEPS

from .decision_steps import stream_host_step
from .streaming import stream_host_memory, stream_host_perception
from ..shared.replay.source import WORKBENCH_DEFAULT_MAX_FRAMES
from ..shared.selection_args import add_selection_args
from .catalog import arm_plugins, disarm_plugins, list_plugins, save_plugins, upload_plugin
from .step_inspect import INSPECT_STEPS, inspect_step

# Each step a host streams, in cycle order.
_STREAMS = {
    "perception": stream_host_perception,
    "memory": stream_host_memory,
    **{step: partial(stream_host_step, step) for step in DECISION_STEPS},
}


def _step_plugin(value: str) -> tuple[str, str]:
    """``step/plugin``, as ``plugins list --long`` names a plugin, as ``(step, plugin)``."""

    step, _, plugin_id = value.partition("/")
    if step not in STEPS or not plugin_id:
        raise argparse.ArgumentTypeError(f"expected <step>/<plugin> with a step from {', '.join(STEPS)}: {value!r}")
    return step, plugin_id


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

    listing = step_plugin_commands.add_parser(
        "list", help="Show the packaged catalog, or what a host has armed, in cycle order.",
        description=(
            "Without a host, list the packaged catalog in implementations/decision_cycle/<step>/catalog.json "
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

    upload = step_plugin_commands.add_parser(
        "upload", help="Register a plugin file; optionally arm it on a runtime host.",
        description=(
            "Store a file in an existing host or workbench catalog. A successful upload makes the plugin "
            "available; source that does not compile is refused, and its imports and dependencies load only when armed. "
            "Re-uploading a name makes a new revision available and leaves the running revision unchanged; "
            "uploads last until the host restarts. Selection stays unchanged unless --arm, which arms the "
            "plugin as plugins arm does and fails with its import or construction error."
        ),
        epilog=(
            "Verify registration with: ./cli/automa plugins list --host-name <host> --step <step> --long. "
            "Uploading never starts automation."
        ),
    )
    upload_target = upload.add_mutually_exclusive_group(required=True)
    upload_target.add_argument("--host-name", help="Host name from hosts status.")
    upload_target.add_argument("--url", help="Runtime or workbench URL; bypass host lookup.")
    upload.add_argument("--file", type=Path, required=True, help="Local source file to upload; it must compile but is not imported until armed.")
    upload.add_argument("--name", required=True, type=_step_plugin, metavar="STEP/PLUGIN",
                        help="Plugin to register, named as plugins list --long will list it; upload the same name again to make a new revision available.")
    upload.add_argument("--entrypoint", required=True, help="Registered module:Class name, for example prototype:Prototype; resolved when armed.")
    upload.add_argument("--config", metavar="JSON", help="JSON object of constructor settings stored with this revision, for example '{\"throttle\": -0.6}'; arming constructs the plugin with it. Default: {}.")
    upload.add_argument("--arm", action="store_true", help="After registration, add/update this plugin in the runtime host's requested selection; other plugins stay selected.")
    upload.add_argument("--json", action="store_true", help="Print the registration receipt as JSON.")
    upload.set_defaults(handler=_handle_plugins_upload)

    arm = step_plugin_commands.add_parser(
        "arm", help="Add/update a host's catalog plugins without restarting automation.",
        description=(
            "Request the latest catalog definitions together. New IDs append; existing IDs update in place; "
            f"on a one-plugin step ({', '.join(ONE_PLUGIN_STEPS)}) the ID replaces the plugin. The host loads "
            "them before answering: an import or construction error exits 2 and keeps the previous selection. "
            "A loaded selection applies at the host's next cycle."
        ),
        epilog="Arming never starts automation or changes its control mode.",
    )
    arm_target = arm.add_mutually_exclusive_group(required=True)
    arm_target.add_argument("--host-name", help="Host name from hosts status.")
    arm_target.add_argument("--url", help="Runtime host URL; workbench catalogs support upload only.")
    arm.add_argument("--name", dest="plugins", action="append", required=True, type=_step_plugin,
                     metavar="STEP/PLUGIN",
                     help="Plugin to arm, named as plugins list --host-name <host> --long lists it; repeat to arm several together, in order.")
    arm.add_argument("--json", action="store_true", help="Print the request receipt and selection state as JSON.")
    arm.set_defaults(handler=_handle_plugins_arm)

    disarm = step_plugin_commands.add_parser(
        "disarm", help="Deselect a host's plugins without deleting them from its catalog.",
        description=(
            f"Deselect plugins on an existing runtime host. A one-plugin step ({', '.join(ONE_PLUGIN_STEPS)}) "
            "keeps its plugin; arm a replacement instead. The host resets disarmed instances and applies "
            "the selection at its next cycle; retained plugins keep their state. Catalog entries remain "
            "available to arm again."
        ),
        epilog="Disarming does not start or stop automation, change control mode, or edit staged activations.",
    )
    disarm_target = disarm.add_mutually_exclusive_group(required=True)
    disarm_target.add_argument("--host-name", help="Host name from hosts status.")
    disarm_target.add_argument("--url", help="Runtime host URL; workbench catalogs support upload only.")
    disarm_input = disarm.add_mutually_exclusive_group(required=True)
    disarm_input.add_argument("--name", dest="plugins", action="append", type=_step_plugin, metavar="STEP/PLUGIN",
                              help="Plugin to disarm, named as plugins list --host-name <host> lists it; repeat to disarm several together.")
    disarm_input.add_argument("--step", choices=CLEARABLE_STEPS, help="Disarm every plugin in this step.")
    disarm_input.add_argument("--all", dest="all_plugins", action="store_true",
                              help="Disarm every plugin in every step that can run without plugins.")
    disarm.add_argument("--json", action="store_true", help="Print the request receipt and selection state as JSON.")
    disarm.set_defaults(handler=_handle_plugins_disarm)

    save = step_plugin_commands.add_parser(
        "save", help="Make what a host has armed the plugins it starts with.",
        description=(
            "Stage the plugins a running host has armed, in every step or one, as the selection it loads "
            "when it starts. hosts update keeps a saved step, with the catalog's current specs and configs, "
            "instead of staging its default; saving a step's default makes it follow the catalog's default "
            "again. A Chase host loads the saved plugins each time it starts; a PiCar once hosts update "
            "ships them. Uploaded plugins cannot be saved, and nothing is saved while the host's last arming "
            "has failed."
        ),
        epilog="Saving does not change what the host runs now.",
    )
    save.add_argument("--host-name", required=True, help="Host name from hosts status.")
    save.add_argument("--step", choices=STEPS, help="Save only this cycle step.")
    save.add_argument("--json", action="store_true", help="Print each saved step's activation, preset and plugins as JSON.")
    save.set_defaults(handler=_handle_plugins_save)

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


def _handle_plugins_list(args: argparse.Namespace) -> int:
    result = list_plugins(host_name=args.host_name, url=args.url, step=args.step, long=args.long,
                          json_output=args.json)
    print(result.message)
    return result.exit_code


def _handle_plugins_upload(args: argparse.Namespace) -> int:
    step, plugin_id = args.name
    result = upload_plugin(url=args.url, host_name=args.host_name, file=args.file, step=step, plugin_id=plugin_id,
                           entrypoint=args.entrypoint, config=args.config, arm=args.arm, json_output=args.json)
    print(result.message)
    return result.exit_code


def _handle_plugins_arm(args: argparse.Namespace) -> int:
    result = arm_plugins(url=args.url, host_name=args.host_name, plugins=args.plugins, json_output=args.json)
    print(result.message)
    return result.exit_code


def _handle_plugins_disarm(args: argparse.Namespace) -> int:
    result = disarm_plugins(url=args.url, host_name=args.host_name, plugins=args.plugins, step=args.step,
                            json_output=args.json)
    print(result.message)
    return result.exit_code


def _handle_plugins_save(args: argparse.Namespace) -> int:
    result = save_plugins(host_name=args.host_name, step=args.step, json_output=args.json)
    print(result.message)
    return result.exit_code


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
