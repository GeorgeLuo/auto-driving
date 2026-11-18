"""Registration and handlers for ``plugins inspect``."""

from __future__ import annotations

import argparse
from pathlib import Path

from ...replay.source import WORKBENCH_DEFAULT_MAX_FRAMES
from ...shared.command_result import CommandResult
from ...shared.cli.selection_args import add_manifest_arg, add_selection_args
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
        _register_step_inspect(inspect_commands, step_name, step_name)
        step_control = commands.add_parser(step_name, help=f"Inspect the {step_name} step.")
        _register_step_inspect(step_control.add_subparsers(required=True), "inspect", step_name)


def _register_step_inspect(commands: argparse._SubParsersAction, name: str, step: str) -> None:
    record_description = {
        "perception": "what perception detects",
        "memory": "what memory retains",
    }.get(step, f"the {step} record")
    parser = commands.add_parser(
        name,
        help=f"Show {record_description} for every frame.",
        description=(
            f"Replay an image, a directory of images, or a recorded run through the whole "
            f"cycle and report {record_description} after each frame. A recording restores "
            f"every step's selection and restages it per frame; --preset or --plugin overrides "
            f"{step}. Otherwise each step uses its default. Absent frames have a null "
            f"record and reset temporal state. Unmanifested images use filename order and "
            f"times 0, 1000, 2000, ... ms. Recordings use manifest.json with per-frame inputs "
            f"and relative image_path values; report.json is output only. Recorded frames "
            f"replay with their recorded drive mode."
        ),
    )
    parser.add_argument("source", type=Path, help="Image file, directory of images, or recorded run.")
    add_selection_args(parser, step, verb="inspect", fallback="the recorded selection, else ")
    add_manifest_arg(parser)
    parser.add_argument(
        "--frame", type=int, metavar="N",
        help="Report only the source's Nth frame (0-based); the frames before it still replay.",
    )
    parser.add_argument(
        "--record", action="store_true",
        help=f"Persist executed frames and inputs in a replay manifest, with the selected report under runtime/{step}-inspections/ for replay.",
    )
    parser.add_argument("--json", action="store_true", help="Print the machine-readable report.")
    parser.add_argument(
        "--shared-memory", type=Path, dest="shared_memory_file", metavar="FILE",
        help="Start inspect from a shared_memory JSON file; recorded as provenance.",
    )
    parser.add_argument(
        "--max-frames", type=int, default=WORKBENCH_DEFAULT_MAX_FRAMES,
        help=(
            "Reject image directories or recordings above this frame count; "
            f"increase for larger sources (default: {WORKBENCH_DEFAULT_MAX_FRAMES})."
        ),
    )
    parser.set_defaults(handler=_handle_plugins_inspect, step=step)


def inspect_step(
    step: str,
    *,
    source: str | Path,
    plugins: list[str] | None = None,
    preset: str | None = None,
    manifest: Path | None = None,
    frame: int | None = None,
    record: bool = False,
    json_output: bool = False,
    max_frames: int = WORKBENCH_DEFAULT_MAX_FRAMES,
    shared_memory_file: Path | None = None,
) -> CommandResult:
    """Run one inspection and return the exit code plus the text to print."""

    result = run_inspect(
        step, source=source, plugins=plugins, preset=preset, manifest=manifest, frame=frame, record=record,
        max_frames=max_frames, shared_memory_file=shared_memory_file,
    )
    if result.report is None:
        return CommandResult(result.exit_code, result.error or "")
    return CommandResult(0, render_inspect(result.report, json_output=json_output, records=result.records))


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
        manifest=args.manifest,
        frame=args.frame,
        record=args.record,
        json_output=args.json,
        max_frames=args.max_frames,
        shared_memory_file=args.shared_memory_file,
    )
    if result.message:
        print(result.message)
    return result.exit_code
