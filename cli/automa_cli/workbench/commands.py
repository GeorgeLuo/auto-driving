"""The ``automa workbench`` parser and its replay handler."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any

from ..shared.selection_args import add_selection_args
from .plugins import WORKBENCH_SELECTABLE_STEPS
from .replay import run_workbench_replay
from .source import WORKBENCH_DEFAULT_MAX_FRAMES


def register_workbench(
    subcommands: argparse._SubParsersAction, *, view_server: Callable[..., Any],
) -> None:
    workbench = subcommands.add_parser(
        "workbench",
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
            "selection preserves retained plugins and their configs; new plugins use defaults. Other steps keep their "
            "selections and configs. The same ordered selection keeps the current "
            "configs and pass. Without --serve, one replay runs "
            "to a terminal state; --serve keeps the viewer's workbench page available for "
            "pause, step, reset, and another run. Changing a step's plugins in a "
            "running replay applies on the next frame without replaying history. While paused, "
            "it refreshes only the latest frame from its pre-frame state. Return to the latest "
            "frame before editing plugins after a backward seek. Reset and Start replay from the beginning."
        ),
    )
    workbench.add_argument(
        "source_dir",
        help="Directory containing supported images and optional ordered manifest.",
    )
    for step_name in WORKBENCH_SELECTABLE_STEPS:
        add_selection_args(workbench, step_name, verb="replay", flag_prefix=f"{step_name}-")
    workbench.add_argument(
        "--cadence-ms",
        type=int,
        default=250,
        help="Delay between frames in milliseconds (default: 250; zero means as fast as possible).",
    )
    workbench.add_argument(
        "--pace",
        choices=("fixed", "realtime"),
        default="fixed",
        help="Playback pacing mode (realtime honors recorded frame timestamps; default: fixed).",
    )
    workbench.add_argument(
        "--max-frames",
        type=int,
        default=WORKBENCH_DEFAULT_MAX_FRAMES,
        help=(
            "Maximum normalized frames accepted from the source "
            f"(default: {WORKBENCH_DEFAULT_MAX_FRAMES})."
        ),
    )
    workbench.add_argument(
        "--host",
        default="127.0.0.1",
        help="Loopback host for --serve (default: 127.0.0.1).",
    )
    workbench.add_argument(
        "--port",
        type=int,
        default=0,
        help="Loopback port for --serve (default: choose a free port).",
    )
    workbench.add_argument(
        "--serve",
        action="store_true",
        help="Keep serving the viewer, with this replay on its workbench page.",
    )
    workbench.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the viewer's workbench page in a browser and imply --serve.",
    )
    workbench.set_defaults(handler=_handle_workbench, view_server=view_server)


def _handle_workbench(args: argparse.Namespace) -> int:
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
        view_server=args.view_server,
    )
    if result.message:
        print(result.message)
    return result.exit_code
