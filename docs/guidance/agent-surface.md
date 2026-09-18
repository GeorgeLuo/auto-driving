# Agent Operating Surface

**When to load:** At the start or resumption of every repository task.

This is a lightweight task router. Load only what the current request needs.

## Start

1. Classify the latest user request.
2. Load only the selected role guidance below.
3. If the operator named an orchestration policy, load that exact file from
   [orchestration/](orchestration/README.md). Do not infer a policy.
4. Inspect the current repository state and the relevant source, tests,
   documentation, configuration, and tooling.
5. Make the smallest complete change that answers the request.
6. Run focused validation and report the result, remaining uncertainty, and
   any requested follow-up.

An explicit operation in the latest request wins. A short continuation such as
`proceed` keeps the current role when the next action is clear; otherwise use
the repository state and the request to classify it.

## Role Routing

| Requested operation | Role guidance |
| --- | --- |
| Change, fix, build, or update code, configuration, or documentation | [roles/engineer.md](roles/engineer.md) |
| Review, audit, diagnose, assess, explain, or investigate | [roles/reviewer.md](roles/reviewer.md) |

Do not preload every guide. Read repository documentation only when it is
relevant to the requested behavior or interface.

Orchestration policies organize execution when the operator names one. QCA
(`python3 -m qca`) can focus inspection; it is observations, not a required
gate.

## Scope

Keep the repository's normal callers and public paths as the compatibility
surface. Prefer a focused implementation and a focused check over speculative
frameworks, broad cleanup, or hypothetical consumers. Preserve unrelated
working-tree changes.

Use ordinary version-control and hosting mechanics when the user asks for a
branch or pull request. They are delivery tools, not a reason to add extra
artifacts or delay an otherwise complete task.

Do not use `docs/deprecated/`.
