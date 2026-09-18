"""Walk the public automa argparse tree for terminal command leaves."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Literal

LeafKind = Literal["action", "meta", "alias"]


@dataclass(frozen=True)
class Leaf:
    leaf_id: str
    tokens: tuple[str, ...]
    help: str
    kind: LeafKind
    alias_of: str | None = None


def _leaf_kind(tokens: tuple[str, ...]) -> LeafKind:
    """Membership rule: trailing ``help`` is meta; other terminals are action."""

    if tokens and tokens[-1] == "help":
        return "meta"
    return "action"


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _help_text_for(
    parser: argparse.ArgumentParser,
    *,
    name: str | None = None,
    helps: dict[str, str] | None = None,
) -> str:
    if name and helps and helps.get(name):
        return helps[name][:200]
    text = (parser.description or parser.format_help().splitlines()[0] or "").strip()
    return text[:200]


def walk_leaves(
    parser: argparse.ArgumentParser,
    *,
    prefix: tuple[str, ...] = (),
    include_help_meta: bool = True,
) -> list[Leaf]:
    """Return terminal command leaves under ``parser``.

    Help subcommands are included by default and tagged ``kind: meta``. Nodes
    that own optional (non-required) subparsers are also public terminals:
    they are emitted as ``kind: alias`` bound to their explicit ``help`` child,
    then children are still walked.
    """

    sub = _subparsers(parser)
    if sub is None or not sub.choices:
        if not prefix:
            return []
        return [
            Leaf(
                leaf_id=".".join(prefix),
                tokens=prefix,
                help=_help_text_for(parser),
                kind=_leaf_kind(prefix),
            )
        ]

    leaves: list[Leaf] = []
    helps: dict[str, str] = {}
    for choice_action in getattr(sub, "_choices_actions", []):
        helps[choice_action.dest] = (choice_action.help or "").strip()

    if prefix and not bool(getattr(sub, "required", False)):
        alias_of = ".".join(prefix + ("help",)) if "help" in sub.choices else None
        leaves.append(
            Leaf(
                leaf_id=".".join(prefix),
                tokens=prefix,
                help=_help_text_for(parser),
                kind="alias",
                alias_of=alias_of,
            )
        )

    for name, child in sorted(sub.choices.items(), key=lambda item: item[0]):
        if name == "help" and not include_help_meta:
            continue
        path = prefix + (name,)
        nested = _subparsers(child)
        if nested is None or not nested.choices:
            leaves.append(
                Leaf(
                    leaf_id=".".join(path),
                    tokens=path,
                    help=_help_text_for(child, name=name, helps=helps),
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
    """Return the argparse parser for a token path, if present."""

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
    rows: list[dict[str, Any]] = []
    for leaf in walk_leaves(parser, include_help_meta=include_help_meta):
        row: dict[str, Any] = {
            "leaf_id": leaf.leaf_id,
            "tokens": list(leaf.tokens),
            "help": leaf.help,
            "kind": leaf.kind,
        }
        if leaf.alias_of:
            row["alias_of"] = leaf.alias_of
        rows.append(row)
    return rows
