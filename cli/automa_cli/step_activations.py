"""A host's staged step activations in its local controller bundle.

Every cycle step is staged the same way: ``bundle/runtime/<step>/active.json``
holds a ``StepActivation`` for a host ``staging_host`` knows. The CLI
records who staged it (host, bundle, release, time) in the activation's
``metadata``. CLI hosts use its bundle path to load staged plugin code;
the core step runners do not read it.
The decision steps (proposal, plan, action) together identify a decision
generation by the content of their activations.
"""

from __future__ import annotations

import json
import shlex
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, TextIO

from autonomy.decision_cycle.activation import (
    DECISION_STEPS,
    STEPS,
    StepActivation,
    activation_generation_id,
    activation_presets,
    read_step_activation_if_present,
    require_step,
    step_activation_path,
    write_step_activation,
)
from autonomy.decision_cycle.steps import builtin_activation, step_runner
from autonomy.plugins import DuplicatePluginIdError
from implementations.decision_cycle.catalog import (
    STEP_PRESETS,
    selection_activation,
    step_plugins,
)

from .bundles import (
    controller_bundle_paths,
    release_activation_summary,
    sync_controller_bundle,
)
from .paths import display_path, safe_path_part
from .vehicles.probes import DEFAULT_READINESS_TIMEOUT_S

def host_bundle(host_name: str, runtime_root: Path) -> dict[str, str]:
    return controller_bundle_paths(Path(runtime_root) / safe_path_part(host_name))


def bundle_activation_path(bundle: dict[str, str], step: str) -> Path:
    return step_activation_path(Path(bundle["runtime_dir"]), step)


def read_bundle_activation(bundle: dict[str, str], step: str) -> StepActivation | None:
    return read_step_activation_if_present(bundle_activation_path(bundle, step), step)


def valid_bundle_activation(bundle: dict[str, str], step: str) -> StepActivation | None:
    """The staged activation, or ``None`` when absent or unreadable; restaging replaces either."""

    try:
        return read_bundle_activation(bundle, step)
    except (OSError, ValueError, TypeError):
        return None


def keep_staged_configs(bundle: dict[str, str], activation: StepActivation) -> StepActivation:
    """``activation`` with the staged config of every plugin whose spec is unchanged.

    A plugin list changes which plugins run, not how each one is configured,
    so a running host can still adopt it without a restart.
    """

    staged = valid_bundle_activation(bundle, activation.step)
    if staged is None:
        return activation
    configs = dict(activation.plugin_configs)
    for plugin_id, spec in activation.plugin_specs.items():
        if staged.plugin_specs.get(plugin_id) != spec:
            continue
        if plugin_id in staged.plugin_configs:
            configs[plugin_id] = deepcopy(staged.plugin_configs[plugin_id])
        else:
            configs.pop(plugin_id, None)
    return StepActivation(
        step=activation.step,
        plugins=activation.plugins,
        plugin_specs=activation.plugin_specs,
        plugin_configs=configs,
        metadata=activation.metadata,
        source_path=activation.source_path,
    )


def changed_plugins(previous: StepActivation | None, staged: StepActivation) -> list[str]:
    """Plugin IDs whose spec or config ``staged`` changes; a running host adopts them only on restart."""

    if previous is None:
        return []
    return sorted(
        plugin_id
        for plugin_id in {*previous.plugin_specs, *staged.plugin_specs}
        if previous.plugin_specs.get(plugin_id) != staged.plugin_specs.get(plugin_id)
        or previous.plugin_configs.get(plugin_id) != staged.plugin_configs.get(plugin_id)
    )


def bundle_activation_problems(
    bundle: dict[str, str], host_name: str, *, steps: tuple[str, ...] = STEPS,
) -> list[dict[str, str]]:
    """Diagnose existing staged documents using the core's common step reader.

    Missing optional steps retain their built-in or empty behavior. An invalid
    document needs explicit restaging; checking never changes its selection or
    configs. Each diagnosis includes the owning step's update command. Callers
    pass the ``steps`` they read, so a command reports exactly what blocks it.
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
                "command": f"./cli/automa hosts stage {step} --name {shlex.quote(host_name)}",
            })
    return problems


def format_activation_problems(problems: list[dict[str, str]]) -> str:
    """Render the same step-specific recovery for startup and status callers."""

    return "\n".join(
        line
        for problem in problems
        for line in (
            f"Invalid {problem['step']} activation: {problem['activation']}",
            f"Reason: {problem['reason']}",
            f"Restage with your intended selection: {problem['command']}",
        )
    )


def apply_staged(host_name: str, provider: Any, step: str, *, changed: list[str] | None = None) -> dict[str, Any]:
    """How a vehicle's running autonomy picks up ``step`` as ``hosts stage`` staged it.

    Every host selects a restaged step's plugins on its next frame. A Chase
    host reads the local runtime directory, so nothing needs to run
    (``selection_command`` is ``None``); a PiCar reads the copy ``hosts update``
    installs onboard. Changed plugin specs or configs take
    effect after ``restart_command``, the same for every vehicle: package a
    release and restart the host onto it. ``changed`` names the plugins whose
    spec or config this restage changed.
    """

    live = not changed
    install = f"./cli/automa hosts update --name {host_name}"
    selection = install if provider == "picar" else None
    restart = f"{install} --restart"
    return {
        "live_selection": live,
        "selection_command": selection if live else restart,
        "restart_command": restart,
        "changed_plugins": list(changed or ()),
    }


def format_apply_staged(apply: dict[str, Any]) -> list[str]:
    if apply.get("changed_plugins"):
        return [f"Apply: {apply.get('restart_command')} "
                f"(specs or configs changed: {', '.join(apply['changed_plugins'])})"]
    if not apply.get("live_selection"):
        return [f"Apply: {apply.get('restart_command')}"]
    selection = apply.get("selection_command") or "automatic on the host's next frame"
    return [
        f"Apply selection: {selection}",
        f"Apply changed specs or configs: {apply.get('restart_command')}",
    ]


def absent_step_error(step: str, host_name: str) -> str:
    """The same guidance for every vehicle whose running autonomy lacks ``step``."""

    restart = apply_staged(host_name, None, step)["restart_command"]
    return (
        f"{host_name} runs no {step} step. Stage it with "
        f"./cli/automa hosts stage {step} --name {host_name}, then restart: {restart}"
    )


HOST_PROVIDERS = ("chase-sim", "picar")


def staging_host(
    host_name: str,
    *,
    runtime_root: Path,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    output: TextIO | None = None,
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
                or configuration.get("provider") not in HOST_PROVIDERS
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
                "kind": "ws_cli_controller" if vehicle.get("provider") == "chase-sim" else "onboard_controller",
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


def refresh_release(bundle: dict[str, str], step: str, release: dict[str, Any]) -> Path | None:
    """Record ``release`` on an existing activation; returns its path when present."""

    activation = read_bundle_activation(bundle, step)
    if activation is None:
        return None
    metadata = deepcopy(dict(activation.metadata))
    controller_bundle = dict(metadata.get("controller_bundle") or {})
    controller_bundle["release"] = release_activation_summary(release)
    metadata["controller_bundle"] = controller_bundle
    return write_step_activation(
        bundle_activation_path(bundle, step), replace_metadata(activation, metadata)
    )


def ensure_step_activations(
    *,
    host_name: str,
    bundle: dict[str, str],
    release: dict[str, Any],
    vehicle: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Stage every step a deploy ships, recording ``release`` on each.

    A selection staged as a named preset is rebuilt from the catalog, so the
    deploy ships the preset's current plugins and configs. Any other staged
    selection keeps its plugins and configs. A step with nothing staged gets
    its default selection.
    """

    paths: dict[str, Path] = {}
    for step in STEPS:
        existing = read_bundle_activation(bundle, step)
        preset = existing.metadata.get("preset") if existing is not None else None
        named = isinstance(preset, str) and preset in STEP_PRESETS.get(step, {})
        if existing is not None and not named:
            refresh_release(bundle, step, release)
            paths[step] = bundle_activation_path(bundle, step)
            continue
        paths[step] = stage_activation(
            bundle,
            selection_activation(step, preset=preset),
            host_name=host_name,
            release=release,
            vehicle=vehicle,
        )
    return paths


def staged_selections(bundle: dict[str, str]) -> dict[str, dict[str, Any]]:
    """What a start runs for every step, and where each selection comes from.

    A step runs its staged activation, else its built-in, else nothing. An
    unreadable document is ``invalid``; its update command restages it.
    """

    selections: dict[str, dict[str, Any]] = {}
    for step in STEPS:
        path = display_path(bundle_activation_path(bundle, step))
        try:
            staged = read_bundle_activation(bundle, step)
        except (OSError, ValueError, TypeError):
            selections[step] = {"activation": path, "source": "invalid", "preset": None, "plugins": []}
            continue
        activation = staged or builtin_activation(step)
        selections[step] = {
            "activation": path,
            "source": "staged" if staged is not None else "builtin" if activation is not None else "none",
            "preset": activation_presets({step: activation}).get(step),
            "plugins": list(activation.plugins) if activation is not None else [],
        }
    return selections


def selection_label(preset: str | None, plugins: list[str]) -> str:
    """A selection as deploy, run and status print it: its preset and plugins."""

    listed = ", ".join(plugins)
    return f"{preset} ({listed or 'no plugins'})" if preset else listed or "(no plugins)"


def step_lines(steps: dict[str, list[str]], presets: dict[str, str]) -> list[str]:
    return [
        f"{step.capitalize()}: {selection_label(presets.get(step), plugins)}"
        for step, plugins in steps.items()
    ]


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


def step_update_error(
    host_name: str, step: str, error: str, message: str,
    *, json_output: bool, **details: Any,
) -> tuple[int, str]:
    """One staging error envelope; vehicle resolution failures use it for every step."""

    if not json_output:
        return 2, message
    return 2, json.dumps(
        {
            "schema": "host_step_update_error",
            "host_name": host_name,
            "step": step,
            "error": error,
            "message": message,
            **details,
        },
        indent=2,
        sort_keys=True,
    )


def stage_host_step(
    *,
    host_name: str,
    step: str,
    plugins: list[str] | None = None,
    preset: str | None = None,
    runtime_root: Path,
    timeout_s: float = DEFAULT_READINESS_TIMEOUT_S,
    dry_run: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    output: TextIO | None = None,
) -> tuple[int, str]:
    """Stage a preset or plugin list; use the step's default when omitted."""

    require_step(step)

    try:
        activation = selection_activation(step, preset=preset, plugins=plugins)
        # Construct the runner once so a selection that cannot load is never staged.
        step_runner(activation)
    except DuplicatePluginIdError:
        # A packaged-catalog clash is not a bad selection; the CLI reports it.
        raise
    except (TypeError, ValueError) as exc:
        return step_update_error(
            host_name, step,
            "invalid_selection",
            f"Cannot stage {step} plugins: {exc}",
            json_output=json_output,
            available_plugins=sorted(step_plugins(step)),
        )

    vehicle, unknown = staging_host(
        host_name, runtime_root=runtime_root, timeout_s=timeout_s, output=output if verbose else None
    )
    if unknown is not None:
        return step_update_error(host_name, step, "unknown_host", unknown, json_output=json_output)

    bundle = host_bundle(host_name, runtime_root)
    previous = valid_bundle_activation(bundle, step)
    if plugins is not None:
        activation = keep_staged_configs(bundle, activation)
    path = bundle_activation_path(bundle, step)
    release: dict[str, Any] | None = None
    if not dry_run:
        release = sync_controller_bundle(bundle, output=output if verbose else None)
    # A dry run reports the document it would write, provenance included.
    staged = staged_activation(bundle, activation, host_name=host_name, release=release, vehicle=vehicle)
    if not dry_run:
        write_step_activation(path, staged)
    payload = {
        "schema": "host_step_update",
        "host_name": host_name,
        "step": step,
        "preset": staged.metadata.get("preset"),
        "plugins": list(staged.plugins),
        "dry_run": dry_run,
        "activation": display_path(path),
        "manifest": staged.to_payload(),
        "release": release_activation_summary(release) if release is not None else None,
        "apply": apply_staged(host_name, vehicle.get("provider"), step,
                              changed=changed_plugins(previous, staged)),
    }
    if json_output:
        return 0, json.dumps(payload, indent=2, sort_keys=True)
    verb = "Would stage" if dry_run else "Staged"
    return 0, "\n".join(
        [
            f"{verb} {step}: {host_name} -> {', '.join(staged.plugins) or '(no plugins)'}",
            *([f"Preset: {payload['preset']}"] if payload["preset"] is not None else []),
            f"Activation: {display_path(path)}",
            *format_apply_staged(payload["apply"]),
        ]
    )


__all__ = [
    "absent_step_error",
    "apply_staged",
    "format_apply_staged",
    "bundle_activation_path",
    "bundle_activation_problems",
    "decision_activations",
    "decision_generation_id",
    "decision_identity",
    "ensure_step_activations",
    "format_activation_problems",
    "proposal_plugin_ids",
    "CONTROLLER_BUNDLE_KEYS",
    "read_bundle_activation",
    "refresh_release",
    "replace_metadata",
    "stage_activation",
    "staged_activation",
    "staging_metadata",
    "staging_host",
    "host_names",
    "write_host_configuration",
    "step_update_error",
    "stage_host_step",
    "host_bundle",
]
