"""Text formatting shared by command output."""

import shlex
from typing import Any

from .browser_result import BrowserOpenResult


def automa_command(*args: str) -> str:
    """The ``./cli/automa`` command line for ``args``, each quoted for a shell when it needs it."""

    return "./cli/automa " + shlex.join(args)


def indented(text: Any) -> list[str]:
    return [f"  {line}" for line in str(text).splitlines()] if text else []


def format_browser_result(result: BrowserOpenResult) -> str:
    if result.opened:
        return f"Browser opened: {result.url}"
    return f"Warning: could not open the browser ({result.error}). Open manually: {result.url}"
