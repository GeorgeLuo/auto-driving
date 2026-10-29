"""The ``automa hosts`` parser and its handlers."""

from __future__ import annotations

import argparse
import sys

from autonomy.decision_cycle.activation import DECISION_STEPS
from autonomy.decision_cycle.steps import STEP_RUNNERS

from ..shared.selection_args import add_selection_args
from ..shared.timeout_input import validate_timeout_input
from ..vehicles.probes import DEFAULT_READINESS_TIMEOUT_S
from .automation import (
    get_hosts_status,
    record_host_automation_terminal_result,
    restart_host_automation,
    run_host_automation,
    start_host_automation_background,
    stop_host_automation,
)
from .deploy import create_host, update_host, delete_host
from .decision_steps import get_host_step_info
from .memory import get_host_memory_info, reset_host_memory
from .operations import run_host_startup_check
from .step_activations import HOST_PROVIDERS
from .perception import get_host_perception_info
from .workbench import run_workbench_replay
from .workbench_plugins import WORKBENCH_SELECTABLE_STEPS
from .workbench_source import WORKBENCH_DEFAULT_MAX_FRAMES
from .streaming import serve_host_viewer


def register_hosts(subcommands: argparse._SubParsersAction) -> None:
    hosts = subcommands.add_parser("hosts", help="Create, configure, and operate vehicle controller hosts.")
    hosts.set_defaults(handler=_handle_hosts_help)
    host_commands = hosts.add_subparsers(dest="host_command")
    host_help = host_commands.add_parser("help", help="Show host-level commands.")
    host_help.set_defaults(handler=_handle_hosts_help)

    _register_hosts_viewer(host_commands)
    _register_hosts_lifecycle(host_commands)
    _register_hosts_operations(host_commands)
    _register_hosts_memory(host_commands)
    _register_hosts_workbench(host_commands)
    _register_hosts_info(host_commands)


def _register_hosts_viewer(host_commands: argparse._SubParsersAction) -> None:
    viewer = host_commands.add_parser(
        "viewer",
        help="Open the runtime viewer on a host.",
        description=(
            "Serve the runtime viewer fed from the host named by --name, or at --host-url "
            "(the URL hosts status shows under the host), until interrupted. Exits 2 when "
            "no such host exists, it is not running, or it does not answer."
        ),
    )
    viewer_target = viewer.add_mutually_exclusive_group(required=True)
    viewer_target.add_argument("--name", dest="host_name", help=_HOST_NAME_HELP)
    viewer_target.add_argument("--host-url", help="The host's URL from hosts status.")
    viewer.set_defaults(handler=_handle_hosts_viewer)


def _register_hosts_lifecycle(host_commands: argparse._SubParsersAction) -> None:
    host_create = host_commands.add_parser("create", help="Create and start a host.")
    host_update = host_commands.add_parser("update", help="Package and deploy a host's release.")
    host_delete = host_commands.add_parser("delete", help="Stop the host and its run, and remove its local files.")
    for command in (host_create, host_update, host_delete):
        command.add_argument("--name", dest="host_name", required=True, help="Host name.")
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
    host_create.set_defaults(handler=_handle_hosts_create)
    host_update.set_defaults(handler=_handle_hosts_update)
    host_delete.set_defaults(handler=_handle_hosts_delete)

    host_run = host_commands.add_parser(
        "run",
        help="Start a run on a host and verify one correlated camera/perception publication.",
        description=(
            "Start autonomous movement using the staged plugins. Success requires one camera frame, its "
            "completed perception result, and a healthy current-generation loopback view. "
            "--host-url runs an existing host instead of starting one; that host reports its host name."
        ),
    )
    run_target = host_run.add_mutually_exclusive_group(required=True)
    run_target.add_argument(
        "--name",
        dest="host_name",
        help=_HOST_NAME_HELP,
    )
    run_target.add_argument(
        "--host-url",
        dest="host_url",
        help=(
            "Base URL of a runtime host that is already running. "
            "Run that host instead of starting one. The host reports its host name."
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
            "Stop with hosts stop."
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
        help="Save per-frame images and cycle records under a timestamped run directory.",
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
    host_run.set_defaults(handler=_handle_hosts_run)

    host_stop = host_commands.add_parser(
        "stop",
        help="Stop the host's run; the host stays attached until hosts delete.",
        description=(
            "Stop autonomous movement and its publication monitor. The host stays attached, with its "
            "deployment staged, until hosts delete; its former view is no longer current-generation "
            "available."
        ),
    )
    host_stop.add_argument(
        "--name",
        required=True,
        dest="host_name",
        help=_HOST_NAME_HELP,
    )
    host_stop.add_argument(
        "--wait-s",
        type=float,
        default=3.0,
        help="Seconds to wait for graceful stop before forcing termination.",
    )
    host_stop.set_defaults(handler=_handle_hosts_stop)

    host_status = host_commands.add_parser(
        "status",
        help="Show each host's state and attachment.",
        description=(
            "Show each host's state and whether its simulator lists it; --long adds "
            "its deployment, run and view. Exits 1 when the --name host is missing, "
            "stopped, unanswering, or errored."
        ),
    )
    host_status.add_argument(
        "--name",
        dest="host_name",
        default=None,
        help="Host name. Omit to list every host.",
    )
    host_status.add_argument(
        "--long",
        action="store_true",
        help="Add each host's deployment, run and view under its block.",
    )
    host_status.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_READINESS_TIMEOUT_S,
        help=(
            f"One wall-clock deadline for host and simulator reads (default: {DEFAULT_READINESS_TIMEOUT_S:g}s)."
        ),
    )
    host_status.add_argument(
        "--json",
        action="store_true",
        help="Print automa_hosts_status JSON.",
    )
    host_status.set_defaults(handler=_handle_hosts_status)

    host_restart = host_commands.add_parser(
        "restart",
        help="Restart the host's run and verify its correlated runtime view.",
        description="Restart the host's run and verify its correlated runtime view.",
    )
    host_restart.add_argument(
        "--name",
        required=True,
        dest="host_name",
        help=_HOST_NAME_HELP,
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
            "Stop with hosts stop."
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
        help="Save per-frame images and cycle records under a timestamped run directory.",
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
    host_restart.set_defaults(handler=_handle_hosts_restart)
    host_serve = host_commands.add_parser(
        "serve",
        help="Serve a Chase car's runtime host in the foreground (hosts create and hosts run start one).",
        description=(
            "Serve the runtime routes for a Chase car from its latest controller release, "
            "and relaunch on a restart command. `hosts create` and `hosts run` start this in the background; "
            "its log is runtime/hosts/<host-name>/bundle/runtime/automation/host.log."
        ),
    )
    host_serve.add_argument("--name", required=True, dest="host_name", help="Chase host name.")
    host_serve.set_defaults(handler=_handle_hosts_serve)


def _register_hosts_operations(host_commands: argparse._SubParsersAction) -> None:
    operation = host_commands.add_parser("operation", help="Run bounded vehicle checks and setup tasks.")
    operation.set_defaults(handler=_handle_hosts_operation_help)
    operation_commands = operation.add_subparsers(dest="operation_command")
    operation_help = operation_commands.add_parser(
        "help",
        help="Show operation-level commands.",
    )
    operation_help.set_defaults(handler=_handle_hosts_operation_help)
    startup_check = operation_commands.add_parser(
        "startup-check",
        help="Send bounded action pulses and verify camera changes around each command.",
        description="Send bounded action pulses and verify camera changes around each command.",
    )
    startup_check.add_argument(
        "--name",
        required=True,
        dest="host_name",
        help="Host name from `automa hosts status`.",
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
    startup_check.set_defaults(handler=_handle_hosts_operation_startup_check)


def _register_hosts_memory(host_commands: argparse._SubParsersAction) -> None:
    memory_control = host_commands.add_parser(
        "memory",
        help="Reset host memory.",
    )
    memory_control.set_defaults(handler=_handle_hosts_memory_help)
    memory_commands = memory_control.add_subparsers(dest="memory_command")
    memory_help = memory_commands.add_parser(
        "help",
        help="Show memory-level commands.",
    )
    memory_help.set_defaults(handler=_handle_hosts_memory_help)
    memory_reset = memory_commands.add_parser(
        "reset",
        help="Reset live memory to a new empty epoch on the host.",
        description=(
            "POST /host/memory/reset to the host. Confirms that every "
            "applied plugin started a new epoch or holds no records when the host answers. "
            "Does not move the vehicle."
        ),
    )
    memory_reset.add_argument(
        "--name",
        required=True,
        dest="host_name",
        help="Host name from `automa hosts status`.",
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
    memory_reset.set_defaults(handler=_handle_hosts_memory_reset)


def _register_hosts_workbench(host_commands: argparse._SubParsersAction) -> None:
    workbench = host_commands.add_parser(
        "workbench",
        help="Replay images through the decision playback workbench.",
    )
    workbench.set_defaults(handler=_handle_hosts_workbench_help)
    workbench_commands = workbench.add_subparsers(
        dest="workbench_command",
        required=True,
    )
    workbench_help = workbench_commands.add_parser(
        "help",
        help="Show workbench-level commands.",
    )
    workbench_help.set_defaults(handler=_handle_hosts_workbench_help)
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
            "to a terminal state; --serve keeps the viewer's workbench page available for "
            "pause, step, reset, and another run. Changing a step's plugins in a "
            "running or paused served replay rebuilds every step's pipeline "
            "and replays from the first frame to the displayed frame before returning."
        ),
    )
    workbench_replay.add_argument(
        "source_dir",
        help="Directory containing supported images and optional ordered manifest.",
    )
    for step_name in WORKBENCH_SELECTABLE_STEPS:
        add_selection_args(workbench_replay, step_name, verb="replay", flag_prefix=f"{step_name}-")
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
        help="Keep serving the viewer, with this replay on its workbench page.",
    )
    workbench_replay.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the viewer's workbench page in a browser and imply --serve.",
    )
    workbench_replay.set_defaults(handler=_handle_hosts_workbench_replay)


def _register_hosts_info(host_commands: argparse._SubParsersAction) -> None:
    info = host_commands.add_parser("info", help="Inspect locally staged controller configuration.")
    info_commands = info.add_subparsers(dest="info_command", required=True)
    info_help = info_commands.add_parser(
        "help",
        help="Show info-level commands.",
    )
    info_help.set_defaults(handler=_handle_hosts_info_help)
    perception_info = info_commands.add_parser(
        "perception",
        help="Show the staged perception preset and plugins, the runner schema and the published view URL.",
        description="Show the staged perception preset and plugins, the runner schema and the published view URL.",
    )
    _add_info_args(
        perception_info,
        json_help="Print the full machine-readable perception info payload.",
    )
    perception_info.set_defaults(handler=_handle_hosts_info_perception)

    memory_info = info_commands.add_parser(
        "memory",
        help="Show the staged memory preset and plugins, the runner schema and the live memory step state.",
        description="Show the staged memory preset and plugins, the runner schema and the live memory step state.",
    )
    _add_info_args(
        memory_info,
        json_help="Print the full machine-readable memory info payload.",
    )
    memory_info.set_defaults(handler=_handle_hosts_info_memory)

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
        _add_info_args(
            step_info,
            json_help=f"Print the full machine-readable {step_name} info payload.",
        )
        step_info.set_defaults(handler=_handle_hosts_info_step, step=step_name)



def _add_info_args(
    parser: argparse.ArgumentParser, *, json_help: str,
) -> None:
    parser.add_argument(
        "--name",
        required=True,
        dest="host_name",
        help="Host name from `automa hosts status`.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=json_help,
    )


def _handle_hosts_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa hosts commands",
                "",
                "- create      create and start a host",
                "- update      package and deploy a host's release",
                "- delete      stop the host and its run, and remove its local files",
                "- run         start a run and verify camera + perception + current view",
                "- stop        stop the run; the host stays attached until hosts delete",
                "- restart     stop and start the run",
                "- serve       serve a Chase host in the foreground",
                "- status      show each host's state and attachment",
                "- operation   run bounded vehicle checks through a host",
                "- info        inspect staged controller configuration",
                "- memory      reset live memory",
                "- workbench   replay images through the controller steps",
                "- viewer      open a host's runtime viewer",
                "- help        show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa hosts <command> --help",
            ]
        )
    )
    return 0


def _handle_hosts_operation_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa hosts operation commands",
                "",
                "- startup-check  send bounded pulses and verify camera changes",
                "- help           show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa hosts operation <command> --help",
            ]
        )
    )
    return 0


def _handle_hosts_info_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa hosts info commands",
                "",
                "- perception  show staged perception schema and live view",
                "- memory      show staged memory schema and live memory",
                "- proposal    show staged proposal schema, the staged decision and live proposals",
                "- plan        show staged plan plugins, the staged decision and the live plan step",
                "- action      show staged action plugins, the staged decision and the live action step",
                "- help        show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa hosts info <command> --help",
            ]
        )
    )
    return 0


_HOST_NAME_HELP = (
    "Name of a host created with hosts create."
)


def _handle_hosts_run(args: argparse.Namespace) -> int:
    if not validate_timeout_input(
        args.timeout_s,
        command="automa hosts run",
        json_output=False,
        human_stream=sys.stdout,
    ):
        return 2
    if args.foreground:
        result = run_host_automation(
            host_name=args.host_name,
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
        result = start_host_automation_background(
            host_name=args.host_name,
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
    if args.foreground and args.host_name:
        record_host_automation_terminal_result(
            host_name=args.host_name,
            result=result,
        )
    return result.exit_code


def _handle_hosts_stop(args: argparse.Namespace) -> int:
    result = stop_host_automation(
        host_name=args.host_name,
        wait_s=args.wait_s,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_hosts_status(args: argparse.Namespace) -> int:
    if not validate_timeout_input(
        args.timeout_s,
        command="automa hosts status",
        json_output=args.json,
        human_stream=sys.stderr,
    ):
        return 2
    result = get_hosts_status(
        host_name=args.host_name,
        timeout_s=args.timeout_s,
        long=args.long,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_hosts_serve(args: argparse.Namespace) -> int:
    from .runtime_hosts import serve_chase_host

    return serve_chase_host(args.host_name)


def _handle_hosts_restart(args: argparse.Namespace) -> int:
    result = restart_host_automation(
        host_name=args.host_name,
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


def _handle_hosts_operation_startup_check(args: argparse.Namespace) -> int:
    result = run_host_startup_check(
        host_name=args.host_name,
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


def _handle_hosts_memory_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa hosts memory commands",
                "",
                "- reset  clear live retained evidence; start a new empty epoch",
                "- help   show this summary",
                "",
                "Inspect what a selection retains: ./cli/automa plugins inspect memory <source>",
                "",
                "Arm a plugin with:            ./cli/automa plugins arm --host-name <host> --name memory/<plugin>",
                "Inspect staged config with:   ./cli/automa hosts info memory --name <host>",
                "Stream live state with:       ./cli/automa plugins stream --step memory --host-name <host>",
                "",
                "Detailed help:",
                "- ./cli/automa hosts memory <command> --help",
            ]
        )
    )
    return 0


def _handle_hosts_memory_reset(args: argparse.Namespace) -> int:
    result = reset_host_memory(
        host_name=args.host_name,
        timeout_s=args.timeout_s,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_hosts_workbench_help(args: argparse.Namespace) -> int:
    print(
        "\n".join(
            [
                "automa hosts workbench commands",
                "",
                (
                    "- replay  replay an ordered image directory through perception, "
                    "memory, proposals, and decisions"
                ),
                "- help    show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa hosts workbench replay --help",
            ]
        )
    )
    return 0


def _handle_hosts_workbench_replay(args: argparse.Namespace) -> int:
    result = run_workbench_replay(
        args.source_dir,
        selections={
            step: (getattr(args, f"{step}_preset"), getattr(args, f"{step}_plugins"))
            for step in WORKBENCH_SELECTABLE_STEPS
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


def _handle_hosts_create(args: argparse.Namespace) -> int:
    result = create_host(
        host_name=args.host_name, provider=args.provider, url=args.url,
        ssh_target=args.ssh_target, pi_home=args.pi_home, drive_args=args.drive_args,
        dry_run=args.dry_run, json_output=args.json, verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
    )
    print(result.message)
    return result.exit_code


def _handle_hosts_viewer(args: argparse.Namespace) -> int:
    result = serve_host_viewer(host_name=args.host_name, host_url=args.host_url)
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_hosts_update(args: argparse.Namespace) -> int:
    result = update_host(
        host_name=args.host_name, dry_run=args.dry_run, restart=args.restart,
        drive_args=args.drive_args, json_output=args.json, verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
    )
    print(result.message)
    return result.exit_code


def _handle_hosts_delete(args: argparse.Namespace) -> int:
    result = delete_host(host_name=args.host_name, verbose=args.verbose, output=sys.stdout)
    print(result.message)
    return result.exit_code


def _handle_hosts_info_perception(args: argparse.Namespace) -> int:
    result = get_host_perception_info(
        host_name=args.host_name,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_hosts_info_memory(args: argparse.Namespace) -> int:
    result = get_host_memory_info(
        host_name=args.host_name,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code


def _handle_hosts_info_step(args: argparse.Namespace) -> int:
    result = get_host_step_info(
        args.step,
        host_name=args.host_name,
        json_output=args.json,
    )
    if result.message:
        print(result.message)
    return result.exit_code
