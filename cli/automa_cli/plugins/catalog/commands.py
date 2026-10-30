"""Registration and handlers for plugin catalog commands."""

from __future__ import annotations

import argparse
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.steps import CLEARABLE_STEPS, ONE_PLUGIN_STEPS

from .render import render_listing, render_mutation, render_save
from .service import arm_plugins, disarm_plugins, list_plugins, save_plugins, upload_plugin


def register_catalog(commands: argparse._SubParsersAction) -> None:
    listing = commands.add_parser(
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
    listing.set_defaults(handler=_handle_list)

    upload = commands.add_parser(
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
    upload.set_defaults(handler=_handle_upload)

    arm = commands.add_parser(
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
    arm.set_defaults(handler=_handle_arm)

    disarm = commands.add_parser(
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
    disarm.set_defaults(handler=_handle_disarm)

    save = commands.add_parser(
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
    save.set_defaults(handler=_handle_save)


def _step_plugin(value: str) -> tuple[str, str]:
    """``step/plugin``, as ``plugins list --long`` names a plugin, as ``(step, plugin)``."""

    step, _, plugin_id = value.partition("/")
    if step not in STEPS or not plugin_id:
        raise argparse.ArgumentTypeError(f"expected <step>/<plugin> with a step from {', '.join(STEPS)}: {value!r}")
    return step, plugin_id


def _handle_list(args: argparse.Namespace) -> int:
    result = list_plugins(host_name=args.host_name, url=args.url, step=args.step, long=args.long)
    print(render_listing(result, json_output=args.json))
    return result.exit_code


def _handle_upload(args: argparse.Namespace) -> int:
    step, plugin_id = args.name
    result = upload_plugin(url=args.url, host_name=args.host_name, file=args.file, step=step, plugin_id=plugin_id,
                           entrypoint=args.entrypoint, config=args.config, arm=args.arm)
    print(render_mutation(result, json_output=args.json))
    return result.exit_code


def _handle_arm(args: argparse.Namespace) -> int:
    result = arm_plugins(url=args.url, host_name=args.host_name, plugins=args.plugins)
    print(render_mutation(result, json_output=args.json))
    return result.exit_code


def _handle_disarm(args: argparse.Namespace) -> int:
    result = disarm_plugins(url=args.url, host_name=args.host_name, plugins=args.plugins, step=args.step)
    print(render_mutation(result, json_output=args.json))
    return result.exit_code


def _handle_save(args: argparse.Namespace) -> int:
    result = save_plugins(host_name=args.host_name, step=args.step)
    print(render_save(result, json_output=args.json))
    return result.exit_code
