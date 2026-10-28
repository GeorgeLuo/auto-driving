"""The ``--preset``/``--plugin`` selection a step takes in ``hosts`` and ``plugins inspect``."""

from __future__ import annotations

import argparse

from implementations.decision_cycle.catalog import DEFAULT_STEP_PLUGINS, DEFAULT_STEP_PRESETS, STEP_PRESETS


def add_selection_args(
    parser: argparse.ArgumentParser,
    step: str,
    *,
    verb: str,
    fallback: str = "",
    flag_prefix: str = "",
) -> None:
    """Add a step's mutually exclusive ``--preset``/``--plugin`` selection.

    Steps without presets take only ``--plugin`` and parse ``preset`` as None.
    ``flag_prefix`` names one step among several on a command, such as
    ``--memory-preset``; ``fallback`` precedes the default in the help text.
    """
    dest = flag_prefix.replace("-", "_")
    group = parser.add_mutually_exclusive_group()
    if step in STEP_PRESETS:
        group.add_argument(
            f"--{flag_prefix}preset",
            dest=f"{dest}preset",
            choices=sorted(STEP_PRESETS[step]),
            default=None,
            help=f"Packaged {step} preset to {verb} (default: {fallback}{DEFAULT_STEP_PRESETS[step]}).",
        )
        plugin_help = f"Packaged {step} plugin to {verb} instead of a preset"
    else:
        parser.set_defaults(**{f"{dest}preset": None})
        plugin_help = (
            f"Packaged {step} plugin to {verb} "
            f"(default: {fallback}{', '.join(DEFAULT_STEP_PLUGINS[step]) or 'none'})"
        )
    group.add_argument(
        f"--{flag_prefix}plugin",
        action="append",
        dest=f"{dest}plugins",
        default=None,
        metavar="PLUGIN_ID",
        help=f"{plugin_help}; repeat to select several in order.",
    )
