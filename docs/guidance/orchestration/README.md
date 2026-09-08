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

## Capability gradient

Successful decomposition should make some work simpler than the work that
preceded it. Treat runtime selection as evidence of that simplification, not
only as a pricing choice.

After choosing the minimum necessary execution topology, use the
**lowest-capability available runtime that can reliably execute each bounded
role**. For the bounded review, repair, and pattern-following implementation
these policies cover, that economy tier is sufficient. Escalate to a
higher-capability runtime only when the unit still has unresolved uncertainty,
needs a capability the lower tier lacks, or the lower tier already failed with
evidence.

Do not keep an in-policy bounded role on a stronger runtime merely because the
work involves judgment, review, or repair.

For a decomposed multi-agent run, reaching completion without executing any
unit on the lowest available capable tier is an **orchestration smell**. Record
why no bounded unit was suitable. Acceptable explanations include:

- the required capability was unavailable in the lowest tier;
- the lowest tier was attempted and produced evidence of insufficiency;
- the run remained direct because the task was too small for delegation to be
  economical.

Do not launch a child merely to satisfy this diagnostic. A small direct-mode
change may correctly stay in one stronger root context. The smell applies when
the orchestrator has already chosen decomposition but still fails to compile
any work down to the lowest capable tier.

The intended capability shape is:

```text
uncertainty / frontier reasoning
        |
        v
bounded plan or accepted pattern
        |
        v
lowest capable execution runtime
        |
        v
deterministic validation / closure
```

Higher-capability execution should be justified by current uncertainty or a
demonstrated capability gap, not inherited from the actor that planned the
work.

## Common operating principles

The first experiments established several cross-policy defaults:

1. **Topology before reasoning tier.** Minimize unnecessary actor boundaries,
   model activations, duplicated context, and duplicate validation before
   optimizing reasoning effort.
2. **Capability should descend with uncertainty.** Once work is bounded, use
   the lowest capable available runtime; in-policy bounded work is in-capability
   for that economy tier. A decomposed run with no lowest-tier work requires an
   explanation.
3. **One subordinate by default.** Additional simultaneous child contexts need
   an explicit independence/parallelism case.
4. **Terminal receipts, not supervision loops.** Do not emulate asynchronous
   completion with repeated model-level polling or progress prompts.
5. **No coordinator by default.** Add an intermediate compression role only
   when it replaces meaningful parent synchronization rather than adding
   another live context.
6. **One owner per validation check.** Re-run a successful check only when the
   final head can invalidate it or repository guidance explicitly requires it.
7. **Durable resumability.** Visible policy state plus repository state must be
   enough for a fresh session to resume without hidden worker reasoning or
   private message packets.
8. **Explicit closure.** The parent/executive owns the deterministic delivery
   tail unless a policy states a concrete reason to delegate it.

These are defaults, not authority to violate a policy-specific invariant or the
canonical milestone contract.
