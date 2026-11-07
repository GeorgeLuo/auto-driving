"""List, upload, arm, disarm, and save plugins on a host catalog."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import STEPS
from autonomy.decision_cycle.steps import CLEARABLE_STEPS
from autonomy.runtime.plugin_catalog import describe_plugin
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PLUGINS, DEFAULT_STEP_PRESETS, STEP_PRESETS, catalog_path, preset_activation,
)

from ...hosts.discovery import runtime_base_url
from ...hosts.publications.plugin_catalog import PluginCatalogClient
from ...releases.paths import bundle_paths
from ...hosts.step_activations import (
    bundle_activation_path,
    bundle_activation_problems,
    save_selections,
    staging_host,
)
from ...hosts.step_formatting import format_activation_problems
from ...shared.paths import display_path
from ...shared.plugin_catalog import packaged_plugin_definitions
from .models import (
    Arming,
    CatalogListing,
    Definition,
    ListingResult,
    Receipt,
    Recovery,
    SavedStep,
    SaveResult,
    SelectionResult,
    UploadResult,
)

# Arming waits for the host's current cycle and for the selection to load.
ARM_TIMEOUT_S = 60.0
_REQUEST_ERRORS = (OSError, ValueError, TypeError, KeyError, RuntimeError)


def catalog_url(host_name: str) -> str:
    """The host's catalog; every host serves ``/api/plugins``."""

    return runtime_base_url(host_name)


def upload_plugin(*, url: str | None, host_name: str | None, file: Path, step: str,
                  plugin_id: str, entrypoint: str, config: str | None = None,
                  arm: bool = False) -> UploadResult:
    def result(upload: Receipt, recovery: Recovery, armed: Receipt | None = None,
               endpoint: str | None = url) -> UploadResult:
        return UploadResult(host_name, endpoint, step, plugin_id, upload, armed, recovery)

    payload: dict[str, Any] = {"step": step, "plugin_id": plugin_id, "entrypoint": entrypoint,
                               "filename": file.name}
    try:
        # The host stores the config with this revision; arming constructs the plugin with it.
        if config is not None:
            payload["config"] = _config_settings(config)
    except ValueError as exc:
        return result(_failure(exc), Recovery(kind="config"))
    try:
        payload["source_base64"] = base64.b64encode(file.read_bytes()).decode()
    except OSError as exc:
        return result(_failure(exc), Recovery(kind="file", file=file))
    endpoint, uploaded = _send(url, host_name, payload, uploaded=True)
    if not uploaded.answered:
        return result(uploaded, Recovery(kind="endpoint", url=url, host_name=host_name), endpoint=endpoint)
    if not uploaded.ok:
        return result(uploaded, Recovery(kind="rejected", file=file), endpoint=endpoint)
    armed = _send(endpoint, None, _arm_request({step: [plugin_id]}), timeout_s=ARM_TIMEOUT_S)[1] if arm else None
    return result(uploaded, Recovery(kind="list", url=url, host_name=host_name, step=step, long=True),
                  armed, endpoint)


def arm_plugins(*, url: str | None, host_name: str | None,
                plugins: list[tuple[str, str]]) -> SelectionResult:
    """Arm ``(step, plugin_id)`` pairs as one request, each step's IDs in the order given."""

    return _change_selection("arm", url, host_name, None, _arm_request(_selections(plugins)))


def arm_preset(*, url: str | None, host_name: str | None, step: str, preset: str) -> SelectionResult:
    """Arm a packaged preset as all that ``step`` runs, in place of what it has armed.

    The host refuses it unless its catalog holds each of the preset's plugins
    as the preset configures it; once they apply, the step reports the preset.
    """

    request = _arm_request({step: preset_activation(step, preset).to_payload()})
    return _change_selection("arm", url, host_name, step, request, preset=preset)


def disarm_plugins(*, url: str | None, host_name: str | None,
                   plugins: list[tuple[str, str]] | None = None,
                   step: str | None = None) -> SelectionResult:
    """Disarm ``(step, plugin_id)`` pairs as one request; otherwise clear ``step``, or every clearable step."""

    if plugins:
        selections, clear = _selections(plugins), False
    else:
        selections, clear = {name: [] for name in ((step,) if step else CLEARABLE_STEPS)}, True
    return _change_selection("disarm", url, host_name, step,
                             {"operation": "disarm", "selections": selections, "all": clear})


def list_plugins(*, host_name: str | None = None, url: str | None = None, step: str | None = None,
                 long: bool = False) -> ListingResult:
    """List what a host has armed, or with ``long`` its whole catalog; without a host, the packaged catalog."""

    steps = (step,) if step is not None else STEPS
    if host_name is None and url is None:
        # The listed step's file, or for every step the pattern matching each one's file.
        source = catalog_path(step or "*")
        return ListingResult(
            host_name, url, step, ok=True,
            defaults={name: list(DEFAULT_STEP_PLUGINS[name]) for name in steps},
            step_presets={name: _listed_presets(name) for name in steps},
            catalog=CatalogListing(display_path(source), [describe_plugin(plugin) for name in steps
                                                          for plugin in packaged_plugin_definitions(name)]),
        )

    endpoint, receipt = _send(url, host_name)
    try:
        _require_answer(receipt)
        if receipt.arming is None and not long:
            raise ValueError("This catalog does not arm plugins; add --long to list it.")
        armed = None if receipt.arming is None else [
            plugin for plugins in _requested(receipt.arming, steps).values() for plugin in plugins
        ]
        listing = None
        if long:
            if receipt.catalog_version is None:
                raise ValueError("Host did not report its catalog version.")
            listing = CatalogListing(endpoint, [plugin for name in steps for plugin in receipt.body["plugins"]
                                                if plugin["step"] == name],
                                     receipt.catalog_version, receipt.host_run_id)
    except _REQUEST_ERRORS as exc:
        return ListingResult(host_name, endpoint, step, ok=False, error=str(exc))
    return ListingResult(host_name, endpoint, step, ok=True, catalog=listing, armed=armed,
                         arming_error=receipt.arming.error if receipt.arming else None)


def save_plugins(*, host_name: str, step: str | None = None) -> SaveResult:
    """Stage what a host has armed in ``step``, or every step, as the plugins it starts with.

    Nothing is saved unless every step can be: the host must report its
    armed plugins, its last arming must have succeeded, and each plugin
    must be packaged in the release the host has staged.
    """

    steps = (step,) if step is not None else STEPS
    endpoint: str | None = None
    try:
        configuration, error = staging_host(host_name)
        if error:
            raise ValueError(error)
        bundle = bundle_paths(host_name)
        problems = bundle_activation_problems(bundle, host_name, steps=steps)
        if problems:
            raise ValueError(format_activation_problems(problems))
        endpoint, receipt = _send(None, host_name)
        _require_answer(receipt)
        requested = _requested(receipt.arming, steps)
        if receipt.arming.error:
            raise ValueError(f"The host's last arming failed: {receipt.arming.error}\n"
                             "Arm the plugins again, then save them.")
        uploaded = [f"{plugin['step']}/{plugin['id']}" for plugins in requested.values() for plugin in plugins
                    if plugin.get("metadata", {}).get("revision")]
        if uploaded:
            raise ValueError(f"{', '.join(uploaded)} {'is' if len(uploaded) == 1 else 'are'} uploaded, and "
                             "uploads last until the host restarts. Arm packaged plugins in their place, "
                             "or save the other steps with --step.")
        saved = save_selections(bundle, requested, host_name=host_name, vehicle=configuration)
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


def _listed_presets(step: str) -> dict[str, dict[str, Any]]:
    """``step``'s presets as its catalog file lists them, each marked whether it is the step's default."""

    default = DEFAULT_STEP_PRESETS.get(step)
    return {name: {**deepcopy(entry), "default": name == default} for name, entry in STEP_PRESETS.get(step, {}).items()}


def _change_selection(operation: str, url: str | None, host_name: str | None, step: str | None,
                      payload: dict[str, Any], *, preset: str | None = None) -> SelectionResult:
    endpoint, receipt = _send(url, host_name, payload, timeout_s=ARM_TIMEOUT_S)
    recovery = (Recovery(kind="list", url=url, host_name=host_name) if receipt.answered
                else Recovery(kind="endpoint", url=url, host_name=host_name))
    return SelectionResult(operation, host_name, endpoint, step, receipt, recovery, preset)


def _send(url: str | None, host_name: str | None, payload: dict[str, Any] | None = None, *,
          timeout_s: float | None = None, uploaded: bool = False) -> tuple[str | None, Receipt]:
    """The endpoint called and its receipt: ``url``, else ``host_name``'s catalog.

    A host that cannot be found or reached, or whose answer cannot be read,
    stands in as an unanswered failure.
    """

    endpoint = url
    try:
        endpoint = url or catalog_url(host_name)
        return endpoint, _receipt(PluginCatalogClient(endpoint)(payload, timeout_s=timeout_s), uploaded=uploaded)
    except _REQUEST_ERRORS as exc:
        return endpoint, _failure(exc)


def _receipt(body: dict[str, Any], *, uploaded: bool) -> Receipt:
    """Read what a receipt reports from a host's answer; an accepted upload must name its revision."""

    ok = bool(body.get("ok"))
    revision = None
    if uploaded and ok:
        plugin = body.get("plugin")
        metadata = plugin.get("metadata") if isinstance(plugin, dict) else None
        revision = metadata.get("revision") if isinstance(metadata, dict) else None
        if revision is None or body.get("catalog_version") is None:
            raise ValueError("Host accepted the upload without reporting its revision and catalog version.")
    arming = body.get("arming")
    return Receipt(
        body, answered=True, ok=ok, status=body.get("status", "failed"), error=body.get("error"),
        revision=revision, catalog_version=body.get("catalog_version"), host_run_id=body.get("host_run_id"),
        arming=None if arming is None else _arming(arming),
    )


def _arming(value: Any) -> Arming:
    """A reported selection; steps whose definitions are not a list are left out."""

    value = value if isinstance(value, dict) else {}

    def by_step(name: str) -> dict[str, list[Definition]]:
        steps = value.get(name)
        if not isinstance(steps, dict):
            return {}
        return {step: plugins for step, plugins in steps.items() if isinstance(plugins, list)}

    generation = value.get("generation_id")
    return Arming(
        value.get("request_id"), value.get("error"), by_step("requested"), by_step("applied"),
        generation if isinstance(generation, str) and generation else None,
    )


def _failure(exc: Exception) -> Receipt:
    return Receipt({"ok": False, "status": "failed", "error": str(exc)}, answered=False, ok=False,
                   status="failed", error=str(exc))


def _require_answer(receipt: Receipt) -> None:
    """Refuse a catalog read the host did not answer successfully."""

    if not receipt.ok:
        raise RuntimeError(receipt.error or "Host plugin catalog is unavailable.")


def _requested(arming: Arming | None, steps: tuple[str, ...]) -> dict[str, list[Definition]]:
    """Each of ``steps``' armed definitions, in run order; refused unless the host reported every step."""

    if arming is None or any(name not in arming.requested for name in steps):
        raise ValueError("Host did not report its armed plugins.")
    return {name: arming.requested[name] for name in steps}


def _config_settings(config: str) -> dict[str, Any]:
    try:
        settings = json.loads(config)
    except ValueError as exc:
        raise ValueError(f"--config is not valid JSON: {exc}") from exc
    if not isinstance(settings, dict):
        raise ValueError("--config must be a JSON object")
    return settings


def _arm_request(selections: dict[str, Any]) -> dict[str, Any]:
    """Each step's ordered plugin IDs, or the whole activation it runs instead."""

    return {"operation": "arm", "selections": selections}


def _selections(plugins: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Group ``(step, plugin_id)`` pairs by step, each step's IDs in the order given."""

    selections: dict[str, list[str]] = {}
    for step, plugin_id in plugins:
        selections.setdefault(step, []).append(plugin_id)
    return selections
