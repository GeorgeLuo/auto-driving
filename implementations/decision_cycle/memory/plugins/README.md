# Memory plugins

One folder per plugin, named for its `plugin_id`. For example, the
`bounded_evidence` plugin lives in `plugins/bounded_evidence/`.

## Rules

- `plugin.py` holds the plugin class, which declares `plugin_id`. Ids are
  unique within the memory step: registering a second definition under an
  existing id raises `DuplicatePluginIdError`. Ids are scoped to a step, so a
  perception plugin may share a memory plugin's id.
- Implement `update(context)`. The runner hands it the frame context without
  `sensor_frame`. Read the current observation with
  `read_observation(context.shared_memory)` from
  `autonomy/decision_cycle/observation/publication.py`; evidence from the feed
  reaches memory through perception and the observation.
- The plugin class exposes `contract`, a `MemoryPluginContract`
  (`autonomy/decision_cycle/memory/plugin.py`): what it does, its
  assumptions, the `belief_ids` it publishes, and its limitations. A plugin
  that does not check what it learns declares `belief_ids=()` and publishes
  no beliefs; readers then get `None`, never a score. The last write to each
  belief key wins. Belief values must be JSON data; constructing a non-JSON
  value raises in the plugin update and stops the cycle.
- Register a plugin with one entry in the `plugins` list of `../catalog.json`:
  its `spec` (`module.path:Class`), a description, and its default config.
- Name ordered plugin selections and configuration recipes under `presets` in
  the same file, as in perception and proposal. `default` names the default preset;
  `plugins` lists available plugins independently of that selection.
- Keep cross-frame state in `context.shared_memory`, under keys the plugin
  owns. Publish what later steps read at the keys named in
  `autonomy/decision_cycle/memory/publication.py`: `EVIDENCE_KEY` for
  retained evidence records, and `belief_key(belief_id)` for each `Belief`
  the contract declares.
- A retained-evidence ledger's status summary uses the names in
  `autonomy/decision_cycle/memory/interface.py`: `LEDGER_SUMMARY_KEYS`
  (`epoch_id`, `health`, `bounds`, `record_count`). `health` is `empty` or
  `healthy`. The framework does not require them. Live CLI probes return null
  for missing fields. Inspect frame rows default a missing `record_count` to
  0, preserve an explicit null, and omit `bounds`. The reset check reads
  `health` and `record_count` from every applied plugin. A reset emptied the
  memory when each plugin reports `empty` health or no records.
- An optional `status(shared_memory)` returns JSON data. Invalid status data
  appears as a visible `status_error` for that plugin.
- The report keeps every applied plugin's status, keyed by `plugin_id`, and
  names `evidence_publisher`: the plugin whose value `EVIDENCE_KEY` holds.
  Inspect, the live probes, the PiCar pages, and the workbench each list
  every plugin and that publisher. The evidence slot itself stays one value,
  last write wins.
  Publisher tracking compares object identity before and after `update` or
  `reset`: replacing the value names that plugin; removing the key clears the
  publisher. Reassigning the same object or changing it in place keeps the
  previous publisher. Publish a replacement value to attribute new evidence
  to this plugin; see `autonomy/decision_cycle/memory/runner.py`.
- Several plugins share that map in selection order: one `EVIDENCE_KEY`,
  last write wins, one value per belief key. What a failure does is declared
  as `FAILURE_POLICY` in `autonomy/decision_cycle/memory/interface.py` (an
  update failure stops the cycle, a reset failure is recorded, plugins are
  still called when `read_observation` returns `None`). `MemoryRunner` reads
  it.
- Reuse goes through `../shared/`. A plugin does not import another plugin's
  modules.
