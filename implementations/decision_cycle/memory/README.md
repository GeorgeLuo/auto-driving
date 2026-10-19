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
| `chase-v1-detect-by-color` | `stop_row` |

The Chase v1 preset's `stop_row` learns how near an obstacle ahead may come
before proposal's `avoid_obstacle` starts reversing. Its hypothesis is an
image row for perception's `clearance_ahead` `nearest_bottom_y`: the first
collision while driving forward starts it, each stop at it (the maneuver's
`stop_phase`, up to `settle_ms`) then either keeps that lower edge above
`blind_row` (stopped short) or not (too close), and the row is bisected between
the deepest that stopped short and the shallowest that came too close. Later
collisions are counted but do not move the row. It lives in shared memory at `memory.stop_row.hypothesis`;
`status` reports the row, its bounds and the outcome counts, and `reset`
forgets it.

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
plugin protocol (`plugin.py`), composition and failure policy (`interface.py`),
and step execution (`runner.py`). Memory's `evidence.py` defines the retained
evidence records, `publication.py` the key later steps read them at, and
`errors.py` the update failure that stops the cycle. Those types belong to the
memory contract, rather than to a concrete retention strategy.

Memory plugins read the shared map and the observation, not the camera feed:
the framework hands them a frame context without `sensor_frame`. Memory needs
no `feeds/` directory. Obstruction tracking is perception's work: the
`multi_obstruction_tracks` perception plugin keeps track history in the map and
emits tracked things, and `bounded_evidence` retains them for the
`avoid_recent_obstruction` proposal plugin. Library code shared by multiple
memory plugins belongs in `shared/`, as in perception and proposal.
