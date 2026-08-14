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
            prefix_reason = _validate_prefix_before_help(prefix)
            if prefix_reason:
                return ArgvReceipt(
                    template_id=template_id,
                    argv=list(argv),
                    leaf_id=leaf_id,
                    ok=False,
                    reason=prefix_reason,
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


def _relax_parser_for_prefix(parser: argparse.ArgumentParser) -> None:
    """Allow discovery prefixes: keep choices/types, drop required and help-exit."""

    parser.add_help = False
    for action in list(parser._actions):
        action.required = False
        if isinstance(action, argparse._HelpAction):
            parser._remove_action(action)
            for option in action.option_strings:
                parser._option_string_actions.pop(option, None)
        elif isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _relax_parser_for_prefix(child)


def _validate_prefix_before_help(prefix: list[str]) -> str:
    """Return a failure reason if the argv prefix is invalid without ``--help``.

    Uses a fresh parser so the live tree is not mutated. Required flags are
    relaxed because help is a discovery form; unknown tokens, extra
    positionals, and invalid choices/types still fail.
    """

    import contextlib
    import io

    from cli.automa_cli.app import build_parser

    probe = build_parser()
    _relax_parser_for_prefix(probe)

    def _error(message: str) -> None:
        raise ArgvValidationError(message)

    _patch_error(probe, _error)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            probe.parse_args(list(prefix))
    except ArgvValidationError as exc:
        return f"invalid tokens before help flag: {exc}"
    except SystemExit as exc:
        return f"invalid tokens before help flag: parser SystemExit {exc.code!r}"
    return ""


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
