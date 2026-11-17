"""One run configuration for local and onboard decision hosts.

num_decisions bounds completed decision cycles; None runs until stopped.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from autonomy.fields import FieldError, require_keys, require_safe_int
from autonomy.runtime.execution import require_mode


@dataclass(frozen=True)
class RunConfiguration:
    mode: str
    num_decisions: int | None

    def __post_init__(self) -> None:
        require_mode(self.mode)
        if self.num_decisions is not None:
            count = require_safe_int(self.num_decisions, field_name="num_decisions")
            if count == 0:
                raise FieldError("num_decisions", "must be positive, or null to run until stopped")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> RunConfiguration:
        """Decode a requested run; a client names every setting, so none defaults."""

        document = require_keys(data, {item.name for item in fields(cls)})
        return cls(mode=document["mode"], num_decisions=document["num_decisions"])
