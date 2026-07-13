from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .automation import (
    get_vehicle_automation_status,
    restart_vehicle_automation,
    run_vehicle_automation,
    start_vehicle_automation_background,
    stop_vehicle_automation,
)
from .deploy import update_vehicle_autonomy, update_vehicle_core
from .decision import (
    available_decision_engine_ids,
    get_vehicle_decision_info,
    update_vehicle_decision,
)
from .lab_plugins import list_perception_candidates, setup_perception_candidate
from .operations import run_vehicle_startup_check
from .perception import (
    DEFAULT_PERCEPTION_ALGORITHM,
    available_perception_algorithm_ids,
    get_vehicle_perception_info,
    set_vehicle_perception_plugin,
    update_vehicle_perception,
)
from .perception_runs import (
    compare_perception_candidates,
    replay_perception_experiment,
    run_perception_experiment,
)
from .simulators import DEFAULT_SCENARIO_ID, ensure_simulator, get_simulator_status
from .streaming import stream_vehicle_perception
from .vehicles import discover_active_vehicles, format_active_vehicles_snapshot


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

    active = vehicle_commands.add_parser(
        "active",
        help="Show active vehicles and readiness diagnostics from configured endpoints.",
        description="Show active vehicles and readiness diagnostics from configured endpoints.",
    )
    active.add_argument(
        "--timeout-s",
        type=float,
        default=1.0,
        help="Per-candidate probe timeout in seconds.",
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
        help="Hide inactive candidates and readiness diagnostics.",
    )
    active.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable discovery payload.",
    )
    active.set_defaults(handler=_handle_vehicles_active)

    automation = vehicle_commands.add_parser("automation", help="Manage vehicle automation runtimes.")
    automation_commands = automation.add_subparsers(dest="automation_command", required=True)
    automation_help = automation_commands.add_parser(
        "help",
        help="Show automation-level commands.",
    )
    automation_help.set_defaults(handler=_handle_vehicles_automation_help)
    automation_run = automation_commands.add_parser(
        "run",
        help="Run the active automation loop for a vehicle.",
        description="Run the active automation loop for a vehicle.",
    )
    automation_run.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
    )
    automation_run.add_argument(
        "--timeout-s",
        type=float,
        default=3.0,
        help="Vehicle/controller timeout in seconds.",
    )
    automation_run.add_argument(
        "--interval-s",
        type=float,
        default=1.0,
        help="Delay between perception frames. Use 0 for a bounded fast run.",
    )
    automation_run.add_argument(
        "--frames",
        type=int,
        default=0,
        help="Number of frames to process. 0 means run until Ctrl-C.",
    )
    automation_run.add_argument(
        "--observe-only",
        action="store_true",
        help="Run perception without taking over simulator WS control.",
    )
    automation_run.add_argument(
        "--record",
        action="store_true",
        help="Save per-frame images and perception artifacts under a timestamped run directory.",
    )
    automation_run.add_argument(
        "--verbose",
        action="store_true",
        help="Print every-frame worker detail when output is connected.",
    )
    automation_run.add_argument(
        "--log",
        action="store_true",
        dest="log_to_disk",
        help="Persist background worker output to automation.log.",
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
        description="Stop the background automation loop for a vehicle.",
    )
    automation_stop.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
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
        help="Show locally deployed automation runtimes and worker status.",
        description="Show locally deployed automation runtimes and worker status.",
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
        help="Stop and start the background automation loop for a vehicle.",
        description="Stop and start the background automation loop for a vehicle.",
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
        default=3.0,
        help="Vehicle/controller timeout in seconds.",
    )
    automation_restart.add_argument(
        "--interval-s",
        type=float,
        default=1.0,
        help="Delay between perception frames.",
    )
    automation_restart.add_argument(
        "--frames",
        type=int,
        default=0,
        help="Number of frames to process. 0 means unbounded.",
    )
    automation_restart.add_argument(
        "--observe-only",
        action="store_true",
        help="Run perception without taking over simulator WS control.",
    )
    automation_restart.add_argument(
        "--record",
        action="store_true",
        help="Save per-frame images and perception artifacts under a timestamped run directory.",
    )
    automation_restart.add_argument(
        "--verbose",
        action="store_true",
        help="Print every-frame worker detail when output is connected.",
    )
    automation_restart.add_argument(
        "--log",
        action="store_true",
        dest="log_to_disk",
        help="Persist background worker output to automation.log.",
    )
    automation_restart.add_argument(
        "--wait-s",
        type=float,
        default=3.0,
        help="Seconds to wait for graceful stop before forcing termination.",
    )
    automation_restart.set_defaults(handler=_handle_vehicles_automation_restart)

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
        help="Capture every comparison without sending movement pulses.",
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
        description="Show the latest perception output, replacing the terminal view as it updates.",
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
        help="Render one snapshot and exit.",
    )
    perception_stream.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not clear the terminal before each render.",
    )
    perception_stream.set_defaults(handler=_handle_vehicles_stream_perception)

    info = vehicle_commands.add_parser("info", help="Inspect locally staged controller configuration.")
    info_commands = info.add_subparsers(dest="info_command", required=True)
    info_help = info_commands.add_parser(
        "help",
        help="Show info-level commands.",
    )
    info_help.set_defaults(handler=_handle_vehicles_info_help)
    perception_info = info_commands.add_parser(
        "perception",
        help="Show the locally staged perception algorithm and input translation schema.",
        description="Show the locally staged perception algorithm and input translation schema.",
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

    decision_info = info_commands.add_parser(
        "decision",
        help="Show the locally staged decision engine and stage schema.",
        description="Show the locally staged decision engine and stage schema.",
    )
    decision_info.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
    )
    decision_info.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable decision info payload.",
    )
    decision_info.set_defaults(handler=_handle_vehicles_info_decision)

    perception_control = vehicle_commands.add_parser(
        "perception",
        help="Run perception experiments and manage perception plugins.",
    )
    perception_control.set_defaults(handler=_handle_vehicles_perception_help)
    perception_commands = perception_control.add_subparsers(dest="perception_command")
    perception_help = perception_commands.add_parser(
        "help",
        help="Show perception-level commands.",
    )
    perception_help.set_defaults(handler=_handle_vehicles_perception_help)

    perception_run = perception_commands.add_parser(
        "run",
        help="Observe a short perception sequence from an active vehicle.",
        description=(
            "Observe five frames from an active vehicle without taking movement control. "
            "When multiple vehicles are active, the simulator is selected by default."
        ),
    )
    perception_run.add_argument(
        "--candidate",
        default=None,
        help="Run an isolated lab candidate from `vehicles perception candidates`.",
    )
    perception_run.add_argument(
        "--algorithm",
        choices=available_perception_algorithm_ids(),
        default=None,
        help="Run one packaged perception algorithm instead of the active selection.",
    )
    perception_run.add_argument(
        "--id",
        dest="vehicle_id",
        default=None,
        help="Specific active vehicle id. Omit to select a safe observation target automatically.",
    )
    perception_run.add_argument(
        "--frames",
        type=int,
        default=5,
        help="Frames to observe (default: 5).",
    )
    perception_run.add_argument(
        "--interval-s",
        type=float,
        default=0.25,
        help="Delay between captures in seconds (default: 0.25).",
    )
    perception_run.add_argument(
        "--timeout-s",
        type=float,
        default=3.0,
        help="Vehicle discovery and capture timeout in seconds (default: 3).",
    )
    perception_run.add_argument(
        "--record",
        action="store_true",
        help="Persist source frames, plugin artifacts, and the comparison report.",
    )
    perception_run.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable experiment report.",
    )
    perception_run.set_defaults(handler=_handle_vehicles_perception_run)

    perception_replay = perception_commands.add_parser(
        "replay",
        help="Run perception against a recorded image directory.",
        description=(
            "Run the recorded mapper configuration, or the default lightweight "
            "observer, against an image directory."
        ),
    )
    perception_replay.add_argument(
        "source_dir",
        type=Path,
        help="Recorded perception run or directory containing image frames.",
    )
    perception_replay.add_argument(
        "--candidate",
        default=None,
        help="Replay with an isolated lab candidate instead of the recorded/default mapper.",
    )
    perception_replay.add_argument(
        "--algorithm",
        choices=available_perception_algorithm_ids(),
        default=None,
        help="Replay with one packaged perception algorithm instead of the recorded/default mapper.",
    )
    perception_replay.add_argument(
        "--record",
        action="store_true",
        help="Persist replay outputs and the comparison report.",
    )
    perception_replay.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable experiment report.",
    )
    perception_replay.set_defaults(handler=_handle_vehicles_perception_replay)

    perception_compare = perception_commands.add_parser(
        "compare",
        help="Compare all ready lab candidates on one image sequence.",
        description=(
            "Replay every ready lab candidate against the same images and compare representation "
            "health, continuity, latency, and memory."
        ),
    )
    perception_compare.add_argument(
        "source_dir",
        type=Path,
        help="Directory containing the image sequence to compare.",
    )
    perception_compare.add_argument(
        "--record",
        action="store_true",
        help="Persist each candidate's overlays, structured output, and review page.",
    )
    perception_compare.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable comparison report.",
    )
    perception_compare.set_defaults(handler=_handle_vehicles_perception_compare)

    perception_candidates = perception_commands.add_parser(
        "candidates",
        help="Show experimental perception candidates and readiness.",
        description="Show locally available lab candidates, dependency readiness, and setup guidance.",
    )
    perception_candidates.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable candidate inventory.",
    )
    perception_candidates.set_defaults(handler=_handle_vehicles_perception_candidates)

    perception_setup = perception_commands.add_parser(
        "setup",
        help="Prepare one isolated perception candidate.",
        description=(
            "Create the candidate-local Python environment, install declared dependencies, "
            "and download its declared model. The candidate id may be omitted when only one exists."
        ),
    )
    perception_setup.add_argument(
        "candidate_id",
        nargs="?",
        default=None,
        help="Candidate id. Omit when the candidate inventory contains exactly one entry.",
    )
    perception_setup.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable setup result.",
    )
    perception_setup.set_defaults(handler=_handle_vehicles_perception_setup)

    perception_enable = perception_commands.add_parser(
        "enable",
        help="Enable one plugin in the locally staged perception activation.",
        description="Enable one plugin in the locally staged perception activation.",
    )
    perception_enable.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
    )
    perception_enable.add_argument(
        "plugin_id",
        help="Plugin id from `automa vehicles info perception --id <vehicle_id>`.",
    )
    perception_enable.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable plugin update payload.",
    )
    perception_enable.set_defaults(handler=_handle_vehicles_perception_enable)

    perception_disable = perception_commands.add_parser(
        "disable",
        help="Disable one plugin in the locally staged perception activation.",
        description="Disable one plugin in the locally staged perception activation.",
    )
    perception_disable.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
    )
    perception_disable.add_argument(
        "plugin_id",
        help="Enabled plugin id to remove from the locally staged perception chain.",
    )
    perception_disable.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable plugin update payload.",
    )
    perception_disable.set_defaults(handler=_handle_vehicles_perception_disable)

    update = vehicle_commands.add_parser(
        "update",
        help="Stage controller selections or deploy code to a vehicle.",
    )
    update_commands = update.add_subparsers(dest="update_command", required=True)
    update_help = update_commands.add_parser(
        "help",
        help="Show update-level commands.",
    )
    update_help.set_defaults(handler=_handle_vehicles_update_help)
    core = update_commands.add_parser(
        "core",
        help="Sync deploy/targets/donkeycar core harness files to a physical PiCar.",
        description="Sync the DonkeyCar core harness files to a physical PiCar.",
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
        help="Restart the Donkey drive server after syncing core files.",
    )
    core.add_argument(
        "--drive-args",
        default=None,
        help="Arguments passed to `manage.py drive` when --restart is used, for example --js.",
    )
    core.add_argument(
        "--verbose",
        action="store_true",
        help="Print each sync command before it runs.",
    )
    core.set_defaults(handler=_handle_vehicles_update_core)

    autonomy = update_commands.add_parser(
        "autonomy",
        help="Deploy a versioned autonomy controller release to a physical PiCar.",
        description="Deploy a versioned autonomy controller release to a physical PiCar.",
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
        help="Restart the Donkey drive server after activating the release.",
    )
    autonomy.add_argument(
        "--drive-args",
        default=None,
        help="Arguments passed to `manage.py drive` when --restart is used, for example --js.",
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
        help="Stage a perception algorithm in a vehicle's local controller bundle.",
        description="Stage a perception algorithm in a vehicle's local controller bundle.",
    )
    perception.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
    )
    perception.add_argument(
        "--timeout-s",
        type=float,
        default=1.0,
        help="Vehicle discovery/controller timeout in seconds.",
    )
    perception.add_argument(
        "--algorithm",
        default=DEFAULT_PERCEPTION_ALGORITHM,
        choices=available_perception_algorithm_ids(),
        help="Perception algorithm to activate.",
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
        help="Re-prepare the simulator WS controller and capture a sample perception.",
    )
    perception.add_argument(
        "--verbose",
        action="store_true",
        help="Print step-by-step activation, controller preparation, and sample perception details.",
    )
    perception.set_defaults(handler=_handle_vehicles_update_perception)

    decision = update_commands.add_parser(
        "decision",
        help="Stage a decision engine in the local controller bundle.",
        description="Stage a decision engine in the local controller bundle.",
    )
    decision.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
    )
    decision.add_argument(
        "--engine",
        default="idle",
        choices=available_decision_engine_ids(),
        help="Decision engine to activate.",
    )
    decision.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the activation manifest without writing it.",
    )
    decision.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable decision update payload.",
    )
    decision.add_argument(
        "--verbose",
        action="store_true",
        help="Print controller release packaging details.",
    )
    decision.set_defaults(handler=_handle_vehicles_update_decision)

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
        help="Use an online simulator or launch the default SimEval deployment.",
        description="Use an online simulator or launch the default SimEval deployment.",
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


def _handle_top_level_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "Automa is the control desk for the vehicles and simulators in this workspace.",
                "It helps you find what is reachable, stage controller choices locally, and deploy code to a physical vehicle.",
                "It can start and stop automation runs without making you remember where the runtime files live.",
                "It also gives you one place to inspect the latest perception output and the controller behavior staged for each vehicle.",
                "Use it when you want to move from editing local code to running that code against a real or simulated vehicle.",
                "The sections below only show the next command level; each command has its own help for details.",
                "",
                "- help       Show this command summary.",
                "- vehicles   Discover vehicles, update vehicle bundles, and inspect active runtime state.",
                "- simulators Prepare simulator environments for local automation testing.",
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
                "- ensure   use an online simulator or launch the default one",
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
                "- active       discover reachable vehicle/controller endpoints",
                "- update       stage controller selections or deploy vehicle code",
                "- automation   manage locally deployed automation workers",
                "- operation    run bounded vehicle checks and setup tasks",
                "- info         inspect locally staged controller configuration",
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
                "- run       start the automation worker",
                "- status    show locally deployed automation state",
                "- restart   stop and start the automation worker",
                "- stop      stop the automation worker",
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
                "- core        deploy physical DonkeyCar harness code",
                "- autonomy    deploy physical autonomy controller release",
                "- perception  stage local vehicle perception code",
                "- decision    stage local decision configuration",
                "- help        show this summary",
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
                "- perception  show locally staged perception schema",
                "- decision    show locally staged decision engine schema",
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
                "- run      observe a short sequence from an active vehicle",
                "- replay   process an existing image sequence",
                "- compare  compare all ready candidates on one sequence",
                "- candidates  show experimental candidates and readiness",
                "- setup    prepare one isolated experimental candidate",
                "- enable   enable one locally staged perception plugin",
                "- disable  disable one locally staged perception plugin",
                "- help     show this summary",
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
                "- help        show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles stream <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_active(args: argparse.Namespace) -> int:
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
        print(format_active_vehicles_snapshot(payload, include_inactive=include_inactive))
    return 0


def _handle_vehicles_automation_run(args: argparse.Namespace) -> int:
    if args.foreground:
        result = run_vehicle_automation(
            vehicle_id=args.vehicle_id,
            timeout_s=args.timeout_s,
            interval_s=args.interval_s,
            frames=args.frames,
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
            frames=args.frames,
            take_control=not args.observe_only,
            record=args.record,
            verbose=args.verbose,
            log_to_disk=args.log_to_disk,
        )
    if result.message:
        print(result.message)
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


def _handle_vehicles_automation_restart(args: argparse.Namespace) -> int:
    result = restart_vehicle_automation(
        vehicle_id=args.vehicle_id,
        timeout_s=args.timeout_s,
        interval_s=args.interval_s,
        frames=args.frames,
        take_control=not args.observe_only,
        record=args.record,
        verbose=args.verbose,
        log_to_disk=args.log_to_disk,
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
        verbose=args.verbose,
        output=sys.stdout,
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
        output=sys.stderr if args.json else sys.stdout,
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


def _handle_vehicles_info_decision(args: argparse.Namespace) -> int:
    result = get_vehicle_decision_info(
        vehicle_id=args.vehicle_id,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_perception_enable(args: argparse.Namespace) -> int:
    result = set_vehicle_perception_plugin(
        vehicle_id=args.vehicle_id,
        plugin_id=args.plugin_id,
        enabled=True,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_perception_run(args: argparse.Namespace) -> int:
    result = run_perception_experiment(
        vehicle_id=args.vehicle_id,
        frames=args.frames,
        interval_s=args.interval_s,
        timeout_s=args.timeout_s,
        record=args.record,
        json_output=args.json,
        candidate_id=args.candidate,
        algorithm=args.algorithm,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_perception_replay(args: argparse.Namespace) -> int:
    result = replay_perception_experiment(
        args.source_dir,
        record=args.record,
        json_output=args.json,
        candidate_id=args.candidate,
        algorithm=args.algorithm,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_perception_compare(args: argparse.Namespace) -> int:
    result = compare_perception_candidates(
        args.source_dir,
        record=args.record,
        json_output=args.json,
        output=None if args.json else sys.stdout,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_perception_candidates(args: argparse.Namespace) -> int:
    result = list_perception_candidates(json_output=args.json)
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_perception_setup(args: argparse.Namespace) -> int:
    result = setup_perception_candidate(
        args.candidate_id,
        json_output=args.json,
        output=None if args.json else sys.stdout,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_perception_disable(args: argparse.Namespace) -> int:
    result = set_vehicle_perception_plugin(
        vehicle_id=args.vehicle_id,
        plugin_id=args.plugin_id,
        enabled=False,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_update_perception(args: argparse.Namespace) -> int:
    result = update_vehicle_perception(
        vehicle_id=args.vehicle_id,
        algorithm=args.algorithm,
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


def _handle_vehicles_update_decision(args: argparse.Namespace) -> int:
    result = update_vehicle_decision(
        vehicle_id=args.vehicle_id,
        engine_id=args.engine,
        dry_run=args.dry_run,
        json_output=args.json,
        verbose=args.verbose,
        output=sys.stdout,
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
    return int(handler(args))
