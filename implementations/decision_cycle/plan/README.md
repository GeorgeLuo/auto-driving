# Plan

Packaged plan plugins: code that turns the proposal step's candidates into
the one command the action step applies, by selecting a candidate or
composing several. The framework that runs them lives in
`autonomy/decision_cycle/plan/`.

## Layout

| Path | Holds |
|---|---|
| `catalog.py` | `PLAN_PLUGINS`: one entry per plugin, with its `spec`, description and default config. |
| `presets.py` | `PLAN_PRESETS`: named, ordered selections of catalog plugins. `DEFAULT_PLAN_PRESET` names the default; `DEFAULT_PLAN_PLUGINS` is its plugin list. |
| `plugins/` | One folder per plugin, named for its `plugin_id`. See `plugins/README.md`. |

## Catalog and presets

The catalog says which plugins exist. A preset says which of them to run, and
in what order. The default selection is a preset too, as in perception,
memory and proposal.

`implementations/decision_cycle/catalog.py` builds an activation from a preset
with `preset_activation("plan", name)`, and from a preset or a plugin list
with `selection_activation("plan", ...)`. The activation records the preset
name in its `preset` metadata key; a plugin list is labeled with the preset it
equals, else `custom`. `automa hosts update` stages the default.

| Preset | Plugins |
|---|---|
| `most_confident` (default) | `highest_confidence` |

## Finding the default business logic

Start at [presets.py](presets.py): `DEFAULT_PLAN_PRESET` selects
`most_confident`, which lists `highest_confidence`.
[catalog.py](catalog.py) names that plugin's entrypoint.
[plugins/highest_confidence/plugin.py](plugins/highest_confidence/plugin.py)
holds `select_highest_confidence_plan` and `HighestConfidencePlan`. The plugin
selects the most confident active candidate; with none, the plan is idle.

## Framework boundary

As in perception, memory, and proposal, `autonomy/decision_cycle/plan/` owns
the plugin protocol (`plugin.py`), composition and failure policy
(`interface.py`), and step execution (`runner.py`). Plan's `values.py` defines
the plan record. The runner checks that a returned plan matches the frame, the
plugin ID, and the proposal step's candidates. A plugin failure or a plan that
fails that check returns no plan, and the action step holds.
