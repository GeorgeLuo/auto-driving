# Adversarial Matrix

**When to load:** When a proposal or review makes a universal guarantee, audits
an enforcement boundary, or needs a fresh adversarial pass after repair.

**Authority:** This summarizes
[Invariant Closure](../milestones/README.md#invariant-closure-when-claiming-universals)
in the canonical contract. The contract wins if any wording conflicts.

## Define The Claim

Record the exact claim, owning boundary, affected paths, transformations,
bypasses, external assumptions, and unverified limits. Terms such as `bounded`,
`detached`, `deterministic`, `exact`, `fail-closed`, `fresh`, and `no movement`
require class-level proof.

## Case Families

Consider only families relevant to the claim:

- strict types, coercion, missing values, and malformed values;
- normalization before and after size or range checks;
- aliases, duplicate paths, and shared mutable state;
- identity, ordering, timestamps, expiry, and replay;
- configuration, catalogs, defaults, and stale runtime state;
- partial failures, retries, interruption, and cleanup;
- exact minimum, maximum, empty, and over-limit boundaries;
- serialization, transport, and final external representation;
- cross-component assumptions and bypasses around the owner.

Every accepted matrix row needs a direct test or an explicit explanation of
which stronger test subsumes it. After repair, add cases derived from the root
cause rather than rerunning only the original reproduction.
