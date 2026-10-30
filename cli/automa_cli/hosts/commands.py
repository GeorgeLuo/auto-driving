"""The ``automa hosts`` parser.

Deployment, runs, and the viewer register their own commands. This module
assembles them in the public command order and prints the host help summary.
"""

from __future__ import annotations

import argparse

from .deployment.commands import register_host_deployment
from .runs.commands import register_host_runs
from .views.commands import register_host_viewer


def register_hosts(subcommands: argparse._SubParsersAction) -> None:
    hosts = subcommands.add_parser("hosts", help="Create, configure, and operate vehicle controller hosts.")
    hosts.set_defaults(handler=_handle_hosts_help)
    host_commands = hosts.add_subparsers(dest="host_command")
    host_help = host_commands.add_parser("help", help="Show host-level commands.")
    host_help.set_defaults(handler=_handle_hosts_help)

    register_host_viewer(host_commands)
    register_host_deployment(host_commands)
    register_host_runs(host_commands)


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
                "- status      show each host's state and attachment",
                "- viewer      open a host's runtime viewer",
                "- help        show this summary",
                "",
                "Detailed help:",
                "- ./cli/automa hosts <command> --help",
            ]
        )
    )
    return 0
