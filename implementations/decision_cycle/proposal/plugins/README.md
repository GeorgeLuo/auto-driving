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
  candidate with a reason. The constructor supplies `proposal_id` from those
  two IDs and validates the lifecycle matrix and size bounds in
  `autonomy/decision_cycle/proposal/values.py`. A raising plugin, wrong return
  type, or wrong plugin/frame identity becomes a synthetic error candidate
  under the selected ID. A returned candidate that violates the lifecycle
  matrix or size bounds fails the whole step with
  `action_proposal_matrix_violated` and no candidates. Plan does not plan and
  action fails closed.
- Read the cycle through the `DecisionDataSource` passed in, and retained
  evidence from the host map. A plugin may declare its key as `evidence_key`.
  The step records an audit copy of the first declared key in selection order
  in every plugin's source; it does not merge keys or create a separate audit
  for each plugin. Every plugin still reads its own key directly from the
  shared host map. `avoid_recent_obstruction` defaults to `EVIDENCE_KEY` from
  `autonomy/decision_cycle/memory/publication.py`; the runner supplies no
  default evidence key for other plugins.
- A plugin may implement `reset()` or `reset(shared_memory)` to begin a new
  epoch. The runner calls it on an explicit reset and when the plugin leaves
  the selection, passing the host map if the method takes an argument. The
  map may be absent in offline use. Reset errors propagate.
- How several plugins share one cycle, and what a failure does, is declared in
  `autonomy/decision_cycle/proposal/interface.py`: `composition_declaration`
  (selection order, a detached source copy per plugin, every candidate kept)
  and `FAILURE_POLICY` (a plugin error becomes that plugin's error candidate
  and the others still propose, a reset error propagates, a missing
  observation is still passed in). The runner's `describe_schema()` reports
  these declarations alongside the candidate admission boundary.
- `ProposalRunner.plugin_report()` reports each applied plugin's last
  invocation duration and error, including isolated synthetic error
  candidates and errors declared by a valid candidate. Records are null
  before invocation and after reset. `status()` counts runs and failures of
  the whole step; isolated candidate errors do not increment its
  `failure_count`. This follows perception's separation of plugin failures
  from failures that stop a frame.
- A plugin does not import another plugin's modules. Code more than one
  plugin needs goes in a `../shared/` package, as in perception and memory.
