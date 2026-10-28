"""CLI upload, arming, disarming and status against a host's live plugin catalog."""

from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from autonomy.decision_cycle.steps import CLEARABLE_STEPS
from autonomy.runtime.plugin_catalog import PLUGIN_CATALOG_PATH

from ..command_result import CommandResult

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


def catalog_url(host_name: str) -> str:
    """The host's catalog; every host serves ``/api/plugins``."""

    from .runtime_hosts import runtime_base_url

    return runtime_base_url(host_name)


def upload_plugin(*, url: str | None, host_name: str | None, file: Path, step: str,
                  plugin_id: str, entrypoint: str, config: str | None = None, arm: bool = False,
                  json_output: bool = False) -> CommandResult:
    recovery = "Pass --config as a JSON object of the plugin's constructor settings."
    endpoint = url
    try:
        # The host stores the config with this revision; arming constructs the plugin with it.
        settings = None
        if config is not None:
            try:
                settings = json.loads(config)
            except ValueError as exc:
                raise ValueError(f"--config is not valid JSON: {exc}") from exc
            if not isinstance(settings, dict):
                raise ValueError("--config must be a JSON object")
        recovery = f"Check the local plugin file: {file}"
        payload = {
            "step": step, "plugin_id": plugin_id, "entrypoint": entrypoint,
            "filename": file.name, "source_base64": base64.b64encode(file.read_bytes()).decode(),
        }
        if settings is not None:
            payload["config"] = settings
        recovery = _catalog_recovery(url, host_name)
        endpoint = url or catalog_url(host_name)
        client = PluginCatalogClient(endpoint)
        result = client(payload)
        if not result.get("ok"):
            recovery = f"Fix {file}, then upload it again."
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    if result.get("ok"):
        recovery = f"./cli/automa hosts plugins status {_target_args(url, host_name)} --step {step}"
        if arm:
            uploaded = result
            try:
                armed = _request_arm(client, {step: [plugin_id]})
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                armed = {"ok": False, "status": "failed", "error": str(exc)}
            result = {**uploaded, **armed, "upload": uploaded}
    return _result(result, operation="upload", url=endpoint, host_name=host_name,
                   step=step, plugin_id=plugin_id, recovery=recovery, json_output=json_output)


def _request_arm(client: PluginCatalogClient, selections: dict[str, Any]) -> dict[str, Any]:
    return client({"operation": "arm", "selections": selections}, timeout_s=ARM_TIMEOUT_S)


def arm_plugins(*, url: str | None, host_name: str | None, step: str | None = None,
                plugins: list[str] | None = None, selection: Path | None = None,
                json_output: bool = False) -> CommandResult:
    endpoint = url
    recovery = f"Check the selection document: {selection}" if selection else _catalog_recovery(url, host_name)
    try:
        selections = json.loads(selection.read_text()) if selection else {step: plugins}
        recovery = _catalog_recovery(url, host_name)
        endpoint = url or catalog_url(host_name)
        result = _request_arm(PluginCatalogClient(endpoint), selections)
        recovery = f"./cli/automa hosts plugins status {_target_args(url, host_name)}"
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    return _result(result, operation="arm", url=endpoint, host_name=host_name, step=step,
                   recovery=recovery, json_output=json_output)


def disarm_plugins(*, url: str | None, host_name: str | None, step: str | None = None,
                   plugins: list[str] | None = None, selection: Path | None = None,
                   all_plugins: bool = False, json_output: bool = False) -> CommandResult:
    endpoint = url
    recovery = f"Check the selection document: {selection}" if selection else _catalog_recovery(url, host_name)
    try:
        if selection is not None:
            selections = json.loads(selection.read_text())
        elif all_plugins:
            selections = {name: [] for name in ((step,) if step else CLEARABLE_STEPS)}
        else:
            selections = {step: plugins}
        recovery = _catalog_recovery(url, host_name)
        endpoint = url or catalog_url(host_name)
        result = PluginCatalogClient(endpoint)(
            {"operation": "disarm", "selections": selections, "all": all_plugins}, timeout_s=ARM_TIMEOUT_S,
        )
        recovery = f"./cli/automa hosts plugins status {_target_args(url, host_name)}"
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    return _result(result, operation="disarm", url=endpoint, host_name=host_name, step=step,
                   recovery=recovery, json_output=json_output)


def list_plugins(*, url: str | None, host_name: str | None, step: str | None = None,
                 json_output: bool = False) -> CommandResult:
    endpoint = url
    try:
        endpoint = url or catalog_url(host_name)
        result = PluginCatalogClient(endpoint)()
        if result.get("ok") and step is not None:
            result["plugins"] = [plugin for plugin in result["plugins"] if plugin["step"] == step]
            if result.get("arming"):
                for name in ("requested", "applied"):
                    result["arming"][name] = {step: result["arming"][name].get(step, [])}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    return _result(result, operation="status", url=endpoint, host_name=host_name, step=step,
                   recovery=None if result.get("ok") else _catalog_recovery(url, host_name),
                   json_output=json_output)


def _target_args(url: str | None, host_name: str | None) -> str:
    return f"--url {shlex.quote(url)}" if url else f"--name {shlex.quote(host_name)}"


def _catalog_recovery(url: str | None, host_name: str | None) -> str | None:
    # The error already says why the vehicle's host cannot be reached.
    if host_name:
        return None
    return f"Check that the catalog host at {url} is running, then retry ./cli/automa hosts plugins status {_target_args(url, host_name)}"


def _catalog_version(result: dict[str, Any]) -> str:
    host_run = result.get("host_run_id")
    return f"Catalog version: {result['catalog_version']}" + (f" (host run {host_run})" if host_run else "")


def _result(result: dict[str, Any], *, operation: str, url: str | None, host_name: str | None,
            step: str | None, recovery: str | None, json_output: bool,
            plugin_id: str | None = None) -> CommandResult:
    """Render the same target, outcome and recovery in human and JSON output."""

    ok = bool(result.get("ok"))
    # A failed selection change also fails status until a request succeeds.
    exit_code = 0 if ok and (result.get("arming") or {}).get("status") != "failed" else 2
    if operation == "arm":
        label, state = "Arming", result.get("status", "failed")
        message = "Plugin selection loaded; the host applies it at its next cycle."
    elif operation == "disarm":
        label, state = "Disarming", result.get("status", "failed")
        message = "Plugin deselection prepared; the host applies it at its next cycle."
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
        "schema": {"upload": "automa_plugin_upload", "arm": "automa_plugin_arm", "disarm": "automa_plugin_disarm", "status": "automa_plugin_catalog_status"}[operation],
        "host_name": host_name, "url": url, "step": step,
        "outcome": {"status": state if operation in ("arm", "disarm") or result.get("upload") else "ok" if ok else state,
                    "message": message, "recovery": recovery},
    }
    if json_output:
        return CommandResult(exit_code, json.dumps(result, indent=2, sort_keys=True))
    lines = [f"Host: {host_name}"] if host_name else []
    if url:
        lines.append(f"Endpoint: {url}")
    if step:
        lines.append(f"Step: {step}")
    lines.append(f"{label}: {state}")
    if result.get("upload"):
        lines.extend([f"Upload: {result['upload']['status']}", f"Plugin: {plugin_id}",
                      f"Revision: {result['plugin']['metadata']['revision']}",
                      _catalog_version(result)])
        if not ok:
            lines.append(f"Reason: {message}")
    elif not ok:
        lines.append(f"Reason: {message}")
    elif operation == "upload":
        lines.extend([
            f"Plugin: {plugin_id}", f"Revision: {result['plugin']['metadata']['revision']}",
            _catalog_version(result), "Selection: unchanged",
        ])
    elif operation == "status":
        lines.extend([_catalog_version(result), "Available plugins:"])
        entries = []
        for plugin in result["plugins"]:
            metadata = plugin.get("metadata", {})
            revision = metadata.get("revision")
            source = f"revision {revision} ({metadata.get('filename', 'uploaded')})" if revision else "packaged"
            entries.append(f"- {plugin['step']}/{plugin['id']}: {source}")
        lines.extend(entries or ["(none)"])
    arming = result.get("arming")
    if arming:
        lines.append(f"Request: {arming['request_id']}" if label in ("Arming", "Disarming")
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
