"""One run configuration for local and onboard decision hosts.

num_decisions bounds completed decision cycles; None runs until stopped.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from autonomy.runtime.execution import require_mode


@dataclass(frozen=True)
class RunConfiguration:
    mode: str
    num_decisions: int | None

    def __post_init__(self) -> None:
        require_mode(self.mode)
        if self.num_decisions is not None and (
            type(self.num_decisions) is not int or self.num_decisions < 1
        ):
            raise ValueError("num_decisions must be a positive integer, or None to run until stopped")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> RunConfiguration:
        """Decode a requested run; a client names every setting, so none defaults."""

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("configuration must be an object")
        names = [field.name for field in fields(cls)]
        missing = [name for name in names if name not in data]
        if missing:
            raise ValueError(f"configuration must name {', '.join(missing)}")
        unknown = sorted(set(data) - set(names))
        if unknown:
            raise ValueError(f"configuration has unknown keys {', '.join(unknown)}")
        return cls(**data)
