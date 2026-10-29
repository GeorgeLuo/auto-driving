# Proposal

Packaged proposal plugins: code that turns the cycle's observation and the
evidence memory retained into a candidate command. The framework that runs
them lives in `autonomy/decision_cycle/proposal/`.

## Layout

| Path | Holds |
|---|---|
| `catalog.json` | `plugins`: one entry per plugin, with its `spec`, description and default config. `presets`: named, ordered selections of those plugins. `default`: the preset `hosts update` stages. |
| `plugins/` | One folder per plugin, named for its `plugin_id`. See `plugins/README.md`. |

## Catalog and presets

The catalog says which plugins exist. A preset says which of them to run, and
in what order. The default selection is a preset too, as in perception and
memory.

`implementations/decision_cycle/catalog.py` reads `catalog.json` and builds an
activation from a preset with `preset_activation("proposal", name)`, and from a
preset or a plugin list with `selection_activation("proposal", ...)`. The
activation records the preset name in its `preset` metadata key; a plugin list
is labeled with the preset it equals, else `custom`. `automa hosts update`
stages the default.

| Preset | Plugins |
|---|---|
| `obstruction_avoidance` (default) | `avoid_recent_obstruction` |

## Finding the default business logic

Start at [catalog.json](catalog.json): its `default`, `obstruction_avoidance`,
lists `avoid_recent_obstruction`, and that plugin's `plugins` entry names its
entrypoint and default settings.
[plugins/avoid_recent_obstruction/plugin.py](plugins/avoid_recent_obstruction/plugin.py)
holds its `propose` function and `AvoidRecentObstruction` class. The plugin
steers away from lateral obstruction evidence retained in shared memory;
without usable evidence it proposes no command.

## Framework boundary

As in perception and memory, `autonomy/decision_cycle/proposal/` owns the
plugin protocol (`plugin.py`), composition and failure policy (`interface.py`),
and step execution (`runner.py`). `values.py` and `result.py` define candidate
commands and their aggregate cycle result. Those types belong to the proposal
contract, rather than to a concrete avoidance strategy.

Proposal plugins read the cycle context and the host map, not a camera
feed. `avoid_recent_obstruction` reads the obstruction evidence that the
`bounded_evidence` memory plugin retains. Proposal needs no `feeds/` directory.
Library code shared by multiple proposal plugins belongs in `shared/`, as in
perception and memory; no such shared implementation is needed yet.
