"""Registration and handlers for ``plugins inspect``."""

from __future__ import annotations

import argparse
from pathlib import Path

from ...replay.source import WORKBENCH_DEFAULT_MAX_FRAMES
from ...shared.command_result import CommandResult
from ...shared.cli.selection_args import add_selection_args
from .render import render_inspect
from .service import INSPECT_STEPS, inspect_step as run_inspect


def register_inspect(commands: argparse._SubParsersAction) -> None:
    inspect_control = commands.add_parser(
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


def inspect_step(
    step: str,
    *,
    source: str | Path,
    plugins: list[str] | None = None,
    preset: str | None = None,
    frame: int | None = None,
    record: bool = False,
    json_output: bool = False,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
    shared_memory_file: Path | None = None,
) -> CommandResult:
    """Run one inspection and return the exit code plus the text to print."""

    result = run_inspect(
        step, source=source, plugins=plugins, preset=preset, frame=frame, record=record,
        max_frames=max_frames, shared_memory_file=shared_memory_file,
    )
    if result.report is None:
        return CommandResult(result.exit_code, result.error or "")
    return CommandResult(0, render_inspect(result.report, json_output=json_output))


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
