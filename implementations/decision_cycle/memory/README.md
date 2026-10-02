# Memory

Packaged memory plugins: code that keeps what later frames need from each
cycle's observation. The framework that runs them lives in
`autonomy/decision_cycle/memory/`.

## Layout

| Path | Holds |
|---|---|
| `catalog.py` | `MEMORY_PLUGINS`: one entry per plugin, with its `spec`, description and default config. `DEFAULT_MEMORY_PLUGINS` names the default selection. |
| `plugins/` | One folder per plugin, named for its `plugin_id`. See `plugins/README.md`. |
| `shared/` | Plain library code used by more than one plugin. See `shared/README.md`. |

## Catalog

The catalog says which plugins exist. `implementations/decision_cycle/catalog.py`
builds a step activation from it with `packaged_activation("memory", ...)`,
selecting the default plugins when none are named.
