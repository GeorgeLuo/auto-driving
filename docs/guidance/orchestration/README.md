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

## Archived policies

| Policy | Version | Use when |
| --- | --- | --- |
| `ad-hoc-implementation` | [v2](ad-hoc-implementation/v2.md) | Reconstructing a historical run that explicitly named `ad-hoc-implementation/v2`. |

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
  - shared_definitions
in_place_ok_for: [typo, link, wording_without_semantic_change]
once_used_on_a_real_run: do_not_materially_rewrite
authority: this_directory
not_authority: historical_issue_comments
```

## Shared definitions

Policies pin the immutable [shared/v1](shared/v1.md) definitions. Substantive
changes to shared execution rules require a new shared version; this index
contains no shared semantic definitions.

Policy-specific invariants win over the pinned shared defaults. The canonical
milestone contract wins over both.
