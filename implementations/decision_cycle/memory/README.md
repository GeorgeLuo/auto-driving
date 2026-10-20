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

## Finding the default business logic

Start at [presets.py](presets.py): `DEFAULT_MEMORY_PRESET` selects
`recency_ledger`, which lists `bounded_evidence`. [catalog.py](catalog.py)
names that plugin's entrypoint and default settings, its record, age and size
bounds. [plugins/bounded_evidence/plugin.py](plugins/bounded_evidence/plugin.py)
holds `BoundedEvidenceLedger`, which keeps its ledger at `LEDGER_KEY` in the
host map and publishes the retained records at `EVIDENCE_KEY`. The ledger and
its reduction live in [shared/evidence_ledger/](shared/evidence_ledger/).

## Framework boundary

As in perception and proposal, `autonomy/decision_cycle/memory/` owns the
required plugin contract and protocol (`plugin.py`), composition and failure
policy (`interface.py`), and step execution (`runner.py`). Memory's
`evidence.py` defines the retained evidence records, `beliefs.py` the checked
beliefs, `publication.py` the keys later steps read them at, and `errors.py`
the update failure that stops the cycle. Those types belong to the memory
contract, rather than to a concrete retention strategy.

Memory plugins read the shared map and the observation, not the camera feed:
the framework hands them a frame context without `sensor_frame`. Memory needs
no `feeds/` directory. Obstruction tracking is perception's work: the
`multi_obstruction_tracks` perception plugin keeps track history in the map and
emits tracked things, and `bounded_evidence` retains them for the
`avoid_recent_obstruction` proposal plugin. Library code shared by multiple
memory plugins belongs in `shared/`, as in perception and proposal.

## Beliefs

A memory plugin learns from observations. A `Belief` keeps a learned value,
its source plugin, open `Trial` predictions, and recent `Check` outcomes. Plugins
choose the check methods and support criteria, and pass a positive `max_checks`
and `max_trials` to bound the recent checks and open trials. Samples and
precision belong in the value or `properties`; checks describe whether
predictions held up.

| Perception framework | Memory framework |
|---|---|
| `plugin.py`: required `PerceptionPluginContract` | `plugin.py`: required `MemoryPluginContract` |
| `evidence/values.py`: what is observed | `beliefs.py`: what is learned and checked |
| `feeds/`: sensor inputs | Frame context and observation: memory inputs |

Every plugin declares its description, assumptions, the `belief_ids` it
publishes, and its limitations. A plugin keeps each declared belief at
`belief_key(belief_id)`, `memory.beliefs.<belief_id>`, as the `Belief` record
itself: that key is both its state and its publication. The last write to each
belief key wins. `read_belief` returns the stored `Belief`, or `None` when no
plugin has published it. Retained `memory.evidence` still uses last-write-wins.

A plugin that does not check what it learns declares `belief_ids=()` and
publishes no beliefs, so readers get `None`, never a score. Reset and
removing a plugin delete its declared keys before invoking its optional reset;
fresh state written by reset survives.

Confidence is `None` before the first check, then
`(support + 1) / (support + contradict + 2)` over the retained checks, so it
never reaches 0 or 1. A belief is corroborated when at least two different
methods each have more supports than contradictions. Revising a value keeps
its open trials; withdrawal drops trials without scoring them. The plugin
chooses whether to keep its checks. Reports and recordings include belief
summaries and confidence; consumers do not yet use confidence
to choose behavior. The framework leaves checks and retention policy to each
plugin.
