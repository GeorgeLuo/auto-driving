"""Text and JSON for plugin catalog listings, changes, and saves."""

from __future__ import annotations

import json
from typing import Any

from autonomy.decision_cycle.activation import STEPS

from ...hosts.step_formatting import step_lines
from ...providers.metadata import PROVIDERS
from ...shared.paths import display_path
from ...shared.text import automa_command
from .models import Arming, Definition, ListingResult, Receipt, Recovery, SaveResult, SelectionResult, UploadResult

_SELECTION = {
    "arm": ("automa_plugin_arm", "Arming", "Plugin selection loaded; the host applies it at its next cycle."),
    "disarm": ("automa_plugin_disarm", "Disarming",
               "Plugin deselection prepared; the host applies it at its next cycle."),
}


def render_listing(result: ListingResult, *, json_output: bool) -> str:
    if json_output:
        return _json(_listing_document(result))
    return "\n".join(_listing_lines(result))


def render_upload(result: UploadResult, *, json_output: bool) -> str:
    """An upload receipt; after ``--arm`` it reports the arming, with the upload under ``upload``."""

    upload, armed = result.upload, result.arm
    last = armed or upload
    if armed is not None:
        label, state = "Arming", armed.status
        message = "Plugin uploaded and loaded; the host applies it at its next cycle."
        answer = {**upload.body, **armed.body, "upload": upload.body}
    else:
        label, state = "Upload", "uploaded" if upload.ok else "failed"
        message = "Plugin uploaded and available; selection unchanged."
        answer = upload.body
    message = message if last.ok else _reason(last)
    recovery = _recovery_text(result.recovery)
    if json_output:
        status = state if armed is not None or not upload.ok else "ok"
        return _json(_receipt_document(answer, "automa_plugin_upload", result.host_name, result.url, result.step,
                                       status, message, recovery))
    lines = [*_target_lines(result.host_name, result.url, result.step), f"{label}: {state}"]
    if armed is not None:
        lines.extend([f"Upload: {upload.status}", *_uploaded_lines(result.plugin_id, upload)])
    if not last.ok:
        lines.append(f"Reason: {message}")
    elif armed is None:
        lines.extend([*_uploaded_lines(result.plugin_id, upload), "Selection: unchanged"])
    return "\n".join([*lines, *_arming_lines(last.arming), *_next_lines(recovery)])


def render_selection(result: SelectionResult, *, json_output: bool) -> str:
    """An arm or disarm receipt: the host's answer and the selection it reports."""

    receipt = result.receipt
    schema, label, message = _SELECTION[result.operation]
    message = message if receipt.ok else _reason(receipt)
    recovery = _recovery_text(result.recovery)
    if json_output:
        return _json({**_receipt_document(receipt.body, schema, result.host_name, result.url, result.step,
                                          receipt.status, message, recovery), "preset": result.preset})
    lines = [*_target_lines(result.host_name, result.url, result.step),
             *([f"Preset: {result.preset}"] if result.preset else []), f"{label}: {receipt.status}"]
    if not receipt.ok:
        lines.append(f"Reason: {message}")
    return "\n".join([*lines, *_arming_lines(receipt.arming), *_next_lines(recovery)])


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
            return _json(payload)
        return "\n".join([f"Host: {result.host_name}", "Save: failed", f"Reason: {result.error}"])
    if json_output:
        return _json(payload)
    selections = payload["steps"]
    rows = step_lines({name: entry["plugins"] for name, entry in selections.items()},
                      {name: entry["preset"] for name, entry in selections.items() if entry["preset"]})
    host_name = result.host_name
    if PROVIDERS[result.provider].local_host:
        startup = f"{host_name} starts with these plugins; hosts update keeps the saved steps."
    else:
        startup = (f"{host_name} starts with these plugins once they ship: "
                   f"{automa_command('hosts', 'update', '--name', host_name)}")
    return "\n".join([
        f"Host: {host_name}",
        *(f"{row} ({'saved' if entry['saved'] else 'default'})" for row, entry in zip(rows, selections.values())),
        f"Activations: {display_path(result.runtime_dir)}",
        f"Startup: {startup}",
    ])


def _json(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True)


def _receipt_document(answer: dict[str, Any], schema: str, host_name: str | None, url: str | None,
                      step: str | None, status: str, message: str, recovery: str | None) -> dict[str, Any]:
    """The host's answer as sent, with the target and the outcome the command reports."""

    return {**answer, "schema": schema, "host_name": host_name, "url": url, "step": step,
            "outcome": {"status": status, "message": message, "recovery": recovery}}


def _listing_document(result: ListingResult) -> dict[str, Any]:
    document: dict[str, Any] = {"schema": "automa_plugin_list", "host_name": result.host_name,
                                "url": result.url, "step": result.step, "ok": result.ok}
    if result.packaged:
        document.update(defaults=result.defaults, step_presets=result.step_presets)
    else:
        document.update(armed=result.armed, arming_error=result.arming_error)
    if result.catalog is not None:
        catalog: dict[str, Any] = {"source": result.catalog.source, "plugins": result.catalog.plugins}
        if not result.packaged:
            catalog.update(version=result.catalog.version, host_run_id=result.catalog.host_run_id)
        document["catalog"] = catalog
    if result.error is not None:
        document["error"] = result.error
    return document


def _listing_lines(result: ListingResult) -> list[str]:
    host_name = result.host_name
    if result.packaged:
        marked = {(name, plugin_id, None) for name, plugin_ids in result.defaults.items() for plugin_id in plugin_ids}
        return [f"Catalog: {result.catalog.source}", *_plugin_rows(result.catalog.plugins, marked, "default"),
                *_preset_rows(result.step_presets)]
    lines = [f"Host: {host_name}"] if host_name else []
    if not result.ok:
        lines.extend(["Catalog: unavailable", f"Reason: {result.error}"])
        return [*lines, *_next_lines(_recovery_text(Recovery(kind="endpoint", url=result.url, host_name=host_name)))]
    armed = result.armed or []
    if result.catalog is not None:
        lines.extend([f"Catalog: {result.url}", _catalog_version(result.catalog.version, result.catalog.host_run_id),
                      *_plugin_rows(_armed_first(result.catalog.plugins, armed),
                                    {_plugin_key(plugin) for plugin in armed}, "armed")])
    else:
        lines.extend([*([] if host_name else [f"Catalog: {result.url}"]), "Armed:", *_plugin_rows(armed)])
    if result.arming_error:
        lines.append(f"Arming error: {result.arming_error}")
    return lines


def _preset_rows(step_presets: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    """One ``- step/preset: plugins`` row per packaged preset, the step's default marked."""

    rows = []
    for step, named in step_presets.items():
        for name, preset in named.items():
            notes = [*(["default"] if preset["default"] else []),
                     *(f"configures {plugin_id}" for plugin_id in preset.get("plugin_configs", {}))]
            rows.append(f"- {step}/{name}: {', '.join(preset['plugins'])}"
                        + (f" ({'; '.join(notes)})" if notes else ""))
    return ["Presets:", *rows] if rows else []


def _target_lines(host_name: str | None, url: str | None, step: str | None) -> list[str]:
    return [line for value, line in ((host_name, f"Host: {host_name}"), (url, f"Endpoint: {url}"),
                                     (step, f"Step: {step}")) if value]


def _uploaded_lines(plugin_id: str, upload: Receipt) -> list[str]:
    return [f"Plugin: {plugin_id}", f"Revision: {upload.revision}",
            _catalog_version(upload.catalog_version, upload.host_run_id)]


def _arming_lines(arming: Arming | None) -> list[str]:
    """The request a host is on, then each step's requested and applied definitions."""

    if arming is None:
        return []
    lines = [] if arming.request_id is None else [f"Request: {arming.request_id}"]
    if arming.generation_id:
        lines.append(f"Decision generation: {arming.generation_id}")
    for label, by_step in (("Requested", arming.requested), ("Applied", arming.applied)):
        for step, definitions in by_step.items():
            selected = ", ".join(f"{item['id']} ({_metadata(item).get('revision', 'packaged')})" for item in definitions)
            lines.append(f"{label} {step}: {selected or '(none)'}")
    return lines


def _reason(receipt: Receipt) -> str:
    return receipt.error or "Catalog request failed."


def _next_lines(recovery: str | None) -> list[str]:
    return [f"Next: {recovery}"] if recovery else []


def _metadata(plugin: Definition) -> dict[str, Any]:
    return plugin.get("metadata") or {}


def _plugin_key(plugin: Definition) -> tuple[str, str, str | None]:
    return plugin["step"], plugin["id"], _metadata(plugin).get("revision")


def _armed_first(catalog: list[Definition], armed: list[Definition]) -> list[Definition]:
    """Each step's armed definitions in run order, then its other catalog entries, newer revisions of armed IDs first."""

    ordered = []
    keys = {_plugin_key(plugin) for plugin in armed}
    for name in STEPS:
        armed_ids = {plugin["id"] for plugin in armed if plugin["step"] == name}
        ordered += [plugin for plugin in armed if plugin["step"] == name]
        ordered += sorted((plugin for plugin in catalog if plugin["step"] == name and _plugin_key(plugin) not in keys),
                          key=lambda plugin: (plugin["id"] not in armed_ids, plugin["id"]))
    return ordered


def _plugin_rows(plugins: list[Definition], marked: set[tuple[str, str, str | None]] = frozenset(),
                 label: str = "") -> list[str]:
    """One ``- step/id: source`` row per definition, with ``(label)`` on the ``marked`` step, ID and revision keys."""

    rows = []
    for plugin in plugins:
        metadata = _metadata(plugin)
        revision = metadata.get("revision")
        source = f"revision {revision} ({metadata.get('filename', 'uploaded')})" if revision else "packaged"
        if _plugin_key(plugin) in marked:
            source += f" ({label})"
        rows.append(f"- {plugin['step']}/{plugin['id']}: {source}")
    return rows or ["(none)"]


def _catalog_version(version: int | None, host_run_id: str | None) -> str:
    return f"Catalog version: {version}" + (f" (host run {host_run_id})" if host_run_id else "")


def _target_args(url: str | None, host_name: str | None) -> tuple[str, str]:
    return ("--url", url) if url else ("--host-name", host_name)


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
        retry = automa_command("plugins", "list", *_target_args(recovery.url, recovery.host_name), "--long")
        return f"Check that the catalog host at {recovery.url} is running, then retry {retry}"
    return automa_command(
        "plugins", "list", *_target_args(recovery.url, recovery.host_name),
        *(("--step", recovery.step) if recovery.step else ()), *(("--long",) if recovery.long else ()),
    )
