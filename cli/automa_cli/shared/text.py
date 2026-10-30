"""Text formatting shared by command output."""

from typing import Any


def indented(text: Any) -> list[str]:
    return [f"  {line}" for line in str(text).splitlines()] if text else []
