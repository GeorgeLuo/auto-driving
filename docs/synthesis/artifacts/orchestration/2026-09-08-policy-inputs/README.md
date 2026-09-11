# Inputs to the frontier implementation policy draft

These are exact last-available local snapshots from `~/Projects/agent-handoffs/`,
archived with operator authorization to retain task setup and execution evidence.
`sources.json` records source paths and SHA-256 digests. Local absolute paths in
historical prompts/state are provenance, not portable commands to execute now.
Do not execute these completed handoffs. Referenced sibling evidence not copied here
is unavailable in this archive; durable GitHub URLs remain evidence pointers.

The three directories hold setup/prompt and final-state records for the initial
PR #196 review, its prose/QCA-guidance rewrite, and its final review/repair.
They do not include complete execution logs, earlier overwritten packet versions,
or independently verified runtime telemetry. Their reported metrics remain intact,
including inconsistent or unavailable fields. No missing transcript is reconstructed.

## Observations and policy consequences

| Observed record | Bounded inference for the new trial |
| --- | --- |
| Initial review separated the PR-body ledger defect from implementation review and retained exact-head acceptance. | Compile current identity once; keep metadata and contract decisions distinct. |
| Rewrite packet explicitly allocated stronger reasoning to prose, economical execution to mechanics, and left expected code delta at zero. | Allocate by uncertainty and operator direction, not by file extension or one model for every actor. |
| Rewrite's first high-tier launch failed and left edits; final state records their preservation. | Treat partial edits as unverified work requiring reconciliation, not a terminal success or automatic discard. |
| Rewrite reported successful generated-link checks; final review found a missing destination anchor and repaired it in Markdown. | A command pass is not proof of the final consumer surface. Inspect the actual externally visible claim at the owning boundary. |
| Initial run reports zero poll-wait turns but four wait/status activations; rewrite reports zero and thirteen respectively. | Track model activations separately from narrower polling definitions; never use one zero field to imply no orchestration overhead. |
| Some `model_calls` fields appear to count child interactions/launches while token telemetry is unavailable. | Do not treat these as per-response model-call telemetry or calculate cost ratios from them. |
| Final review preserved prior acceptance, repaired one anchor defect, and reviewed the repaired head. | Preserve prior evidence while rebinding current decisions to current commits. |

All three runs concern documentation, not an accepted product frontier. They support
handoff and execution hypotheses; they do not validate product-design discretion,
first-slice topology, or relative token savings across different task sizes.
The operator reports improved token use with task-size confounding; this remains
an observation, not a controlled comparison. `product-implementation/v1` needs a
real accepted-frontier trial before a claim of success.

## Evidence links

- [PR #196](https://github.com/GeorgeLuo/auto-driving/pull/196): merged testing guidance.
- [Final review finding](https://github.com/GeorgeLuo/auto-driving/pull/196#pullrequestreview-5147604366): generated-link defect.
- [Final acceptance](https://github.com/GeorgeLuo/auto-driving/pull/196#pullrequestreview-5147728729).
- [PR #199](https://github.com/GeorgeLuo/auto-driving/pull/199): versioned policy foundation.
- [Issue #197](https://github.com/GeorgeLuo/auto-driving/issues/197): policy-library experiment.
