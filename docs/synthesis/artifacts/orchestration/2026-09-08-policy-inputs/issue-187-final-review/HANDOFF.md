# Issue 187 / PR 196: final review and bounded repair

Prepared 2026-09-08. Standalone execution packet. This starts a fresh review of
the completed rewrite, not another implementation pass. Do not execute the older
issue-187 or issue-187-prose-rewrite packets. No prior conversation is required.

## Target and observed evidence

Repository: `/Users/gluo/Projects/auto-driving` (`GeorgeLuo/auto-driving`).
PR: https://github.com/GeorgeLuo/auto-driving/pull/196
Issue: https://github.com/GeorgeLuo/auto-driving/issues/187
Branch: `docs/187-testing-purpose`; base: `main`.
Observed head: `328f4fdc38ccfc8ecc9ad6b345bd245bdc635ea2`.
Observed base: `b88861baf0740247ba8e6a76b968478c82a8f02a`.
PR is open. CI run 34281203803 passed on the rewritten head:
https://github.com/GeorgeLuo/auto-driving/actions/runs/34281203803
A superseded concurrent run was cancelled; do not treat it as an unfixed code failure.
Prior accepted receipt applies ONLY to `761310438afbef47b1f4937fb6867ec4174fc61d`:
https://github.com/GeorgeLuo/auto-driving/pull/196#pullrequestreview-5146057563
There is no observed acceptance on the new head. Ledger has the zero-cycle table.
The operator-requested rewrite was not a changes-requested repair cycle.

Reported implementation validation: render/check, workflow validate for plans
005/007/008, diff check, directly affected links/anchors/rendered tables, and
`python3 -m qca diff --help` all passed. Affected source sections decreased from
496 to 428 whitespace-delimited tokens. Counts are descriptive, not an acceptance
threshold. Product tests were not run locally; required remote CI passed.

Revalidate live PR state/head/base/reviews/CI once before dispatch. If merged,
report that and stop. If the head changed, refresh the packet before review;
do not apply findings or receipts to an unexplained different commit.
Use a clean isolated worktree; do not switch or overwrite the unrelated primary
workspace or another session's pending work. No milestone transition is involved.

## Policy and runtime

Select `review-repair/v1`, pinned to `shared/v1`. Exact snapshots beside this
file come from merged commit b88861baf0740247ba8e6a76b968478c82a8f02a; original
relative links resolve in their repository directories. Load current repository
entry router and only selected review/re-review guidance. The canonical milestone
contract remains authoritative; this is process documentation, so inspect relevant
canonical authority and load the full contract when directed.

Runtime mapping preserves the operator's higher-reasoning prose preference:
- Fresh root: `gpt-5.6-luna`, reasoning `low`.
- Fresh independent prose/contract reviewer: `gpt-6-astra`, reasoning `high`.
- Approved prose repairs: SAME Astra-high reviewer in that cycle.
- Any separately justified code implementation: `gpt-5.6-luna`, reasoning `low`;
  conditional only, as described below. Expected code change is zero.

The launch prompt explicitly selects this prose allocation instead of v1's
usual economy reviewer default. Do not silently rewrite the versioned policy.
One live subordinate by default; no coordinator, no full-conversation fork.
Verify actual runtime when exposed; unavailable identity/telemetry is unknown,
not proof that the requested runtime ran. Model unavailability is a capability
issue; do not silently retry many times or substitute a different model.

## Frozen review question

Does the concise testing guidance preserve #187's test-purpose and regression-value
requirements while explaining how existing QCA observations can inform testing
decisions, without adding mandatory process, new tooling, or automatic judgments?

Accepted scope and cases:
1. Consumer, boundary, and narrowly justified mechanism purposes remain distinct
   and independent of test owner/layer. CLI tests may serve different purposes.
2. New/materially changed tests identify a concrete plausible regression; public
   behavior is preferred, with justified internal checks still available.
3. Missing/malformed/unsupported/raced/degraded cases retain appropriate boundary
   checks; the rule does not demand coverage for every hypothetical situation.
4. Reading back a directly assigned value is distinguished from meaningful field
   assertions across setters, normalization, schemas, serialization, transport,
   or consumer-visible output. No blanket ban on field assertions/private calls.
5. Lightweight names/docstrings suffice. No retroactive suite migration, mandatory
   decision record, new schema/tagging infrastructure, or coverage target.
6. One compact canonical decision table/rule/examples is usable without repeating
   full rationale in derived docs. Concision preserves exceptions and readability;
   do not demand more compression solely for a smaller word count.
7. QCA guidance maps supported existing observations to concrete inspection
   questions: test-effectiveness candidates to exercised boundary/regression;
   contract/API observations to consumer compatibility; supplied execution/coverage
   evidence to unexercised behavior. Check capabilities at the ACTUAL PR revision,
   not the unrelated primary workspace's possibly newer QCA branch.
8. Static candidates are advisory. Counts/private calls do not prove poor tests;
   absent evidence is unmeasured, not failure. No automatic deletion, quality score,
   required QCA run, numeric gate, or claim of proven test effectiveness.
9. Canonical wording lives in `docs/milestones/README.md`; validation guidance and
   test README summarize/link it; generated HTML matches and links work. Existing
   workflow, ownership, safety, and merged #199 orchestration rules remain intact.

Review the TOTAL PR diff against its governing base. Use the delta from 7613104
only to understand the rewrite, not as a substitute for totality review. The old
acceptance remains evidence for that older head, not acceptance of the new one.
Do not invent a broader matrix during repair cycles.

Expected source files:
- `docs/milestones/README.md` (testing-purpose section only)
- `docs/guidance/validation.md` (corresponding summary)
- `tests/README.md` (corresponding guidance/link)
- `docs/milestones/planning-contract.html` (generated)

Non-goals: implement QCA detectors, new reporting pipeline, mandated annotations,
mandatory review artifacts, workflow-validator changes, test-suite rewrite,
product behavior changes, or #180/#197 completion. Do not require a QCA report
for this docs-only PR. Refer to `qca/README.md` at the reviewed revision for existing
capabilities; unsupported claims can be narrowed in prose instead of adding code.

## Cycle procedure

1. Root records exact target and dispatches ONE fresh reviewer with this question,
   cases, file allowlist, governing guidance refs, and validation evidence. Reviewer
   reads the necessary source and returns a terminal receipt. No edits or GitHub
   writes before root disposition. Root does not duplicate the review in parallel.
2. Receipt contains cycle, head_reviewed, findings with stable IDs/severity/evidence,
   why-in-scope, minimum repair, focused validation plan, classification where
   applicable, and escalation_required. No blockers means `findings: []` and
   `outcome: no_blockers`; do not turn advisory polish into a formal repair.
3. Root consolidates and records action-forcing review according to the canonical
   contract. Preserve classification and exact-head evidence. Same-account receipts
   use COMMENTED reviews, not self-APPROVED/CHANGES_REQUESTED. Keep documentary
   findings/classification separate from a strict Contract Review Receipt where
   required. Do not fabricate cycles for metadata-only housekeeping.
4. For approved prose blockers, recheck current head equals reviewed head, then send
   the SAME reviewer a bounded repair packet. It edits only approved findings,
   regenerates affected HTML with existing commands, validates, commits locally,
   and returns head_after/changed files/validation/unresolved/anomalies. Root owns
   push and PR-body/ledger updates. Release the worker after its repair receipt.
5. Fresh independent reviewer for each repaired head. No indefinite open-ended
   search for more observations; verify prior findings and these fixed cases.
6. On current-head no-blocker receipt, release reviewer. Root owns final validation
   evidence reconciliation, exact-head acceptance, and final closure. No closure
   worker and no reviewer retained for CI monitoring or PR-description work.

Conditional code: this is a docs review; normally adjust prose to existing supported
behavior. If a reproducible necessary documentation-mechanics defect requires code,
return it as an anomaly with exact reproduction, owner, files, smallest repair, and
validation. Do not let Astra implement code or launch a nested worker by itself.
Root must explicitly approve a separate Luna-low unit under the operator allocation,
release the current reviewer before dispatch, and record this deviation from v1's
same-reviewer repair topology. A fresh independent review is required afterwards.
Only existing rendering/link mechanics and a necessary focused regression fit this
exception; broader code, schema, detector, gate, or behavior changes require operator
decision. No code unit should exist merely to exercise the lower model.

## Validation ownership and final acceptance

Use the reported focused passes as evidence unless head changes or a concrete
concern justifies revalidation. A repair worker owns affected focused commands:
`python3 docs/render_markdown.py`, `python3 docs/render_markdown.py --check`,
`python3 docs/milestones/workflow.py validate`, `git diff --check`, and directly
affected links/rendered sections. Readability inspection is not pixel-perfect
product UI acceptance. Do not add brittle prose-string tests or rerun a full suite
just to mirror already-passing CI on unchanged inputs.

Root verifies current-head required remote checks and current review/ledger/body.
Exact acceptance uses a new unedited review on that head containing only:

```markdown
## Contract Review Receipt

- Outcome: `accepted`
```

Never carry the old receipt forward. After any new push, obtain new-head review.
PR edits/review submission can trigger CI; batch necessary body changes and avoid
editing solely to announce each CI completion. Report any acceptance-triggered rerun
truthfully and reconcile its final result before declaring merge readiness.
Do not merge or close issues. Completion is current-head acceptance, required checks
passing, no unresolved contract blockers, and an accurate PR body/ledger.

## Waiting, state, and usage

Prefer terminal events, no progress prompts, no worker transcript/file inspection
while waiting. Avoid one-second checks and repeated nested wait wrappers. Where
supported, a single CI watcher can wait at process level and keep raw logs on disk.
Respect higher-priority runtime limits/user-update requirements; count resulting
activations rather than claiming zero polling. If completion without polling is
unavailable, honor v1's documented runtime gate/fallback or return a precise checkpoint.
A policy is not runtime enforcement and ending a turn is not guaranteed auto-resumption.

Keep `STATE.json` beside this packet. Record live head/base, cycle, worker, phase,
finding dispositions, repair commit, review/acceptance URLs, validation owner/result,
CI run, next action, and deviations after each boundary. Resume from it plus GitHub.
Report per-actor actual model/effort, calls, input, cached input, output, reasoning
output, wait/status activations, duplicates, and max live subordinates when telemetry
exists. Otherwise mark unavailable. Cached input is part of input; reasoning is part
of output. Child launches are NOT model-call counts; do not sum cumulative counters.
