from __future__ import annotations

from typing import Any

from autonomy.decision_cycle.confidence import confidence_text
from autonomy.decision_cycle.perception.evidence.values import PerceivedThing, PerceptionSignal


def text_for_stored_perception(perception: dict[str, Any] | None) -> str:
    """Render one stored perception record. Recordings do not keep lines or text."""

    if not isinstance(perception, dict):
        return ""
    from autonomy.decision_cycle.perception.interface import PerceptionPluginRun

    runs = tuple(
        PerceptionPluginRun.from_dict(item)
        for item in perception.get("plugin_runs") or ()
        if isinstance(item, dict)
    )
    signals = tuple(
        PerceptionSignal.from_dict(item)
        for item in perception.get("signals") or ()
        if isinstance(item, dict)
    )
    things = tuple(
        PerceivedThing.from_dict(item)
        for item in perception.get("things") or ()
        if isinstance(item, dict)
    )
    return render_perception_text(
        schema=str(perception.get("schema") or ""),
        plugin_id=str(perception.get("plugin_id") or ""),
        plugin_runs=runs,
        signals=signals,
        things=things,
    )


def render_perception_text(
    *,
    schema: str,
    plugin_id: str,
    plugin_runs: tuple[Any, ...] | list[Any],
    signals: tuple[Any, ...] | list[Any],
    things: tuple[Any, ...] | list[Any],
) -> str:
    """The text view of one perception result. It is not stored with the evidence."""

    lines = [
        f"schema={schema}",
        f"plugin={plugin_id}",
        "plugins=" + ",".join(
            str(getattr(run, "plugin_id", "")) for run in plugin_runs
        ),
    ]
    signals_by_plugin: dict[str, list[Any]] = {}
    things_by_plugin: dict[str, list[Any]] = {}
    for signal in signals:
        signals_by_plugin.setdefault(getattr(signal, "source_plugin_id", None), []).append(signal)
    for thing in things:
        things_by_plugin.setdefault(getattr(thing, "source_plugin_id", None), []).append(thing)
    for run in plugin_runs:
        catalog_id = getattr(run, "plugin_id", "")
        run_signals = signals_by_plugin.pop(catalog_id, [])
        run_things = things_by_plugin.pop(catalog_id, [])
        lines.append(
            f"plugin_run id={catalog_id} status={getattr(run, 'status', '')} "
            f"duration_ms={float(getattr(run, 'duration_ms', 0.0)):.3f} "
            f"signals={len(run_signals)} things={len(run_things)}"
        )
        error = getattr(run, "error", None)
        if error:
            lines.append(
                f"plugin_status id={catalog_id} status={getattr(run, 'status', '')} "
                f"detail={_value_text(error)}"
            )
        lines.extend(signal_line(signal) for signal in run_signals)
        lines.extend(thing_line(thing) for thing in run_things)
    for signal in [item for group in signals_by_plugin.values() for item in group]:
        lines.append(signal_line(signal))
    for thing in [item for group in things_by_plugin.values() for item in group]:
        lines.append(thing_line(thing))
    return "\n".join(lines)


def signal_line(signal: PerceptionSignal) -> str:
    properties = _property_text(signal.properties)
    suffix = f" {properties}" if properties else ""
    source = f" plugin={signal.source_plugin_id}" if signal.source_plugin_id else ""
    return (
        f"signal id={signal.signal_id}{source} value={_value_text(signal.value)} "
        f"confidence={confidence_text(signal.confidence)}{suffix}"
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
        f"bbox_xyxy_norm={bbox_text} confidence={confidence_text(thing.confidence)}{suffix}"
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
