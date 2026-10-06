# Proposal

Packaged proposal plugins: code that turns the cycle's observation and the
evidence memory retained into a candidate command. The framework that runs
them lives in `autonomy/decision_cycle/proposal/`.

## Layout

| Path | Holds |
|---|---|
| `catalog.py` | `PROPOSAL_PLUGINS`: one entry per plugin, with its `spec`, description and default config. `DEFAULT_PROPOSAL_PLUGINS` is the default selection. |
| `inspection.py` | Left and right obstruction scenarios for the selected plugins, which `automa vehicles decision inspect` runs side by side. |
| `plugins/` | One folder per plugin, named for its `plugin_id`. See `plugins/README.md`. |

## Catalog and selection

The catalog says which plugins exist. Proposal currently selects an ordered
plugin list without named configuration presets:
`implementations/decision_cycle/catalog.py` builds an activation with
`packaged_activation("proposal", plugins)`, and from
`DEFAULT_PROPOSAL_PLUGINS` when the list is omitted. Named configuration
recipes belong in `presets.py` when needed, as in perception and memory;
their usefulness does not depend on the number of plugins. `automa vehicles
update proposal --plugin ID` stages a selection.

Proposal plugins read a detached copy of the cycle's inputs and the host map,
not the camera feed. `avoid_recent_obstruction` steers away from the
obstruction evidence that the `bounded_evidence` memory plugin retains.
