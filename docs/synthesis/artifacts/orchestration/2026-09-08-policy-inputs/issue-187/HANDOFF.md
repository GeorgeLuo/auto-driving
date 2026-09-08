# Issue 187 / PR 196 orchestration handoff

Prepared 2026-09-08. This packet is task context, not a new repository policy.
No prior conversation, PR 199 transcript, or private worker reasoning is needed.
Read this file first. Supporting snapshots are evidence; do not load them all by default.

## Outcome and authorized execution

Bring existing PR #196 to merge readiness for issue #187: current-head independent
review, bounded approved repairs, passing required validation, accurate PR body,
and exact-head acceptance. Stop before merging or closing issues. The launch
prompt authorizes the work; this file alone is not authorization to begin.

Repository: `/Users/gluo/Projects/auto-driving`
Remote: `GeorgeLuo/auto-driving`
Issue: https://github.com/GeorgeLuo/auto-driving/issues/187
PR: https://github.com/GeorgeLuo/auto-driving/pull/196
Branch: `docs/187-testing-purpose`; base: `main`
Observed PR head: `52c6f46333c5d92db78bbf347de61ba3db83fb3f`
Observed main: `b88861baf0740247ba8e6a76b968478c82a8f02a`
Observed state: issue and PR open, no submitted reviews, failing CI.
This is standalone documentation maintenance. No milestone transition is required.
The original workspace is on unrelated branch `qca-independent-indicators`;
do not switch it, overwrite its work, or commit this packet to that branch.
Use a separate worktree for PR #196 and revalidate all observed identities.

## Frozen review question

Does this change give new or materially changed tests a concrete regression
purpose—consumer, boundary, or justified mechanism—independently of existing
ownership/layer organization, while distinguishing assignment-level tautologies
from meaningful cross-boundary assertions, without imposing a new test framework,
coverage target, or repository-wide suite migration?

Acceptance cases:
1. Consumer tests protect public CLI/API/UI behavior and observable compatibility.
2. Boundary tests protect missing, malformed, unsupported, raced, or degraded cases.
3. Mechanism tests are narrowly justified when a useful public-door test cannot close the case.
4. Every new/materially changed test identifies a concrete regression it catches.
5. Field assertions across normalization, schema, serialization, transport, or
   consumer-visible output remain valid; simply reading back an assigned value is insufficient.
6. Purpose stays separate from owner/layer; lightweight names or docstrings suffice.
7. Canonical wording is owned by `docs/milestones/README.md`; guidance and test
   README summarize/link it, and generated contract HTML matches the Markdown.

Expected files (observed diff):
- `docs/milestones/README.md`
- `docs/milestones/planning-contract.html`
- `docs/guidance/validation.md`
- `tests/README.md`

Non-goals: rewrite existing tests, add tagging/filtering infrastructure, impose
coverage thresholds, change product behavior, alter milestone state, implement
#137/#148/#188/#197, or redesign review/repair acceptance rules.

## Known CI evidence and first action

Run https://github.com/GeorgeLuo/auto-driving/actions/runs/34079649228 failed with:
`Milestone workflow error: ## Repair Cycle Ledger must contain a Markdown table`
The saved PR body has `## Repair Cycle Ledger` followed by `None.`.
This is an observed metadata-validation failure, not evidence of failing product tests.
`ci-failure-excerpt.txt` preserves the relevant log lines.

Recheck the current PR/head and current validator behavior before repair. Use the
repository's existing zero-cycle table syntax if still required; do not fabricate a
review cycle, review URL, or acceptance receipt. Do not modify the workflow validator
merely to accommodate this body. Reconcile all real review history if it changed.
Metadata-only cleanup is root-owned and need not generate a new contract repair cycle.

## Authority, policy, and models

Explicitly select repository policy `review-repair/v1`, pinned to `shared/v1`.
Its merged source is commit `b88861baf0740247ba8e6a76b968478c82a8f02a`.
Copies are beside this file: `review-repair-v1.md` and `shared-v1.md`.
They retain their original repository-relative links; resolve those links in the
repository, not relative to this packet directory. Prefer the repository versions.
Do not silently edit v1 semantics or select an unrelated policy.

Read the repository entry router from current main, then only selected role/task
guidance. The PR predates the policy landing, so its worktree alone may not contain
these files. Use `git show origin/main:<path>` or a separate main checkout to load
current routing/policy. Review PR behavior against its governing base contract;
proposed contract wording cannot authorize its own acceptance. Load the full canonical
contract when required for this process-docs review, not unrelated milestone history.

Runtime mapping for this run:
- Root orchestrator: `gpt-5.6-luna`, reasoning `max`, selected when opening the session.
- Independent reviewer / same-agent approved repair: `gpt-5.6-luna`, reasoning `max`.
- Escalation only with a concrete unresolved uncertainty or demonstrated lower-tier
  failure: `gpt-6-astra`, reasoning `low`, bounded question and one dense answer.
Root cannot assume it can change its own model. If the session is configured differently,
report the actual runtime; do not claim this experiment used Luna root.
One live subordinate by default; no coordinator; fresh context per review cycle.

## Execution sequence

1. Fetch and inspect current PR metadata/reviews/CI once, establish a clean isolated
   worktree, and record head/base. Inspect only required routing/contract context.
   Fetching does not mean rebasing. Integrate current main only if needed for actual
   conflicts or validation; preserve #199's merged contract addition and regenerate
   HTML rather than manually reconciling generated output. Any new head needs review.
2. Correct the confirmed PR-body ledger defect using established syntax. Inspect the
   current PR diff only as needed to compile the frozen question and packet.
3. Dispatch one fresh reviewer against the exact head. Include this question, seven
   acceptance cases, prior findings, governing guidance references, expected files,
   and validation ownership. No full conversation replay. Reviewer returns actionable
   findings with evidence, severity, proposed repair, and focused validation; no edits yet.
4. Root disposes findings. For in-scope blockers, record the canonical consolidated
   verdict, then authorize the SAME reviewer to repair those findings only. Recheck
   head before dispatch. That reviewer validates, commits, and returns a repair receipt.
   Unexpected authority/scope changes require a bounded escalation, not silent expansion.
5. Release the reviewer after its repair receipt (or immediately after no blockers).
   Root owns publishing, PR-body reconciliation, repair ledger, final CI, and closure.
   Use a fresh reviewer for the repaired head. Do not retain workers for CI monitoring.
6. When current-head review finds no blockers and required validation passes, submit
   ordinary exact-head acceptance according to the governing contract. Same-account
   acceptance uses an unedited COMMENTED review containing only:

   ## Contract Review Receipt

   - Outcome: `accepted`

   Keep classification/findings evidence separate where required by the contract;
   preserve append-only ledger history. Never infer approval from a merge or green CI.
7. Return PR URL, final head, findings disposition, validation links, acceptance link,
   actual usage metrics, and any pending checks. Do not merge or close issue #187.

## Validation ownership

Repair worker owns focused checks when its edits affect them:
- `git diff --check`
- `python3 docs/render_markdown.py` when Markdown requires regenerated HTML
- `python3 docs/render_markdown.py --check`
- `python3 docs/milestones/workflow.py validate`
- Bounded comparison of the four changed documentation surfaces with the seven cases.

Root owns final remote CI, including event-backed PR validation and the deterministic
suite required by the repository workflow. Inspect the actual workflow for current
commands; do not invent a new test suite for prose changes. A local validate-pr needs
correct base/head identity and PR-body evidence; inspect documented usage before running.
Do not repeat passed checks without changed inputs, an explicit final-head requirement,
or a concrete unresolved concern. Fresh reviewer may verify focused evidence instead
of rerunning it. Report what was actually run, not copied claims from an earlier head.

## Waiting and context discipline

These are execution targets, not runtime-enforced guarantees. Higher-priority runtime
instructions still apply. A failed capability gate must be reported honestly.
- Prefer terminal completion delivery without model polling when the runtime supports it.
- Do not query worker progress, reread its files, or inspect raw transcripts while waiting.
- Avoid one-second wait calls and redundant exec/wait layers. Use the longest permitted
  event wait compatible with runtime limits and required user updates.
- `gh run watch <run-id> --exit-status` can poll inside a process; avoid repeatedly
  activating the model to issue equivalent status queries. Keep logs in a local file.
- Required periodic updates can force root activations. Count them; do not claim zero
  polling or promise automatic resumption after ending a turn unless actually supported.
- If v1's delegation/runtime gate fails, use its documented fallback or return a precise
  resumable checkpoint; do not quietly invent a new topology to satisfy the metric.
- PR-body edits and submitted reviews trigger CI. Batch necessary body updates and
  avoid bookkeeping edits solely to announce each passing run. Record the final status
  truthfully if an acceptance-triggered rerun remains pending.
- Root should consume findings/receipts and concise status. Raw code, diffs, successful
  test logs, and worker transcripts stay with the worker unless a specific decision needs them.

## Durable state and receipts

Keep mutable progress beside this file in `STATE.json` (outside the product PR).
Update after dispatch, review, disposition, repair, publication, and acceptance.
Store any detailed evidence beside it and reference it by path/URL.
Minimum fields: PR, head, base, policy source, root runtime, cycle, active worker,
phase, finding IDs/dispositions, repair SHA, validation owner/result, receipt URLs,
next permitted action, outstanding CI run ID, and any runtime deviation.
A new session must resume from that state plus GitHub without private transcripts.

Reviewer receipt:
```yaml
cycle: 1
head_reviewed: <full SHA>
findings: []  # or IDs, severity, evidence, proposed repair, validation
escalation_required: false
outcome: no_blockers
```
Repair receipt adds: `head_after`, changed files, addressed/unresolved IDs,
validation commands/results, anomalies, and evidence references.

Record per actor/phase: model, effort, calls, input, cached input, output,
reasoning output, wait/status activations, duplicate validation, review cycles,
and maximum live subordinates. Use actual telemetry when available; otherwise
mark unavailable. Cached input is part of input; reasoning output is part of
output. Do not sum cumulative usage records repeatedly or count them twice.

## Completion and escalation

Complete when current-head independent review has no contract blockers, required
validation passes, PR description/ledger match reality, and exact-head acceptance
is recorded. Leave merge to the operator.
Escalate only for a real acceptance/authority ambiguity, unrelated necessary code
change, unexplained head drift, unavailable required capability, or failed lower-tier
reasoning. More review cycles alone are not an escalation trigger.
