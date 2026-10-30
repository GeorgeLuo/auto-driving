# Perception

Packaged perception plugins: code that turns a sensor frame into perception
evidence. The framework that runs them lives in `autonomy/decision_cycle/perception/`.

## Layout

| Path | Holds |
|---|---|
| `catalog.json` | `plugins`: one entry per plugin, with its `spec`, description and default config. `presets`: named, ordered selections of those plugins, with config overrides (`plugin_configs`). `default`: the preset `hosts update` stages. |
| `plugins/` | One folder per plugin, named for its `plugin_id`. See `plugins/README.md`. |
| `feeds/` | Per-sensor adapters that turn a sensor reading into a typed plugin input. See `feeds/README.md`. |
| `shared/` | Plain library code used by more than one plugin, by memory plugins, or by tools. See `shared/README.md`. |

## Catalog and presets

The catalog says which plugins exist. A preset says which of them to run, in
what order, and with what config overrides. The default selection is a preset
too, the one `default` names.

`implementations/decision_cycle/catalog.py` reads `catalog.json` and builds an
activation from a preset with `preset_activation("perception", name)`, and from
a preset or a plugin list with `selection_activation("perception", ...)`. The
activation records the preset name in its `preset` metadata key; a plugin list
is labeled with the preset it equals, else `custom`. `automa hosts update`
stages the default.

| Preset | Plugins |
|---|---|
| `lightweight_observer` (default) | `frame`, `floor_plane` |
| `sim_debug` | `frame`, `sim_color_targets` |
| `chase-v1-detect-by-color` | `sim_cardboard_obstacle`, `sim_cardboard_permanence` |
| `visual_observer` | `frame`, `floor_plane`, `motion_tracks` |
| `multi_obstruction` | `multi_obstruction_tracks`, `floor_continuity`, `classical_regions` |
| `obstruction_observer` | `frame`, `floor_plane`, `multi_obstruction_tracks`, with config overrides |

## Finding the default business logic

Start at [catalog.json](catalog.json): its `default`, `lightweight_observer`,
lists `frame` and `floor_plane`, and their `plugins` entries name their
entrypoints and default settings.
[plugins/frame/plugin.py](plugins/frame/plugin.py) holds
`FrameObservationPlugin`, which reports the front camera frame's dimensions and
basic light statistics.
[plugins/floor_plane/plugin.py](plugins/floor_plane/plugin.py) holds
`FloorPlanePlugin`, which estimates the traversable floor and its first-hit
boundaries from the same frame; `model.py` beside it holds the floor model and
`FloorPlaneConfig`.

## Framework boundary

As in memory and proposal, `autonomy/decision_cycle/perception/` owns the
plugin protocol (`plugin.py`), composition and failure policy (`interface.py`),
and step execution (`runner.py`). Perception's `inputs.py` builds each frame's
plugin request, and `evidence/` defines the signals, spatial evidence, and
measurements plugins emit. Those types belong to the perception contract,
rather than to a concrete detector.

Perception is the step that reads sensors. Its plugins declare their inputs
through `feeds/`, which turns a sensor reading into a typed plugin input; the
framework's `feeds/` resolves those declarations per frame. Library code shared
by multiple perception plugins, memory plugins or tools belongs in `shared/`,
as in memory and proposal.
