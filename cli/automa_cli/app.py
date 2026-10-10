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
from .deploy import update_vehicle_autonomy, update_vehicle_core
from .decision_steps import get_vehicle_step_info, inspect_decision_step, stream_vehicle_step
from .memory import (
    get_vehicle_memory_info,
    update_vehicle_memory,
)
from .memory_runs import inspect_memory, reset_vehicle_memory
from .operations import run_vehicle_startup_check
from autonomy.plugins import DuplicatePluginIdError
from autonomy.runtime.session import DEFAULT_INTERVAL_S
from implementations.decision_cycle.catalog import DEFAULT_STEP_PLUGINS
from implementations.decision_cycle.memory.presets import (
    DEFAULT_MEMORY_PRESET,
    available_memory_preset_ids,
)
from implementations.decision_cycle.perception.presets import (
    DEFAULT_PERCEPTION_PRESET,
    available_perception_preset_ids,
)

from .step_activations import GENERIC_UPDATE_STEPS, update_vehicle_step
from .runtime_hosts import RUNTIME_ROOT as VEHICLES_RUNTIME_ROOT
from .perception import (
    get_vehicle_perception_info,
    update_vehicle_perception,
)
from .perception_runs import (
    inspect_perception,
)
from .workbench import run_workbench_replay
from .plugin_catalog import arm_plugins, list_plugins, upload_plugin
from autonomy.runtime.plugin_catalog import ARMING_STEPS
from autonomy.decision_cycle.activation import DECISION_STEPS, STEPS
from .workbench_source import WORKBENCH_DEFAULT_MAX_FRAMES
from .simulators import DEFAULT_SCENARIO_ID, ensure_simulator, get_simulator_status
from .viability import (
    run_memory_viability_measurement,
    run_perception_viability_measurement,
)
from .streaming import stream_vehicle_memory, stream_vehicle_perception
from .vehicles import (
    DEFAULT_READINESS_TIMEOUT_S,
    discover_active_vehicles,
    format_active_vehicles,
    format_vehicle_status,
    get_vehicle_status,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="automa",
        description="Single access point for local and vehicle-facing automation commands.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

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

    plugins = vehicle_commands.add_parser("plugins", help="Upload, arm and inspect plugins in a live catalog.")
    plugins.set_defaults(handler=_handle_plugins_help)
    plugin_commands = plugins.add_subparsers(dest="plugin_command")
    plugins_help = plugin_commands.add_parser("help", help="Show upload, arming and catalog inspection usage.")
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
    upload.add_argument("--arm", action="store_true", help="After registration, add/update this ID in the runtime host's requested selection; other plugins stay selected.")
    upload.add_argument("--json", dest="json_output", action="store_true", help="Print the registration receipt as JSON.")
    upload.set_defaults(handler=_handle_plugin_upload)

    arm = plugin_commands.add_parser(
        "arm", help="Add/update uploaded or packaged plugins without restarting automation.",
        description="Request the latest catalog definitions together. New IDs append; existing IDs update in place. The host loads them before answering: an import or construction error exits 2 and keeps the previous selection. A loaded selection applies at the host's next cycle.",
        epilog="With --selection, use a JSON map such as {\"perception\": [\"prototype\"], \"memory\": [\"helper\"]}. Arming never starts automation or changes its control mode.",
    )
    arm_target = arm.add_mutually_exclusive_group(required=True)
    arm_target.add_argument("--id", dest="vehicle_id", help="Vehicle whose runtime host receives the request.")
    arm_target.add_argument("--url", help="Runtime host URL; workbench catalogs support upload only.")
    arm_input = arm.add_mutually_exclusive_group(required=True)
    arm_input.add_argument("--step", choices=ARMING_STEPS, help="Step receiving the ordered --plugin IDs.")
    arm_input.add_argument("--selection", type=Path, help="JSON map of supported steps to ordered plugin-ID lists; arm the group together.")
    arm.add_argument("--plugin", action="append", dest="plugins", help="Plugin ID to add/update; repeat in the desired append order with --step.")
    arm.add_argument("--json", dest="json_output", action="store_true", help="Print the request receipt and selection state as JSON.")
    arm.set_defaults(handler=_handle_plugin_arm)

    active = vehicle_commands.add_parser(
        "active",
        help="Discover vehicle endpoints; this does not imply deployment or a running worker.",
        description=(
            "Discover front-camera-capable vehicle endpoints. Discoverable does not "
            "mean automation is deployed, running, or publishing a view."
        ),
    )
    active.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            "One wall-clock readiness deadline per candidate in seconds "
            f"(default: {DEFAULT_READINESS_TIMEOUT_S:g})."
        ),
    )
    active.add_argument(
        "--picar-url",
        action="append",
        default=[],
        help="Additional PiCar Donkey HTTP base URL to probe. May be repeated.",
    )
    active.add_argument(
        "--chase-ws-url",
        action="append",
        default=[],
        help="Additional Chase simulator Metrics UI WS URL to probe. May be repeated.",
    )
    active.add_argument(
        "--no-picar",
        action="store_true",
        help="Skip PiCar Donkey HTTP discovery.",
    )
    active.add_argument(
        "--no-sim",
        action="store_true",
        help="Skip Chase simulator WS discovery.",
    )
    active.add_argument(
        "--active-only",
        action="store_true",
        help="Hide undiscoverable candidates and readiness diagnostics.",
    )
    active.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable discovery payload.",
    )
    active.set_defaults(handler=_handle_vehicles_active)

    status = vehicle_commands.add_parser(
        "status",
        help="Show simulator, vehicle, deployment, worker, and perception-view state.",
        description=(
            "Read the complete passive Chase journey state without starting a "
            "simulator, changing its session, launching a browser, or starting a worker."
        ),
    )
    status.add_argument(
        "--id",
        dest="vehicle_id",
        default=None,
        help="Vehicle id to inspect. Omit to list every discoverable or locally deployed id.",
    )
    status.add_argument(
        "--chase-url",
        default=None,
        help=(
            "Metrics UI HTTP(S) or WS(S) URL. HTTP origins are resolved to "
            "/ws/control (default: CHASE_UI_WS_URL, then http://localhost:5050)."
        ),
    )
    status.add_argument(
        "--chase-ws-url",
        default=None,
        help=(
            "Compatibility form for an explicit Metrics UI WebSocket URL; "
            "cannot be combined with --chase-url."
        ),
    )
    status.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            "One wall-clock deadline for all Chase readiness phases in seconds "
            f"(default: {DEFAULT_READINESS_TIMEOUT_S:g})."
        ),
    )
    status.add_argument(
        "--json",
        action="store_true",
        help="Print automa_vehicle_status_v1 JSON.",
    )
    status.set_defaults(handler=_handle_vehicles_status)

    automation = vehicle_commands.add_parser(
        "automation",
        help=(
            "Run the shared decision and movement runtime on a vehicle. "
            "The vehicle ID selects local or onboard hosting."
        ),
    )
    automation.set_defaults(handler=_handle_vehicles_automation_help)
    automation_commands = automation.add_subparsers(dest="automation_command")
    automation_help = automation_commands.add_parser(
        "help",
        help="Show automation-level commands.",
    )
    automation_help.set_defaults(handler=_handle_vehicles_automation_help)
    automation_run = automation_commands.add_parser(
        "run",
        help="Start an automation run and verify one correlated camera/perception publication.",
        description=(
            "Start autonomous movement using the staged plugins. Success requires one camera frame, its "
            "completed perception result, and a healthy current-generation loopback view."
        ),
    )
    automation_run.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Discoverable vehicle id from `automa vehicles status`.",
    )
    automation_run.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            "Runtime readiness deadline in seconds "
            f"(default: {DEFAULT_READINESS_TIMEOUT_S:g})."
        ),
    )
    automation_run.add_argument(
        "--interval-s",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help="Target seconds between camera captures. Decisions immediately use the newest pending frame.",
    )
    automation_run.add_argument(
        "--num-decisions",
        type=int,
        default=0,
        help=(
            "Number of decision cycles to complete; a run that applies control needs one. "
            "0 (unbounded) is allowed only with --observe-only; stop it with vehicles automation stop."
        ),
    )
    automation_run.add_argument(
        "--observe-only",
        action="store_true",
        help=(
            "Run plugins without applying their output. The same mode works on every vehicle."
        ),
    )
    automation_run.add_argument(
        "--open-view",
        action="store_true",
        help=(
            "Open the local Automa runtime views after the first correlated "
            "camera/perception publication is healthy."
        ),
    )
    automation_run.add_argument(
        "--record",
        action="store_true",
        help="Save per-frame images and perception artifacts under a timestamped run directory.",
    )
    automation_run.add_argument(
        "--verbose",
        action="store_true",
        help="Print every frame's detail when output is connected.",
    )
    automation_run.add_argument(
        "--log",
        action="store_true",
        dest="log_to_disk",
        help="Persist background run output to automation.log.",
    )
    automation_run.add_argument(
        "--foreground",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    automation_run.set_defaults(handler=_handle_vehicles_automation_run)

    automation_stop = automation_commands.add_parser(
        "stop",
        help="Stop the background automation loop for a vehicle.",
        description=(
            "Stop autonomous movement and its publication monitor. The deployment remains staged and "
            "its former view is no longer current-generation available."
        ),
    )
    automation_stop.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Locally deployed vehicle id from `automa vehicles status`.",
    )
    automation_stop.add_argument(
        "--wait-s",
        type=float,
        default=3.0,
        help="Seconds to wait for graceful stop before forcing termination.",
    )
    automation_stop.set_defaults(handler=_handle_vehicles_automation_stop)

    automation_status = automation_commands.add_parser(
        "status",
        help="Show locally deployed automation runtimes and run status.",
        description="Show locally deployed automation runtimes and run status.",
    )
    automation_status.add_argument(
        "--id",
        dest="vehicle_id",
        default=None,
        help="Vehicle id from `automa vehicles active`. Omit to list locally deployed automation runtimes.",
    )
    automation_status.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable automation status payload.",
    )
    automation_status.set_defaults(handler=_handle_vehicles_automation_status)

    automation_restart = automation_commands.add_parser(
        "restart",
        help="Restart the automation run and verify its correlated runtime view.",
        description="Restart the automation run and verify its correlated runtime view.",
    )
    automation_restart.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
    )
    automation_restart.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            "Runtime readiness deadline in seconds "
            f"(default: {DEFAULT_READINESS_TIMEOUT_S:g})."
        ),
    )
    automation_restart.add_argument(
        "--interval-s",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help="Target seconds between camera captures. Decisions immediately use the newest pending frame.",
    )
    automation_restart.add_argument(
        "--num-decisions",
        type=int,
        default=0,
        help=(
            "Number of decision cycles to complete; a run that applies control needs one. "
            "0 (unbounded) is allowed only with --observe-only; stop it with vehicles automation stop."
        ),
    )
    automation_restart.add_argument(
        "--observe-only",
        action="store_true",
        help=(
            "Run plugins without applying their output. The same mode works on every vehicle."
        ),
    )
    automation_restart.add_argument(
        "--open-view",
        action="store_true",
        help=(
            "Open the local Automa runtime views after the first correlated "
            "camera/perception publication is healthy."
        ),
    )
    automation_restart.add_argument(
        "--record",
        action="store_true",
        help="Save per-frame images and perception artifacts under a timestamped run directory.",
    )
    automation_restart.add_argument(
        "--verbose",
        action="store_true",
        help="Print every frame's detail when output is connected.",
    )
    automation_restart.add_argument(
        "--log",
        action="store_true",
        dest="log_to_disk",
        help="Persist background run output to automation.log.",
    )
    automation_restart.add_argument(
        "--wait-s",
        type=float,
        default=3.0,
        help="Seconds to wait for graceful stop before forcing termination.",
    )
    automation_restart.set_defaults(handler=_handle_vehicles_automation_restart)
    automation_host = automation_commands.add_parser(
        "host",
        help="Serve a Chase car's runtime host in the foreground (automation run starts one).",
        description=(
            "Serve the runtime routes for a Chase car from its latest controller release, "
            "and relaunch on a restart command. `automation run` starts this in the background; "
            "its log is runtime/vehicles/<id>/bundle/runtime/automation/host.log."
        ),
    )
    automation_host.add_argument("--id", required=True, dest="vehicle_id", help="Chase vehicle id.")
    automation_host.set_defaults(handler=_handle_vehicles_automation_host)

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
        help="Vehicle id from `automa vehicles active`.",
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

    stream = vehicle_commands.add_parser("stream", help="Read rolling local automation output.")
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
            "/autonomy/observation/latest and serves a local frame-matched view whose "
            "URL the terminal shows."
        ),
    )
    perception_stream.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
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
        help="Vehicle id from `automa vehicles active`.",
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
            help="Vehicle id from `automa vehicles active`.",
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
            "POST /autonomy/memory/reset to the vehicle's runtime host. Confirms that every "
            "applied plugin started a new epoch or holds no records when the host answers. "
            "Does not move the vehicle."
        ),
    )
    memory_reset.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
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
    memory_inspect_selection = memory_inspect.add_mutually_exclusive_group()
    memory_inspect_selection.add_argument(
        "--preset",
        choices=available_memory_preset_ids(),
        default=None,
        help=f"Override memory with this preset (without a recorded selection: {DEFAULT_MEMORY_PRESET}).",
    )
    memory_inspect_selection.add_argument(
        "--plugin",
        dest="plugins",
        action="append",
        default=None,
        metavar="PLUGIN",
        help="Inspect these packaged memory plugins, in order, with their default configs. Repeatable.",
    )
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
                f"restaged per frame as recorded; --plugin overrides {step_name}. Otherwise each "
                f"step uses its default. Recorded frames replay with their recorded drive mode."
            ),
        )
        step_inspect.add_argument(
            "source",
            type=Path,
            help="Image file, directory of images, or recorded run.",
        )
        step_inspect.add_argument(
            "--plugin",
            dest="plugins",
            action="append",
            default=None,
            metavar="PLUGIN",
            help=f"Inspect these packaged {step_name} plugins, in order, with their default configs. Repeatable.",
        )
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
        help="Vehicle id from `automa vehicles active`.",
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
            "and timestamps. Perception and memory each start from a packaged preset "
            "or an ordered plugin list, as the inspect and update commands take "
            "them; a preset keeps its plugin configs. Proposal has no presets and "
            "starts from an ordered plugin list, as vehicles update proposal takes "
            "it, or its default plugins. Page checkboxes retain the order "
            "of selected plugins and append newly checked plugins. A changed ordered "
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
    workbench_replay_perception = workbench_replay.add_mutually_exclusive_group()
    workbench_replay_perception.add_argument(
        "--perception-preset",
        default=None,
        choices=available_perception_preset_ids(),
        help=f"Packaged perception preset to replay (default: {DEFAULT_PERCEPTION_PRESET}).",
    )
    workbench_replay_perception.add_argument(
        "--perception-plugin",
        action="append",
        dest="perception_plugins",
        default=None,
        metavar="PLUGIN_ID",
        help="Packaged perception plugin to select instead of a preset; repeat to select several in order.",
    )
    workbench_replay_memory = workbench_replay.add_mutually_exclusive_group()
    workbench_replay_memory.add_argument(
        "--memory-preset",
        default=None,
        choices=available_memory_preset_ids(),
        help=f"Packaged memory preset to replay (default: {DEFAULT_MEMORY_PRESET}).",
    )
    workbench_replay_memory.add_argument(
        "--memory-plugin",
        action="append",
        dest="memory_plugins",
        default=None,
        metavar="PLUGIN_ID",
        help="Packaged memory plugin to select instead of a preset; repeat to select several in order.",
    )
    workbench_replay.add_argument(
        "--proposal-plugin",
        action="append",
        dest="proposal_plugins",
        default=None,
        metavar="PLUGIN_ID",
        help="Packaged proposal plugin to select instead of the default plugins; repeat to select several in order.",
    )
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
        help="Vehicle id from `automa vehicles active`.",
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
        help="Vehicle id from `automa vehicles active`.",
    )
    memory_info.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable memory info payload.",
    )
    memory_info.set_defaults(handler=_handle_vehicles_info_memory)

    for step_name in DECISION_STEPS:
        step_info = info_commands.add_parser(
            step_name,
            help=f"Show the staged {step_name} plugins, the decision they belong to and the live {step_name} step state.",
            description=(
                f"Show the staged {step_name} plugins"
                + (" and the runner schema" if step_name == "proposal" else "")
                + ", the proposal, plan and action plugins of the staged decision with its "
                f"generation and authority, and the {step_name} step as the running autonomy "
                "engine has it."
            ),
        )
        step_info.add_argument(
            "--id",
            required=True,
            dest="vehicle_id",
            help="Vehicle id from `automa vehicles active`.",
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
    perception_inspect_selection = perception_inspect.add_mutually_exclusive_group()
    perception_inspect_selection.add_argument(
        "--preset",
        choices=available_perception_preset_ids(),
        default=None,
        help="Inspect one packaged perception preset instead of the recorded or staged selection.",
    )
    perception_inspect_selection.add_argument(
        "--plugin",
        dest="plugins",
        action="append",
        default=None,
        metavar="PLUGIN",
        help="Inspect these packaged perception plugins, in order, with their default configs. Repeatable.",
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
            "cadence, result age, processing duration, skip policy, and that no control "
            "was applied, plus "
            "host RSS/CPU when the vehicle supplies an ssh_target. Measurements save "
            "report.json and summary.md under lab/runs/perception-viability/ unless --no-record."
        ),
    )
    perception_viability.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
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
    core = update_commands.add_parser(
        "core",
        help="Sync the Donkey harness and install its boot-enabled runtime service.",
        description=(
            "Sync the DonkeyCar core harness and install its supervised, boot-enabled "
            "runtime service on a PiCar."
        ),
    )
    core.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
    )
    core.add_argument(
        "--timeout-s",
        type=float,
        default=1.0,
        help="Vehicle discovery timeout in seconds.",
    )
    core.add_argument(
        "--ssh-target",
        default=None,
        help="Override SSH target, for example piracer@piracer.local.",
    )
    core.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Deploy over SSH without requiring the Donkey HTTP endpoint to be discoverable.",
    )
    core.add_argument(
        "--pi-home",
        default=None,
        help="Override remote home directory. Defaults to PI_HOME or /home/piracer.",
    )
    core.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the sync commands without executing them.",
    )
    core.add_argument(
        "--restart",
        action="store_true",
        help="Restart an already-running Donkey runtime after syncing; inactive service starts automatically.",
    )
    core.add_argument(
        "--drive-args",
        default=None,
        help="Persist arguments for `manage.py drive` when --restart is used, for example --js.",
    )
    core.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable core update result.",
    )
    core.add_argument(
        "--verbose",
        action="store_true",
        help="Print each sync command before it runs.",
    )
    core.set_defaults(handler=_handle_vehicles_update_core)

    autonomy = update_commands.add_parser(
        "autonomy",
        help="Package the staged steps as a controller release for the vehicle's host.",
        description=(
            "Package the staged steps as a versioned controller release: a PiCar receives it "
            "over SSH, a simulator vehicle keeps it locally. "
            "With --restart, the host restarts onto it and verifies every step runs its staged plugins. "
            "Memory activation ships here; manage.py load path ships with core—if "
            "verification reports no memory step, update core then re-run autonomy."
        ),
    )
    autonomy.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
    )
    autonomy.add_argument(
        "--timeout-s",
        type=float,
        default=1.0,
        help="Vehicle discovery timeout in seconds.",
    )
    autonomy.add_argument(
        "--ssh-target",
        default=None,
        help="Override SSH target, for example piracer@piracer.local.",
    )
    autonomy.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Deploy over SSH without requiring the Donkey HTTP endpoint to be discoverable.",
    )
    autonomy.add_argument(
        "--pi-home",
        default=None,
        help="Override remote home directory. Defaults to PI_HOME or /home/piracer.",
    )
    autonomy.add_argument(
        "--dry-run",
        action="store_true",
        help="Describe the release and transfer commands without writing or connecting.",
    )
    autonomy.add_argument(
        "--restart",
        action="store_true",
        help=(
            "Restart the vehicle's host (the PiCar's Donkey service or the simulator worker) "
            "onto the release. Without it, "
            "a running runtime selects restaged plugins on its next frame; changed plugin "
            "specs or configs, plan, and action need a restart."
        ),
    )
    autonomy.add_argument(
        "--drive-args",
        default=None,
        help="Persist arguments for `manage.py drive` when --restart is used, for example --js.",
    )
    autonomy.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable deployment result.",
    )
    autonomy.add_argument(
        "--verbose",
        action="store_true",
        help="Print each transfer command before it runs.",
    )
    autonomy.set_defaults(handler=_handle_vehicles_update_autonomy)

    perception = update_commands.add_parser(
        "perception",
        help="Stage a perception preset or plugins in a vehicle's local controller bundle.",
        description=(
            "Stage a perception preset or plugin list in a local vehicle bundle. "
            "Absent observation, plan and action activations receive their built-in "
            "plugins; existing selections are preserved, and proposals and memory "
            "are staged separately. For Chase, the result checks passive capture "
            "and staged activations for observation-only automation."
        ),
    )
    perception.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help=_STAGING_VEHICLE_ID_HELP,
    )
    perception.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            _STAGING_DISCOVERY_TIMEOUT_HELP + " Also bounds each Chase readiness "
            "check after staging and live simulator operations with --restart."
        ),
    )
    perception_selection = perception.add_mutually_exclusive_group()
    perception_selection.add_argument(
        "--preset",
        default=None,
        choices=available_perception_preset_ids(),
        help=f"Packaged perception preset to activate (default: {DEFAULT_PERCEPTION_PRESET}).",
    )
    perception_selection.add_argument(
        "--plugin",
        action="append",
        dest="plugins",
        default=None,
        metavar="PLUGIN_ID",
        help="Packaged perception plugin to select instead of a preset; repeat to select several in order. Staged plugins keep their configs.",
    )
    perception.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the activation manifest without writing it.",
    )
    perception.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable perception update payload.",
    )
    perception.add_argument(
        "--restart",
        action="store_true",
        help="Discover the live simulator, re-prepare its WS controller, and capture a sample perception; local identity metadata does not bypass discovery.",
    )
    perception.add_argument(
        "--verbose",
        action="store_true",
        help="Print step-by-step activation, controller preparation, and sample perception details.",
    )
    perception.set_defaults(handler=_handle_vehicles_update_perception)

    for step_name in GENERIC_UPDATE_STEPS:
        step_parser = update_commands.add_parser(
            step_name,
            help=f"Stage {step_name} plugins in the local controller bundle.",
            description=(
                f"Stage packaged {step_name} plugins in the local controller bundle "
                f"(runtime/{step_name}/active.json). Repeat --plugin to select several, "
                "in order; omit it for the step's default selection."
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
        step_parser.add_argument(
            "--plugin",
            action="append",
            dest="plugins",
            default=None,
            metavar="PLUGIN_ID",
            help=(
                f"Packaged {step_name} plugin to select "
                f"(default: {', '.join(DEFAULT_STEP_PLUGINS[step_name]) or 'none'})."
            ),
        )
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

    memory = update_commands.add_parser(
        "memory",
        help="Stage memory plugins in the local controller bundle.",
        description="Stage memory plugins in the local controller bundle.",
    )
    memory.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help=_STAGING_VEHICLE_ID_HELP,
    )
    memory.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=_STAGING_DISCOVERY_TIMEOUT_HELP,
    )
    memory_selection = memory.add_mutually_exclusive_group()
    memory_selection.add_argument(
        "--preset",
        default=None,
        choices=available_memory_preset_ids(),
        help=f"Packaged memory preset to activate (default: {DEFAULT_MEMORY_PRESET}).",
    )
    memory_selection.add_argument(
        "--plugin",
        action="append",
        dest="plugins",
        default=None,
        metavar="PLUGIN_ID",
        help="Packaged memory plugin to select instead of a preset; repeat to select several in order. Staged plugins keep their configs.",
    )
    memory.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the activation manifest without writing it.",
    )
    memory.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable memory update payload.",
    )
    memory.add_argument(
        "--verbose",
        action="store_true",
        help="Print controller release packaging details.",
    )
    memory.set_defaults(handler=_handle_vehicles_update_memory)

    simulators = subcommands.add_parser("simulators", help="Prepare and inspect simulator environments.")
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
    return parser


def _handle_plugins_help(args: argparse.Namespace) -> int:
    print("\n".join([
        "automa vehicles plugins commands", "",
        "- upload  store a file and register an available plugin revision",
        "- arm     add/update named plugins together on a runtime host",
        "- status  show live catalog availability and plugin revisions (list is an alias)",
        "- help    show this summary", "",
        "Requires an existing host or workbench catalog. Plain upload leaves selection unchanged; neither upload nor arm starts automation.",
        "Use ./cli/automa vehicles status --id <vehicle> for the next startup action when no host is running.",
        "Use --url <runtime-or-workbench-url> to address an existing catalog directly.", "",
        "Example:",
        "  ./cli/automa vehicles plugins upload --id chase-sim-chaser --file ./prototype.py --step perception --plugin-id prototype --entrypoint prototype:Prototype",
        "  ./cli/automa vehicles plugins status --id chase-sim-chaser --step perception", "",
        "Upload refuses source that does not compile; imports and dependencies load when armed.",
        "Re-uploading an ID leaves the running revision unchanged. Restart restoration is not guaranteed.", "",
        "Use upload --arm for one-command registration and arming; it calls the same operation as arm.",
        "Arming supports perception, memory and proposal. New IDs append; existing IDs update in place.",
        "Other plugins retain their definitions/configs. The host loads the selection before answering;",
        "a load error exits 2 and keeps the previous selection. A loaded selection applies at the next cycle.",
        "Status distinguishes available, requested and applied revisions, and exits 2 while the last arm failed.",
        "Arming does not start automation or change control mode. Workbench catalogs support upload only.", "",
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
        plugin_id=args.plugin_id, entrypoint=args.entrypoint, arm=args.arm, json_output=args.json_output,
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


def _handle_top_level_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "Automa is the control desk for the vehicles and simulators in this workspace.",
                "It helps you find what is reachable, stage controller choices locally, and deploy code to a PiCar.",
                "It can start and stop automation runs without making you remember where the runtime files live.",
                "It also gives you one place to inspect the latest perception output and the controller behavior staged for each vehicle.",
                "Use it when you want to move from editing local code to running that code against a real or simulated vehicle.",
                "The sections below only show the next command level; each command has its own help for details.",
                "",
                "- help       Show this command summary.",
                "- vehicles   Discover vehicles, stage bundles, and inspect each runtime layer.",
                "- simulators Explicitly prepare or inspect simulator environments.",
                "",
                "Detailed help:",
                "- ./cli/automa --help",
                "- ./cli/automa <command> help",
            ]
        )
    )
    return 0


def _handle_simulators_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa simulators commands",
                "",
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
                "- active       discover vehicle endpoints (not worker/deployment state)",
                "- status       inspect simulator, deployment, worker, and view layers",
                "- update       stage controller selections or deploy vehicle code",
                "- plugins      upload, arm and inspect plugins in a live catalog",
                "- automation   manage locally deployed automation workers",
                "- operation    run bounded vehicle checks and setup tasks",
                "- info         inspect locally staged controller configuration",
                "- memory       operate memory (inspect, viability, reset)",
                "- proposal     inspect proposals offline; same for plan and action",
                (
                    "- workbench    replay images through perception, memory, proposals, "
                    "and decisions"
                ),
                "- perception   run and configure vehicle perception",
                "- stream       read rolling local automation outputs",
                "- help         show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles <group> help",
                "- ./cli/automa vehicles <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_automation_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles automation commands",
                "",
                "- run       start a run and verify camera + perception + current view",
                "- status    show locally deployed run and view state",
                "- restart   stop and start the automation run",
                "- stop      stop the run; keep its deployment staged",
                "- help      show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles automation <command> --help",
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
                "- core         deploy the DonkeyCar harness to a PiCar",
                "- autonomy     package staged steps as a release for the vehicle's host",
                "- perception   stage local vehicle perception code",
                "- observation  stage observation plugins",
                "- memory       stage memory plugins",
                "- proposal     stage proposal plugins",
                "- plan         stage the plan plugin",
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
    "Vehicle id from `automa vehicles active`. A chase-sim-* id, or a vehicle with "
    "matching identity metadata in any staged step, is known without discovery."
)
_STAGING_DISCOVERY_TIMEOUT_HELP = (
    "Timeout in seconds for each vehicle discovery probe when local identity is unavailable "
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


def _handle_vehicles_active(args: argparse.Namespace) -> int:
    if not _validate_timeout_input(
        args.timeout_s,
        command="automa vehicles active",
        json_output=args.json,
        human_stream=sys.stderr,
    ):
        return 2
    include_inactive = not args.active_only
    payload = discover_active_vehicles(
        timeout_s=args.timeout_s,
        picar_urls=tuple(args.picar_url),
        chase_ws_urls=tuple(args.chase_ws_url),
        include_picar=not args.no_picar,
        include_chase_sim=not args.no_sim,
        include_inactive=include_inactive,
    )
    if args.json:
        import json

        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_active_vehicles(payload, include_inactive=include_inactive))
    return 0


def _handle_vehicles_status(args: argparse.Namespace) -> int:
    if not _validate_timeout_input(
        args.timeout_s,
        command="automa vehicles status",
        json_output=args.json,
        human_stream=sys.stderr,
    ):
        return 2
    if args.chase_url is not None and args.chase_ws_url is not None:
        print(
            "automa vehicles status: --chase-url and --chase-ws-url cannot be used together",
            file=sys.stderr,
        )
        return 2
    try:
        payload = get_vehicle_status(
            vehicle_id=args.vehicle_id,
            chase_url=args.chase_url,
            chase_ws_url=args.chase_ws_url,
            timeout_s=args.timeout_s,
        )
    except ValueError as exc:
        print(f"automa vehicles status: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_vehicle_status(payload))
    return _vehicle_status_exit_code(payload)


def _vehicle_status_exit_code(payload: dict[str, Any]) -> int:
    cards = payload.get("vehicles")
    status_cards = (
        [card for card in cards if isinstance(card, dict)]
        if isinstance(cards, list)
        else [payload]
    )
    normal_next_steps = {
        "automation_not_deployed",
        "worker_stopped",
    }
    for card in status_cards:
        next_action = card.get("next_action")
        if not isinstance(next_action, dict):
            continue
        if str(next_action.get("reason")) not in normal_next_steps:
            return 1
    return 0


def _handle_vehicles_automation_run(args: argparse.Namespace) -> int:
    if not _validate_timeout_input(
        args.timeout_s,
        command="automa vehicles automation run",
        json_output=False,
        human_stream=sys.stdout,
    ):
        return 2
    if args.foreground:
        result = run_vehicle_automation(
            vehicle_id=args.vehicle_id,
            timeout_s=args.timeout_s,
            interval_s=args.interval_s,
            num_decisions=args.num_decisions,
            take_control=not args.observe_only,
            record=args.record,
            verbose=args.verbose,
            output=sys.stdout,
        )
    else:
        result = start_vehicle_automation_background(
            vehicle_id=args.vehicle_id,
            timeout_s=args.timeout_s,
            interval_s=args.interval_s,
            num_decisions=args.num_decisions,
            take_control=not args.observe_only,
            record=args.record,
            verbose=args.verbose,
            log_to_disk=args.log_to_disk,
            open_view=args.open_view,
        )
    if result.message:
        print(result.message)
    if args.foreground:
        record_vehicle_automation_terminal_result(
            vehicle_id=args.vehicle_id,
            result=result,
        )
    return result.exit_code


def _handle_vehicles_automation_stop(args: argparse.Namespace) -> int:
    result = stop_vehicle_automation(
        vehicle_id=args.vehicle_id,
        wait_s=args.wait_s,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_automation_status(args: argparse.Namespace) -> int:
    result = get_vehicle_automation_status(
        vehicle_id=args.vehicle_id,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_automation_host(args: argparse.Namespace) -> int:
    from .runtime_hosts import serve_chase_host

    return serve_chase_host(args.vehicle_id)


def _handle_vehicles_automation_restart(args: argparse.Namespace) -> int:
    result = restart_vehicle_automation(
        vehicle_id=args.vehicle_id,
        timeout_s=args.timeout_s,
        interval_s=args.interval_s,
        num_decisions=args.num_decisions,
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
        plugins=args.plugins,
        frame=args.frame,
        record=args.record,
        json_output=args.json,
        max_frames=args.max_frames,
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
        perception_preset=args.perception_preset,
        perception_plugins=args.perception_plugins,
        memory_preset=args.memory_preset,
        memory_plugins=args.memory_plugins,
        proposal_plugins=args.proposal_plugins,
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


def _handle_vehicles_update_core(args: argparse.Namespace) -> int:
    result = update_vehicle_core(
        vehicle_id=args.vehicle_id,
        timeout_s=args.timeout_s,
        ssh_target=args.ssh_target,
        pi_home=args.pi_home,
        skip_discovery=args.skip_discovery,
        dry_run=args.dry_run,
        restart=args.restart,
        drive_args=args.drive_args,
        json_output=args.json,
        verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_update_autonomy(args: argparse.Namespace) -> int:
    result = update_vehicle_autonomy(
        vehicle_id=args.vehicle_id,
        timeout_s=args.timeout_s,
        ssh_target=args.ssh_target,
        pi_home=args.pi_home,
        skip_discovery=args.skip_discovery,
        dry_run=args.dry_run,
        restart=args.restart,
        drive_args=args.drive_args,
        json_output=args.json,
        verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
    )
    if result.message:
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


def _handle_vehicles_update_memory(args: argparse.Namespace) -> int:
    if not _validate_timeout_input(
        args.timeout_s,
        command="automa vehicles update memory",
        json_output=args.json,
        human_stream=sys.stdout,
    ):
        return 2
    result = update_vehicle_memory(
        vehicle_id=args.vehicle_id,
        preset=args.preset,
        plugins=args.plugins,
        timeout_s=args.timeout_s,
        dry_run=args.dry_run,
        json_output=args.json,
        verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
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










def _handle_vehicles_update_perception(args: argparse.Namespace) -> int:
    if not _validate_timeout_input(
        args.timeout_s,
        command="automa vehicles update perception",
        json_output=args.json,
        human_stream=sys.stdout,
    ):
        return 2
    result = update_vehicle_perception(
        vehicle_id=args.vehicle_id,
        preset=args.preset,
        plugins=args.plugins,
        timeout_s=args.timeout_s,
        restart=args.restart,
        dry_run=args.dry_run,
        json_output=args.json,
        verbose=args.verbose,
        output=sys.stdout,
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
        runtime_root=VEHICLES_RUNTIME_ROOT,
        timeout_s=args.timeout_s,
        dry_run=args.dry_run,
        json_output=args.json,
        verbose=args.verbose,
        output=sys.stdout,
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
    if handler is None:
        parser.print_help()
        return 2
    try:
        return int(handler(args))
    except DuplicatePluginIdError as exc:
        # Plugins declare their own IDs; the implementations owner must resolve a clash.
        print(f"error: {exc}", file=sys.stderr)
        return 2
