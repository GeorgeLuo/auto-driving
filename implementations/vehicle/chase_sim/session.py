"""Public Chase session identity and passive receipt contracts."""
from __future__ import annotations

import math
from typing import Any

RUN_STABLE_FIELDS = ("game_id", "scenario_id", "simulation_epoch", "control_source")

PASSIVE_SESSION_FIELDS = (
    "game_id",
    "scenario_id",
    "simulation_epoch",
    "playback",
    "control_source",
    "control_input",
)


CHASE_PASSIVE_PRESERVED_FIELDS = (
    "gameId",
    "scenarioId",
    "simulationEpoch",
    "playback",
    "controlSource",
    "controlInput",
    "actorId",
    "cameraId",
)

PASSIVE_RECEIPT_FIELD_NAMES = {
    "gameId": "game_id",
    "scenarioId": "scenario_id",
    "simulationEpoch": "simulation_epoch",
    "playback": "playback",
    "controlSource": "control_source",
    "controlInput": "control_input",
    "actorId": "actor_id",
    "cameraId": "camera_id",
}


class ChasePassiveCaptureError(RuntimeError):
    """Structured failure at the passive simulator-observation boundary."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.detail = message
        self.details = details or {}
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        layer = {
            "simulator_unreachable": "simulator_server",
            "frontend_disconnected": "simulator_frontend",
            "wrong_game": "chase_game",
            "front_view_unavailable": "vehicle",
            "simulator_capability_missing": "passive_capture",
            "simulator_state_changed": "passive_capture",
        }.get(self.code, "passive_capture")
        return {
            "schema": "automa_cli_error_v1",
            "error": self.code,
            "layer": layer,
            "message": self.detail,
            "details": self.details,
            "recovery": None,
            "exit_code": 1,
        }


def normalize_passive_receipt_fingerprint(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    normalized = {
        normalized_name: source.get(protocol_name)
        for protocol_name, normalized_name in PASSIVE_RECEIPT_FIELD_NAMES.items()
    }
    unknown_fields: list[str] = []
    for protocol_name, normalized_name in PASSIVE_RECEIPT_FIELD_NAMES.items():
        field_value = normalized[normalized_name]
        if protocol_name == "controlInput":
            valid = protocol_name in source and (
                field_value is None
                or (
                    isinstance(field_value, dict)
                    and isinstance(field_value.get("source"), str)
                    and bool(field_value["source"].strip())
                    and isinstance(field_value.get("forward"), bool)
                    and isinstance(field_value.get("reverse"), bool)
                    and isinstance(field_value.get("steering"), (int, float))
                    and not isinstance(field_value.get("steering"), bool)
                    and math.isfinite(float(field_value["steering"]))
                )
            )
        elif protocol_name == "playback":
            valid = (
                isinstance(field_value, dict)
                and isinstance(field_value.get("frameIndex"), int)
                and not isinstance(field_value.get("frameIndex"), bool)
                and field_value["frameIndex"] >= 0
                and field_value.get("phase")
                in {"running", "paused-before-actions"}
                and isinstance(field_value.get("pendingAction"), bool)
            )
        else:
            valid = isinstance(field_value, str) and bool(field_value.strip())
        if not valid:
            unknown_fields.append(normalized_name)
    return {
        "schema": "chase_session_fingerprint_v1",
        **normalized,
        "unknown_fields": unknown_fields,
    }


def compare_passive_receipt_fingerprints(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    field_names = tuple(PASSIVE_RECEIPT_FIELD_NAMES.values())
    unknown_fields = sorted(
        {
            *(
                str(item)
                for item in before.get("unknown_fields", [])
                if isinstance(item, str)
            ),
            *(
                str(item)
                for item in after.get("unknown_fields", [])
                if isinstance(item, str)
            ),
        }
    )
    changed_fields = [
        field_name
        for field_name in field_names
        if before.get(field_name) != after.get(field_name)
    ]
    return {
        "preserved": not unknown_fields and not changed_fields,
        "unknown_fields": unknown_fields,
        "changed_fields": changed_fields,
        "before": before,
        "after": after,
    }


def passive_receipt_environment(
    fingerprint: dict[str, Any],
) -> dict[str, Any]:
    return {
        key: fingerprint.get(key)
        for key in (
            "game_id",
            "scenario_id",
            "simulation_epoch",
            "playback",
            "control_source",
            "control_input",
        )
    }


def build_chase_session_fingerprint(
    *,
    state: dict[str, Any],
    debug: dict[str, Any],
) -> dict[str, Any]:
    """Build the documented read-only Chase session fields used for preservation."""

    sidebar = _play_sidebar_values(state)
    playback_source = state.get("playback")
    playback_source = playback_source if isinstance(playback_source, dict) else {}
    playback = {
        key: playback_source.get(key)
        for key in ("isPlaying", "playbackRate", "rate")
        if key in playback_source
    }
    control_input_source = _first_dict(
        _nested_get(debug, ("actions", "chaserInput")),
        _nested_get(debug, ("actors", "chaser", "input")),
    )
    control_input = _bounded_input(control_input_source)
    control_source = _first_nonempty(
        sidebar.get("chaser-control-source"),
        _nested_get(debug, ("actions", "chaserInput", "source")),
        _nested_get(debug, ("actions", "chaserAction", "source")),
        _nested_get(debug, ("actors", "chaser", "action", "source")),
    )
    fingerprint = {
        "game_id": _first_nonempty(debug.get("gameId"), state.get("gameId")),
        "scenario_id": _first_nonempty(
            sidebar.get("scenario-select"),
            debug.get("scenarioId"),
        ),
        "simulation_epoch": _first_nonempty(
            debug.get("simulationEpoch"),
            _nested_get(debug, ("frameIdentity", "simulationEpoch")),
        ),
        "playback": playback or None,
        "control_source": control_source,
        "control_input": control_input,
    }
    unknown_fields = [
        field_name
        for field_name in PASSIVE_SESSION_FIELDS
        if fingerprint.get(field_name) is None
    ]
    return {
        "schema": "chase_session_fingerprint_v1",
        **fingerprint,
        "unknown_fields": unknown_fields,
    }


def compare_chase_session_fingerprints(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    field_names: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Compare session fingerprints.

    When field_names is set (continuous runs in every mode), only those fields
    are compared so playback/input may advance as observed state.
    """

    compared_fields = field_names or PASSIVE_SESSION_FIELDS
    unknown = sorted(
        {
            *(
                str(item)
                for item in before.get("unknown_fields", [])
                if isinstance(item, str) and item in compared_fields
            ),
            *(
                str(item)
                for item in after.get("unknown_fields", [])
                if isinstance(item, str) and item in compared_fields
            ),
        }
    )
    changed = [
        field_name
        for field_name in compared_fields
        if before.get(field_name) != after.get(field_name)
    ]
    return {
        "preserved": not unknown and not changed,
        "compared_fields": list(compared_fields),
        "unknown_fields": unknown,
        "changed_fields": changed,
        "before": before,
        "after": after,
    }


def _play_sidebar_values(state: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    sections = state.get("playSidebarSections")
    if not isinstance(sections, list):
        return values
    for section in sections:
        if not isinstance(section, dict):
            continue
        rows = section.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            if isinstance(row_id, str) and "value" in row:
                values[row_id] = row.get("value")
    return values


def _nested_get(record: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_dict(*values: Any) -> dict[str, Any] | None:
    return next((value for value in values if isinstance(value, dict)), None)


def _bounded_input(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    source = _first_nonempty(value.get("source"))
    steering = value.get("steering")
    if (
        source is None
        or isinstance(steering, bool)
        or not isinstance(steering, (int, float))
        or not math.isfinite(float(steering))
    ):
        return None
    motion = _first_nonempty(value.get("motion"))
    forward = value.get("forward")
    reverse = value.get("reverse")
    if motion is None and (
        not isinstance(forward, bool) or not isinstance(reverse, bool)
    ):
        return None
    result: dict[str, Any] = {
        "source": source,
        "steering": float(steering),
    }
    if motion is not None:
        result["motion"] = motion
    if isinstance(forward, bool) and isinstance(reverse, bool):
        result["forward"] = forward
        result["reverse"] = reverse
    return result
