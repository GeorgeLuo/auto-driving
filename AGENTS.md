# Repository Agent Entry Point

This file is the automatic entrypoint for repository-aware agents. It is a
router, not a task specification.

For every new or resumed request:

1. Read `docs/guidance/agent-surface.md`.
2. Classify the requested operation using its role-routing table.
3. Load only the selected role guidance.
4. Inspect the current repository state and the files, tests, and tooling
   relevant to the request.

Use the latest user request as the scope. Do not invent extra deliverables,
planning artifacts, or handoffs. Keep unrelated working-tree changes intact.

If the operator names an orchestration policy, load that exact file from
`docs/guidance/orchestration/`. Do not infer one. QCA (`python3 -m qca`) is
available for change inspection and is not a merge gate.
