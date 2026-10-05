# Memory

Packaged memory plugins: code that keeps what later frames need from each
cycle's observation. The framework that runs them lives in
`autonomy/decision_cycle/memory/`.

## Layout

| Path | Holds |
|---|---|
| `catalog.py` | `MEMORY_PLUGINS`: one entry per plugin, with its `spec`, description and default config. |
| `presets.py` | `MEMORY_PRESETS`: named, ordered selections of catalog plugins. `DEFAULT_MEMORY_PRESET` names the default; `DEFAULT_MEMORY_PLUGINS` is its plugin list. |
| `plugins/` | One folder per plugin, named for its `plugin_id`. See `plugins/README.md`. |
| `shared/` | Plain library code used by more than one plugin. See `shared/README.md`. |

## Catalog and presets

The catalog says which plugins exist. A preset says which of them to run, and
in what order. The default selection is a preset too.

`implementations/decision_cycle/catalog.py` builds an activation from a preset
with `preset_activation("memory", name)`, and from a preset or a plugin list
with `selection_activation("memory", ...)`. The activation records the preset
name in its `preset` metadata key; a plugin list is labeled with the preset it
equals, else `custom`. `automa vehicles update memory --preset NAME` stages one.

| Preset | Plugins |
|---|---|
| `recency_ledger` (default) | `bounded_evidence` |

Memory plugins read the shared map and the observation, not the camera feed:
the framework hands them a frame context without `sensor_frame`. Obstruction
tracking is perception's work: the `multi_obstruction_tracks` perception plugin
keeps track history in the map and emits tracked things, and `bounded_evidence`
retains them for the `avoid_recent_obstruction` proposal plugin.
