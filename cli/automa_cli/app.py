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
from .decision import (
    RUNTIME_ROOT as DECISION_RUNTIME_ROOT,
    apply_vehicle_decision,
    get_vehicle_decision_info,
    stream_vehicle_decision,
)
from .decision_inspector import run_decision_inspector
from .decision_live import run_live_decision_monitor
from .memory import (
    get_vehicle_memory_info,
    inspect_memory,
    reset_vehicle_memory,
    stream_vehicle_memory,
    update_vehicle_memory,
)
from .operations import run_vehicle_startup_check
from autonomy.plugins import DuplicatePluginIdError
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
from .perception import (
    get_vehicle_perception_info,
    update_vehicle_perception,
)
from .perception_runs import (
    inspect_perception,
)
from .workbench import run_workbench_replay
from .workbench_source import WORKBENCH_DEFAULT_MAX_FRAMES
from .simulators import DEFAULT_SCENARIO_ID, ensure_simulator, get_simulator_status
from .physical_viability import (
    run_memory_viability_measurement,
    run_physical_viability_measurement,
)
from .streaming import stream_vehicle_perception
from .vehicles import (
    DEFAULT_CHASE_READINESS_TIMEOUT_S,
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
        default=DEFAULT_CHASE_READINESS_TIMEOUT_S,
        help=(
            "One wall-clock readiness deadline per candidate in seconds "
            f"(default: {DEFAULT_CHASE_READINESS_TIMEOUT_S:g})."
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
        default=DEFAULT_CHASE_READINESS_TIMEOUT_S,
        help=(
            "One wall-clock deadline for all Chase readiness phases in seconds "
            f"(default: {DEFAULT_CHASE_READINESS_TIMEOUT_S:g})."
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
        help="Manage locally deployed automation workers and their current views.",
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
        help="Start a worker and verify one correlated camera/perception publication.",
        description=(
            "Start the automation worker. Success requires one camera frame, its "
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
        default=DEFAULT_CHASE_READINESS_TIMEOUT_S,
        help=(
            "One wall-clock Chase readiness deadline in seconds "
            f"(default: {DEFAULT_CHASE_READINESS_TIMEOUT_S:g})."
        ),
    )
    automation_run.add_argument(
        "--interval-s",
        type=float,
        default=0.25,
        help="Target delay between camera captures. Slow perception skips superseded frames.",
    )
    automation_run.add_argument(
        "--frames",
        type=int,
        default=0,
        help="Number of camera frames to capture. 0 means run until Ctrl-C.",
    )
    automation_run.add_argument(
        "--observe-only",
        action="store_true",
        help=(
            "Passively observe without changing scenario, playback, control source, "
            "input, or applying vehicle control."
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
        description=(
            "Stop the background worker. The local deployment remains staged and "
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
        help="Restart the automation worker and verify its first camera frame.",
        description="Restart the automation worker and verify its first camera frame.",
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
        default=DEFAULT_CHASE_READINESS_TIMEOUT_S,
        help=(
            "One wall-clock Chase readiness deadline in seconds "
            f"(default: {DEFAULT_CHASE_READINESS_TIMEOUT_S:g})."
        ),
    )
    automation_restart.add_argument(
        "--interval-s",
        type=float,
        default=0.25,
        help="Target delay between camera captures. Slow perception skips superseded frames.",
    )
    automation_restart.add_argument(
        "--frames",
        type=int,
        default=0,
        help="Number of camera frames to capture. 0 means unbounded.",
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
        description=(
            "Show the latest perception output, replacing the terminal view as it updates. "
            "Chase uses the local automation worker; PiCar polls onboard "
            "/autonomy/observation/latest and opens a local frame-matched view."
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
        help="Render once and exit.",
    )
    perception_stream.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not clear the terminal before each render.",
    )
    perception_stream.set_defaults(handler=_handle_vehicles_stream_perception)

    memory_stream = stream_commands.add_parser(
        "memory",
        help="Inspect live memory as a key→value ledger (terminal + local map page).",
        description=(
            "Inspect live memory as a key→value ledger. Terminal shows health and counts; "
            "on PiCar a local loopback page lists record_id keys and the selected value. "
            "Chase reads automation worker state. No history is written by default."
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
        help="Render once and exit.",
    )
    memory_stream.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not clear the terminal before each render.",
    )
    memory_stream.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable live memory probes (one JSON object per refresh).",
    )
    memory_stream.set_defaults(handler=_handle_vehicles_stream_memory)

    decision_stream = stream_commands.add_parser(
        "decision",
        help="Show the latest decision frame (generation-scoped latest replacement).",
        description=(
            "Read automation/latest_decision.json for the staged proposal, plan, and "
            "action steps. "
            "Accepts only generation-matched frames from a running live worker within the "
            "configured max age. No history is written. Use --once for a single accepted frame."
        ),
    )
    decision_stream.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Vehicle id from `automa vehicles active`.",
    )
    decision_stream.add_argument(
        "--refresh-s",
        type=float,
        default=0.5,
        help="Refresh cadence for the replacing terminal view.",
    )
    decision_stream.add_argument(
        "--once",
        action="store_true",
        help="Accept one frame and exit.",
    )
    decision_stream.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not clear the terminal before each render.",
    )
    decision_stream.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable decision stream frames (one JSON object per refresh).",
    )
    decision_stream.set_defaults(handler=_handle_vehicles_stream_decision)

    decision_control = vehicle_commands.add_parser(
        "decision",
        help=(
            "Inspect or operate vehicle decision "
            "(offline inspect/replay or read-only live monitor)."
        ),
    )
    decision_control.set_defaults(handler=_handle_vehicles_decision_help)
    decision_control_commands = decision_control.add_subparsers(dest="decision_command")
    decision_help = decision_control_commands.add_parser(
        "help",
        help="Show decision-level commands.",
    )
    decision_help.set_defaults(handler=_handle_vehicles_decision_help)
    decision_inspect = decision_control_commands.add_parser(
        "inspect", help="Open an offline decision inspector for a saved input sequence.",
        description="Compute left/right proposal scenarios from one saved frame. No live worker or capture is needed.",
    )
    decision_inspect.add_argument("--from-run", required=True, help="Sequence JSON file or directory containing sequence.json.")
    decision_inspect.add_argument("--frame", type=int, default=0, help="Zero-based frame position (default: 0).")
    decision_inspect.add_argument("--id", dest="vehicle_id", help="Use this vehicle's staged hold-action configuration; otherwise use packaged defaults.")
    decision_inspect.add_argument("--port", type=int, default=0, help="Local port (default: automatically selected).")
    decision_inspect.add_argument("--open", dest="open_browser", action="store_true", help="Open the inspector in your browser.")
    decision_inspect.add_argument("--json", action="store_true", help="Print both artifacts and exit without starting a server.")
    decision_inspect.set_defaults(handler=_handle_vehicles_decision_inspect)
    decision_apply = decision_control_commands.add_parser(
        "apply",
        help="Replay a recorded decision sequence through staged hold-action offline.",
        description=(
            "Feed a recorded observation+memory sequence through the vehicle's staged "
            "hold-action activation. Requires --id. Reports a deterministic digest "
            "(canonical_json_utf8 byte equality across two passes). Writes no files unless "
            "--record is passed for exact-frame HTML under lab/runs/decision-apply/."
        ),
    )
    decision_apply.add_argument(
        "--id",
        required=False,
        dest="vehicle_id",
        help="Vehicle id used to resolve the staged decision activation.",
    )
    decision_apply.add_argument(
        "--from-run",
        required=True,
        dest="from_run",
        help="Directory containing sequence.json (schema automa_decision_apply_sequence_v1).",
    )
    decision_apply.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable apply result (includes digest).",
    )
    decision_apply.add_argument(
        "--record",
        action="store_true",
        help=(
            "Opt-in: write a bounded exact-frame review directory with HTML, digest, and "
            "manifest. Disabled by default."
        ),
    )
    decision_apply.set_defaults(handler=_handle_vehicles_decision_apply)

    decision_live = decision_control_commands.add_parser(
        "live",
        help="Open the shared read-only decision view for a live PiCar.",
        description=(
            "Adapt the PiCar decision publication into the same RuntimeViewServer "
            "decision page used by Chase, with matched image-relative evidence and "
            "proposed versus authorized output. It sends no vehicle commands."
        ),
    )
    decision_live.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="PiCar vehicle id from `automa vehicles active`.",
    )
    decision_live.add_argument(
        "--port",
        type=int,
        default=0,
        help="Preferred local loopback port (0 chooses an available port).",
    )
    decision_live.add_argument(
        "--open",
        action="store_true",
        dest="open_browser",
        help="Open the shared decision view in the default browser.",
    )
    decision_live.add_argument(
        "--timeout-s",
        type=float,
        default=2.0,
        help="Per-request Pi timeout.",
    )
    decision_live.set_defaults(handler=_handle_vehicles_decision_live)

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
        help="Reset live memory to a new empty epoch on Chase or PiCar.",
        description=(
            "Reset the activated memory step on the live host. Chase uses the "
            "automation worker; PiCar POSTs /autonomy/memory/reset. Confirms an "
            "empty epoch via live probe. Does not move the vehicle or write history."
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
        help="HTTP/discovery timeout in seconds.",
    )
    memory_reset.add_argument(
        "--wait-s",
        type=float,
        default=5.0,
        help="Seconds to wait for Chase automation worker to acknowledge reset.",
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
            "images, or a recorded perception run) goes through perception and observation, "
            "then the selected memory plugins, frame by frame. Reports each plugin's health, "
            "record count and epoch after every frame, and the observation a plugin "
            "published in place of the frame's own. It reads the source only; record live "
            "frames with `perception inspect --record` and inspect that run."
        ),
    )
    memory_inspect.add_argument(
        "source",
        type=Path,
        help="Image file, recorded perception run, or directory of images.",
    )
    memory_inspect_selection = memory_inspect.add_mutually_exclusive_group()
    memory_inspect_selection.add_argument(
        "--preset",
        choices=available_memory_preset_ids(),
        default=None,
        help=f"Inspect one packaged memory preset (default: {DEFAULT_MEMORY_PRESET}).",
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
        help="Persist the source frames and the per-frame report.",
    )
    memory_inspect.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable report.",
    )
    memory_inspect.set_defaults(handler=_handle_vehicles_memory_inspect)
    memory_viability = memory_commands.add_parser(
        "viability",
        help="Health-check memory on a vehicle.",
        description=(
            "Health-check memory on a vehicle. A PiCar's live memory step is polled for a "
            "bounded interval (default 60s) to record update cadence, update duration, "
            "failures, health, and epoch stability. "
            "The simulator has no probe yet and passes automatically."
        ),
    )
    memory_viability.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Physical vehicle id from `automa vehicles active` (picar only).",
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
        help="Print the machine-readable viability report.",
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
            "Replay an ordered image directory through perception, memory, and "
            "decisions."
        ),
        description=(
            "Run the bounded decision playback workbench against an ordered "
            "local image directory. The server owns source ordering, perception, "
            "observation, bounded memory, decision state, and any selected "
            "manifest-backed plugins. "
            "Without --serve, one replay runs "
            "to a terminal state; --serve keeps the loopback page available for "
            "pause, step, reset, and another run."
        ),
    )
    workbench_replay.add_argument(
        "source_dir",
        help="Directory containing supported images and optional ordered manifest.",
    )
    workbench_replay.add_argument(
        "--plugin",
        "--active-plugin",
        "--active-plugin-id",
        dest="active_plugin_ids",
        action="append",
        default=None,
        help=(
            "Select one packaged perception plugin id; repeat to select more, in "
            "order. Omit this option for the default lightweight selection."
        ),
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
        help="Show the staged perception schema and current published view URL.",
        description="Show the staged perception schema and current published view URL.",
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
        help="Show the locally staged decision steps (proposal, plan, action).",
        description="Show every step's staged plugins and the proposal, plan, and action contract.",
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

    memory_info = info_commands.add_parser(
        "memory",
        help="Show the locally staged memory implementation and bounds.",
        description="Show the locally staged memory implementation and bounds.",
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
            "when several are active, the simulator is selected by default."
        ),
    )
    perception_inspect.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=None,
        help="Image file, recorded perception run, or directory of images. Omit to read a live vehicle.",
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
        help="Persist source frames, plugin artifacts, and the comparison report.",
    )
    perception_inspect.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable experiment report.",
    )
    perception_inspect.set_defaults(handler=_handle_vehicles_perception_inspect)

    perception_viability = perception_commands.add_parser(
        "viability",
        help="Health-check perception on a vehicle.",
        description=(
            "Health-check perception on a vehicle. A PiCar is polled for a bounded "
            "interval (default 60s) to record cadence, result age, processing duration, "
            "and skip policy, plus host RSS/CPU when the vehicle supplies an ssh_target. "
            "The simulator has no probe yet and passes automatically."
        ),
    )
    perception_viability.add_argument(
        "--id",
        required=True,
        dest="vehicle_id",
        help="Physical vehicle id from `automa vehicles active` (picar only).",
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
        help="Print the machine-readable viability report.",
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
            "runtime service on a physical PiCar."
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
        help="Deploy a versioned autonomy controller release to a physical PiCar.",
        description=(
            "Deploy a versioned autonomy controller release to a physical PiCar. "
            "With --restart, verifies every deployed step runs its staged plugins. "
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
        help="Restart the supervised Donkey runtime after activating the release.",
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
            "Idempotently stage a perception preset (or plugin list) and safe idle decision in a "
            "local vehicle bundle. For Chase, the result reports whether the same "
            "passive capture gate is ready for observation-only automation."
        ),
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
        default=DEFAULT_CHASE_READINESS_TIMEOUT_S,
        help=(
            "One wall-clock Chase readiness deadline in seconds "
            f"(default: {DEFAULT_CHASE_READINESS_TIMEOUT_S:g})."
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
        help="Packaged perception plugin to select instead of a preset; repeat to select several in order.",
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
            help="Vehicle id from `automa vehicles active`.",
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
        help="Vehicle id from `automa vehicles active`.",
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
        help="Packaged memory plugin to select instead of a preset; repeat to select several in order.",
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
                "- automation   manage locally deployed automation workers",
                "- operation    run bounded vehicle checks and setup tasks",
                "- info         inspect locally staged controller configuration",
                "- memory       operate memory (inspect, viability, reset)",
                "- decision     offline decision apply/replay (stage via update decision)",
                (
                    "- workbench    replay images through perception, memory, and "
                    "decisions"
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
                "- run       start a worker and verify camera + perception + current view",
                "- status    show locally deployed worker and view state",
                "- restart   stop and start the automation worker",
                "- stop      stop the worker; keep its deployment staged",
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
                "- core         deploy physical DonkeyCar harness code",
                "- autonomy     deploy physical autonomy controller release",
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
                "- decision    show the staged steps and decision contract",
                "- memory      show locally staged memory plugins",
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
                "- viability  health-check perception (PiCar cadence/freshness; simulator stub)",
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


def _handle_vehicles_stream_decision(args: argparse.Namespace) -> int:
    result = stream_vehicle_decision(
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


def _handle_vehicles_decision_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa vehicles decision commands",
                "",
                "- inspect offline browser inspector for saved decision input",
                "- apply   offline replay of a recorded sequence; digest; optional --record",
                "- live    read-only local browser monitor for a live PiCar publication",
                "- help    show this summary",
                "",
                "Stage proposals (held idle):  ./cli/automa vehicles update proposal --id <vehicle>",
                "Apply them in live modes:     ./cli/automa vehicles update action --id <vehicle> --plugin mode",
                "Inspect contract with: ./cli/automa vehicles info decision --id <vehicle>",
                "Open saved input:      ./cli/automa vehicles decision inspect --from-run <sequence.json> --open",
                "Stream latest frame:   ./cli/automa vehicles stream decision --id <vehicle>",
                "",
                "Detailed help:",
                "- ./cli/automa vehicles decision <command> --help",
            ]
        )
    )
    return 0


def _handle_vehicles_decision_inspect(args: argparse.Namespace) -> int:
    result = run_decision_inspector(
        args.from_run, frame_index=args.frame, vehicle_id=args.vehicle_id,
        port=args.port, open_browser=args.open_browser, json_output=args.json, output=sys.stdout,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_decision_apply(args: argparse.Namespace) -> int:
    result = apply_vehicle_decision(
        vehicle_id=args.vehicle_id,
        from_run=args.from_run,
        json_output=args.json,
        record=args.record,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_decision_live(args: argparse.Namespace) -> int:
    result = run_live_decision_monitor(
        vehicle_id=args.vehicle_id,
        port=args.port,
        open_browser=args.open_browser,
        timeout_s=args.timeout_s,
        output=sys.stdout,
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
                "- viability  health-check memory (PiCar update cadence/failures/epoch; simulator stub)",
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
        wait_s=args.wait_s,
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
                    "memory, and decisions"
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
        active_plugin_ids=args.active_plugin_ids,
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


def _handle_vehicles_info_decision(args: argparse.Namespace) -> int:
    result = get_vehicle_decision_info(
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


def _handle_vehicles_update_memory(args: argparse.Namespace) -> int:
    result = update_vehicle_memory(
        vehicle_id=args.vehicle_id,
        preset=args.preset,
        plugins=args.plugins,
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
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_vehicles_perception_viability(args: argparse.Namespace) -> int:
    result = run_physical_viability_measurement(
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
    exit_code, message = update_vehicle_step(
        vehicle_id=args.vehicle_id,
        step=args.step,
        plugins=args.plugins,
        runtime_root=DECISION_RUNTIME_ROOT,
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
