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
| `chase-v1-detect-by-color` | `stop_row`, `turn_model`, `approach_model` |

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

Its `turn_model` learns how the car's own turn moves what it sees, in image
widths per second, from the landmarks `sim_cardboard_permanence` tracks. Each
new camera frame closes an interval named by the `avoid_obstacle` maneuver
before and over it (`turn_start`, `turning`, `turn_end` or `straight`), whose
sample is the median shift of the landmarks seen at both ends; each kind's
speed is the median of its last `max_samples`. Boxes at the image edge, or
that change size by more than `max_size_change`, do not count. Slow turning is
not learned, and `stall_intervals` slow intervals in a row are a stall.
Landmarks out of view keep a bearing carried by that speed, and one seen again
records the error of its carried bearing. Nothing is tuned in advance and no
command changes. It lives at `memory.turn_model.state`; `status` reports each
speed with its quartiles, the stalls, the position errors and the landmarks.

Its `approach_model` learns how fast things ahead come closer while the car
drives straight, in image rows and milliseconds on the camera's capture clock.
Each new camera frame closes an interval; when every command over it drove
straight, each lower edge in the band ahead from `sim_wall` or
`sim_cardboard_permanence` that stepped down exactly one of `row_bins` rows
records how long it spent on the row it left. Each row's time is the median of
its last `max_samples`. A straight run that ends when `avoid_obstacle` starts
backing up is how long the car could drive before it had to stop, less any
static time; `run_ms` is their median, and runs that end any other way do not
count. Nothing is tuned in advance and no command changes. It lives at
`memory.approach_model.state`; `status` reports each row's time and samples,
`run_ms` and the runs. Perception's `closeness` reads it.

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
