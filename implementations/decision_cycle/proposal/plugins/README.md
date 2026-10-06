# Proposal plugins

One folder per plugin, named for its `plugin_id`. For example, the
`avoid_recent_obstruction` plugin lives in `plugins/avoid_recent_obstruction/`.

## Rules

- `plugin.py` holds the plugin class, which declares `plugin_id` and
  implements `propose(source, shared_memory)`, the `ProposalPlugin` protocol in
  `autonomy/decision_cycle/proposal/plugin.py`. Ids match
  `[A-Za-z0-9._:-]{1,64}` and are unique within the proposal step:
  registering a second definition under an existing id raises
  `DuplicatePluginIdError`.
- Register a plugin with one entry in `../catalog.py`: its `spec`
  (`module.path:Class`), a description, and its default config.
- Return one `ActionProposal` each cycle, under the plugin's own `plugin_id`
  and the source's `frame_id`. With nothing to propose, return an `inactive`
  candidate with a reason. The step replaces any other return with an error
  candidate under the plugin's id.
- Read the cycle through the `DecisionDataSource` passed in, and retained
  evidence from the host map. A plugin that reads evidence declares that key
  as `evidence_key`, by default `EVIDENCE_KEY` from
  `autonomy/decision_cycle/memory/publication.py`; the step records an audit
  copy of it in the source, and `vehicles info decision` lists it.
- How several plugins share one cycle, and what a failure does, is declared in
  `autonomy/decision_cycle/proposal/interface.py`: `composition_declaration`
  (selection order, a detached source copy per plugin, every candidate kept)
  and `FAILURE_POLICY` (a plugin error becomes that plugin's error candidate
  and the others still propose, a reset error propagates, a missing
  observation is still passed in). `ProposalRunner` reads those values.
- A plugin does not import another plugin's modules. Code more than one
  plugin needs goes in a `../shared/` package, as in perception and memory.
