Select **gpt-5.6-luna / low** for a fresh root session, then paste:

Read `/Users/gluo/Projects/agent-handoffs/issue-187-prose-rewrite/HANDOFF.md`.
Execute this bounded operator-requested rewrite of issue #187's open PR #196 using
`ad-hoc-implementation/v3` and `shared/v1`.

Assign source prose rewriting to **gpt-6-astra / high**. Assign rendering,
validation, and any strictly necessary approved code mechanics to **gpt-5.6-luna / low**.
This explicit prose allocation overrides the policy's economy-first default for A1.
Follow the packet's solution constraints: concise canonical decision table and
examples, lightweight derived links, and unchanged testing requirements. Include
concise guidance connecting existing QCA observations to testing decisions; verify
capabilities in the actual revision. No new QCA tooling, mandatory records, or gates.

You may create an isolated worktree, delegate these units, edit within the allowlist,
run validation, commit/push the existing PR branch, and reconcile its description.
Preserve prior review history. Finish at ready-for-fresh-review with actual validation
and usage evidence. Do not merge, close issues, or treat the old acceptance as approval
of the new head. Persist resumable state beside the packet. Do not execute the older
issue-187 review-repair packet or inherit its stale failure state.
