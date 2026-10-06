"""A staged step and its runner's ``describe_schema()`` as the info commands report them.

``vehicles info perception``, ``vehicles info memory`` and ``vehicles info
decision`` (for the proposal step) read the step's staged activation and load
its runner from the controller bundle with ``staged_step_info``. Their JSON
carries its ``activation`` and ``controller_bundle`` records and the step's
schema under ``<step>_schema``, with ``<step>_schema_source`` naming the
runner. ``format_staged_step`` prints those in the same lines for each step,
and ``format_schema`` prints the schema in the same sections, in this order:
Inputs, Plugins, Output, Composition, Failure policy. A field the step's
schema does not report is left out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import read_step_activation
from autonomy.decision_cycle.steps import STEP_RUNNERS

from .paths import display_path
from .step_activations import (
    CONTROLLER_BUNDLE_KEYS,
    bundle_activation_path,
    bundle_activation_problems,
    format_activation_problems,
)
from .step_hosting import load_staged_runner


def staged_step_info(
    bundle: dict[str, str], vehicle_id: str, step: str
) -> tuple[dict[str, Any] | None, str | None]:
    """The step's staged ``activation``, ``controller_bundle`` and schema entries.

    The runner is loaded from the controller bundle its activation records,
    as the worker loads it. Returns the entries, or ``None`` and the message
    that names what to fix.
    """

    path = bundle_activation_path(bundle, step)
    restage = f"Run: ./cli/automa vehicles update {step} --id <vehicle_id>"
    if not path.exists():
        return None, "\n".join([
            f"No active {step} activation found for {vehicle_id!r}.",
            f"Expected activation: {display_path(path)}",
            restage,
        ])
    problems = bundle_activation_problems(bundle, vehicle_id, steps=(step,))
    if problems:
        return None, format_activation_problems(problems)
    try:
        activation = read_step_activation(path, step)
    except (OSError, ValueError, TypeError) as exc:
        return None, f"Could not read {step} activation {display_path(path)}: {exc}"
    stored_bundle = activation.metadata.get("controller_bundle")
    if not isinstance(stored_bundle, dict):
        stored_bundle = {}
    bundle_root = stored_bundle.get("root_dir")
    if not isinstance(bundle_root, str) or not bundle_root:
        return None, f"Activation {display_path(path)} does not record its controller bundle."
    if not Path(bundle_root).exists():
        return None, "\n".join([
            f"Controller bundle is missing for {vehicle_id!r}: {display_path(Path(bundle_root))}",
            restage,
        ])

    def failure(verb: str, exc: Exception) -> str:
        return "\n".join([
            f"Could not {verb} active {step} for {vehicle_id!r}.",
            f"Plugins: {', '.join(activation.plugins) or '(none)'}",
            f"Reason: {type(exc).__name__}: {exc}",
        ])

    try:
        manager = activation.plugin_manager()
        runner = load_staged_runner(activation)
    except Exception as exc:  # noqa: BLE001 - staged plugins are third-party code
        return None, failure("load", exc)
    try:
        schema = runner.describe_schema()
    except Exception as exc:  # noqa: BLE001 - staged plugins are third-party code
        return None, failure("inspect", exc)
    available = manager.available
    return {
        "activation": {
            "path": display_path(path),
            "preset": activation.metadata.get("preset"),
            "plugins": list(manager.selected_ids),
            "available_plugins": sorted(item.plugin_id for item in available),
            "plugin_specs": {item.plugin_id: item.entrypoint for item in available},
            "plugin_configs": {item.plugin_id: dict(item.config) for item in available},
        },
        "controller_bundle": {key: stored_bundle.get(key) for key in CONTROLLER_BUNDLE_KEYS},
        f"{step}_schema_source": schema_source(step),
        f"{step}_schema": schema,
    }, None


def schema_source(step: str) -> dict[str, str]:
    """The ``<step>_schema_source`` entry: the step runner's ``module:Class``."""

    runner = STEP_RUNNERS[step]
    return {
        "kind": "runner_method",
        "method": "describe_schema",
        "runner": f"{runner.__module__}:{runner.__qualname__}",
    }


def format_staged_step(
    step: str, payload: dict[str, Any], *, view: str | None = None
) -> list[str]:
    """``staged_step_info``'s entries in ``payload`` as text; ``view`` follows the plugins."""

    activation = payload["activation"]
    bundle = payload.get("controller_bundle")
    bundle = bundle if isinstance(bundle, dict) else {}
    preset = activation.get("preset")
    lines = [
        f"{step.capitalize()}: {payload['vehicle_id']}" + (f" -> {preset}" if preset else ""),
        f"Enabled plugins: {', '.join(activation.get('plugins') or []) or 'none'}",
        f"Available plugins: {', '.join(activation.get('available_plugins') or []) or 'none'}",
        *([view] if view else []),
        f"Bundle: {bundle.get('root_dir') or 'unknown'}",
        f"Activation: {activation['path']}",
    ]
    release = bundle.get("release") if isinstance(bundle.get("release"), dict) else {}
    if release:
        lines.append(f"Release: {release.get('tree_sha256', 'unknown')}")
        if release.get("archive"):
            lines.append(f"Archive: {release['archive']}")
        if release.get("manifest"):
            lines.append(f"Release manifest: {release['manifest']}")
    else:
        lines.append(
            f"Release: not recorded; run `vehicles update {step}` "
            "to package and attach release metadata"
        )
    schema = payload.get(f"{step}_schema")
    if isinstance(schema, dict):
        lines.extend(format_schema(payload.get(f"{step}_schema_source"), schema))
    return lines


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
