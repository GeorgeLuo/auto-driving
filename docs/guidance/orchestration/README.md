# Orchestration Policies

Operator-selected execution policies. They organize how work is delegated and
synchronized. They are usable on their own.

## Invocation

Do not infer a policy. The operator names an immutable id:

```text
Follow orchestration policy ad-hoc-implementation/v4 for this change.
```

```text
Follow orchestration policy review-repair/v2 for PR #<number> through final review.
```

```text
Follow orchestration policy product-implementation/v1 for this change.
Deliver ready for independent review.
```

Load that version in addition to role guidance from
[agent-surface.md](../agent-surface.md).

## Current policies

| Policy | Current version | Shared pin | Use when |
| --- | --- | --- | --- |
| `ad-hoc-implementation` | [v4](ad-hoc-implementation/v4.md) | [v2](shared/v2.md) | Bounded implementation; existing authority is enough. |
| `review-repair` | [v2](review-repair/v2.md) | [v2](shared/v2.md) | Existing implementation, one frozen review question, remove blockers. |
| `product-implementation` | [v1](product-implementation/v1.md) | [v2](shared/v2.md) | Larger implementation with ready-for-review delivery. |

## Reference workflows

These informal references can help with a specifically requested workflow.
They are not versioned policies and do not establish authority or default
execution behavior.

| Reference | Use when |
| --- | --- |
| [Planner-grader experiment](references/MULTI-AGENT-PLANNER-GRADER-INIT.md) | The operator asks to use this approach for solution-space exploration. |

Example request:

```text
Use the planner-grader experiment reference to explore whether <approach> is
viable under <conditions>.
```

## Archived policies

| Policy | Version | Shared pin | Use when |
| --- | --- | --- | --- |
| `ad-hoc-implementation` | [v2](ad-hoc-implementation/v2.md) | [v1](shared/v1.md) | Reconstructing a historical run that explicitly named `ad-hoc-implementation/v2`. |
| `ad-hoc-implementation` | [v3](ad-hoc-implementation/v3.md) | [v1](shared/v1.md) | Reconstructing a historical run that explicitly named `ad-hoc-implementation/v3`. |
| `review-repair` | [v1](review-repair/v1.md) | [v1](shared/v1.md) | Reconstructing a historical run that explicitly named `review-repair/v1`. |

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

Each policy pins one immutable shared version. This index contains no shared
semantic definitions.

| Shared | Status | Pin when |
| --- | --- | --- |
| [v1](shared/v1.md) | frozen | Reconstructing a run or policy that names `shared/v1`. |
| [v2](shared/v2.md) | current | New policies; terminal receipt with liveness wait. |

Substantive changes to shared execution rules require a new shared version.
Policy-specific invariants win over the pinned shared defaults.
