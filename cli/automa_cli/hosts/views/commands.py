"""``automa hosts viewer``."""

from __future__ import annotations

import argparse

from .models import ViewerEvent
from .render import render_viewer_event, render_viewer_result
from .viewer import serve_host_viewer

_HOST_NAME_HELP = "Name of a host created with hosts create."


def register_host_viewer(host_commands: argparse._SubParsersAction) -> None:
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


def _handle_hosts_viewer(args: argparse.Namespace) -> int:
    result = serve_host_viewer(host_name=args.host_name, host_url=args.host_url, emit=_present)
    message = render_viewer_result(result)
    if message:
        print(message)
    return result.exit_code


def _present(event: ViewerEvent) -> None:
    print(render_viewer_event(event), flush=True)
