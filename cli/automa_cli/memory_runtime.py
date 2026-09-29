"""CLI-owned memory bundle loading and live selection sync."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autonomy.memory import PluginMemoryRunner, MemoryActivation, read_memory_activation
from autonomy.memory.activation import memory_selection_config

from .staged_bundle import StagedBundleImport


# Implementations and lab plugins come from the staged bundle. autonomy stays
# on the host so DecisionCycle sees the host MemorySnapshot class.
_BUNDLE_PREFIXES = ("implementations", "lab")


def load_memory_step_from_bundle(activation: MemoryActivation) -> PluginMemoryRunner:
    bundle = activation.payload.get("controller_bundle", {})
    root = bundle.get("root_dir") if isinstance(bundle, dict) else None
    if not root:
        return PluginMemoryRunner(activation)
    if not Path(root).is_dir():
        raise FileNotFoundError(f"Controller bundle is missing: {root}")
    import_context = StagedBundleImport(Path(root), _BUNDLE_PREFIXES)
    with import_context.activate():
        step = PluginMemoryRunner(activation)
    for method_name in ("update", "reset", "snapshot"):
        method = getattr(step, method_name)

        def invoke_in_bundle(*args, _method=method, **kwargs):
            with import_context.activate():
                return _method(*args, **kwargs)

        setattr(step, method_name, invoke_in_bundle)
    return step


def _sync_live_memory_plugin_selection(
    step: PluginMemoryRunner,
    activation_path: Path,
    *,
    loaded_config: dict[str, Any],
) -> None:
    """Synchronize CLI selection edits into the running manager before a cycle."""

    try:
        live_config = memory_selection_config(read_memory_activation(activation_path))
        # As in perception, changed code/config/catalog requires a worker restart.
        if any(live_config[key] != loaded_config[key] for key in ("plugin_specs", "plugin_configs")):
            return
        plugin_ids = live_config["plugins"]
        if tuple(plugin_ids) != step.plugin_manager.selected_ids:
            step.plugin_manager.select(plugin_ids)
    except (OSError, ValueError, TypeError):
        # An incomplete or stale activation must not replace the current set.
        return
