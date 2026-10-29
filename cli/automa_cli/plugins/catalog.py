"""Plugin listing and CLI operations against a host's live catalog."""

from __future__ import annotations

import base64
import json
import shlex
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.steps import CLEARABLE_STEPS
from autonomy.runtime.plugin_catalog import PLUGIN_CATALOG_PATH, describe_plugin
from implementations.decision_cycle.catalog import DEFAULT_STEP_PLUGINS, catalog_path

from ..shared.command_result import CommandResult
from ..shared.paths import display_path
from ..shared.plugin_catalog import packaged_plugin_definitions

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

    from ..hosts.runtime_hosts import runtime_base_url

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
        recovery = f"./cli/automa plugins list {_target_args(url, host_name)} --step {step} --long"
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


def _selections(plugins: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Group ``(step, plugin_id)`` pairs by step, each step's IDs in the order given."""

    selections: dict[str, list[str]] = {}
    for step, plugin_id in plugins:
        selections.setdefault(step, []).append(plugin_id)
    return selections


def arm_plugins(*, url: str | None, host_name: str | None, plugins: list[tuple[str, str]],
                json_output: bool = False) -> CommandResult:
    """Arm ``(step, plugin_id)`` pairs as one request, each step's IDs in the order given."""

    endpoint = url
    recovery = _catalog_recovery(url, host_name)
    try:
        endpoint = url or catalog_url(host_name)
        result = _request_arm(PluginCatalogClient(endpoint), _selections(plugins))
        recovery = f"./cli/automa plugins list {_target_args(url, host_name)}"
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    return _result(result, operation="arm", url=endpoint, host_name=host_name, step=None,
                   recovery=recovery, json_output=json_output)


def disarm_plugins(*, url: str | None, host_name: str | None, plugins: list[tuple[str, str]] | None = None,
                   step: str | None = None, json_output: bool = False) -> CommandResult:
    """Disarm ``(step, plugin_id)`` pairs as one request; otherwise clear ``step``, or every clearable step."""

    endpoint = url
    recovery = _catalog_recovery(url, host_name)
    if plugins:
        selections, clear = _selections(plugins), False
    else:
        selections, clear = {name: [] for name in ((step,) if step else CLEARABLE_STEPS)}, True
    try:
        endpoint = url or catalog_url(host_name)
        result = PluginCatalogClient(endpoint)(
            {"operation": "disarm", "selections": selections, "all": clear}, timeout_s=ARM_TIMEOUT_S,
        )
        recovery = f"./cli/automa plugins list {_target_args(url, host_name)}"
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    return _result(result, operation="disarm", url=endpoint, host_name=host_name, step=step,
                   recovery=recovery, json_output=json_output)


def list_plugins(*, host_name: str | None = None, url: str | None = None, step: str | None = None,
                 long: bool = False, json_output: bool = False) -> CommandResult:
    """List what a host has armed, or with ``long`` its whole catalog; without a host, the packaged catalog."""

    steps = (step,) if step is not None else STEPS
    payload: dict[str, Any] = {"schema": "automa_plugin_list", "host_name": host_name, "url": url,
                               "step": step, "ok": False}
    if host_name is None and url is None:
        defaults = {name: list(DEFAULT_STEP_PLUGINS[name]) for name in steps}
        # The listed step's file, or for every step the pattern matching each one's file.
        source = catalog_path(step or "*")
        payload.update(
            ok=True, defaults=defaults,
            catalog={"source": display_path(source),
                     "plugins": [describe_plugin(plugin) for name in steps
                                 for plugin in packaged_plugin_definitions(name)]},
        )
        lines = [f"Catalog: {payload['catalog']['source']}",
                 *_plugin_rows(payload["catalog"]["plugins"],
                               {(name, plugin_id, None) for name in steps for plugin_id in defaults[name]}, "default")]
        return _listing(payload, lines, json_output)

    payload.update(armed=None, arming_error=None)
    lines = [f"Host: {host_name}"] if host_name else []
    try:
        payload["url"] = url or catalog_url(host_name)
        snapshot = PluginCatalogClient(payload["url"])()
        if not snapshot.get("ok"):
            raise RuntimeError(snapshot.get("error") or "Host plugin catalog is unavailable.")
        arming = snapshot.get("arming")
        if arming is None and not long:
            raise ValueError("This catalog does not arm plugins; add --long to list it.")
        if arming is not None:
            requested = arming.get("requested") if isinstance(arming, dict) else None
            if not isinstance(requested, dict) or any(not isinstance(requested.get(name), list) for name in steps):
                raise ValueError("Host did not report its armed plugins.")
            payload.update(armed=[plugin for name in steps for plugin in requested[name]],
                           arming_error=arming.get("error"))
        if long:
            payload["catalog"] = {
                "source": payload["url"], "version": snapshot["catalog_version"],
                "host_run_id": snapshot.get("host_run_id"),
                "plugins": [plugin for name in steps for plugin in snapshot["plugins"] if plugin["step"] == name],
            }
        payload["ok"] = True
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        payload["error"] = str(exc)
        lines.extend(["Catalog: unavailable", f"Reason: {payload['error']}"])
        recovery = _catalog_recovery(url, host_name)
        return _listing(payload, [*lines, f"Next: {recovery}"] if recovery else lines, json_output)

    armed = payload["armed"] or []
    if long:
        lines.extend([f"Catalog: {payload['url']}", _catalog_version(snapshot),
                      *_plugin_rows(_armed_first(payload["catalog"]["plugins"], armed),
                                    {_plugin_key(plugin) for plugin in armed}, "armed")])
    else:
        lines.extend([*([] if host_name else [f"Catalog: {payload['url']}"]), "Armed:", *_plugin_rows(armed)])
    if payload["arming_error"]:
        lines.append(f"Arming error: {payload['arming_error']}")
    return _listing(payload, lines, json_output)


def save_plugins(*, host_name: str, step: str | None = None, json_output: bool = False) -> CommandResult:
    """Stage what a host has armed in ``step``, or every step, as the plugins it starts with.

    Nothing is saved unless every step can be: the host must report its
    armed plugins, its last arming must have succeeded, and each plugin
    must be packaged in the release the host has staged.
    """

    from ..hosts.runtime_hosts import RUNTIME_ROOT
    from ..hosts.step_activations import (
        bundle_activation_path, bundle_activation_problems, format_activation_problems, host_bundle,
        save_selections, staging_host, step_lines,
    )

    steps = (step,) if step is not None else STEPS
    payload: dict[str, Any] = {"schema": "automa_plugin_save", "host_name": host_name, "step": step,
                               "url": None, "ok": False, "steps": None}
    lines = [f"Host: {host_name}"]
    try:
        configuration, error = staging_host(host_name, runtime_root=RUNTIME_ROOT)
        if error:
            raise ValueError(error)
        bundle = host_bundle(host_name, RUNTIME_ROOT)
        problems = bundle_activation_problems(bundle, host_name, steps=steps)
        if problems:
            raise ValueError(format_activation_problems(problems))
        payload["url"] = catalog_url(host_name)
        snapshot = PluginCatalogClient(payload["url"])()
        if not snapshot.get("ok"):
            raise RuntimeError(snapshot.get("error") or "Host plugin catalog is unavailable.")
        arming = snapshot.get("arming")
        requested = arming.get("requested") if isinstance(arming, dict) else None
        if not isinstance(requested, dict) or any(not isinstance(requested.get(name), list) for name in steps):
            raise ValueError("Host did not report its armed plugins.")
        if arming.get("error"):
            raise ValueError(f"The host's last arming failed: {arming['error']}\n"
                             "Arm the plugins again, then save them.")
        uploaded = [f"{plugin['step']}/{plugin['id']}" for name in steps for plugin in requested[name]
                    if plugin.get("metadata", {}).get("revision")]
        if uploaded:
            raise ValueError(f"{', '.join(uploaded)} {'is' if len(uploaded) == 1 else 'are'} uploaded, and "
                             "uploads last until the host restarts. Arm packaged plugins in their place, "
                             "or save the other steps with --step.")
        saved = save_selections(bundle, {name: requested[name] for name in steps},
                                host_name=host_name, vehicle=configuration)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        payload["error"] = str(exc)
        lines.extend(["Save: failed", f"Reason: {payload['error']}"])
        return CommandResult(2, json.dumps(payload, indent=2, sort_keys=True) if json_output else "\n".join(lines))

    payload.update(ok=True, steps={
        name: {"activation": display_path(bundle_activation_path(bundle, name)),
               "preset": activation.metadata.get("preset"), "plugins": list(activation.plugins),
               "saved": bool(activation.metadata.get("saved"))}
        for name, activation in saved.items()
    })
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    selections = payload["steps"]
    rows = step_lines({name: entry["plugins"] for name, entry in selections.items()},
                      {name: entry["preset"] for name, entry in selections.items() if entry["preset"]})
    startup = (f"{host_name} starts with these plugins; hosts update keeps the saved steps."
               if configuration["provider"] == "chase-sim" else
               f"{host_name} starts with these plugins once they ship: "
               f"./cli/automa hosts update --name {shlex.quote(host_name)}")
    return CommandResult(0, "\n".join([
        *lines,
        *(f"{row} ({'saved' if entry['saved'] else 'default'})" for row, entry in zip(rows, selections.values())),
        f"Activations: {display_path(Path(bundle['runtime_dir']))}",
        f"Startup: {startup}",
    ]))


def _listing(payload: dict[str, Any], lines: list[str], json_output: bool) -> CommandResult:
    # A host whose last arming failed lists what it has armed but exits 2 until an arm succeeds.
    exit_code = 0 if payload["ok"] and not payload.get("arming_error") else 2
    if json_output:
        return CommandResult(exit_code, json.dumps(payload, indent=2, sort_keys=True))
    return CommandResult(exit_code, "\n".join(lines))


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


def _target_args(url: str | None, host_name: str | None) -> str:
    return f"--url {shlex.quote(url)}" if url else f"--host-name {shlex.quote(host_name)}"


def _catalog_recovery(url: str | None, host_name: str | None) -> str | None:
    # The error already says why the vehicle's host cannot be reached.
    if host_name:
        return None
    return f"Check that the catalog host at {url} is running, then retry ./cli/automa plugins list {_target_args(url, host_name)} --long"


def _catalog_version(result: dict[str, Any]) -> str:
    host_run = result.get("host_run_id")
    return f"Catalog version: {result['catalog_version']}" + (f" (host run {host_run})" if host_run else "")


def _result(result: dict[str, Any], *, operation: str, url: str | None, host_name: str | None,
            step: str | None, recovery: str | None, json_output: bool,
            plugin_id: str | None = None) -> CommandResult:
    """Render the same target, outcome and recovery in human and JSON output."""

    ok = bool(result.get("ok"))
    # A failed selection change exits 2.
    exit_code = 0 if ok else 2
    if operation == "arm":
        label, state = "Arming", result.get("status", "failed")
        message = "Plugin selection loaded; the host applies it at its next cycle."
    elif operation == "disarm":
        label, state = "Disarming", result.get("status", "failed")
        message = "Plugin deselection prepared; the host applies it at its next cycle."
    elif result.get("upload"):
        label, state = "Arming", result.get("status", "failed")
        message = "Plugin uploaded and loaded; the host applies it at its next cycle."
    else:
        label, state = "Upload", "uploaded" if ok else "failed"
        message = "Plugin uploaded and available; selection unchanged."
    if not ok:
        message = result.get("error", "Catalog request failed.")
    result = {
        **result,
        "schema": {"upload": "automa_plugin_upload", "arm": "automa_plugin_arm", "disarm": "automa_plugin_disarm"}[operation],
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
    arming = result.get("arming")
    if arming:
        lines.append(f"Request: {arming['request_id']}")
        for name in ("requested", "applied"):
            for selected_step, definitions in arming[name].items():
                selected = ", ".join(f"{item['id']} ({item['metadata'].get('revision', 'packaged')})" for item in definitions)
                lines.append(f"{name.capitalize()} {selected_step}: {selected or '(none)'}")
    if recovery:
        lines.append(f"Next: {recovery}")
    return CommandResult(exit_code, "\n".join(lines))
