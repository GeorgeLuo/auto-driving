# Proposal plugins

One folder per plugin, named for its `plugin_id`. For example, the
`avoid_recent_obstruction` plugin lives in `plugins/avoid_recent_obstruction/`.

## Rules

- `plugin.py` holds the plugin class, which declares `plugin_id` and
  implements `propose(context)`, the `ProposalPlugin` protocol in
  `autonomy/decision_cycle/proposal/plugin.py`. `context.shared_memory` is the
  host map, or `None` without one. Ids match
  `[A-Za-z0-9._:-]{1,64}` and are unique within the proposal step:
  registering a second definition under an existing id raises
  `DuplicatePluginIdError`.
- Register a plugin with one entry in the `plugins` list of `../catalog.json`:
  its `spec` (`module.path:Class`), a description, and its default config.
- Name ordered plugin selections and configuration recipes under `presets` in
  the same file, as in perception and memory. `default` names the default preset;
  `plugins` lists available plugins independently of that selection.
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
- Read the cycle from `context` and retained evidence from the host map.
  Read the current observation with `read_observation(context.shared_memory)`
  from `autonomy/decision_cycle/observation/publication.py`.
  `avoid_recent_obstruction` keeps `evidence_key` as its own config and
  defaults to `EVIDENCE_KEY` from
  `autonomy/decision_cycle/memory/publication.py`. Other plugins choose the
  keys they read.
- A plugin may implement `reset()` or `reset(shared_memory)` to begin a new
  epoch. The runner calls it on an explicit reset and when the plugin leaves
  the selection, passing the host map if the method takes an argument. The
  map may be absent in offline use. Reset errors propagate.
- Several plugins share one cycle in selection order: every plugin reads the
  same host map, and every candidate is kept. A plugin error becomes that
  plugin's error candidate and the others still propose, and an absent
  observation is unknown. `autonomy/decision_cycle/proposal/runner.py`
  applies these rules with the candidate admission boundary.
- `ProposalRunner.plugin_report()` reports each applied plugin's last
  invocation duration and error, including isolated synthetic error
  candidates and errors declared by a valid candidate. Records are null
  before invocation and after reset. `status()` counts runs and failures of
  the whole step; isolated candidate errors do not increment its
  `failure_count`. This follows perception's separation of plugin failures
  from failures that stop a frame.
- A plugin does not import another plugin's modules. Code more than one
  plugin needs goes in a `../shared/` package, as in perception and memory.
