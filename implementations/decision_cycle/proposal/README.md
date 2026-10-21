# Proposal

Packaged proposal plugins: code that turns the cycle's observation and the
evidence memory retained into a candidate command. The framework that runs
them lives in `autonomy/decision_cycle/proposal/`.

## Layout

| Path | Holds |
|---|---|
| `catalog.py` | `PROPOSAL_PLUGINS`: one entry per plugin, with its `spec`, description and default config. |
| `presets.py` | `PROPOSAL_PRESETS`: named, ordered selections of catalog plugins. `DEFAULT_PROPOSAL_PRESET` names the default; `DEFAULT_PROPOSAL_PLUGINS` is its plugin list. |
| `plugins/` | One folder per plugin, named for its `plugin_id`. See `plugins/README.md`. |

## Catalog and presets

The catalog says which plugins exist. A preset says which of them to run, and
in what order. The default selection is a preset too, as in perception and
memory.

`implementations/decision_cycle/catalog.py` builds an activation from a preset
with `preset_activation("proposal", name)`, and from a preset or a plugin list
with `selection_activation("proposal", ...)`. The activation records the preset
name in its `preset` metadata key; a plugin list is labeled with the preset it
equals, else `custom`. `automa vehicles update proposal --preset NAME` stages one.

| Preset | Plugins |
|---|---|
| `obstruction_avoidance` (default) | `avoid_recent_obstruction` |
| `chase-v1-detect-by-color` | `drive_forward`, `drive_backwards`, `avoid_obstacle` |

The matching Chase v1 perception, proposal and plan presets share the name
`chase-v1-detect-by-color`. Proposal's `drive_forward` always requests steering
`0.0` and throttle `0.60` (configurable), regardless of detections or retained
memory. `drive_backwards` likewise always requests steering `0.0` and throttle
`-0.60`. Both run every cycle; the plan picks one. They are the indiscriminate
movement baselines for later stopping and avoidance comparisons. Their
confidence describes the fixed command choice, not whether the path is clear.
`avoid_obstacle` is the collision recovery: after `view_static` while
`obstacle_in_sight` or `wall_in_sight`, it requests reverse (`-0.60`) until
perception's `clearance_right` is true, then a full right turn (steering `1.0`,
throttle `0.60`) until `clearance_ahead` is true. If the view stays static for
more than 8 cycles while reversing, the car is blocked behind and turns right
anyway; a turn that stalls reverses again. Its phase and static-cycle count
live in shared memory at `proposal.avoid_obstacle.maneuver`; outside the
maneuver its candidate is inactive. Once memory's `stop_row` has learned a stop
row (belief `stop_row.row`), the same maneuver also starts before a
collision, when `clearance_ahead`'s `nearest_bottom_y` reaches that row.
Short of that row, when perception's `closeness_ahead` is `near` and
`closeness_left` or `closeness_right` has more reach (an empty band has
unbounded reach), it turns toward the side with the most reach, right on a
tie, while that side still has more reach than ahead; it backs up if the box
ahead reaches the stop row or the view goes static.

## Finding the default business logic

Start at [presets.py](presets.py): `DEFAULT_PROPOSAL_PRESET` selects
`obstruction_avoidance`, which lists `avoid_recent_obstruction`.
[catalog.py](catalog.py) names that plugin's entrypoint and default settings.
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
