# Plan plugins

One folder per plugin, named for its `plugin_id`. The `highest_confidence`
plugin lives in `plugins/highest_confidence/`.

## Rules

- The plan step runs exactly one plugin. Arming a plugin replaces the current one.
- `plugin.py` holds the plugin class, which declares `plugin_id` and
  implements `plan(context, candidates)`, the `PlanPlugin` protocol in
  `autonomy/decision_cycle/plan/plugin.py`. Frame identity, the timestamp, and
  the host map come from `context`. Read the current observation with
  `read_observation(context.shared_memory)` from
  `autonomy/decision_cycle/observation/publication.py`.
  `ActionProposal.is_active(frame_id)` says which candidates a plan may act
  on. Ids match
  `[A-Za-z0-9._:-]{1,64}` and are unique within the plan step:
  registering a second definition under an existing id raises
  `DuplicatePluginIdError`.
- Register a plugin with one entry in the `plugins` list of `../catalog.json`:
  its `spec` (`module.path:Class`), a description, and its default config.
- Name the ordered selection under `presets` in the same file. `default`
  names the default preset; `plugins` lists available plugins independently of
  that selection.
- Return one `ActionPlan`. Its `selector_id` is the plugin ID. The plan's
  `frame_id` matches the cycle frame, and its candidates are the proposal
  step's candidates. `PlanRunner._check_plan` rejects a plan that does not.
  Its `status` is one of:

  | Status | Command, reason, confidence | Contributions |
  |---|---|---|
  | `idle` | none | none |
  | `selected` | the candidate's, named by `selected_proposal_id` | that candidate, weight 1.0, role `selected` |
  | `composed` | the plan's own; confidence may be `None` | the candidates behind the command, each with a finite weight and a role |

- How the plugin plans, and what a failure does, is declared in
  `autonomy/decision_cycle/plan/interface.py`: `composition_declaration`
  (one plugin; the cycle context and the proposal candidates in; one
  `ActionPlan` out) and `FAILURE_POLICY` (a plugin error or a plan that fails the check
  returns no plan and the action holds, a reset error propagates, a failed
  proposal skips the plugin). An absent proposal step still calls the plugin
  with no candidates. The runner's `describe_schema()` reports these
  declarations.
