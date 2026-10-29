"""One run configuration for local and onboard decision hosts.

num_decisions bounds completed decision cycles; zero keeps the run unbounded.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

from autonomy.runtime.execution import require_mode


@dataclass(frozen=True)
class RunConfiguration:
    mode: str = "autonomy"
    num_decisions: int = 0

    def __post_init__(self) -> None:
        require_mode(self.mode)
        if type(self.num_decisions) is not int or self.num_decisions < 0:
            raise ValueError("num_decisions must be a nonnegative integer")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunConfiguration:
        """Decode a requested run; a client names every setting, so none defaults."""

        if not isinstance(data, dict):
            raise ValueError("configuration must be an object")
        missing = [field.name for field in fields(cls) if field.name not in data]
        if missing:
            raise ValueError(f"configuration must name {', '.join(missing)}")
        return cls(**data)
