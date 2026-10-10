"""CLI upload, arming and status, plus shared HTTP transport for live catalogs."""

from __future__ import annotations

import base64
import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from autonomy.runtime.plugin_catalog import PLUGIN_CATALOG_PATH

# Arming waits for the host's current cycle and for the selection to load.
ARM_TIMEOUT_S = 60.0


class PluginCatalogClient:
    def __init__(self, url: str, *, timeout_s: float = 5.0) -> None:
        self.url = url.rstrip("/")
        if not self.url.endswith(PLUGIN_CATALOG_PATH):
            self.url += PLUGIN_CATALOG_PATH
        self.timeout_s = timeout_s

    def __call__(self, payload: dict[str, Any] | None = None, *, timeout_s: float | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.url, data=data, headers={"Content-Type": "application/json"})
        timeout_s = self.timeout_s if timeout_s is None else timeout_s
        try:
            with urlopen(request, timeout=timeout_s) as response:
                return json.load(response)
        except HTTPError as exc:
            with exc:
                try:
                    return json.load(exc)
                except (ValueError, UnicodeError):
                    raise RuntimeError(f"catalog request failed: HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            if not isinstance(getattr(exc, "reason", exc), TimeoutError):
                raise
            raise TimeoutError(f"{self.url} did not answer within {timeout_s:g} s") from exc


def serve_plugin_catalog(handler: Any, api: Callable | None, *, include_body: bool = True) -> None:
    """Thin HTTP adapter shared by the runtime viewer and replay workbench."""

    if api is None:
        handler._send_json(503, {"ok": False, "error": "catalog unavailable"}, include_body=include_body)
        return
    try:
        payload = None
        if handler.command == "POST":
            size = int(handler.headers.get("Content-Length", "0"))
            if size < 0:
                raise ValueError("negative content length")
            payload = json.loads(handler.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("upload body must be an object")
        result = api(payload)
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    handler._send_json(200 if result.get("ok") else 400, result, include_body=include_body)


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


def catalog_url(vehicle_id: str) -> str:
    """The catalog of the vehicle's runtime host; every host serves ``/api/plugins``."""

    from .runtime_hosts import runtime_base_url

    return runtime_base_url(vehicle_id)


def upload_plugin(*, url: str | None, vehicle_id: str | None, file: Path, step: str,
                  plugin_id: str, entrypoint: str, arm: bool = False,
                  json_output: bool = False) -> CommandResult:
    recovery = f"Check the local plugin file: {file}"
    endpoint = url
    try:
        payload = {
            "step": step, "plugin_id": plugin_id, "entrypoint": entrypoint,
            "filename": file.name, "source_base64": base64.b64encode(file.read_bytes()).decode(),
        }
        recovery = _catalog_recovery(url, vehicle_id)
        endpoint = url or catalog_url(vehicle_id)
        client = PluginCatalogClient(endpoint)
        result = client(payload)
        if not result.get("ok"):
            recovery = f"Fix {file}, then upload it again."
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    if result.get("ok"):
        recovery = f"./cli/automa vehicles plugins status {_target_args(url, vehicle_id)} --step {step}"
        if arm:
            uploaded = result
            try:
                armed = _request_arm(client, {step: [plugin_id]})
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                armed = {"ok": False, "status": "failed", "error": str(exc)}
            result = {**uploaded, **armed, "upload": uploaded}
    return _result(result, operation="upload", url=endpoint, vehicle_id=vehicle_id,
                   step=step, plugin_id=plugin_id, recovery=recovery, json_output=json_output)


def _request_arm(client: PluginCatalogClient, selections: dict[str, Any]) -> dict[str, Any]:
    return client({"operation": "arm", "selections": selections}, timeout_s=ARM_TIMEOUT_S)


def arm_plugins(*, url: str | None, vehicle_id: str | None, step: str | None = None,
                plugins: list[str] | None = None, selection: Path | None = None,
                json_output: bool = False) -> CommandResult:
    endpoint = url
    recovery = f"Check the selection document: {selection}" if selection else _catalog_recovery(url, vehicle_id)
    try:
        selections = json.loads(selection.read_text()) if selection else {step: plugins}
        recovery = _catalog_recovery(url, vehicle_id)
        endpoint = url or catalog_url(vehicle_id)
        result = _request_arm(PluginCatalogClient(endpoint), selections)
        recovery = f"./cli/automa vehicles plugins status {_target_args(url, vehicle_id)}"
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    return _result(result, operation="arm", url=endpoint, vehicle_id=vehicle_id, step=step,
                   recovery=recovery, json_output=json_output)


def list_plugins(*, url: str | None, vehicle_id: str | None, step: str | None = None,
                 json_output: bool = False) -> CommandResult:
    endpoint = url
    try:
        endpoint = url or catalog_url(vehicle_id)
        result = PluginCatalogClient(endpoint)()
        if result.get("ok") and step is not None:
            result["plugins"] = [plugin for plugin in result["plugins"] if plugin["step"] == step]
            if result.get("arming"):
                for name in ("requested", "applied"):
                    result["arming"][name] = {step: result["arming"][name].get(step, [])}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    return _result(result, operation="status", url=endpoint, vehicle_id=vehicle_id, step=step,
                   recovery=None if result.get("ok") else _catalog_recovery(url, vehicle_id),
                   json_output=json_output)


def _target_args(url: str | None, vehicle_id: str | None) -> str:
    return f"--url {shlex.quote(url)}" if url else f"--id {shlex.quote(vehicle_id)}"


def _catalog_recovery(url: str | None, vehicle_id: str | None) -> str:
    if vehicle_id:
        return f"./cli/automa vehicles status --id {shlex.quote(vehicle_id)}"
    return f"Check that the catalog host at {url} is running, then retry ./cli/automa vehicles plugins status {_target_args(url, vehicle_id)}"


def _result(result: dict[str, Any], *, operation: str, url: str | None, vehicle_id: str | None,
            step: str | None, recovery: str | None, json_output: bool,
            plugin_id: str | None = None) -> CommandResult:
    """Render the same target, outcome and recovery in human and JSON output."""

    ok = bool(result.get("ok"))
    # A selection the host could not load fails status too, until an arm succeeds.
    exit_code = 0 if ok and (result.get("arming") or {}).get("status") != "failed" else 2
    if operation == "arm":
        label, state = "Arming", result.get("status", "failed")
        message = "Plugin selection loaded; the host applies it at its next cycle."
    elif result.get("upload"):
        label, state = "Arming", result.get("status", "failed")
        message = "Plugin uploaded and loaded; the host applies it at its next cycle."
    elif operation == "upload":
        label, state = "Upload", "uploaded" if ok else "failed"
        message = "Plugin uploaded and available; selection unchanged."
    else:
        label, state = "Catalog", "available" if ok else "unavailable"
        message = "Live plugin catalog is available."
    if not ok:
        message = result.get("error", "Catalog request failed.")
    result = {
        **result,
        "schema": {"upload": "automa_plugin_upload_v0", "arm": "automa_plugin_arm_v0", "status": "automa_plugin_catalog_v0"}[operation],
        "vehicle_id": vehicle_id, "url": url, "step": step,
        "outcome": {"status": state if operation == "arm" or result.get("upload") else "ok" if ok else state,
                    "message": message, "recovery": recovery},
    }
    if json_output:
        return CommandResult(exit_code, json.dumps(result, indent=2, sort_keys=True))
    lines = [f"Vehicle: {vehicle_id}"] if vehicle_id else []
    if url:
        lines.append(f"Endpoint: {url}")
    if step:
        lines.append(f"Step: {step}")
    lines.append(f"{label}: {state}")
    if result.get("upload"):
        lines.extend([f"Upload: {result['upload']['status']}", f"Plugin: {plugin_id}",
                      f"Revision: {result['plugin']['metadata']['revision']}",
                      f"Catalog version: {result['catalog_version']}"])
        if not ok:
            lines.append(f"Reason: {message}")
    elif not ok:
        lines.append(f"Reason: {message}")
    elif operation == "upload":
        lines.extend([
            f"Plugin: {plugin_id}", f"Revision: {result['plugin']['metadata']['revision']}",
            f"Catalog version: {result['catalog_version']}", "Selection: unchanged",
        ])
    elif operation == "status":
        lines.extend([f"Catalog version: {result['catalog_version']}", "Available plugins:"])
        entries = []
        for plugin in result["plugins"]:
            metadata = plugin.get("metadata", {})
            revision = metadata.get("revision")
            source = f"revision {revision} ({metadata.get('filename', 'uploaded')})" if revision else "packaged"
            entries.append(f"- {plugin['step']}/{plugin['id']}: {source}")
        lines.extend(entries or ["(none)"])
    arming = result.get("arming")
    if arming:
        lines.append(f"Request: {arming['request_id']}" if label == "Arming"
                     else f"Arming: {arming['status']} (request {arming['request_id']})")
        for name in ("requested", "applied"):
            for selected_step, definitions in arming[name].items():
                selected = ", ".join(f"{item['id']} ({item['metadata'].get('revision', 'packaged')})" for item in definitions)
                lines.append(f"{name.capitalize()} {selected_step}: {selected or '(none)'}")
        if ok and arming.get("error"):
            lines.append(f"Reason: {arming['error']}")
    if recovery:
        lines.append(f"Next: {recovery}")
    return CommandResult(exit_code, "\n".join(lines))
