# Reviewer Role

**When to load:** When reviewing, diagnosing, auditing, assessing, explaining,
or investigating repository behavior.

## Working method

1. Establish the requested behavior and the relevant owner or boundary.
2. Inspect the current implementation, diff, callers, and tests before
   forming a conclusion.
3. Reproduce the reported behavior or run the smallest check that can
   distinguish the likely causes.
4. Report concrete findings with impact, evidence, and a direct corrective
   action. Separate confirmed failures from unmeasured behavior and optional
   improvements.
5. Keep the assessment within the requested scope; do not turn every possible
   follow-up into a blocker.

Do not edit product files while acting as a reviewer unless the user explicitly
changes the request to implementation.

Optional: `python3 -m qca diff --base <base> --head <head>` can focus
inspection. It is observations, not a required gate.

## Handoff

State the verdict first, then the evidence, remaining uncertainty, and the
smallest next action.
