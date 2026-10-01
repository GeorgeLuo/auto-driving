# Perception

Packaged perception plugins: code that turns a sensor snapshot into perception
evidence. The framework that runs them lives in `autonomy/decision_cycle/perception/`.

## Layout

| Path | Holds |
|---|---|
| `catalog.py` | `PERCEPTION_PLUGINS`: one entry per plugin, with its `spec`, description and default config. |
| `presets.py` | `PERCEPTION_PRESETS`: named, ordered selections of catalog plugins, with config overrides. `DEFAULT_PERCEPTION_PRESET` names the default. |
| `plugins/` | One folder per plugin, named for its `plugin_id`. See `plugins/README.md`. |
| `feeds/` | Per-sensor adapters that turn a sensor reading into a typed plugin input. See `feeds/README.md`. |
| `shared/` | Plain library code used by more than one plugin, by memory plugins, or by tools. See `shared/README.md`. |

## Catalog and presets

The catalog says which plugins exist. A preset says which of them to run, in
what order, and with what config overrides. The default selection is a preset
too: `DEFAULT_PERCEPTION_PLUGINS` is the plugin list of
`DEFAULT_PERCEPTION_PRESET`.

`implementations/decision_cycle/catalog.py` builds an activation from a preset
with `perception_preset_activation`. The activation records the preset name in
its `algorithm` metadata key.
