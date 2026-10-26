from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, TextIO

from .automation import (
    get_vehicle_automation_status,
    record_vehicle_automation_terminal_result,
    restart_vehicle_automation,
    run_vehicle_automation,
    start_vehicle_automation_background,
    stop_vehicle_automation,
)
from .deploy import create_host, update_host, delete_host
from .decision_steps import get_vehicle_step_info, inspect_decision_step, stream_vehicle_step
from .memory import get_vehicle_memory_info
from .memory_runs import inspect_memory, reset_vehicle_memory
from .operations import run_vehicle_startup_check
from autonomy.plugins import DuplicatePluginIdError
from implementations.decision_cycle.catalog import DEFAULT_STEP_PLUGINS, DEFAULT_STEP_PRESETS, SELECTABLE_STEPS, STEP_PRESETS

from .step_activations import HOST_PROVIDERS, update_vehicle_step
from .runtime_hosts import RUNTIME_ROOT as VEHICLES_RUNTIME_ROOT
from .perception import get_vehicle_perception_info
from .perception_runs import (
    inspect_perception,
)
from .workbench import run_workbench_replay
from .plugin_catalog import arm_plugins, disarm_plugins, list_plugins, upload_plugin
from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS
from autonomy.decision_cycle.steps import ONE_PLUGIN_STEPS, STEP_RUNNERS
from .workbench_source import WORKBENCH_DEFAULT_MAX_FRAMES
from .simulators import (
    DEFAULT_SCENARIO_ID, create_simulator, delete_simulator, ensure_simulator, get_simulator_status,
)
from .viability import (
    run_memory_viability_measurement,
    run_perception_viability_measurement,
)
from .runtime_view import serve_viewer
from .streaming import serve_host_viewer, stream_vehicle_memory, stream_vehicle_perception
from .vehicles import (
    DEFAULT_READINESS_TIMEOUT_S,
    discover_vehicle,
    format_chase_simulator_status,
    get_chase_simulator_status,
    remove_vehicle,
)


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

    vehicles = subcommands.add_parser(
        "vehicles",
        help="Discover vehicles and manage their controller runtimes.",
    )
    vehicles.set_defaults(handler=_handle_vehicles_help)
    vehicle_commands = vehicles.add_subparsers(dest="vehicle_command")

    vehicles_help = vehicle_commands.add_parser(
        "help",
        help="Show vehicle-level commands.",
    )
    vehicles_help.set_defaults(handler=_handle_vehicles_help)

    plugins = vehicle_commands.add_parser("plugins", help="Upload, arm, disarm and inspect plugins in a live catalog.")
    plugins.set_defaults(handler=_handle_plugins_help)
    plugin_commands = plugins.add_subparsers(dest="plugin_command")
    plugins_help = plugin_commands.add_parser("help", help="Show upload, arming, disarming and catalog inspection usage.")
    plugins_help.set_defaults(handler=_handle_plugins_help)
    listing = plugin_commands.add_parser("status", aliases=["list"], help="Show live catalog availability and plugin revisions (list is an alias).")
    listing_target = listing.add_mutually_exclusive_group(required=True)
    listing_target.add_argument("--id", dest="vehicle_id", help="Vehicle whose live catalog to read.")
    listing_target.add_argument("--url", help="Runtime or workbench URL; bypass vehicle discovery.")
    listing.add_argument("--step", choices=STEPS, help="Show only this cycle step.")
    listing.add_argument("--json", dest="json_output", action="store_true", help="Print the catalog snapshot as JSON.")
    listing.set_defaults(handler=_handle_plugin_status)
    upload = plugin_commands.add_parser(
        "upload", help="Register a plugin file; optionally arm it on a runtime host.",
        description="Store a file in an existing live catalog. Upload success means available; source that does not compile is refused. Selection stays unchanged unless --arm, which loads it on the host and fails with its import or construction error.",
        epilog="Verify registration with: ./cli/automa vehicles plugins status --id <vehicle> --step <step>. Use --url for an existing workbench catalog.",
    )
    target = upload.add_mutually_exclusive_group(required=True)
    target.add_argument("--id", dest="vehicle_id", help="Vehicle whose catalog receives the upload.")
    target.add_argument("--url", help="Runtime or workbench URL; bypass vehicle discovery.")
    upload.add_argument("--file", type=Path, required=True, help="Local source file to upload; it must compile but is not imported until armed.")
    upload.add_argument("--step", choices=STEPS, required=True, help="Cycle step whose catalog receives the file.")
    upload.add_argument("--plugin-id", required=True, help="Plugin ID within the step; repeat an ID to make a new revision available.")
    upload.add_argument("--entrypoint", required=True, help="Registered module:Class name, for example prototype:Prototype; resolved when armed.")
    upload.add_argument("--config", metavar="JSON", help="JSON object of constructor settings stored with this revision, for example '{\"throttle\": -0.6}'; arming constructs the plugin with it. Default: {}.")
    upload.add_argument("--arm", action="store_true", help="After registration, add/update this ID in the runtime host's requested selection; other plugins stay selected.")
    upload.add_argument("--json", dest="json_output", action="store_true", help="Print the registration receipt as JSON.")
    upload.set_defaults(handler=_handle_plugin_upload)

    arm = plugin_commands.add_parser(
        "arm", help="Add/update uploaded or packaged plugins without restarting automation.",
        description=f"Request the latest catalog definitions together. New IDs append; existing IDs update in place; on a one-plugin step ({', '.join(ONE_PLUGIN_STEPS)}) the ID replaces the plugin. The host loads them before answering: an import or construction error exits 2 and keeps the previous selection. A loaded selection applies at the host's next cycle.",
        epilog="With --selection, use a JSON map such as {\"perception\": [\"prototype\"], \"memory\": [\"helper\"]}. Arming never starts automation or changes its control mode.",
    )
    arm_target = arm.add_mutually_exclusive_group(required=True)
    arm_target.add_argument("--id", dest="vehicle_id", help="Vehicle whose runtime host receives the request.")
    arm_target.add_argument("--url", help="Runtime host URL; workbench catalogs support upload only.")
    arm_input = arm.add_mutually_exclusive_group(required=True)
    arm_input.add_argument("--step", choices=STEPS, help="Step receiving the ordered --plugin IDs.")
    arm_input.add_argument("--selection", type=Path, help="JSON map of supported steps to ordered plugin-ID lists; arm the group together.")
    arm.add_argument("--plugin", action="append", dest="plugins", help="Plugin ID to add/update; repeat in the desired append order with --step.")
    arm.add_argument("--json", dest="json_output", action="store_true", help="Print the request receipt and selection state as JSON.")
    arm.set_defaults(handler=_handle_plugin_arm)

    disarm = plugin_commands.add_parser(
        "disarm", help="Deselect plugins without deleting them from the live catalog.",
        description=f"Deselect plugins on an existing runtime host. A one-plugin step ({', '.join(ONE_PLUGIN_STEPS)}) keeps its plugin; arm a replacement instead. The host resets disarmed instances and applies the selection at its next cycle; retained plugins keep their state. Catalog entries remain available to arm again.",
        epilog="Use --step with repeatable --plugin IDs, --selection with a JSON step-to-ID-list map, or --all to clear one step (with --step) or every step that can run without plugins. Empty batch lists remove nothing. Disarming does not start or stop automation, change control mode, or edit staged activations.",
    )
    disarm_target = disarm.add_mutually_exclusive_group(required=True)
    disarm_target.add_argument("--id", dest="vehicle_id", help="Vehicle whose runtime host receives the request.")
    disarm_target.add_argument("--url", help="Runtime host URL; workbench catalogs support upload only.")
    disarm_input = disarm.add_mutually_exclusive_group()
    disarm.add_argument("--step", choices=STEPS, help="Step whose plugins to disarm; combine with --plugin or --all.")
    disarm_input.add_argument("--selection", type=Path, help="JSON map of supported steps to plugin-ID lists to disarm together.")
    disarm_input.add_argument("--all", dest="all_plugins", action="store_true", help="Disarm every plugin in --step, or in every step that can run without plugins.")
    disarm.add_argument("--plugin", action="append", dest="plugins", help="Plugin ID to disarm from --step; repeat to name several.")
    disarm.add_argument("--json", dest="json_output", action="store_true", help="Print the request receipt and selection state as JSON.")
    disarm.set_defaults(handler=_handle_plugin_disarm)

    status = vehicle_commands.add_parser(
        "status",
        help="Show each discovered vehicle and each created host.",
        description=(
            "Show the default simulator (CHASE_UI_WS_URL, else http://localhost:5050), "
            "each vehicle that vehicles discover listed, and the state of created hosts. "
            "Exits 1 when no vehicle is reachable, or --id names a missing, "
            "stopped, unanswering, or errored host."
        ),
    )
    status.add_argument(
        "--id",
        dest="vehicle_id",
        default=None,
        help=(
            "Show only this vehicle's host, and exit 1 when it is not found, not "
            "answering, or in error. Omit to list every host."
        ),
    )
    status.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            "One wall-clock deadline in seconds for the vehicle probes and the "
            f"host reads, which run in parallel (default: {DEFAULT_READINESS_TIMEOUT_S:g})."
        ),
    )
    status.add_argument(
        "--json",
        action="store_true",
        help="Print automa_simulator_status_v2 JSON.",
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
    discover.add_argument("--provider", required=True, choices=HOST_PROVIDERS,
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
    viewer = vehicle_commands.add_parser(
        "viewer",
        help="Open the runtime viewer on a host.",
        description=(
            "Serve the runtime viewer fed from the host at --host-url, the URL vehicles status "
            "shows under the host, until interrupted. Exits 2 when no host has that URL "
            "or it does not answer."
        ),
    )
    viewer.add_argument("--host-url", required=True, help="The host's URL from vehicles status.")
    viewer.set_defaults(handler=_handle_vehicles_viewer)

    host = vehicle_commands.add_parser("host", help="Create, update, run, stop and delete hosts.")
    host.set_defaults(handler=_handle_vehicles_host_help)
    host_commands = host.add_subparsers(dest="host_command")
    host_help = host_commands.add_parser("help", help="Show host-level commands.")
    host_help.set_defaults(handler=_handle_vehicles_host_help)
    host_create = host_commands.add_parser("create", help="Create and start a host.")
    host_update = host_commands.add_parser("update", help="Package and deploy a host's release.")
    host_delete = host_commands.add_parser("delete", help="Stop the host and its run, and remove its local files.")
    for command in (host_create, host_update, host_delete):
        command.add_argument("--id", dest="vehicle_id", required=True, help="Host name.")
        command.add_argument("--verbose", action="store_true")
    for command in (host_create, host_update):
        command.add_argument("--dry-run", action="store_true", help="Print the deployment plan without writing or connecting.")
        command.add_argument("--json", action="store_true")
        command.add_argument("--drive-args", help="Persist PiCar drive arguments; update requires --restart.")
    host_create.add_argument("--provider", required=True, choices=HOST_PROVIDERS,
                             help="What the host controls: the simulator's chaser (chase-sim) or a PiCar (picar).")
    host_create.add_argument("--url", required=True,
                             help="The vehicle's URL: the simulator's HTTP(S) origin or WS(S) control URL, "
                                  "or the PiCar's HTTP(S) URL. vehicles status lists both.")
    host_create.add_argument("--ssh-target", help="PiCar SSH target (default: PI_HOST or the URL host).")
    host_create.add_argument("--pi-home", help="PiCar home (default: PI_HOME or /home/piracer).")
    host_update.add_argument("--restart", action="store_true", help="Restart the host onto this release.")
    host_create.set_defaults(handler=_handle_vehicles_host_create)
    host_update.set_defaults(handler=_handle_vehicles_host_update)
    host_delete.set_defaults(handler=_handle_vehicles_host_delete)

    host_run = host_commands.add_parser(
        "run",
        help="Start a run on a host and verify one correlated camera/perception publication.",
        description=(
            "Start autonomous movement using the staged plugins. Success requires one camera frame, its "
            "completed perception result, and a healthy current-generation loopback view. "
            "--host-url runs an existing host instead of starting one; that host reports its vehicle id."
        ),
    )
    run_target = host_run.add_mutually_exclusive_group(required=True)
    run_target.add_argument(
        "--id",
        dest="vehicle_id",
        help=_STAGING_VEHICLE_ID_HELP,
    )
    run_target.add_argument(
        "--host-url",
        dest="host_url",
        help=(
            "Base URL of a runtime host that is already running. "
            "Run that host instead of starting one. The host reports its vehicle id."
        ),
    )
    host_run.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            "Runtime readiness deadline in seconds "
            f"(default: {DEFAULT_READINESS_TIMEOUT_S:g})."
        ),
    )
    host_run.add_argument(
        "--num-decisions",
        type=int,
        default=0,
        help=(
            "Number of decision cycles to complete. Movement requires a positive count "
            "unless --unbounded is set; 0 remains unbounded with --observe-only."
        ),
    )
    host_run.add_argument(
        "--unbounded",
        action="store_true",
        help=(
            "Run until stopped, overriding --num-decisions. "
            "Stop with vehicles host stop."
        ),
    )
    host_run.add_argument(
        "--observe-only",
        action="store_true",
        help=(
            "Run plugins without applying their output. The same mode works on every vehicle."
        ),
    )
    host_run.add_argument(
        "--open-view",
        action="store_true",
        help=(
            "Open the local Automa runtime views after the first correlated "
            "camera/perception publication is healthy."
        ),
    )
    host_run.add_argument(
        "--record",
        action="store_true",
        help="Save per-frame images and perception artifacts under a timestamped run directory.",
    )
    host_run.add_argument(
        "--verbose",
        action="store_true",
        help="Print every frame's detail when output is connected.",
    )
    host_run.add_argument(
        "--log",
        action="store_true",
        dest="log_to_disk",
        help="Persist background run output to automation.log.",
    )
    host_run.add_argument(
        "--foreground",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    host_run.set_defaults(handler=_handle_vehicles_host_run)

    host_stop = host_commands.add_parser(
        "stop",
        help="Stop the host's run; the host stays attached until host delete.",
        description=(
            "Stop autonomous movement and its publication monitor. The host stays attached, with its "
            "deployment staged, until vehicles host delete; its former view is no longer current-generation "
            "available."
        ),
    )
    host_stop.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help=_STAGING_VEHICLE_ID_HELP,
    )
    host_stop.add_argument(
        "--wait-s",
        type=float,
        default=3.0,
        help="Seconds to wait for graceful stop before forcing termination.",
    )
    host_stop.set_defaults(handler=_handle_vehicles_host_stop)

    host_status = host_commands.add_parser(
        "status",
        help="Show each host's deployment, run and view state.",
        description="Show each host's deployment, run and view state.",
    )
    host_status.add_argument(
        "--id",
        dest="vehicle_id",
        default=None,
        help="Host name. Omit to list every host.",
    )
    host_status.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable host status payload.",
    )
    host_status.set_defaults(handler=_handle_vehicles_host_status)

    host_restart = host_commands.add_parser(
        "restart",
        help="Restart the host's run and verify its correlated runtime view.",
        description="Restart the host's run and verify its correlated runtime view.",
    )
    host_restart.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help=_STAGING_VEHICLE_ID_HELP,
    )
    host_restart.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            "Runtime readiness deadline in seconds "
            f"(default: {DEFAULT_READINESS_TIMEOUT_S:g})."
        ),
    )
    host_restart.add_argument(
        "--num-decisions",
        type=int,
        default=0,
        help=(
            "Number of decision cycles to complete. Movement requires a positive count "
            "unless --unbounded is set; 0 remains unbounded with --observe-only."
        ),
    )
    host_restart.add_argument(
        "--unbounded",
        action="store_true",
        help=(
            "Run until stopped, overriding --num-decisions. "
            "Stop with vehicles host stop."
        ),
    )
    host_restart.add_argument(
        "--observe-only",
        action="store_true",
        help=(
            "Run plugins without applying their output. The same mode works on every vehicle."
        ),
    )
    host_restart.add_argument(
        "--open-view",
        action="store_true",
        help=(
            "Open the local Automa runtime views after the first correlated "
            "camera/perception publication is healthy."
        ),
    )
    host_restart.add_argument(
        "--record",
        action="store_true",
        help="Save per-frame images and perception artifacts under a timestamped run directory.",
    )
    host_restart.add_argument(
        "--verbose",
        action="store_true",
        help="Print every frame's detail when output is connected.",
    )
    host_restart.add_argument(
        "--log",
        action="store_true",
        dest="log_to_disk",
        help="Persist background run output to automation.log.",
    )
    host_restart.add_argument(
        "--wait-s",
        type=float,
        default=3.0,
        help="Seconds to wait for graceful stop before forcing termination.",
    )
    host_restart.set_defaults(handler=_handle_vehicles_host_restart)
    host_serve = host_commands.add_parser(
        "serve",
        help="Serve a Chase car's runtime host in the foreground (host create and host run start one).",
        description=(
            "Serve the runtime routes for a Chase car from its latest controller release, "
            "and relaunch on a restart command. `host create` and `host run` start this in the background; "
            "its log is runtime/vehicles/<id>/bundle/runtime/automation/host.log."
        ),
    )
    host_serve.add_argument("--id", required=True, dest="vehicle_id", help="Chase host name.")
    host_serve.set_defaults(handler=_handle_vehicles_host_serve)

    operation = vehicle_commands.add_parser("operation", help="Run bounded vehicle checks and setup tasks.")
    operation.set_defaults(handler=_handle_vehicles_operation_help)
    operation_commands = operation.add_subparsers(dest="operation_command")
    operation_help = operation_commands.add_parser(
        "help",
        help="Show operation-level commands.",
    )
    operation_help.set_defaults(handler=_handle_vehicles_operation_help)
    startup_check = operation_commands.add_parser(
        "startup-check",
        help="Send bounded action pulses and verify camera changes around each command.",
        description="Send bounded action pulses and verify camera changes around each command.",
    )
    startup_check.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles status`.",
    )
    startup_check.add_argument(
        "--timeout-s",
        type=float,
        default=8.0,
        help="Vehicle discovery and operation timeout in seconds.",
    )
    startup_check.add_argument(
        "--throttle",
        type=float,
        default=0.22,
        help="Throttle magnitude for each movement pulse.",
    )
    startup_check.add_argument(
        "--duration-s",
        type=float,
        default=0.3,
        help="Duration of each movement pulse.",
    )
    startup_check.add_argument(
        "--settle-s",
        type=float,
        default=0.35,
        help="Delay after each pulse before the comparison capture.",
    )
    startup_check.add_argument(
        "--dry-run",
        action="store_true",
        help="Capture every comparison without acquiring control or sending movement pulses.",
    )
    startup_check.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable operation result.",
    )
    startup_check.set_defaults(handler=_handle_vehicles_operation_startup_check)

    stream = vehicle_commands.add_parser("stream", help="Read a host's rolling outputs.")
    stream_commands = stream.add_subparsers(dest="stream_command", required=True)
    stream_help = stream_commands.add_parser(
        "help",
        help="Show stream-level commands.",
    )
    stream_help.set_defaults(handler=_handle_vehicles_stream_help)
    perception_stream = stream_commands.add_parser(
        "perception",
        help="Show the latest perception output, replacing the terminal view as it updates.",
        description=(
            "Show the latest perception output, replacing the terminal view as it updates. "
            "Chase uses the local automation worker; PiCar polls onboard "
            "/host/observation/latest and serves a local frame-matched view whose "
            "URL the terminal shows."
        ),
    )
    perception_stream.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles status`.",
    )
    perception_stream.add_argument(
        "--refresh-s",
        type=float,
        default=0.5,
        help="Refresh cadence for the replacing terminal view.",
    )
    perception_stream.add_argument(
        "--once",
        action="store_true",
        help="Render once and exit; exit 2 unless perception is live.",
    )
    perception_stream.add_argument(
        "--no-clear",
        action="store_true",
        help="Print each render below the previous one, keeping earlier renders in scrollback.",
    )
    perception_stream.add_argument(
        "--json",
        action="store_true",
        help="Print one vehicle_perception_live_v0 JSON probe per refresh in place of the terminal view and local view; discovery failures emit an unavailable probe and exit 2.",
    )
    perception_stream.set_defaults(handler=_handle_vehicles_stream_perception)

    memory_stream = stream_commands.add_parser(
        "memory",
        help="Inspect live memory as a key→value ledger (terminal + local map page).",
        description=(
            "Inspect live memory as a key→value ledger, replacing the terminal view as it updates. "
            "Terminal shows each applied plugin's health, epoch and record count, the evidence "
            "publisher, and the step's counters. Chase reads automation worker state; PiCar "
            "serves a local map page, whose URL the terminal shows, listing record_id keys "
            "and the selected value."
        ),
    )
    memory_stream.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles status`.",
    )
    memory_stream.add_argument(
        "--refresh-s",
        type=float,
        default=0.5,
        help="Refresh cadence for the replacing terminal view.",
    )
    memory_stream.add_argument(
        "--once",
        action="store_true",
        help="Render once and exit; exit 2 unless memory is live.",
    )
    memory_stream.add_argument(
        "--no-clear",
        action="store_true",
        help="Print each render below the previous one, keeping earlier renders in scrollback.",
    )
    memory_stream.add_argument(
        "--json",
        action="store_true",
        help="Print one vehicle_memory_live_v1 JSON probe per refresh in place of the terminal view and local view; discovery failures emit an unavailable probe and exit 2.",
    )
    memory_stream.set_defaults(handler=_handle_vehicles_stream_memory)

    for step_name in DECISION_STEPS:
        step_stream = stream_commands.add_parser(
            step_name,
            help=f"Show the {step_name} record of the latest cycle the vehicle published.",
            description=(
                f"Read the vehicle runtime host's latest cycle and print its {step_name} record"
                + {
                    "proposal": ", with the observation and memory it read and its candidates",
                    "plan": ", with the selected candidate and contributions",
                    "action": ", with its authority and the host's application of the output",
                }[step_name]
                + ". Accepts only a fresh cycle whose generation matches its step selections, "
                "replacing the terminal view as each arrives."
            ),
        )
        step_stream.add_argument(
            "--id",
            required=True,
            dest="vehicle_id",
            help="Vehicle id from `automa vehicles status`.",
        )
        step_stream.add_argument(
            "--refresh-s",
            type=float,
            default=0.5,
            help="Refresh cadence for the replacing terminal view.",
        )
        step_stream.add_argument(
            "--once",
            action="store_true",
            help="Accept one cycle and exit; exit 2 when none is available.",
        )
        step_stream.add_argument(
            "--no-clear",
            action="store_true",
            help="Print each render below the previous one, keeping earlier renders in scrollback.",
        )
        step_stream.add_argument(
            "--json",
            action="store_true",
            help=f"Print one vehicle_{step_name}_stream_v1 JSON object per refresh.",
        )
        step_stream.set_defaults(handler=_handle_vehicles_stream_step, step=step_name)

    memory_control = vehicle_commands.add_parser(
        "memory",
        help="Operate vehicle memory (inspect, viability, reset).",
    )
    memory_control.set_defaults(handler=_handle_vehicles_memory_help)
    memory_commands = memory_control.add_subparsers(dest="memory_command")
    memory_help = memory_commands.add_parser(
        "help",
        help="Show memory-level commands.",
    )
    memory_help.set_defaults(handler=_handle_vehicles_memory_help)
    memory_reset = memory_commands.add_parser(
        "reset",
        help="Reset live memory to a new empty epoch on the vehicle's runtime host.",
        description=(
            "POST /host/memory/reset to the vehicle's runtime host. Confirms that every "
            "applied plugin started a new epoch or holds no records when the host answers. "
            "Does not move the vehicle."
        ),
    )
    memory_reset.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles status`.",
    )
    memory_reset.add_argument(
        "--timeout-s",
        type=float,
        default=3.0,
        help="HTTP timeout in seconds.",
    )
    memory_reset.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable reset payload.",
    )
    memory_reset.set_defaults(handler=_handle_vehicles_memory_reset)
    memory_inspect = memory_commands.add_parser(
        "inspect",
        help="Show what a memory selection retains, from images or a recorded run.",
        description=(
            "Show what a memory selection retains. The source (an image, a directory of "
            "images, or a recorded perception or memory run) goes through perception and observation, "
            "then the selected memory plugins, frame by frame. A recorded run restores its "
            "perception and memory selections, including their configs; --preset or --plugin "
            "overrides memory. Otherwise each step uses its default. Reports each plugin's health, "
            "record count and epoch, and the evidence publisher, after every frame. It reads "
            "the source only; record live frames with `perception inspect --record` and "
            "inspect that run. Absent frames "
            "update memory with an empty observation. Unmanifested images use filename order "
            "and times 0, 1000, 2000, ... ms, as perception inspect and workbench replay do."
        ),
    )
    memory_inspect.add_argument(
        "source",
        type=Path,
        help="Image file, recorded perception or memory run, or directory of images.",
    )
    _add_selection_args(memory_inspect, "memory", verb="inspect", fallback="the recorded selection, else ")
    memory_inspect.add_argument(
        "--record",
        action="store_true",
        help="Persist source frames, timing, both step selections and the per-frame report for replay.",
    )
    memory_inspect.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable report.",
    )
    memory_inspect.set_defaults(handler=_handle_vehicles_memory_inspect)

    decision_inspects = []
    for step_name in DECISION_STEPS:
        step_control = vehicle_commands.add_parser(
            step_name,
            help=f"Inspect the {step_name} step offline, from images or a recorded run.",
        )
        step_control.set_defaults(handler=_handle_vehicles_decision_step_help, step=step_name)
        step_commands = step_control.add_subparsers(dest=f"{step_name}_command")
        step_commands.add_parser(
            "help", help=f"Show {step_name}-level commands.",
        ).set_defaults(handler=_handle_vehicles_decision_step_help, step=step_name)
        step_inspect = step_commands.add_parser(
            "inspect",
            help=f"Show the {step_name} record for every frame of images or a recorded run.",
            description=(
                f"Replay the source (an image, a directory of images, or a recorded automation, "
                f"perception, memory or decision-step inspect run) through perception, observation "
                f"and memory, then proposal, plan and action, frame by frame, and report the "
                f"{step_name} record after each. A recording restores every step's selection, "
                f"restaged per frame as recorded; explicit selections override {step_name}. Otherwise each "
                f"step uses its default. Recorded frames replay with their recorded drive mode."
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
            help=f"Persist the reported frames, every step's selection and the per-frame report under runtime/{step_name}-inspections/ for replay.",
        )
        step_inspect.add_argument(
            "--json",
            action="store_true",
            help="Print the machine-readable report.",
        )
        step_inspect.set_defaults(handler=_handle_vehicles_decision_step_inspect, step=step_name)
        decision_inspects.append(step_inspect)
    memory_viability = memory_commands.add_parser(
        "viability",
        help="Health-check memory on a vehicle.",
        description=(
            "Health-check memory on a vehicle. The live memory step of the vehicle's "
            "runtime host is polled for a bounded interval (default 60s) to "
            "record update cadence, update duration, failures, and each applied "
            "plugin's health and epoch stability. Measurements save report.json under "
            "lab/runs/memory-viability/ unless --no-record."
        ),
    )
    memory_viability.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles status`.",
    )
    memory_viability.add_argument(
        "--duration-s",
        type=float,
        default=60.0,
        help="Measurement window in seconds (default: 60).",
    )
    memory_viability.add_argument(
        "--sample-period-s",
        type=float,
        default=0.25,
        help="Status poll period in seconds (default: 0.25).",
    )
    memory_viability.add_argument(
        "--timeout-s",
        type=float,
        default=3.0,
        help="Per-request timeout in seconds (default: 3).",
    )
    memory_viability.add_argument(
        "--no-record",
        action="store_true",
        help="Do not write a viability report directory.",
    )
    memory_viability.add_argument(
        "--json",
        action="store_true",
        help="Print the report or preflight error as JSON; the report is also saved unless --no-record.",
    )
    memory_viability.set_defaults(handler=_handle_vehicles_memory_viability)

    workbench = vehicle_commands.add_parser(
        "workbench",
        help="Replay images through the decision playback workbench.",
    )
    workbench.set_defaults(handler=_handle_vehicles_workbench_help)
    workbench_commands = workbench.add_subparsers(
        dest="workbench_command",
        required=True,
    )
    workbench_help = workbench_commands.add_parser(
        "help",
        help="Show workbench-level commands.",
    )
    workbench_help.set_defaults(handler=_handle_vehicles_workbench_help)
    workbench_replay = workbench_commands.add_parser(
        "replay",
        help=(
            "Replay an ordered image directory through perception, memory, "
            "proposals, and decisions."
        ),
        description=(
            "Run the bounded decision playback workbench against an ordered "
            "local image directory. The server owns source ordering, perception, "
            "observation, bounded memory, decision state, and any selected "
            "packaged plugins. Recorded perception and memory runs preserve frame order "
            "and timestamps. Perception, memory, proposal and plan each start from a packaged preset "
            "or an ordered plugin list, as the inspect and update commands take "
            "them; a preset keeps its plugin configs. Page checkboxes retain the order "
            "of selected plugins and append newly checked plugins. A one-plugin step "
            "is a single choice and cannot be cleared. A changed ordered "
            "selection uses default configs for that step; the other steps keep their "
            "selections and configs. The same ordered selection keeps the current "
            "configs and pass. Without --serve, one replay runs "
            "to a terminal state; --serve keeps the loopback page available for "
            "pause, step, reset, and another run. Changing a step's plugins in a "
            "running or paused served replay rebuilds every step's pipeline "
            "and replays from the first frame to the displayed frame before returning."
        ),
    )
    workbench_replay.add_argument(
        "source_dir",
        help="Directory containing supported images and optional ordered manifest.",
    )
    for step_name in SELECTABLE_STEPS:
        _add_selection_args(workbench_replay, step_name, verb="replay", flag_prefix=f"{step_name}-")
    workbench_replay.add_argument(
        "--cadence-ms",
        type=int,
        default=250,
        help="Delay between frames in milliseconds (default: 250; zero means as fast as possible).",
    )
    workbench_replay.add_argument(
        "--pace",
        choices=("fixed", "realtime"),
        default="fixed",
        help="Playback pacing mode (realtime honors recorded frame timestamps; default: fixed).",
    )
    workbench_replay.add_argument(
        "--max-frames",
        type=int,
        default=WORKBENCH_DEFAULT_MAX_FRAMES,
        help=(
            "Maximum normalized frames accepted from the source "
            f"(default: {WORKBENCH_DEFAULT_MAX_FRAMES})."
        ),
    )
    workbench_replay.add_argument(
        "--host",
        default="127.0.0.1",
        help="Loopback host for --serve (default: 127.0.0.1).",
    )
    workbench_replay.add_argument(
        "--port",
        type=int,
        default=0,
        help="Loopback port for --serve (default: choose a free port).",
    )
    workbench_replay.add_argument(
        "--serve",
        action="store_true",
        help="Keep the loopback workbench server alive after starting the replay.",
    )
    workbench_replay.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the loopback workbench page in a browser and imply --serve.",
    )
    workbench_replay.set_defaults(handler=_handle_vehicles_workbench_replay)

    info = vehicle_commands.add_parser("info", help="Inspect locally staged controller configuration.")
    info_commands = info.add_subparsers(dest="info_command", required=True)
    info_help = info_commands.add_parser(
        "help",
        help="Show info-level commands.",
    )
    info_help.set_defaults(handler=_handle_vehicles_info_help)
    perception_info = info_commands.add_parser(
        "perception",
        help="Show the staged perception preset and plugins, the runner schema and the published view URL.",
        description="Show the staged perception preset and plugins, the runner schema and the published view URL.",
    )
    perception_info.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles status`.",
    )
    perception_info.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable perception info payload.",
    )
    perception_info.set_defaults(handler=_handle_vehicles_info_perception)

    memory_info = info_commands.add_parser(
        "memory",
        help="Show the staged memory preset and plugins, the runner schema and the live memory step state.",
        description="Show the staged memory preset and plugins, the runner schema and the live memory step state.",
    )
    memory_info.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles status`.",
    )
    memory_info.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable memory info payload.",
    )
    memory_info.set_defaults(handler=_handle_vehicles_info_memory)

    for step_name in DECISION_STEPS:
        runner_schema = (
            " and the runner schema" if STEP_RUNNERS[step_name].schema_name else ""
        )
        step_info = info_commands.add_parser(
            step_name,
            help=f"Show the staged {step_name} plugins, the decision they belong to and the live {step_name} step state.",
            description=(
                f"Show the staged {step_name} plugins"
                + runner_schema
                + ", the proposal, plan and action plugins of the staged decision with its "
                f"generation and authority, and the {step_name} step as the running autonomy "
                "engine has it."
            ),
        )
        step_info.add_argument(
            "--id",
            required=True,
            dest="vehicle_id",
            help="Vehicle id from `automa vehicles status`.",
        )
        step_info.add_argument(
            "--json",
            action="store_true",
            help=f"Print the full machine-readable {step_name} info payload.",
        )
        step_info.set_defaults(handler=_handle_vehicles_info_step, step=step_name)

    perception_control = vehicle_commands.add_parser(
        "perception",
        help="Inspect what perception detects and measure its viability.",
    )
    perception_control.set_defaults(handler=_handle_vehicles_perception_help)
    perception_commands = perception_control.add_subparsers(dest="perception_command")
    perception_help = perception_commands.add_parser(
        "help",
        help="Show perception-level commands.",
    )
    perception_help.set_defaults(handler=_handle_vehicles_perception_help)

    perception_inspect = perception_commands.add_parser(
        "inspect",
        help="Show what a perception selection detects, from images or a live vehicle.",
        description=(
            "Show what a perception selection detects. With a source (an image, a directory "
            "of images, or a recorded run) the selection is applied to those images. Without "
            "one, frames are read from an active vehicle without taking movement control; "
            "when several are active, the simulator is selected by default. Recorded "
            "perception and memory runs restore their perception selection and preserve "
            "frame identity and timestamps; --preset or --plugin overrides that selection. "
            "Absent frames are reported as unavailable and reset temporal state. "
            "Unmanifested images use filename order and times 0, 1000, 2000, ... ms, "
            "as memory inspect and workbench replay do."
        ),
    )
    perception_inspect.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=None,
        help="Image file, recorded perception or memory run, or directory of images. Omit to read a live vehicle.",
    )
    _add_selection_args(
        perception_inspect, "perception", verb="inspect", fallback="the recorded or staged selection, else ",
    )
    perception_inspect.add_argument(
        "--id",
        dest="vehicle_id",
        default=None,
        help="Live vehicle id. Omit to select a safe observation target automatically.",
    )
    perception_inspect.add_argument(
        "--frames",
        type=int,
        default=None,
        help="Live frames to observe (default: 5).",
    )
    perception_inspect.add_argument(
        "--interval-s",
        type=float,
        default=None,
        help="Delay between live captures in seconds (default: 0.25).",
    )
    perception_inspect.add_argument(
        "--timeout-s",
        type=float,
        default=None,
        help="Vehicle discovery and capture timeout in seconds (default: 3).",
    )
    perception_inspect.add_argument(
        "--record",
        action="store_true",
        help=(
            "Persist the selection, timing, per-frame plugin outputs and report for replay; "
            "live reads also keep the captured frames."
        ),
    )
    perception_inspect.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable report.",
    )
    perception_inspect.set_defaults(handler=_handle_vehicles_perception_inspect)

    for inspection in (perception_inspect, memory_inspect, *decision_inspects):
        inspection.add_argument(
            "--shared-memory",
            type=Path,
            dest="shared_memory_file",
            default=None,
            metavar="FILE",
            help="Start inspect from a shared_memory_v1 JSON file; recorded as provenance.",
        )
        inspection.add_argument(
            "--max-frames",
            type=int,
            default=WORKBENCH_DEFAULT_MAX_FRAMES,
            help=(
                "Reject image directories or recordings above this frame count; "
                f"increase for larger sources (default: {WORKBENCH_DEFAULT_MAX_FRAMES})."
            ),
        )

    perception_viability = perception_commands.add_parser(
        "viability",
        help="Health-check perception on a vehicle.",
        description=(
            "Health-check perception on a vehicle. The runtime host's observation "
            "publication is polled for a bounded interval (default 60s) to record "
            "cadence, result age, processing duration, cycle period, and that no control "
            "was applied, plus "
            "host RSS/CPU when the vehicle supplies an ssh_target. Measurements save "
            "report.json and summary.md under lab/runs/perception-viability/ unless --no-record."
        ),
    )
    perception_viability.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles status`.",
    )
    perception_viability.add_argument(
        "--duration-s",
        type=float,
        default=60.0,
        help="Measurement window in seconds (default: 60).",
    )
    perception_viability.add_argument(
        "--sample-period-s",
        type=float,
        default=0.25,
        help="Publication poll period in seconds (default: 0.25).",
    )
    perception_viability.add_argument(
        "--timeout-s",
        type=float,
        default=3.0,
        help="Per-request timeout in seconds (default: 3).",
    )
    perception_viability.add_argument(
        "--no-record",
        action="store_true",
        help="Do not write a viability report directory.",
    )
    perception_viability.add_argument(
        "--json",
        action="store_true",
        help="Print the report or preflight error as JSON; the report is also saved unless --no-record.",
    )
    perception_viability.set_defaults(handler=_handle_vehicles_perception_viability)

    update = vehicle_commands.add_parser(
        "update",
        help="Stage controller selections or deploy code to a vehicle.",
    )
    update.set_defaults(handler=_handle_vehicles_update_help)
    update_commands = update.add_subparsers(dest="update_command")
    update_help = update_commands.add_parser(
        "help",
        help="Show update-level commands.",
    )
    update_help.set_defaults(handler=_handle_vehicles_update_help)
    for step_name in STEPS:
        step_parser = update_commands.add_parser(
            step_name,
            help=f"Stage {step_name} plugins in the local controller bundle.",
            description=(
                f"Stage packaged {step_name} plugins in the local controller bundle "
                f"(runtime/{step_name}/active.json). Omit a selection to stage the step's default."
            ),
        )
        step_parser.add_argument(
            "--id",
            required=True,
            dest="vehicle_id",
            help=_STAGING_VEHICLE_ID_HELP,
        )
        step_parser.add_argument(
            "--timeout-s",
            type=float,
            default=DEFAULT_READINESS_TIMEOUT_S,
            help=_STAGING_DISCOVERY_TIMEOUT_HELP,
        )
        _add_selection_args(step_parser, step_name, verb="stage")
        step_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the activation manifest without writing it.",
        )
        step_parser.add_argument(
            "--json",
            action="store_true",
            help=f"Print the full machine-readable {step_name} update payload.",
        )
        step_parser.add_argument(
            "--verbose",
            action="store_true",
            help="Print controller release packaging details.",
        )
        step_parser.set_defaults(handler=_handle_vehicles_update_step, step=step_name)

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
    return parser


def _add_selection_args(
    parser: argparse.ArgumentParser,
    step: str,
    *,
    verb: str,
    fallback: str = "",
    flag_prefix: str = "",
) -> None:
    """Add a step's mutually exclusive ``--preset``/``--plugin`` selection.

    Steps without presets take only ``--plugin`` and parse ``preset`` as None.
    ``flag_prefix`` names one step among several on a command, such as
    ``--memory-preset``; ``fallback`` precedes the default in the help text.
    """
    dest = flag_prefix.replace("-", "_")
    group = parser.add_mutually_exclusive_group()
    if step in STEP_PRESETS:
        group.add_argument(
            f"--{flag_prefix}preset",
            dest=f"{dest}preset",
            choices=sorted(STEP_PRESETS[step]),
            default=None,
            help=f"Packaged {step} preset to {verb} (default: {fallback}{DEFAULT_STEP_PRESETS[step]}).",
        )
        plugin_help = f"Packaged {step} plugin to {verb} instead of a preset"
    else:
        parser.set_defaults(**{f"{dest}preset": None})
        plugin_help = (
            f"Packaged {step} plugin to {verb} "
            f"(default: {fallback}{', '.join(DEFAULT_STEP_PLUGINS[step]) or 'none'})"
        )
    group.add_argument(
        f"--{flag_prefix}plugin",
        action="append",
        dest=f"{dest}plugins",
        default=None,
        metavar="PLUGIN_ID",
        help=f"{plugin_help}; repeat to select several in order.",
    )


def _handle_plugins_help(args: argparse.Namespace) -> int:
    print("\n".join([
        "automa vehicles plugins commands", "",
        "- upload  store a file and register an available plugin revision",
        "- arm     add/update named plugins together on a runtime host",
        "- disarm  deselect named plugins or clear steps on a runtime host",
        "- status  show live catalog availability and plugin revisions (list is an alias)",
        "- help    show this summary", "",
        "Requires an existing host or workbench catalog. Plain upload leaves selection unchanged; upload, arm and disarm do not start automation.",
        "Use ./cli/automa vehicles status --id <vehicle> to see its host's state.",
        "Use --url <runtime-or-workbench-url> to address an existing catalog directly.", "",
        "Example:",
        "  ./cli/automa vehicles plugins upload --id chase-sim-chaser --file ./prototype.py --step perception --plugin-id prototype --entrypoint prototype:Prototype",
        "  ./cli/automa vehicles plugins status --id chase-sim-chaser --step perception", "",
        "Upload refuses source that does not compile; imports and dependencies load when armed.",
        "Re-uploading an ID leaves the running revision unchanged. Restart restoration is not guaranteed.",
        "Use upload --config '<JSON object>' to store constructor settings with the revision; arming uses them.", "",
        "Use upload --arm for one-command registration and arming; it calls the same operation as arm.",
        "Arming supports every step. New IDs append; existing IDs update in place.",
        f"On a one-plugin step ({', '.join(ONE_PLUGIN_STEPS)}) the armed ID replaces the plugin.",
        "Other plugins retain their definitions/configs. The host loads the selection before answering;",
        "a load error exits 2 and keeps the previous selection. A loaded selection applies at the next cycle.",
        "Status distinguishes available, requested and applied revisions, and exits 2 while the last selection request failed.",
        "Disarming supports the same steps; a one-plugin step keeps its plugin. Use --step with --plugin IDs,",
        "or --selection for grouped removals. Use disarm --all to clear every step that can run without",
        "plugins, or disarm --step <step> --all to clear one step.",
        "The next cycle resets disarmed instances; catalog entries remain available to arm again.",
        "Arm and disarm change the current host only; staged selections are restored on host restart.",
        "Neither changes control mode or starts or stops automation. Workbench catalogs support upload only.", "",
        "Runtime viewer: open Plugins from its home page. Workbench: catalogs refresh in the step panels.", "",
        "Detailed help: ./cli/automa vehicles plugins <command> --help",
    ]))
    return 0


def _handle_plugin_status(args: argparse.Namespace) -> int:
    result = list_plugins(url=args.url, vehicle_id=args.vehicle_id, step=args.step, json_output=args.json_output)
    print(result.message)
    return result.exit_code


def _handle_plugin_upload(args: argparse.Namespace) -> int:
    result = upload_plugin(
        url=args.url, vehicle_id=args.vehicle_id, file=args.file, step=args.step,
        plugin_id=args.plugin_id, entrypoint=args.entrypoint, config=args.config, arm=args.arm,
        json_output=args.json_output,
    )
    print(result.message)
    return result.exit_code


def _handle_plugin_arm(args: argparse.Namespace) -> int:
    if (args.step and not args.plugins) or (args.selection and args.plugins):
        print("Use --step with one or more --plugin IDs, or --selection without --plugin.")
        return 2
    result = arm_plugins(url=args.url, vehicle_id=args.vehicle_id, step=args.step,
                         plugins=args.plugins, selection=args.selection, json_output=args.json_output)
    print(result.message)
    return result.exit_code


def _handle_plugin_disarm(args: argparse.Namespace) -> int:
    named = bool(args.step and args.plugins)
    if args.selection and (args.step or args.plugins) or args.all_plugins and args.plugins or not (
            args.selection or args.all_plugins or named):
        print("Use --step with --plugin IDs, --selection alone, or --all with an optional --step.")
        return 2
    result = disarm_plugins(url=args.url, vehicle_id=args.vehicle_id, step=args.step,
                            plugins=args.plugins, selection=args.selection,
                            all_plugins=args.all_plugins, json_output=args.json_output)
    print(result.message)
    return result.exit_code


def _handle_top_level_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "Automa is the control desk for the vehicles and simulators in this workspace.",
                "It helps you find what is reachable, stage controller choices locally, and deploy code to a PiCar.",
                "It can create hosts and start and stop their runs without making you remember where the runtime files live.",
                "It also gives you one place to inspect the latest perception output and the controller behavior staged for each vehicle.",
                "Use it when you want to move from editing local code to running that code against a real or simulated vehicle.",
                "The sections below only show the next command level; each command has its own help for details.",
                "",
                "- help       Show this command summary.",
                "- vehicles   Discover vehicles, stage bundles, and inspect each runtime layer.",
                "- simulators Create headless simulators, or prepare and inspect SimEval environments.",
                "",
                "Open the runtime viewer. It does not require a host:",
                "- ./cli/automa",
                "Or on a host from vehicles status:",
                "- ./cli/automa vehicles viewer --host-url <host URL>",
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


def _handle_vehicles_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles commands",
                "",
                "- host         create, update, run, stop and delete hosts",
                "- discover     list a vehicle in status",
                "- remove       stop listing a discovered vehicle",
                "- viewer       open the runtime viewer on a host",
                "- status       show each discovered vehicle and each host's state",
                "- update       stage controller selections or deploy vehicle code",
                "- plugins      upload, arm, disarm and inspect plugins in a live catalog",
                "- operation    run bounded vehicle checks and setup tasks",
                "- info         inspect locally staged controller configuration",
                "- memory       operate memory (inspect, viability, reset)",
                "- proposal     inspect proposals offline; same for plan and action",
                (
                    "- workbench    replay images through perception, memory, proposals, "
                    "and decisions"
                ),
                "- perception   run and configure vehicle perception",
                "- stream       read a host's rolling outputs",
                "- help         show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles <group> help",
                "- ./cli/automa vehicles <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_host_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles host commands",
                "",
                "- create    create and start a host",
                "- update    package staged steps as a release for the host",
                "- delete    stop the host and its run, and remove its local files",
                "- run       start a run and verify camera + perception + current view",
                "- stop      stop the run; the host stays attached until host delete",
                "- restart   stop and start the run",
                "- status    show each host's deployment, run and view state",
                "- help      show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles host <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_operation_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles operation commands",
                "",
                "- startup-check  send bounded pulses and verify camera changes",
                "- help           show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles operation <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_update_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles update commands",
                "",
                "- perception   stage a perception preset or plugin selection",
                "- observation  stage observation plugins",
                "- memory       stage a memory preset or plugin selection",
                "- proposal     stage a proposal preset or plugin selection",
                "- plan         stage a plan preset or plugin selection",
                "- action       stage the action plugin (hold or mode)",
                "- help         show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles update <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_info_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles info commands",
                "",
                "- perception  show staged perception schema and live view",
                "- memory      show staged memory schema and live memory",
                "- proposal    show staged proposal schema, the staged decision and live proposals",
                "- plan        show staged plan plugins, the staged decision and the live plan step",
                "- action      show staged action plugins, the staged decision and the live action step",
                "- help        show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles info <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_perception_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles perception commands",
                "",
                "- inspect    show what a selection detects, from images or a live vehicle",
                "- viability  health-check perception cadence and freshness",
                "- help       show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles perception <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_stream_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles stream commands",
                "",
                "- perception  show latest local automation perception output",
                "- memory      show live memory lifecycle health",
                "- proposal    show the latest cycle's proposals and their inputs",
                "- plan        show the latest cycle's plan and selected candidate",
                "- action      show the latest cycle's authority and host application",
                "- help        show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles stream <command> --help",
            ]
        )
    )
    return 0


_TIMEOUT_INPUT_ERROR = "timeout_invalid"
_TIMEOUT_INPUT_CONSTRAINT = "finite number greater than zero"
_TIMEOUT_INPUT_RECOVERY = "Provide a finite --timeout-s greater than zero."
# Every `vehicles update <step>` resolves its vehicle with `staging_vehicle`.
_STAGING_VEHICLE_ID_HELP = (
    "Name of a host created with vehicles host create."
)
_STAGING_DISCOVERY_TIMEOUT_HELP = (
    "Request timeout in seconds; local staging reads the host record without probing "
    f"(default: {DEFAULT_READINESS_TIMEOUT_S:g})."
)


def _validate_timeout_input(
    timeout_s: float,
    *,
    command: str,
    json_output: bool,
    human_stream: TextIO,
) -> bool:
    """Reject malformed command-envelope timeouts before dispatching work."""

    if math.isfinite(timeout_s) and timeout_s > 0:
        return True

    message = f"{command}: --timeout-s must be a {_TIMEOUT_INPUT_CONSTRAINT}"
    if json_output:
        print(
            json.dumps(
                {
                    "schema": "automa_cli_error_v1",
                    "error": _TIMEOUT_INPUT_ERROR,
                    "layer": "input",
                    "message": message,
                    "details": {
                        "argument": "--timeout-s",
                        "constraint": _TIMEOUT_INPUT_CONSTRAINT,
                    },
                    "recovery": _TIMEOUT_INPUT_RECOVERY,
                    "exit_code": 2,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(message, file=human_stream)
    return False


def _handle_vehicles_status(args: argparse.Namespace) -> int:
    if not _validate_timeout_input(
        args.timeout_s,
        command="automa vehicles status",
        json_output=args.json,
        human_stream=sys.stderr,
    ):
        return 2
    try:
        payload = get_chase_simulator_status(vehicle_id=args.vehicle_id, timeout_s=args.timeout_s)
    except ValueError as exc:
        print(f"automa vehicles status: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_chase_simulator_status(payload))
    if all(vehicle["state"] == "unreachable" for vehicle in payload["vehicles"]):
        return 1
    # Without --id, no host's state changes the exit.
    failed = {"not found", "not running", "not answering", "error"}
    if args.vehicle_id is not None and any(host["state"] in failed for host in payload["hosts"]):
        return 1
    return 0


def _handle_vehicles_host_run(args: argparse.Namespace) -> int:
    if not _validate_timeout_input(
        args.timeout_s,
        command="automa vehicles host run",
        json_output=False,
        human_stream=sys.stdout,
    ):
        return 2
    if args.foreground:
        result = run_vehicle_automation(
            vehicle_id=args.vehicle_id,
            host_url=args.host_url,
            timeout_s=args.timeout_s,
            num_decisions=args.num_decisions,
            unbounded=args.unbounded,
            take_control=not args.observe_only,
            record=args.record,
            verbose=args.verbose,
            output=sys.stdout,
        )
    else:
        result = start_vehicle_automation_background(
            vehicle_id=args.vehicle_id,
            host_url=args.host_url,
            timeout_s=args.timeout_s,
            num_decisions=args.num_decisions,
            unbounded=args.unbounded,
            take_control=not args.observe_only,
            record=args.record,
            verbose=args.verbose,
            log_to_disk=args.log_to_disk,
            open_view=args.open_view,
        )
    if result.message:
        print(result.message)
    if args.foreground and args.vehicle_id:
        record_vehicle_automation_terminal_result(
            vehicle_id=args.vehicle_id,
            result=result,
        )
    return result.exit_code


def _handle_vehicles_host_stop(args: argparse.Namespace) -> int:
    result = stop_vehicle_automation(
        vehicle_id=args.vehicle_id,
        wait_s=args.wait_s,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_host_status(args: argparse.Namespace) -> int:
    result = get_vehicle_automation_status(
        vehicle_id=args.vehicle_id,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_host_serve(args: argparse.Namespace) -> int:
    from .runtime_hosts import serve_chase_host

    return serve_chase_host(args.vehicle_id)


def _handle_vehicles_host_restart(args: argparse.Namespace) -> int:
    result = restart_vehicle_automation(
        vehicle_id=args.vehicle_id,
        timeout_s=args.timeout_s,
        num_decisions=args.num_decisions,
        unbounded=args.unbounded,
        take_control=not args.observe_only,
        record=args.record,
        verbose=args.verbose,
        log_to_disk=args.log_to_disk,
        open_view=args.open_view,
        wait_s=args.wait_s,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_operation_startup_check(args: argparse.Namespace) -> int:
    result = run_vehicle_startup_check(
        vehicle_id=args.vehicle_id,
        timeout_s=args.timeout_s,
        throttle=args.throttle,
        duration_s=args.duration_s,
        settle_s=args.settle_s,
        dry_run=args.dry_run,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_stream_perception(args: argparse.Namespace) -> int:
    result = stream_vehicle_perception(
        vehicle_id=args.vehicle_id,
        refresh_s=args.refresh_s,
        once=args.once,
        no_clear=args.no_clear,
        json_output=args.json,
        output=sys.stdout,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_stream_memory(args: argparse.Namespace) -> int:
    result = stream_vehicle_memory(
        vehicle_id=args.vehicle_id,
        refresh_s=args.refresh_s,
        once=args.once,
        no_clear=args.no_clear,
        json_output=args.json,
        output=sys.stdout,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_stream_step(args: argparse.Namespace) -> int:
    result = stream_vehicle_step(
        args.step,
        vehicle_id=args.vehicle_id,
        refresh_s=args.refresh_s,
        once=args.once,
        no_clear=args.no_clear,
        json_output=args.json,
        output=sys.stdout,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_decision_step_help(args: argparse.Namespace) -> int:
    step = args.step
    print(
        "\n".join(
            [
                f"automa vehicles {step} commands",
                "",
                f"- inspect  the {step} record for every frame of images or a recorded run; optional --frame, --record",
                "- help     show this summary",
                "",
                f"Stage plugins with:              ./cli/automa vehicles update {step} --id <vehicle>",
                f"Inspect staged config with:      ./cli/automa vehicles info {step} --id <vehicle>",
                f"Stream the latest cycle:         ./cli/automa vehicles stream {step} --id <vehicle>",
                "Act on proposals in live modes:  ./cli/automa vehicles update action --id <vehicle> --plugin mode",
                "",
                "Detailed help:",
                f"- ./cli/automa vehicles {step} inspect --help",
            ]
        )
    )
    return 0


def _handle_vehicles_decision_step_inspect(args: argparse.Namespace) -> int:
    result = inspect_decision_step(
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


def _handle_vehicles_memory_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles memory commands",
                "",
                "- inspect what a memory selection retains for an image source, frame by frame; optional --record",
                "- viability  health-check memory update cadence, failures, and epoch",
                "- reset   clear live retained evidence; start a new empty epoch",
                "- help    show this summary",
                "",
                "Stage an implementation with: ./cli/automa vehicles update memory --id <vehicle>",
                "Inspect staged config with:   ./cli/automa vehicles info memory --id <vehicle>",
                "Stream live state with:       ./cli/automa vehicles stream memory --id <vehicle>",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles memory <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_memory_reset(args: argparse.Namespace) -> int:
    result = reset_vehicle_memory(
        vehicle_id=args.vehicle_id,
        timeout_s=args.timeout_s,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_memory_inspect(args: argparse.Namespace) -> int:
    result = inspect_memory(
        str(args.source),
        preset=args.preset,
        plugins=args.plugins,
        record=args.record,
        json_output=args.json,
        max_frames=args.max_frames,
        shared_memory_file=args.shared_memory_file,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_memory_viability(args: argparse.Namespace) -> int:
    result = run_memory_viability_measurement(
        vehicle_id=args.vehicle_id,
        duration_s=args.duration_s,
        sample_period_s=args.sample_period_s,
        timeout_s=args.timeout_s,
        record=not args.no_record,
        json_output=args.json,
        output=None if args.json else sys.stdout,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_workbench_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles workbench commands",
                "",
                (
                    "- replay  replay an ordered image directory through perception, "
                    "memory, proposals, and decisions"
                ),
                "- help    show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles workbench replay --help",
            ]
        )
    )
    return 0


def _handle_vehicles_workbench_replay(args: argparse.Namespace) -> int:
    result = run_workbench_replay(
        args.source_dir,
        selections={
            step: (getattr(args, f"{step}_preset"), getattr(args, f"{step}_plugins"))
            for step in SELECTABLE_STEPS
        },
        cadence_ms=args.cadence_ms,
        pace=args.pace,
        max_frames=args.max_frames,
        host=args.host,
        port=args.port,
        serve=args.serve,
        open_browser=args.open_browser,
        output=sys.stdout,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_host_create(args: argparse.Namespace) -> int:
    result = create_host(
        vehicle_id=args.vehicle_id, provider=args.provider, url=args.url,
        ssh_target=args.ssh_target, pi_home=args.pi_home, drive_args=args.drive_args,
        dry_run=args.dry_run, json_output=args.json, verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
    )
    print(result.message)
    return result.exit_code


def _handle_vehicles_discover(args: argparse.Namespace) -> int:
    result = discover_vehicle(provider=args.provider, url=args.url, force=args.force)
    print(result.message)
    return result.exit_code


def _handle_vehicles_remove(args: argparse.Namespace) -> int:
    result = remove_vehicle(url=args.url)
    print(result.message)
    return result.exit_code


def _handle_vehicles_viewer(args: argparse.Namespace) -> int:
    result = serve_host_viewer(host_url=args.host_url)
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_host_update(args: argparse.Namespace) -> int:
    result = update_host(
        vehicle_id=args.vehicle_id, dry_run=args.dry_run, restart=args.restart,
        drive_args=args.drive_args, json_output=args.json, verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
    )
    print(result.message)
    return result.exit_code


def _handle_vehicles_host_delete(args: argparse.Namespace) -> int:
    result = delete_host(vehicle_id=args.vehicle_id, verbose=args.verbose, output=sys.stdout)
    print(result.message)
    return result.exit_code


def _handle_vehicles_info_perception(args: argparse.Namespace) -> int:
    result = get_vehicle_perception_info(
        vehicle_id=args.vehicle_id,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_info_memory(args: argparse.Namespace) -> int:
    result = get_vehicle_memory_info(
        vehicle_id=args.vehicle_id,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_info_step(args: argparse.Namespace) -> int:
    result = get_vehicle_step_info(
        args.step,
        vehicle_id=args.vehicle_id,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_perception_inspect(args: argparse.Namespace) -> int:
    result = inspect_perception(
        args.source,
        vehicle_id=args.vehicle_id,
        frames=args.frames,
        interval_s=args.interval_s,
        timeout_s=args.timeout_s,
        record=args.record,
        json_output=args.json,
        preset=args.preset,
        plugins=args.plugins,
        max_frames=args.max_frames,
        shared_memory_file=args.shared_memory_file,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_perception_viability(args: argparse.Namespace) -> int:
    result = run_perception_viability_measurement(
        vehicle_id=args.vehicle_id,
        duration_s=args.duration_s,
        sample_period_s=args.sample_period_s,
        timeout_s=args.timeout_s,
        record=not args.no_record,
        json_output=args.json,
        output=None if args.json else sys.stdout,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_update_step(args: argparse.Namespace) -> int:
    if not _validate_timeout_input(
        args.timeout_s,
        command=f"automa vehicles update {args.step}",
        json_output=args.json,
        human_stream=sys.stdout,
    ):
        return 2
    exit_code, message = update_vehicle_step(
        vehicle_id=args.vehicle_id,
        step=args.step,
        plugins=args.plugins,
        preset=args.preset,
        runtime_root=VEHICLES_RUNTIME_ROOT,
        timeout_s=args.timeout_s,
        dry_run=args.dry_run,
        json_output=args.json,
        verbose=args.verbose,
        # Progress stays off stdout when stdout carries the JSON payload.
        output=sys.stderr if args.json else sys.stdout,
    )
    if message:
        print(message)
    return exit_code


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
