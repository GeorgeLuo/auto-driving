"""Field checks shared by the readers of what a runtime host publishes.

Each reader passes ``invalid(text, field)``, which builds its own error, so a
decision publication and host telemetry keep their own message prefix and
reason.
"""

from __future__ import annotations

from typing import Any, Callable

from autonomy.decision_cycle.action_identifiers import require_ascii_id


class PublicationError(ValueError):
    """A host publication is absent or fails its reader's contract."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message_text = message
        self.details = details or {}


Invalid = Callable[[str, str], PublicationError]


def require_mapping(value: object, *, field: str, invalid: Invalid) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid(f"{field} must be an object.", field)
    return value


def require_id(value: object, *, field: str, invalid: Invalid) -> str:
    try:
        return require_ascii_id(value, field_name=field)
    except ValueError as exc:
        raise invalid(f"{field} is not a valid identity.", field) from exc
