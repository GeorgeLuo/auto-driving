"""Walk the public automa argparse tree for terminal command leaves."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Literal

LeafKind = Literal["action", "meta"]


@dataclass(frozen=True)
class Leaf:
    leaf_id: str
    tokens: tuple[str, ...]
    help: str
    kind: LeafKind


def _leaf_kind(tokens: tuple[str, ...]) -> LeafKind:
    """Single documented membership rule: bare ``help`` token ends are meta.

    Action leaves are every other terminal public command. Help meta nodes remain
    in the inventory (tagged ``kind: meta``) so US-01 help chains can bind, but
    they are excluded from action-leaf counts and from help-drift comparisons.
    """

    if tokens and tokens[-1] == "help":
        return "meta"
    return "action"


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def walk_leaves(
    parser: argparse.ArgumentParser,
    *,
    prefix: tuple[str, ...] = (),
    include_help_meta: bool = True,
) -> list[Leaf]:
    """Return terminal command leaves under ``parser``.

    Help subcommands are included by default and tagged ``kind: meta``. Pass
    ``include_help_meta=False`` only when the caller wants action leaves alone.
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
                kind=_leaf_kind(prefix),
            )
        ]

    leaves: list[Leaf] = []
    helps: dict[str, str] = {}
    for choice_action in getattr(sub, "_choices_actions", []):
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
                Leaf(
                    leaf_id=".".join(path),
                    tokens=path,
                    help=help_text[:200],
                    kind=_leaf_kind(path),
                )
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


def action_leaf_ids(parser: argparse.ArgumentParser | None = None) -> list[str]:
    if parser is None:
        from cli.automa_cli.app import build_parser

        parser = build_parser()
    return [
        leaf.leaf_id
        for leaf in walk_leaves(parser, include_help_meta=True)
        if leaf.kind == "action"
    ]


def leaf_parser_for_tokens(
    parser: argparse.ArgumentParser,
    tokens: tuple[str, ...] | list[str],
) -> argparse.ArgumentParser | None:
    """Return the terminal argparse parser for a leaf token path, if present."""

    current = parser
    for token in tokens:
        sub = _subparsers(current)
        if sub is None or token not in sub.choices:
            return None
        current = sub.choices[token]
    return current


def leaf_supports_json(
    parser: argparse.ArgumentParser,
    tokens: tuple[str, ...] | list[str],
) -> bool:
    """True when the terminal leaf parser declares a ``--json`` option."""

    leaf_parser = leaf_parser_for_tokens(parser, tokens)
    if leaf_parser is None:
        return False
    for action in leaf_parser._actions:
        if "--json" in (action.option_strings or ()):
            return True
    return False


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
            "kind": leaf.kind,
        }
        for leaf in walk_leaves(parser, include_help_meta=include_help_meta)
    ]
