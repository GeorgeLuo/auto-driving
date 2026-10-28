"""The exit code and printable message returned by a CLI operation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str
