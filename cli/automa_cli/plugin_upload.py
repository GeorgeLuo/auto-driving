"""CLI and HTTP transport for a live catalog, independent of run state."""

from __future__ import annotations

import base64
import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from autonomy.runtime.plugin_catalog import PLUGIN_CATALOG_PATH


class PluginCatalogClient:
    def __init__(self, url: str, *, timeout_s: float = 5.0) -> None:
        self.url = url.rstrip("/")
        if not self.url.endswith(PLUGIN_CATALOG_PATH):
            self.url += PLUGIN_CATALOG_PATH
        self.timeout_s = timeout_s

    def __call__(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                return json.load(response)
        except HTTPError as exc:
            with exc:
                try:
                    return json.load(exc)
                except (ValueError, UnicodeError):
                    raise RuntimeError(f"catalog request failed: HTTP {exc.code}") from exc


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
    from .automation import RUNTIME_ROOT, _staged_onboard_vehicle
    from .bundles import controller_bundle_paths
    from .paths import safe_path_part
    from .perception_view import VIEW_RECORD_NAME

    bundle = controller_bundle_paths(RUNTIME_ROOT / safe_path_part(vehicle_id))
    record = Path(bundle["runtime_dir"]) / "automation" / VIEW_RECORD_NAME
    if record.exists():
        view = json.loads(record.read_text())
        if view.get("available") and view.get("url"):
            return view["url"]
    vehicle = _staged_onboard_vehicle(vehicle_id)
    if vehicle is not None:
        return vehicle["connection"]["base_url"]
    raise RuntimeError(f"No live catalog endpoint for {vehicle_id}; use --url to address an existing host.")


def upload_plugin(*, url: str | None, vehicle_id: str | None, file: Path, step: str,
                  plugin_id: str, entrypoint: str, json_output: bool = False) -> CommandResult:
    recovery = f"Check the local plugin file: {file}"
    endpoint = url
    try:
        payload = {
            "step": step, "plugin_id": plugin_id, "entrypoint": entrypoint,
            "filename": file.name, "source_base64": base64.b64encode(file.read_bytes()).decode(),
        }
        recovery = _catalog_recovery(url, vehicle_id)
        endpoint = url or catalog_url(vehicle_id)
        result = PluginCatalogClient(endpoint)(payload)
        if not result.get("ok"):
            recovery = "./cli/automa vehicles plugins upload --help"
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    if result.get("ok"):
        recovery = f"./cli/automa vehicles plugins status {_target_args(url, vehicle_id)} --step {step}"
    return _result(result, operation="upload", url=endpoint, vehicle_id=vehicle_id,
                   step=step, plugin_id=plugin_id, recovery=recovery, json_output=json_output)


def list_plugins(*, url: str | None, vehicle_id: str | None, step: str | None = None,
                 json_output: bool = False) -> CommandResult:
    endpoint = url
    try:
        endpoint = url or catalog_url(vehicle_id)
        result = PluginCatalogClient(endpoint)()
        if result.get("ok") and step is not None:
            result["plugins"] = [plugin for plugin in result["plugins"] if plugin["step"] == step]
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
    if operation == "upload":
        label, state = "Upload", "uploaded" if ok else "failed"
        message = "Plugin uploaded and available; selection unchanged."
    else:
        label, state = "Catalog", "available" if ok else "unavailable"
        message = "Live plugin catalog is available."
    if not ok:
        message = result.get("error", "Catalog request failed.")
    result = {
        **result,
        "schema": "automa_plugin_upload_v0" if operation == "upload" else "automa_plugin_catalog_v0",
        "vehicle_id": vehicle_id, "url": url, "step": step,
        "outcome": {"status": "ok" if ok else state, "message": message, "recovery": recovery},
    }
    if json_output:
        return CommandResult(0 if ok else 1, json.dumps(result, indent=2, sort_keys=True))
    lines = [f"Vehicle: {vehicle_id}"] if vehicle_id else []
    if url:
        lines.append(f"Endpoint: {url}")
    if step:
        lines.append(f"Step: {step}")
    lines.append(f"{label}: {state}")
    if not ok:
        lines.append(f"Reason: {message}")
    elif operation == "upload":
        lines.extend([
            f"Plugin: {plugin_id}", f"Revision: {result['plugin']['metadata']['revision']}",
            f"Catalog version: {result['catalog_version']}", "Selection: unchanged",
        ])
    else:
        lines.extend([f"Catalog version: {result['catalog_version']}", "Available plugins:"])
        entries = []
        for plugin in result["plugins"]:
            metadata = plugin.get("metadata", {})
            revision = metadata.get("revision")
            source = f"revision {revision} ({metadata.get('filename', 'uploaded')})" if revision else "packaged"
            entries.append(f"- {plugin['step']}/{plugin['id']}: {source}")
        lines.extend(entries or ["(none)"])
    if recovery:
        lines.append(f"Next: {recovery}")
    return CommandResult(0 if ok else 1, "\n".join(lines))
