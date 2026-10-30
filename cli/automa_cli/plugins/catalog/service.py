"""List, upload, arm, disarm, and save plugins on a host catalog."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.steps import CLEARABLE_STEPS
from autonomy.runtime.plugin_catalog import describe_plugin
from implementations.decision_cycle.catalog import DEFAULT_STEP_PLUGINS, catalog_path

from ...hosts.publications.plugin_catalog import PluginCatalogClient
from ...hosts.runtime_hosts import runtime_base_url
from ...hosts.step_activations import (
    bundle_activation_path,
    bundle_activation_problems,
    format_activation_problems,
    host_bundle,
    save_selections,
    staging_host,
)
from ...shared.paths import RUNTIME_ROOT, display_path
from ...shared.plugin_catalog import packaged_plugin_definitions
from .models import ListingResult, MutationResult, Recovery, SaveResult, SavedStep

# Arming waits for the host's current cycle and for the selection to load.
ARM_TIMEOUT_S = 60.0
_REQUEST_ERRORS = (OSError, ValueError, TypeError, KeyError, RuntimeError)


def catalog_url(host_name: str) -> str:
    """The host's catalog; every host serves ``/api/plugins``."""

    return runtime_base_url(host_name)


def upload_plugin(*, url: str | None, host_name: str | None, file: Path, step: str,
                  plugin_id: str, entrypoint: str, config: str | None = None,
                  arm: bool = False) -> MutationResult:
    recovery = Recovery(kind="config")
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
        recovery = Recovery(kind="file", file=file)
        payload = {
            "step": step, "plugin_id": plugin_id, "entrypoint": entrypoint,
            "filename": file.name, "source_base64": base64.b64encode(file.read_bytes()).decode(),
        }
        if settings is not None:
            payload["config"] = settings
        recovery = Recovery(kind="endpoint", url=url, host_name=host_name)
        endpoint = url or catalog_url(host_name)
        client = PluginCatalogClient(endpoint)
        result = client(payload)
        if not result.get("ok"):
            recovery = Recovery(kind="rejected", file=file)
    except _REQUEST_ERRORS as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    if result.get("ok"):
        recovery = Recovery(kind="list", url=url, host_name=host_name, step=step, long=True)
        if arm:
            uploaded = result
            try:
                armed = _request_arm(client, {step: [plugin_id]})
            except _REQUEST_ERRORS as exc:
                armed = {"ok": False, "status": "failed", "error": str(exc)}
            result = {**uploaded, **armed, "upload": uploaded}
    return MutationResult("upload", result, endpoint, host_name, step, plugin_id, recovery)


def arm_plugins(*, url: str | None, host_name: str | None,
                plugins: list[tuple[str, str]]) -> MutationResult:
    """Arm ``(step, plugin_id)`` pairs as one request, each step's IDs in the order given."""

    endpoint = url
    recovery = Recovery(kind="endpoint", url=url, host_name=host_name)
    try:
        endpoint = url or catalog_url(host_name)
        result = _request_arm(PluginCatalogClient(endpoint), _selections(plugins))
        recovery = Recovery(kind="list", url=url, host_name=host_name)
    except _REQUEST_ERRORS as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    return MutationResult("arm", result, endpoint, host_name, None, None, recovery)


def disarm_plugins(*, url: str | None, host_name: str | None,
                   plugins: list[tuple[str, str]] | None = None,
                   step: str | None = None) -> MutationResult:
    """Disarm ``(step, plugin_id)`` pairs as one request; otherwise clear ``step``, or every clearable step."""

    endpoint = url
    recovery = Recovery(kind="endpoint", url=url, host_name=host_name)
    if plugins:
        selections, clear = _selections(plugins), False
    else:
        selections, clear = {name: [] for name in ((step,) if step else CLEARABLE_STEPS)}, True
    try:
        endpoint = url or catalog_url(host_name)
        result = PluginCatalogClient(endpoint)(
            {"operation": "disarm", "selections": selections, "all": clear}, timeout_s=ARM_TIMEOUT_S,
        )
        recovery = Recovery(kind="list", url=url, host_name=host_name)
    except _REQUEST_ERRORS as exc:
        result = {"ok": False, "status": "failed", "error": str(exc)}
    return MutationResult("disarm", result, endpoint, host_name, step, None, recovery)


def list_plugins(*, host_name: str | None = None, url: str | None = None, step: str | None = None,
                 long: bool = False) -> ListingResult:
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
        return ListingResult(payload)

    payload.update(armed=None, arming_error=None)
    try:
        payload["url"] = url or catalog_url(host_name)
        catalog = PluginCatalogClient(payload["url"])()
        if not catalog.get("ok"):
            raise RuntimeError(catalog.get("error") or "Host plugin catalog is unavailable.")
        arming = catalog.get("arming")
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
                "source": payload["url"], "version": catalog["catalog_version"],
                "host_run_id": catalog.get("host_run_id"),
                "plugins": [plugin for name in steps for plugin in catalog["plugins"] if plugin["step"] == name],
            }
        payload["ok"] = True
    except _REQUEST_ERRORS as exc:
        payload["error"] = str(exc)
    return ListingResult(payload)


def save_plugins(*, host_name: str, step: str | None = None) -> SaveResult:
    """Stage what a host has armed in ``step``, or every step, as the plugins it starts with.

    Nothing is saved unless every step can be: the host must report its
    armed plugins, its last arming must have succeeded, and each plugin
    must be packaged in the release the host has staged.
    """

    steps = (step,) if step is not None else STEPS
    endpoint: str | None = None
    try:
        configuration, error = staging_host(host_name, runtime_root=RUNTIME_ROOT)
        if error:
            raise ValueError(error)
        bundle = host_bundle(host_name, RUNTIME_ROOT)
        problems = bundle_activation_problems(bundle, host_name, steps=steps)
        if problems:
            raise ValueError(format_activation_problems(problems))
        endpoint = catalog_url(host_name)
        catalog = PluginCatalogClient(endpoint)()
        if not catalog.get("ok"):
            raise RuntimeError(catalog.get("error") or "Host plugin catalog is unavailable.")
        arming = catalog.get("arming")
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
    except _REQUEST_ERRORS as exc:
        return SaveResult(host_name, step, endpoint, ok=False, error=str(exc))

    return SaveResult(
        host_name, step, endpoint, ok=True,
        steps={
            name: SavedStep(
                bundle_activation_path(bundle, name),
                activation.metadata.get("preset"),
                tuple(activation.plugins),
                bool(activation.metadata.get("saved")),
            )
            for name, activation in saved.items()
        },
        runtime_dir=Path(bundle["runtime_dir"]),
        provider=configuration["provider"],
    )


def _request_arm(client: PluginCatalogClient, selections: dict[str, Any]) -> dict[str, Any]:
    return client({"operation": "arm", "selections": selections}, timeout_s=ARM_TIMEOUT_S)


def _selections(plugins: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Group ``(step, plugin_id)`` pairs by step, each step's IDs in the order given."""

    selections: dict[str, list[str]] = {}
    for step, plugin_id in plugins:
        selections.setdefault(step, []).append(plugin_id)
    return selections
