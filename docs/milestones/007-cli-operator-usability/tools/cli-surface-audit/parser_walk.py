"""Walk the public automa argparse tree for terminal command leaves."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Leaf:
    leaf_id: str
    tokens: tuple[str, ...]
    help: str


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def walk_leaves(
    parser: argparse.ArgumentParser,
    *,
    prefix: tuple[str, ...] = (),
    include_help_meta: bool = False,
) -> list[Leaf]:
    """Return terminal command leaves under ``parser``.

    ``help`` subcommands are excluded from the public leaf set unless
    ``include_help_meta`` is true (they are meta documentation surfaces).
    """

    sub = _subparsers(parser)
    if sub is None or not sub.choices:
        if not prefix:
            return []
        help_text = (parser.description or parser.format_help().splitlines()[0] or "").strip()
        return [
            Leaf(
                leaf_id=".".join(prefix),
                tokens=prefix,
                help=help_text[:200],
            )
        ]

    leaves: list[Leaf] = []
    helps: dict[str, str] = {}
    for choice_action in getattr(sub, "_choices_actions", []):
        # add_parser name is stored on the choice action.
        helps[choice_action.dest] = (choice_action.help or "").strip()
    for name, child in sorted(sub.choices.items(), key=lambda item: item[0]):
        if name == "help" and not include_help_meta:
            continue
        path = prefix + (name,)
        nested = _subparsers(child)
        if nested is None or not nested.choices:
            help_text = (
                helps.get(name) or (getattr(child, "description", None) or "")
            ).strip()
            leaves.append(
                Leaf(leaf_id=".".join(path), tokens=path, help=help_text[:200])
            )
        else:
            leaves.extend(
                walk_leaves(
                    child,
                    prefix=path,
                    include_help_meta=include_help_meta,
                )
            )
    return leaves


def public_leaf_ids(
    parser: argparse.ArgumentParser | None = None,
    *,
    include_help_meta: bool = True,
) -> list[str]:
    if parser is None:
        from cli.automa_cli.app import build_parser

        parser = build_parser()
    return [
        leaf.leaf_id
        for leaf in walk_leaves(parser, include_help_meta=include_help_meta)
    ]


def leaf_skeleton(
    parser: argparse.ArgumentParser | None = None,
    *,
    include_help_meta: bool = True,
) -> list[dict[str, Any]]:
    if parser is None:
        from cli.automa_cli.app import build_parser

        parser = build_parser()
    return [
        {
            "leaf_id": leaf.leaf_id,
            "tokens": list(leaf.tokens),
            "help": leaf.help,
        }
        for leaf in walk_leaves(parser, include_help_meta=include_help_meta)
    ]
