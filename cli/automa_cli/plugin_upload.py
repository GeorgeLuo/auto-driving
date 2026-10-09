"""CLI and HTTP transport for a live catalog, independent of run state."""

from __future__ import annotations

import base64
import json
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
    try:
        payload = {
            "step": step, "plugin_id": plugin_id, "entrypoint": entrypoint,
            "filename": file.name, "source_base64": base64.b64encode(file.read_bytes()).decode(),
        }
        result = PluginCatalogClient(url or catalog_url(vehicle_id))(payload)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    if json_output:
        message = json.dumps(result, indent=2, sort_keys=True)
    elif result.get("ok"):
        revision = result["plugin"]["metadata"]["revision"]
        import shlex
        target = f"--url {shlex.quote(url)}" if url else f"--id {shlex.quote(vehicle_id)}"
        message = (
            f"Uploaded {step}/{plugin_id} revision {revision}; catalog {result['catalog_version']}. Available; selection unchanged.\n"
            f"Verify: automa vehicles plugins list {target} --step {step}"
        )
    else:
        message = f"Upload failed: {result.get('error', 'catalog rejected upload')}"
    return CommandResult(0 if result.get("ok") else 1, message)


def list_plugins(*, url: str | None, vehicle_id: str | None, step: str | None = None,
                 json_output: bool = False) -> CommandResult:
    try:
        result = PluginCatalogClient(url or catalog_url(vehicle_id))()
        if result.get("ok") and step is not None:
            result["plugins"] = [plugin for plugin in result["plugins"] if plugin["step"] == step]
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    if json_output:
        message = json.dumps(result, indent=2, sort_keys=True)
    elif result.get("ok"):
        entries = []
        for plugin in result["plugins"]:
            metadata = plugin.get("metadata", {})
            revision = metadata.get("revision")
            source = f"revision {revision} ({metadata.get('filename', 'uploaded')})" if revision else "packaged"
            entries.append(f"- {plugin['step']}/{plugin['id']}: {source}")
        message = "\n".join([
            f"Plugin catalog: {vehicle_id or url}", f"Catalog version: {result['catalog_version']}",
            "Available plugins:", *(entries or ["(none)"]),
        ])
    else:
        message = f"Catalog unavailable: {result.get('error', 'request failed')}"
    return CommandResult(0 if result.get("ok") else 1, message)
