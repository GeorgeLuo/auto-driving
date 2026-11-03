"""Text formatting shared by command output."""

from typing import Any

from .browser_result import BrowserOpenResult


def indented(text: Any) -> list[str]:
    return [f"  {line}" for line in str(text).splitlines()] if text else []


def format_browser_result(result: BrowserOpenResult) -> str:
    if result.opened:
        return f"Browser opened: {result.url}"
    return f"Warning: could not open the browser ({result.error}). Open manually: {result.url}"
