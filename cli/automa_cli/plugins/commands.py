"""The ``automa plugins`` parser and its handlers."""

from __future__ import annotations

import argparse

from .catalog.commands import register_catalog
from .inspect.commands import register_inspect
from .stream.commands import register_stream


def register_plugins(subcommands: argparse._SubParsersAction) -> None:
    step_plugins = subcommands.add_parser(
        "plugins",
        help="List a plugin catalog or what a host has armed, upload, arm, disarm and save a host's plugins, stream a step on a host, or inspect plugins from images and recordings.",
    )
    step_plugins.set_defaults(handler=_handle_plugins_help)
    step_plugin_commands = step_plugins.add_subparsers(dest="plugin_command")
    step_plugin_commands.add_parser(
        "help",
        help="Show plugin listing and inspection commands.",
    ).set_defaults(handler=_handle_plugins_help)

    register_catalog(step_plugin_commands)
    register_stream(step_plugin_commands)
    register_inspect(step_plugin_commands)


def _handle_plugins_help(args: argparse.Namespace) -> int:
    print("\n".join([
        "automa plugins commands", "",
        "- list     show the packaged catalog or what a host has armed",
        "- upload   register a plugin file in a host's or workbench's catalog",
        "- arm      arm catalog plugins on a host by step/plugin",
        "- disarm   deselect a host's plugins by step/plugin, or clear a step or every step",
        "- save     make what a host has armed the plugins it starts with",
        "- stream   show a step's latest output on a host as it updates",
        "- inspect  report a step's plugins from images or a recorded run",
        "- help     show this summary", "",
        "Packaged catalog: ./cli/automa plugins list",
        "Armed on a host: ./cli/automa plugins list --host-name <host>",
        "A host's catalog: ./cli/automa plugins list --host-name <host> --long",
        "Upload to a host: ./cli/automa plugins upload --host-name <host> --file <file> --name <step>/<plugin> --entrypoint <module>:<Class>",
        "Arm on a host: ./cli/automa plugins arm --host-name <host> --name <step>/<plugin>",
        "Disarm on a host: ./cli/automa plugins disarm --host-name <host> --name <step>/<plugin>",
        "Start a host with what it has armed: ./cli/automa plugins save --host-name <host>",
        "Stream on a host: ./cli/automa plugins stream --host-name <host> --step <step>",
        "Detailed help: ./cli/automa plugins <command> --help",
    ]))
    return 0
