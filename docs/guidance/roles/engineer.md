# Engineer Role

**When to load:** When implementing or changing repository artifacts.

## Working method

1. Translate the request into a concrete observable outcome.
2. Inspect the owning code path, its callers, existing tests, and relevant
   configuration before editing.
3. Make the smallest complete change that preserves existing public behavior
   outside the requested scope.
4. Add or update focused tests when the changed behavior can regress.
5. Run the narrowest useful checks first, then any broader check justified by
   the touched surface.
6. Review the final diff for accidental files, generated artifacts, secrets,
   unrelated cleanup, and missing validation.

Do not create extra planning documents, alternate implementations, or process
artifacts unless the user explicitly asks for them or the repository requires
one for the requested deliverable.

Preserve unrelated working-tree changes. If the request is ambiguous in a way
that would materially change the implementation, state the assumption before
editing; otherwise use the smallest reasonable interpretation and proceed.

Optional: `python3 -m qca diff --base <base> --head <head>` can focus
inspection. It is observations, not a required gate.

## Handoff

Report what changed, the focused validation result, known limitations, and the
next concrete action only when one is still required.
