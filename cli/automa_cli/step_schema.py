"""A step runner's ``describe_schema()`` as the info commands print it.

``vehicles info perception``, ``vehicles info memory`` and ``vehicles info
decision`` (for the proposal step) put a step's schema in their JSON under
``<step>_schema``, with ``<step>_schema_source`` naming the runner. Their text
prints it in the same sections, in this order: Inputs, Plugins, Output,
Composition, Failure policy. A field the step's schema does not report is left
out.
"""

from __future__ import annotations

import json
from typing import Any


def schema_source(runner: str) -> dict[str, str]:
    """The ``<step>_schema_source`` entry for a runner's ``module:Class``."""

    return {"kind": "runner_method", "method": "describe_schema", "runner": runner}


def format_schema(source: Any, schema: dict[str, Any]) -> list[str]:
    runner = source.get("runner") if isinstance(source, dict) else None
    return [f"Schema source: {runner or 'unknown'}.describe_schema()", *_sections(schema)]


def _sections(schema: dict[str, Any]) -> list[str]:
    lines = ["", "Inputs:"]
    for item in _list(schema.get("inputs")):
        if not isinstance(item, dict):
            continue
        label = item.get("name") or item.get("feed_id") or "unknown"
        required = "required" if item.get("required") else "optional"
        lines.append(f"- {label} ({required})")
        required_by = item.get("required_by")
        if isinstance(required_by, list) and required_by:
            lines.append(f"  requested by: {', '.join(map(str, required_by))}")
        if item.get("source"):
            lines.append(f"  source: {item['source']}")
        if item.get("missing_behavior"):
            lines.append(f"  missing: {item['missing_behavior']}")

    plugins = [plugin for plugin in _list(schema.get("plugins")) if isinstance(plugin, dict)]
    if plugins:
        lines.extend(["", "Plugins:"])
        for plugin in plugins:
            lines.append(f"- {plugin.get('plugin_id', 'unknown')} [{plugin.get('spec', 'unknown')}]")
            contract = plugin.get("contract")
            if isinstance(contract, dict):
                feeds = [
                    str(item.get("feed_id", "unknown"))
                    for item in _list(contract.get("inputs"))
                    if isinstance(item, dict)
                ]
                lines.append(
                    f"  contract: {contract.get('state_mode', 'unknown')} "
                    f"feeds={', '.join(feeds) or 'none'}"
                )
            if plugin.get("evidence_key"):
                lines.append(f"  evidence_key: {plugin['evidence_key']}")

    output = schema.get("output") if isinstance(schema.get("output"), dict) else {}
    lines.extend(["", "Output:", f"- schema: {output.get('schema', 'unknown')}"])
    for key, value in output.items():
        if key == "schema":
            continue
        if isinstance(value, str):
            lines.append(f"- {key}: {value}")
        elif isinstance(value, list) and value:
            lines.append(f"- {key}:")
            lines.extend(f"  - {_format_item(item)}" for item in value)

    for title, key in (("Composition", "composition"), ("Failure policy", "failure_policy")):
        values = schema.get(key) if isinstance(schema.get(key), dict) else {}
        if values:
            lines.extend(["", f"{title}:"])
            lines.extend(f"- {name}: {value}" for name, value in values.items())
    return lines


def _format_item(item: Any) -> str:
    if isinstance(item, dict) and isinstance(item.get("record"), str):
        meaning = item.get("meaning")
        return f"{item['record']} - {meaning}" if isinstance(meaning, str) else item["record"]
    if isinstance(item, str):
        return item
    return json.dumps(item, sort_keys=True)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
