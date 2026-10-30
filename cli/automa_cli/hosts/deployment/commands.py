"""``automa hosts`` create, update, and delete."""

from __future__ import annotations

import argparse
import sys

from ...vehicles.providers import PROVIDERS
from .render import present_deployment
from .service import create_host, delete_host, update_host


def register_host_deployment(host_commands: argparse._SubParsersAction) -> None:
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
    host_create.add_argument("--provider", required=True, choices=PROVIDERS,
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


def _handle_hosts_create(args: argparse.Namespace) -> int:
    result = create_host(
        host_name=args.host_name, provider=args.provider, url=args.url,
        ssh_target=args.ssh_target, pi_home=args.pi_home, drive_args=args.drive_args,
        dry_run=args.dry_run, verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
    )
    print(present_deployment(result, json_output=args.json))
    return result.exit_code


def _handle_hosts_update(args: argparse.Namespace) -> int:
    result = update_host(
        host_name=args.host_name, dry_run=args.dry_run, restart=args.restart,
        drive_args=args.drive_args, verbose=args.verbose,
        output=None if args.dry_run else (sys.stderr if args.json else sys.stdout),
    )
    print(present_deployment(result, json_output=args.json))
    return result.exit_code


def _handle_hosts_delete(args: argparse.Namespace) -> int:
    result = delete_host(host_name=args.host_name, verbose=args.verbose, output=sys.stdout)
    print(present_deployment(result, json_output=False))
    return result.exit_code
