"""A host's staged step activations in its local controller bundle.

Every cycle step is staged the same way: ``bundle/runtime/<step>/active.json``
holds a ``StepActivation`` for a host ``staging_host`` knows. ``hosts update``
writes each step's saved selection there, else its default, and a host
process loads it at startup; ``plugins save`` stages what a running host has
armed and marks it ``saved``. The CLI records who staged it (host, bundle,
release, time) in the activation's ``metadata``. CLI hosts use its bundle
path to load staged plugin code; the core step runners do not read it.
The decision steps (proposal, plan, action) together identify a decision
generation by the content of their activations.
"""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import (
    DECISION_STEPS,
    STEPS,
    StepActivation,
    activation_generation_id,
    activation_presets,
    read_step_activation_if_present,
    step_activation_path,
    write_step_activation,
)
from autonomy.decision_cycle.steps import builtin_activation
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PLUGINS,
    PRESET_STEPS,
    plugin_list_preset,
    selection_activation,
)

from ..providers.catalog import PROVIDERS
from ..releases.paths import controller_bundle_paths
from ..shared.paths import display_path, safe_path_part
from .bundles import release_activation_summary


def host_bundle(host_name: str, runtime_root: Path) -> dict[str, str]:
    return controller_bundle_paths(Path(runtime_root) / safe_path_part(host_name))


def bundle_activation_path(bundle: dict[str, str], step: str) -> Path:
    return step_activation_path(Path(bundle["runtime_dir"]), step)


def read_bundle_activation(bundle: dict[str, str], step: str) -> StepActivation | None:
    return read_step_activation_if_present(bundle_activation_path(bundle, step), step)


def bundle_activation_problems(
    bundle: dict[str, str], host_name: str, *, steps: tuple[str, ...] = STEPS,
) -> list[dict[str, str]]:
    """Diagnose existing staged documents using the core's common step reader.

    Missing optional steps retain their built-in or empty behavior. Checking
    never changes a document; ``hosts update`` replaces an invalid one with
    the step's default, the command each diagnosis includes. Callers pass
    the ``steps`` they read, so a command reports exactly what blocks it.
    """

    problems: list[dict[str, str]] = []
    for step in steps:
        try:
            read_bundle_activation(bundle, step)
        except (OSError, TypeError, ValueError) as exc:
            problems.append({
                "step": step,
                "activation": display_path(bundle_activation_path(bundle, step)),
                "reason": str(exc),
                "command": f"./cli/automa hosts update --name {shlex.quote(host_name)}",
            })
    return problems


def absent_step_error(step: str, host_name: str) -> str:
    """The same guidance for every vehicle whose running autonomy lacks ``step``."""

    return (
        f"{host_name} runs no {step} step. Arm a plugin for it with "
        f"./cli/automa plugins arm --host-name {host_name} --name {step}/<plugin>, "
        f"or restart it with each step's saved plugins, else its default: "
        f"./cli/automa hosts update --name {host_name} --restart"
    )


def staging_host(
    host_name: str, *, runtime_root: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read the explicit host record; directory names and activations are not identity."""

    if host_name in {".", ".."} or safe_path_part(host_name) != host_name or not host_name:
        return None, "Host names may contain letters, digits, dots, underscores and hyphens, excluding . and .."
    path = Path(runtime_root) / host_name / "host-configuration.json"
    if not path.is_file():
        names = host_names(runtime_root)
        known = f"Hosts: {', '.join(names)}." if names else "No hosts exist."
        return None, f"No host {host_name!r}. {known}"
    try:
        configuration = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(configuration, dict) or configuration.get("schema") != "automa_host_configuration"
                or configuration.get("host_name") != host_name
                or configuration.get("provider") not in PROVIDERS
                or not isinstance(configuration.get("connection"), dict)):
            raise ValueError("invalid host record")
    except (OSError, ValueError) as exc:
        return None, f"Cannot read host {host_name!r}: {exc}"
    return configuration, None


def host_names(runtime_root: Path) -> list[str]:
    return sorted(path.parent.name for path in Path(runtime_root).glob("*/host-configuration.json") if path.is_file())


def write_host_configuration(runtime_root: Path, configuration: dict[str, Any]) -> Path:
    """Create a host identity without overwriting an existing host."""
    host_name = configuration["host_name"]
    if host_name in {".", ".."} or safe_path_part(host_name) != host_name or not host_name:
        raise ValueError("Invalid host name")
    path = Path(runtime_root) / host_name / "host-configuration.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump({**configuration, "schema": "automa_host_configuration"}, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return path


# Keys of ``metadata.controller_bundle`` on every staged step.
CONTROLLER_BUNDLE_KEYS = (
    "root_dir",
    "autonomy_dir",
    "implementations_dir",
    "runtime_dir",
    "release",
)


def staging_metadata(
    *,
    host_name: str | None,
    bundle: dict[str, str],
    release: dict[str, Any] | None = None,
    vehicle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Provenance recorded on every step's staged activation.

    ``controller_bundle`` has the same keys for every step
    (``CONTROLLER_BUNDLE_KEYS``). A packaged ``release`` is summarized;
    otherwise ``release`` is null until a caller records one. Vehicle
    identity (``provider``, ``vehicle_kind``, ``runtime``) is recorded
    when ``vehicle`` is supplied. Steps do not add their own keys.
    """

    metadata: dict[str, Any] = {
        "host_name": host_name,
        "activated_at_ms": int(time.time() * 1000),
        "controller_bundle": {
            "root_dir": bundle["root_dir"],
            "autonomy_dir": bundle["autonomy_dir"],
            "implementations_dir": bundle["implementations_dir"],
            "runtime_dir": bundle["runtime_dir"],
            "release": release_activation_summary(release) if release is not None else None,
        },
    }
    if vehicle is not None:
        metadata.update({
            "provider": vehicle.get("provider"),
            "vehicle_kind": vehicle.get("vehicle_kind"),
            "runtime": {
                "kind": PROVIDERS[vehicle["provider"]].runtime_kind,
                "connection": deepcopy(vehicle.get("connection")),
            },
        })
    return metadata


def staged_activation(
    bundle: dict[str, str],
    activation: StepActivation,
    *,
    host_name: str | None,
    release: dict[str, Any] | None = None,
    vehicle: dict[str, Any] | None = None,
    release_summary: dict[str, Any] | None = None,
) -> StepActivation:
    """``activation`` plus staging provenance, not yet written.

    ``release`` is a bundle just packaged by ``sync_controller_bundle``
    and wins. ``release_summary`` is an already recorded summary kept
    when the bundle is not repackaged.
    """

    metadata = {**dict(activation.metadata), **staging_metadata(
        host_name=host_name, bundle=bundle, release=release, vehicle=vehicle,
    )}
    if release is None and release_summary is not None:
        controller_bundle = dict(metadata.get("controller_bundle") or {})
        controller_bundle["release"] = deepcopy(release_summary)
        metadata["controller_bundle"] = controller_bundle
    return replace_metadata(activation, metadata)


def stage_activation(
    bundle: dict[str, str],
    activation: StepActivation,
    *,
    host_name: str | None,
    release: dict[str, Any] | None = None,
    vehicle: dict[str, Any] | None = None,
    release_summary: dict[str, Any] | None = None,
) -> Path:
    """Write ``activation`` with the provenance ``staged_activation`` builds."""

    return write_step_activation(
        bundle_activation_path(bundle, activation.step),
        staged_activation(
            bundle,
            activation,
            host_name=host_name,
            release=release,
            vehicle=vehicle,
            release_summary=release_summary,
        ),
    )


def replace_metadata(activation: StepActivation, metadata: dict[str, Any]) -> StepActivation:
    return StepActivation(
        step=activation.step,
        plugins=activation.plugins,
        plugin_specs=activation.plugin_specs,
        plugin_configs=activation.plugin_configs,
        metadata=metadata,
        source_path=activation.source_path,
    )


def startup_activations(bundle: dict[str, str], host_name: str) -> dict[str, StepActivation]:
    """What each step starts with after ``hosts update``.

    A step ``plugins save`` saved keeps its plugins, rebuilt from the catalog
    with their current specs and configs. Every other step gets its default,
    including one whose document cannot be read. Raises ``ValueError``,
    before anything is packaged or written, when the catalog no longer has a
    saved plugin.
    """

    activations: dict[str, StepActivation] = {}
    for step in STEPS:
        try:
            staged = read_bundle_activation(bundle, step)
        except (OSError, TypeError, ValueError):
            staged = None
        if staged is None or not staged.metadata.get("saved"):
            activations[step] = selection_activation(step)
            continue
        try:
            rebuilt = selection_activation(step, plugins=staged.plugins)
        except ValueError as exc:
            raise ValueError("\n".join([
                f"{host_name}'s saved {step} plugins are not all in the catalog: {exc}",
                "Change them on the running host with plugins arm or disarm, then save them again: "
                f"./cli/automa plugins save --host-name {shlex.quote(host_name)} --step {step}",
            ])) from exc
        activations[step] = replace_metadata(rebuilt, {**rebuilt.metadata, "saved": True})
    return activations


def ensure_step_activations(
    *,
    host_name: str,
    bundle: dict[str, str],
    release: dict[str, Any],
    activations: Mapping[str, StepActivation],
    vehicle: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Stage each step's activation from ``startup_activations``, recording ``release`` on each.

    The deploy replaces whatever a step had staged. A plugin armed on a
    running host lasts until the host process restarts, unless
    ``plugins save`` saves it.
    """

    return {
        step: stage_activation(
            bundle,
            activations[step],
            host_name=host_name,
            release=release,
            vehicle=vehicle,
        )
        for step in STEPS
    }


def save_selections(
    bundle: dict[str, str],
    armed: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    host_name: str,
    vehicle: dict[str, Any],
) -> dict[str, StepActivation]:
    """Stage each step's ``armed`` plugins as the host's startup selection; return what was written.

    ``armed`` lists each step's plugins as the host's catalog describes them
    (``id``, ``entrypoint``, ``config``). The step keeps the specs, configs
    and release ``hosts update`` staged, so each must match what the host
    runs. A selection other than the step's default is marked ``saved``,
    which ``hosts update`` keeps; a default is left to follow the catalog.
    Raises ``ValueError``, having written nothing, when any step cannot be
    saved.
    """

    restart = f"./cli/automa hosts update --name {shlex.quote(host_name)} --restart"
    saved: dict[str, StepActivation] = {}
    for step, plugins in armed.items():
        staged = read_bundle_activation(bundle, step)
        if staged is None:
            raise ValueError(f"{host_name} has no staged {step} activation. Run: {restart}")
        for plugin in plugins:
            plugin_id = plugin["id"]
            if (staged.plugin_specs.get(plugin_id) != plugin["entrypoint"]
                    or dict(staged.plugin_configs.get(plugin_id, {})) != dict(plugin["config"])):
                raise ValueError("\n".join([
                    f"{host_name} runs a {step}/{plugin_id} its staged release does not package.",
                    f"Restart it onto that release, arm the plugins again and save: {restart}",
                ]))
        ids = tuple(plugin["id"] for plugin in plugins)
        metadata: dict[str, Any] = {"preset": plugin_list_preset(step, ids)} if step in PRESET_STEPS else {}
        if ids != tuple(DEFAULT_STEP_PLUGINS[step]):
            metadata["saved"] = True
        selection = StepActivation(
            step=step, plugins=ids, plugin_specs=staged.plugin_specs,
            plugin_configs=staged.plugin_configs, metadata=metadata,
        )
        saved[step] = staged_activation(
            bundle, selection, host_name=host_name, vehicle=vehicle,
            release_summary=(staged.metadata.get("controller_bundle") or {}).get("release"),
        )
    for activation in saved.values():
        write_step_activation(bundle_activation_path(bundle, activation.step), activation)
    return saved


def staged_selections(bundle: dict[str, str]) -> dict[str, dict[str, Any]]:
    """What a start runs for every step, and where each selection comes from.

    A step runs its staged activation, else its built-in, else nothing. An
    unreadable document is ``invalid``; ``hosts update`` replaces it. A staged
    selection ``plugins save`` saved is ``saved``; any other is the step's default.
    ``plugin_configs`` holds what each selected plugin is configured with.
    """

    selections: dict[str, dict[str, Any]] = {}
    for step in STEPS:
        path = display_path(bundle_activation_path(bundle, step))
        try:
            staged = read_bundle_activation(bundle, step)
        except (OSError, ValueError, TypeError):
            selections[step] = {
                "activation": path, "source": "invalid", "saved": False, "preset": None, "plugins": [], "plugin_configs": {},
            }
            continue
        activation = staged or builtin_activation(step)
        selections[step] = {
            "activation": path,
            "source": "staged" if staged is not None else "builtin" if activation is not None else "none",
            "saved": staged is not None and bool(staged.metadata.get("saved")),
            "preset": activation_presets({step: activation}).get(step),
            "plugins": list(activation.plugins) if activation is not None else [],
            "plugin_configs": {
                plugin_id: dict(activation.plugin_configs.get(plugin_id, {})) for plugin_id in activation.plugins
            } if activation is not None else {},
        }
    return selections


def decision_activations(bundle: dict[str, str]) -> dict[str, StepActivation | None]:
    """The proposal, plan, and action activations a host would run, with built-ins."""

    return {
        step: read_bundle_activation(bundle, step) or builtin_activation(step)
        for step in DECISION_STEPS
    }


def decision_generation_id(activations: dict[str, StepActivation | None]) -> str:
    return activation_generation_id(activations, prefix="decision")


def decision_identity(bundle: dict[str, str]) -> dict[str, Any]:
    """The staged decision steps and the generation they identify."""

    activations = decision_activations(bundle)
    return {
        "generation_id": decision_generation_id(activations),
        "steps": {
            step: activation.to_payload() if activation is not None else None
            for step, activation in activations.items()
        },
    }


def proposal_plugin_ids(steps: dict[str, Any]) -> list[str]:
    """Selected proposal plugin IDs from a decision identity's step payloads."""

    payload = steps.get("proposal") if isinstance(steps, dict) else None
    plugins = payload.get("plugins") if isinstance(payload, dict) else None
    return list(plugins) if isinstance(plugins, list) else []


__all__ = [
    "absent_step_error",
    "bundle_activation_path",
    "bundle_activation_problems",
    "decision_activations",
    "decision_generation_id",
    "decision_identity",
    "ensure_step_activations",
    "proposal_plugin_ids",
    "CONTROLLER_BUNDLE_KEYS",
    "read_bundle_activation",
    "replace_metadata",
    "save_selections",
    "stage_activation",
    "staged_activation",
    "staging_metadata",
    "staging_host",
    "startup_activations",
    "host_names",
    "write_host_configuration",
    "host_bundle",
]
