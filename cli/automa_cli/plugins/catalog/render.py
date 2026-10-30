"""Text and JSON for plugin catalog listings, changes, and saves."""

from __future__ import annotations

import json
import shlex
from typing import Any

from autonomy.decision_cycle.activation import STEPS

from ...hosts.step_formatting import step_lines
from ...providers.metadata import PROVIDERS
from ...shared.paths import display_path
from .models import ListingResult, MutationResult, Recovery, SaveResult


def render_listing(result: ListingResult, *, json_output: bool) -> str:
    payload = result.payload
    if json_output:
        return json.dumps(payload, indent=2, sort_keys=True)
    return "\n".join(_listing_lines(payload))


def render_mutation(result: MutationResult, *, json_output: bool) -> str:
    response = result.response
    ok = bool(response.get("ok"))
    operation = result.operation
    recovery = _recovery_text(result.recovery)
    if operation == "arm":
        label, state = "Arming", response.get("status", "failed")
        message = "Plugin selection loaded; the host applies it at its next cycle."
    elif operation == "disarm":
        label, state = "Disarming", response.get("status", "failed")
        message = "Plugin deselection prepared; the host applies it at its next cycle."
    elif response.get("upload"):
        label, state = "Arming", response.get("status", "failed")
        message = "Plugin uploaded and loaded; the host applies it at its next cycle."
    else:
        label, state = "Upload", "uploaded" if ok else "failed"
        message = "Plugin uploaded and available; selection unchanged."
    if not ok:
        message = response.get("error", "Catalog request failed.")
    body = {
        **response,
        "schema": {"upload": "automa_plugin_upload", "arm": "automa_plugin_arm", "disarm": "automa_plugin_disarm"}[operation],
        "host_name": result.host_name, "url": result.url, "step": result.step,
        "outcome": {
            "status": state if operation in ("arm", "disarm") or response.get("upload") else "ok" if ok else state,
            "message": message, "recovery": recovery,
        },
    }
    if json_output:
        return json.dumps(body, indent=2, sort_keys=True)
    lines = [f"Host: {result.host_name}"] if result.host_name else []
    if result.url:
        lines.append(f"Endpoint: {result.url}")
    if result.step:
        lines.append(f"Step: {result.step}")
    lines.append(f"{label}: {state}")
    if body.get("upload"):
        lines.extend([f"Upload: {body['upload']['status']}", f"Plugin: {result.plugin_id}",
                      f"Revision: {body['plugin']['metadata']['revision']}",
                      _catalog_version(body)])
        if not ok:
            lines.append(f"Reason: {message}")
    elif not ok:
        lines.append(f"Reason: {message}")
    elif operation == "upload":
        lines.extend([
            f"Plugin: {result.plugin_id}", f"Revision: {body['plugin']['metadata']['revision']}",
            _catalog_version(body), "Selection: unchanged",
        ])
    arming = body.get("arming")
    if arming:
        lines.append(f"Request: {arming['request_id']}")
        for name in ("requested", "applied"):
            for selected_step, definitions in arming[name].items():
                selected = ", ".join(f"{item['id']} ({item['metadata'].get('revision', 'packaged')})" for item in definitions)
                lines.append(f"{name.capitalize()} {selected_step}: {selected or '(none)'}")
    if recovery:
        lines.append(f"Next: {recovery}")
    return "\n".join(lines)


def render_save(result: SaveResult, *, json_output: bool) -> str:
    payload: dict[str, Any] = {
        "schema": "automa_plugin_save", "host_name": result.host_name, "step": result.step,
        "url": result.url, "ok": result.ok, "steps": None,
    }
    if result.steps is not None:
        payload["steps"] = {
            name: {"activation": display_path(entry.activation), "preset": entry.preset,
                   "plugins": list(entry.plugins), "saved": entry.saved}
            for name, entry in result.steps.items()
        }
    if not result.ok:
        payload["error"] = result.error
        if json_output:
            return json.dumps(payload, indent=2, sort_keys=True)
        return "\n".join([f"Host: {result.host_name}", "Save: failed", f"Reason: {result.error}"])
    if json_output:
        return json.dumps(payload, indent=2, sort_keys=True)
    selections = payload["steps"]
    rows = step_lines({name: entry["plugins"] for name, entry in selections.items()},
                      {name: entry["preset"] for name, entry in selections.items() if entry["preset"]})
    host_name = result.host_name
    if PROVIDERS[result.provider].local_host:
        startup = f"{host_name} starts with these plugins; hosts update keeps the saved steps."
    else:
        startup = (f"{host_name} starts with these plugins once they ship: "
                   f"./cli/automa hosts update --name {shlex.quote(host_name)}")
    return "\n".join([
        f"Host: {host_name}",
        *(f"{row} ({'saved' if entry['saved'] else 'default'})" for row, entry in zip(rows, selections.values())),
        f"Activations: {display_path(result.runtime_dir)}",
        f"Startup: {startup}",
    ])


def _listing_lines(payload: dict[str, Any]) -> list[str]:
    host_name = payload["host_name"]
    if payload.get("defaults") is not None and payload["ok"]:
        marked = {(name, plugin_id, None) for name, plugin_ids in payload["defaults"].items() for plugin_id in plugin_ids}
        return [f"Catalog: {payload['catalog']['source']}",
                *_plugin_rows(payload["catalog"]["plugins"], marked, "default")]
    lines = [f"Host: {host_name}"] if host_name else []
    if not payload["ok"]:
        lines.extend(["Catalog: unavailable", f"Reason: {payload['error']}"])
        recovery = _recovery_text(Recovery(kind="endpoint", url=payload["url"], host_name=host_name))
        return [*lines, f"Next: {recovery}"] if recovery else lines
    armed = payload["armed"] or []
    if "catalog" in payload:
        lines.extend([f"Catalog: {payload['url']}", _catalog_version_fields(payload["catalog"]),
                      *_plugin_rows(_armed_first(payload["catalog"]["plugins"], armed),
                                    {_plugin_key(plugin) for plugin in armed}, "armed")])
    else:
        lines.extend([*([] if host_name else [f"Catalog: {payload['url']}"]), "Armed:", *_plugin_rows(armed)])
    if payload["arming_error"]:
        lines.append(f"Arming error: {payload['arming_error']}")
    return lines


def _plugin_key(plugin: dict[str, Any]) -> tuple[str, str, str | None]:
    return plugin["step"], plugin["id"], plugin.get("metadata", {}).get("revision")


def _armed_first(catalog: list[dict[str, Any]], armed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each step's armed definitions in run order, then its other catalog entries, newer revisions of armed IDs first."""

    ordered = []
    keys = {_plugin_key(plugin) for plugin in armed}
    for name in STEPS:
        armed_ids = {plugin["id"] for plugin in armed if plugin["step"] == name}
        ordered += [plugin for plugin in armed if plugin["step"] == name]
        ordered += sorted((plugin for plugin in catalog if plugin["step"] == name and _plugin_key(plugin) not in keys),
                          key=lambda plugin: (plugin["id"] not in armed_ids, plugin["id"]))
    return ordered


def _plugin_rows(plugins: list[dict[str, Any]], marked: set[tuple[str, str, str | None]] = frozenset(),
                 label: str = "") -> list[str]:
    """One ``- step/id: source`` row per definition, with ``(label)`` on the ``marked`` step, ID and revision keys."""

    rows = []
    for plugin in plugins:
        metadata = plugin.get("metadata", {})
        revision = metadata.get("revision")
        source = f"revision {revision} ({metadata.get('filename', 'uploaded')})" if revision else "packaged"
        if _plugin_key(plugin) in marked:
            source += f" ({label})"
        rows.append(f"- {plugin['step']}/{plugin['id']}: {source}")
    return rows or ["(none)"]


def _catalog_version(result: dict[str, Any]) -> str:
    host_run = result.get("host_run_id")
    return f"Catalog version: {result['catalog_version']}" + (f" (host run {host_run})" if host_run else "")


def _catalog_version_fields(catalog: dict[str, Any]) -> str:
    """The version line for a listing, whose JSON stores the version under ``catalog``."""

    return _catalog_version({"catalog_version": catalog["version"], "host_run_id": catalog.get("host_run_id")})


def _target_args(url: str | None, host_name: str | None) -> str:
    return f"--url {shlex.quote(url)}" if url else f"--host-name {shlex.quote(host_name)}"


def _recovery_text(recovery: Recovery) -> str | None:
    if recovery.kind == "config":
        return "Pass --config as a JSON object of the plugin's constructor settings."
    if recovery.kind == "file":
        return f"Check the local plugin file: {recovery.file}"
    if recovery.kind == "rejected":
        return f"Fix {recovery.file}, then upload it again."
    if recovery.kind == "endpoint":
        # The error already says why the vehicle's host cannot be reached.
        if recovery.host_name:
            return None
        return (f"Check that the catalog host at {recovery.url} is running, then retry "
                f"./cli/automa plugins list {_target_args(recovery.url, recovery.host_name)} --long")
    command = f"./cli/automa plugins list {_target_args(recovery.url, recovery.host_name)}"
    if recovery.step:
        command += f" --step {recovery.step}"
    if recovery.long:
        command += " --long"
    return command
