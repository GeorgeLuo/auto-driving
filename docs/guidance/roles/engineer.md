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
   the touched surface. Choose tests as
   [tests/README.md](../../tests/README.md#validating-a-change) describes:
   select systems tests with `--systems <group> -k <pattern>` by the code
   they drive, and run a whole group only when the change reaches code that
   most of its tests use. CI runs only the deterministic suite, so list each
   systems run under Validation.
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

## Meta

Before commit, verify changes do not introduce new vocabulary for like concepts
in the codebase. Verify overlapping vocabulary for different concepts is not
introduced. Verify code for readability, that branches of code from the same
abstraction points have similar patterns, customization only appears when
appropriate. Err on the side of readability over coverage of edge-case behavior
and flag where refactors can be applied to branching lanes from common abstraction.

Think deeply around if the change touches the right place. Generally the "hardness"
of code is autonomy enforces things that all automation shares in terms of things
that proceed in time, exist in the substrate of the environment and affect the
environment. Steps should be treated as equally important and from this, form
of the code should agree with function between the steps (filesystem layout, patterns).
The implementations shared code should capture abstractions that are general to
step behavior, plugins themselves are where specific environment dependent values
are set.

tag: review_scope, audit

## Documentation

Comments, READMEs are intended primarily to aid implementers of 

tag: review_scope, audit
