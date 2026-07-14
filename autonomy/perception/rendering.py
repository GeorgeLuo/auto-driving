from __future__ import annotations

from typing import Any

from .evidence import PerceivedThing, PerceptionSignal


def signal_line(signal: PerceptionSignal) -> str:
    properties = _property_text(signal.properties)
    suffix = f" {properties}" if properties else ""
    source = f" plugin={signal.source_plugin_id}" if signal.source_plugin_id else ""
    return (
        f"signal id={signal.signal_id}{source} value={_value_text(signal.value)} "
        f"confidence={signal.confidence:.3f}{suffix}"
    )


def thing_line(thing: PerceivedThing) -> str:
    bbox = thing.location.bbox_xyxy_norm
    bbox_text = "none" if bbox is None else ",".join(f"{value:.4f}" for value in bbox)
    properties = _property_text(thing.properties)
    suffix = f" {properties}" if properties else ""
    source = f" plugin={thing.source_plugin_id}" if thing.source_plugin_id else ""
    return (
        f"thing id={thing.thing_id}{source} kind={thing.kind} "
        f"zone={thing.location.zone} frame={thing.location.frame} "
        f"bbox_xyxy_norm={bbox_text} confidence={thing.confidence:.3f}{suffix}"
    )


def _property_text(properties: dict[str, Any]) -> str:
    return " ".join(
        f"{key}={_value_text(value)}"
        for key, value in sorted(properties.items())
        if isinstance(value, (str, int, float, bool)) or value is None
    )


def _value_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "none"
    return str(value)
