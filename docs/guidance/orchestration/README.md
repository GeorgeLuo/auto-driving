# Orchestration Policies

Operator-selected execution policies. They do not replace the canonical
[Milestone Planning And Delivery Contract](../../milestones/README.md) and do
not authorize a phase transition that recorded workflow state forbids.

## Invocation

Do not infer a policy. The operator names an immutable id:

```text
Follow orchestration policy ad-hoc-implementation/v3 for this change.
```

```text
Follow orchestration policy review-repair/v1 for PR #<number> through final review.
```

Load that version in addition to role/task guidance from
[agent-surface.md](../agent-surface.md).

## Current policies

| Policy | Current version | Use when |
| --- | --- | --- |
| `ad-hoc-implementation` | [v3](ad-hoc-implementation/v3.md) | Bounded implementation, no accepted proposal, existing authority is enough. |
| `review-repair` | [v1](review-repair/v1.md) | Existing implementation, one frozen review question, remove blockers. |

No other policy IDs exist in this directory.

## Versioning

Id = `<policy>/<version>`.

```yaml
new_version_required_for:
  - chain_of_command
  - authority
  - synchronization
  - reasoning_topology
  - escalation
  - completion
in_place_ok_for: [typo, link, wording_without_semantic_change]
once_used_on_a_real_run: do_not_materially_rewrite
authority: this_directory
not_authority: historical_issue_comments
```

## Shared definitions

```yaml
min_max:
  defined_work:
    examples: [frozen_review, approved_repair, accepted_pattern, owned_validation]
    compute: lowest_capable_available
    tokens: artifact   # source, tests, diffs, evidence
    in_policy_economy_tier: sufficient
    execute_on: child
  steering:
    actors: [root, executive]
    tokens: dense_receipt
    receipt_fields: [head, status, finding_ids, pass_fail, evidence_refs]
    forbidden_inputs:
      [repo_reread, raw_logs, worker_transcripts, poll_wait_output]
    owns: [compile_packet, disposition, recompile, closure]

runtime:
  child_default: lowest_capable_available
  escalate_if:
    [unresolved_uncertainty, missing_capability, lower_tier_failed_with_evidence]
  not_escalate_if: [work_involves_judgment]
  inherit_planner_runtime: false

delegation:
  default: always_after_compiled_packet
  collapse_only_if: not terminal_receipt_without_model_polling
  not_grounds_to_skip:
    [small_or_tightly_coupled, root_already_holds_context, no_independent_parallelism]
  cost_model:
    dense_packet: true
    high_tier_child_overhead_accepted: ~1.1x
    # Skipping that ~10% tax also skips economy-tier attempts (~1/40th work cost).

smell:
  compiled_unit_executed_on_root: requires_explanation
  valid_explanations: [no_terminal_receipt_without_polling]
  invalid:
    [direct_mode_too_small, spawn_child_to_satisfy_metric]

defaults:
  live_subordinates: 1
  coordinator: false
  sync: terminal_receipt
  sync_forbidden: [model_polling, progress_prompts, stdin_peeks, sleep_wait_loops]
  clarification_max: 1
  validation: {one_owner_per_check: true}
  closure_owner: parent
  resume_from: [visible_policy_state, repository_state]
```

Policy-specific invariants win over these defaults. The canonical milestone
contract wins over both.
