"""Parser-aware argv validation for sequence command templates."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from typing import Any, Sequence


PLACEHOLDER_RE = re.compile(r"^\{[a-zA-Z_][a-zA-Z0-9_]*\}$")
DEFAULT_PLACEHOLDERS = {
    "vehicle_id": "chase-sim-chaser",
    "src_dir": "/tmp/m007-audit-src",
    "record_dir": "/tmp/m007-audit-record",
    "algorithm": "lightweight_observer",
}


class ArgvValidationError(Exception):
    """Command template is not accepted by the public parser."""


@dataclass(frozen=True)
class ArgvReceipt:
    template_id: str
    argv: list[str]
    leaf_id: str
    ok: bool
    reason: str


def normalize_placeholders(
    argv: Sequence[str],
    *,
    placeholders: dict[str, str] | None = None,
) -> list[str]:
    mapping = dict(DEFAULT_PLACEHOLDERS)
    if placeholders:
        mapping.update(placeholders)
    out: list[str] = []
    for token in argv:
        if PLACEHOLDER_RE.fullmatch(token):
            key = token[1:-1]
            if key not in mapping:
                raise ArgvValidationError(
                    f"unknown placeholder {token!r}; document it or provide a fixture"
                )
            out.append(mapping[key])
        elif "{" in token or "}" in token:
            raise ArgvValidationError(
                f"malformed placeholder token {token!r}; use whole-token {{name}} only"
            )
        else:
            out.append(token)
    return out


def _leaf_id_from_argv(argv: Sequence[str], parser: argparse.ArgumentParser) -> str:
    """Best-effort leaf id from argv tokens before options."""

    tokens: list[str] = []
    current = parser
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("-"):
            break
        sub = None
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                sub = action
                break
        if sub is None or tok not in sub.choices:
            break
        tokens.append(tok)
        current = sub.choices[tok]
        i += 1
    if not tokens:
        raise ArgvValidationError("argv has no public leaf path")
    return ".".join(tokens)


def validate_argv(
    argv: Sequence[str],
    *,
    parser: argparse.ArgumentParser | None = None,
    template_id: str = "command",
    placeholders: dict[str, str] | None = None,
) -> ArgvReceipt:
    if parser is None:
        from cli.automa_cli.app import build_parser

        parser = build_parser()

    try:
        normalized = normalize_placeholders(argv, placeholders=placeholders)
        # Drop leading program name if present.
        if normalized and normalized[0] in {"automa", "./cli/automa", "cli/automa"}:
            normalized = normalized[1:]
        if not normalized:
            raise ArgvValidationError("empty argv")

        leaf_id = _leaf_id_from_argv(normalized, parser)

        def _error(message: str) -> None:
            raise ArgvValidationError(message)

        # Fail closed without process exit.
        parser.error = _error  # type: ignore[method-assign]
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    _patch_error(child, _error)

        # Suppress help printing noise during validation.
        import contextlib
        import io

        help_indexes = [
            i for i, tok in enumerate(normalized) if tok in {"-h", "--help"}
        ]
        if help_indexes:
            # Help must be the final token; reject ``--help --bogus`` and similar.
            if help_indexes[-1] != len(normalized) - 1 or len(help_indexes) != 1:
                return ArgvReceipt(
                    template_id=template_id,
                    argv=list(argv),
                    leaf_id=leaf_id,
                    ok=False,
                    reason="help flag must be sole trailing token without extra args",
                )
            prefix = normalized[:-1]
            extras = _unknown_tokens_before_help(parser, prefix)
            if extras:
                return ArgvReceipt(
                    template_id=template_id,
                    argv=list(argv),
                    leaf_id=leaf_id,
                    ok=False,
                    reason=f"unknown tokens before help flag: {extras}",
                )

        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                parser.parse_args(list(normalized))
        except SystemExit as exc:
            # ``--help`` exits 0 after printing help; treat as valid discovery argv
            # only when help is the trailing token (checked above).
            if (
                exc.code in (0, None)
                and help_indexes
                and help_indexes[-1] == len(normalized) - 1
            ):
                return ArgvReceipt(
                    template_id=template_id,
                    argv=list(normalized),
                    leaf_id=leaf_id,
                    ok=True,
                    reason="ok_help",
                )
            return ArgvReceipt(
                template_id=template_id,
                argv=list(argv),
                leaf_id=leaf_id,
                ok=False,
                reason=f"parser SystemExit: {exc.code!r}",
            )
        return ArgvReceipt(
            template_id=template_id,
            argv=list(normalized),
            leaf_id=leaf_id,
            ok=True,
            reason="ok",
        )
    except ArgvValidationError as exc:
        return ArgvReceipt(
            template_id=template_id,
            argv=list(argv),
            leaf_id="",
            ok=False,
            reason=str(exc),
        )


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _option_takes_value(action: argparse.Action) -> bool:
    if isinstance(
        action,
        (
            argparse._StoreTrueAction,
            argparse._StoreFalseAction,
            argparse._HelpAction,
            argparse._VersionAction,
            argparse._CountAction,
        ),
    ):
        return False
    if action.nargs == 0:
        return False
    return True


def _unknown_tokens_before_help(
    parser: argparse.ArgumentParser, prefix: list[str]
) -> list[str]:
    """Return unknown options/positionals in the argv prefix before trailing help.

    Argparse exits on ``--help`` before checking earlier unknown tokens, and
    ``parse_known_args`` may still demand required options. Inspect the leaf
    parser's declared options without invoking ``parser.error``.
    """

    current = parser
    index = 0
    while index < len(prefix):
        token = prefix[index]
        if token.startswith("-"):
            break
        sub = _subparsers(current)
        if sub is None or token not in sub.choices:
            return list(prefix[index:])
        current = sub.choices[token]
        index += 1
    rest = prefix[index:]
    options = {
        option: action
        for action in current._actions
        for option in (action.option_strings or ())
    }
    accepts_positional = any(
        not action.option_strings
        and not isinstance(action, argparse._SubParsersAction)
        and action.dest not in {"help"}
        for action in current._actions
    )
    extras: list[str] = []
    cursor = 0
    while cursor < len(rest):
        token = rest[cursor]
        if token.startswith("-"):
            name = token.split("=", 1)[0]
            action = options.get(name)
            if action is None:
                extras.append(token)
                cursor += 1
                continue
            if _option_takes_value(action) and "=" not in token:
                cursor += 2
            else:
                cursor += 1
            continue
        if not accepts_positional:
            extras.append(token)
        cursor += 1
    return extras


def _patch_error(parser: argparse.ArgumentParser, error_fn: Any) -> None:
    parser.error = error_fn  # type: ignore[method-assign]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _patch_error(child, error_fn)


def argv_from_shell_line(line: str) -> list[str]:
    """Split a single shell-ish argv line (no pipes)."""

    import shlex

    return shlex.split(line)
