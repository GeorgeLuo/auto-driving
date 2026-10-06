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

The catalog says which plugins exist. With one plugin, proposal has no
presets: `implementations/decision_cycle/catalog.py` builds an activation from
a plugin list with `packaged_activation("proposal", plugins)`, and from
`DEFAULT_PROPOSAL_PLUGINS` when the list is omitted. A second plugin brings a
`presets.py`, as perception and memory have. `automa vehicles update proposal
--plugin ID` stages a selection.

Proposal plugins read a detached copy of the cycle's inputs and the host map,
not the camera feed. `avoid_recent_obstruction` steers away from the
obstruction evidence that the `bounded_evidence` memory plugin retains.
