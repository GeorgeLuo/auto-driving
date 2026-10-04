# Memory plugins

One folder per plugin, named for its `plugin_id`. For example, the
`bounded_evidence` plugin lives in `plugins/bounded_evidence/`.

## Rules

- `plugin.py` holds the plugin class, which declares `plugin_id`. Ids are
  unique within the memory step: registering a second definition under an
  existing id raises `DuplicatePluginIdError`. Ids are scoped to a step, so a
  perception plugin may share a memory plugin's id.
- Memory plugins do not read the camera feed. The runner hands them the frame
  context without `sensor_frame`; evidence from the feed comes through the
  observation, from a perception plugin.
- Register a plugin with one entry in `../catalog.py`: its `spec`
  (`module.path:Class`), a description, and its default config.
- Keep cross-frame state in `context.shared_memory`, under keys the plugin
  owns. Publish what later steps read at the keys named in
  `autonomy/decision_cycle/memory/publication.py`: `OBSERVATION_KEY` for a
  replacement observation, `EVIDENCE_KEY` for retained evidence records.
- Reuse goes through `../shared/`. A plugin does not import another plugin's
  modules.
