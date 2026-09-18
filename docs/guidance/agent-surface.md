# Agent Operating Surface

**When to load:** At the start or resumption of every repository task.

This is a lightweight task router. It keeps startup guidance separate from
product documentation and asks an agent to load only what the current request
needs.

## Start

1. Classify the latest user request.
2. Load only the selected role guidance below.
3. Inspect the current repository state and the relevant source, tests,
   documentation, configuration, and tooling.
4. Make the smallest complete change that answers the request.
5. Run focused validation and report the result, remaining uncertainty, and
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

## Scope

Keep the repository's normal callers and public paths as the compatibility
surface. Prefer a focused implementation and a focused check over speculative
frameworks, broad cleanup, or hypothetical consumers. Preserve unrelated
working-tree changes.

Use ordinary version-control and hosting mechanics when the user asks for a
branch or pull request. They are delivery tools, not a reason to add extra
artifacts or delay an otherwise complete task.
