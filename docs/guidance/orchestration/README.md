# Orchestration Policies

This directory contains operator-selected orchestration policies for recurring
agent work shapes. A policy defines chain of command, communication boundaries,
execution topology, escalation, validation ownership, and termination. It does
not replace the repository's canonical planning and delivery contract.

The canonical [Milestone Planning And Delivery Contract](../../milestones/README.md)
wins if a policy conflicts with it. A policy also does not authorize a phase
transition that the recorded workflow state does not permit.

## Invocation

Policies are selected explicitly by the operator. Do not infer a policy merely
because a task resembles one.

Use the immutable policy identifier in the request:

```text
Follow orchestration policy ad-hoc-implementation/v2 for this change.
```

```text
Follow orchestration policy review-repair/v1 for PR #<number> through final review.
```

When a policy is named, load that version in addition to the normal role and
task guidance selected by [agent-surface.md](../agent-surface.md).

## Current policies

| Policy | Current version | Use when |
| --- | --- | --- |
| `ad-hoc-implementation` | [v2](ad-hoc-implementation/v2.md) | A bounded implementation has no accepted proposal and should be planned locally without becoming product discovery. |
| `review-repair` | [v1](review-repair/v1.md) | Existing implementation should be independently reviewed and repaired against one frozen accepted review question until no blockers remain. |

No other orchestration policy IDs are defined by this directory. Do not invent
additional stages or policy names from prior conversations or historical issue
comments.

## Versioning

The path is the policy identifier: `<policy>/<version>`.

- A substantive change to chain of command, authority, synchronization,
  reasoning topology, escalation, or completion semantics requires a new
  version file.
- Keep prior versions so a recorded run can be reconstructed against the policy
  that governed it.
- Update the current-version table above when a new cut becomes preferred for
  future runs.
- Typo, link, or wording fixes that cannot change execution semantics may be
  corrected in place.
- Once a policy version has governed a real run, do not materially rewrite it.

Historical issue threads are experiment evidence, not policy authority. New
runs should reference files in this directory.

## Common operating principles

The first experiments established several cross-policy defaults:

1. **Topology before reasoning tier.** Minimize unnecessary actor boundaries,
   model activations, duplicated context, and duplicate validation before
   optimizing reasoning effort.
2. **One subordinate by default.** Additional simultaneous child contexts need
   an explicit independence/parallelism case.
3. **Terminal receipts, not supervision loops.** Do not emulate asynchronous
   completion with repeated model-level polling or progress prompts.
4. **No coordinator by default.** Add an intermediate compression role only
   when it replaces meaningful parent synchronization rather than adding
   another live context.
5. **One owner per validation check.** Re-run a successful check only when the
   final head can invalidate it or repository guidance explicitly requires it.
6. **Durable resumability.** Visible policy state plus repository state must be
   enough for a fresh session to resume without hidden worker reasoning or
   private message packets.
7. **Explicit closure.** The parent/executive owns the deterministic delivery
   tail unless a policy states a concrete reason to delegate it.

These are defaults, not authority to violate a policy-specific invariant or the
canonical milestone contract.
