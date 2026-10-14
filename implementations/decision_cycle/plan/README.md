# Plan

Packaged plan plugins: code that selects one candidate from the proposal
step. The framework that runs them lives in `autonomy/decision_cycle/plan/`.

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
equals, else `custom`. `automa vehicles update plan --preset NAME` stages one.

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

`autonomy/decision_cycle/plan/` owns the plugin protocol (`plugin.py`), the
step contract (`interface.py`), the step runner (`runner.py`), and the plan
record (`values.py`). `interface.py` declares `composition_declaration` (one
plugin; the proposal step's candidates, `frame_id`, and `timestamp_ms` in;
one `ActionPlan` whose `selector_id` is the plugin ID out) and `FAILURE_POLICY`
(a plugin error or a plan that fails the runner's check returns no plan and
the action holds, a reset error propagates, a failed proposal skips the
plugin). An absent proposal step still calls the plugin with no candidates.
The runner reads that policy, and `describe_schema()` reports it.
