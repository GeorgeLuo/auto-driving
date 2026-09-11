# M006 D2 review-repair orchestration receipt — cycle 1

Historical review of the former live publisher design. The 2026-09-10
operator-directed standalone inspector supersedes that implementation; this
receipt does not assess the current PR head.

Status: `changes_requested` — browser evidence remains open.

This is the executed review-repair record for the D2 combined implementation.
It is not an acceptance receipt, a claim of D2 completion, or a claim that
PR #202 is complete.

## Scope and frozen question

- Base: `a8c4483dea577afdb78094caa36d650f47882a27`
- Reviewed repair head: `7208ae482e1e3ea7fd100367e0770ab5de48293e`
- Branch: `m006/live-decision-view`
- Initial repair runtime: `gpt-5.6-luna/max`
- Escalation runtime: `gpt-6-astra/low`
- Final re-review runtime: `gpt-6-astra/low`
- Astra-medium escalation: not used for this resumed cycle
- Live subordinate limit: one

Frozen question:

> Does `info decision` expose a reachable, generation-bound view of the accepted
> decision cycle, with spatial evidence on its actual current or retained source
> image, and explicit refusal or unavailability when correlation cannot be
> established?

## Review and repair sequence

1. The initial Luna-max repair attempt was capped at 15 minutes. It committed
   `4eb3732f4ec120bb8be58d0a9291ae07c79b0088`; no terminal worker receipt was
   returned, so root retained the commit as unverified until review.
2. Astra-low escalation reproduced the remaining total-read-deadline defect:
   a trickling HTTP response exceeded the 250 ms probe budget.
3. Luna-max applied the bounded repair. Root verified and committed the exact
   two-file patch as `7208ae482e1e3ea7fd100367e0770ab5de48293e`.
4. Fresh Astra-low review of that exact head found no remaining code defect,
   but returned `changes_requested` because actual browser
   expiry/delayed-installation/tab-resume evidence is unavailable.

## Finding disposition

| Finding | Disposition |
| --- | --- |
| Browser freshness and transaction expiry | Implemented structurally; browser behavior evidence remains open. |
| Producer identity and generation | Addressed; rejection/discovery checks pass. |
| Total trickling-response deadline | Addressed; real loopback 200/503 regressions pass. |
| Pre-decode image bounds | Addressed; header-bound checks pass. |
| Non-identity EXIF orientation | Addressed; ingress rejection passes. |

The cycle is `substantial` because the original verdict contained an in-contract
P2 failure. No new adversarial matrix or contract change was introduced.

## Validation

- D2 focused module: `27/27` pass, no skips.
- Compatibility group: `48/48` pass, no skips.
- Full flagless suite: `1028` pass, `1` failure, `2` expected live skips. The
  failure is the historical M007 frozen-parser audit when run from this linked
  worktree; it reports the worktree `.git` file as an unusable parser source.
- Workflow plan validation: pass.
- Markdown render check: pass.
- `git diff --check`: pass.
- QCA: advisory report was generated before the final repair; it was not rerun
  at the final head and is not treated as final-head acceptance evidence.
- Browser inspection: unavailable; no visual acceptance inferred.
- Chase/Pi live evidence: not claimed.

## Remaining gate

Run the disposable local fixture in an actual browser and capture current,
retained, expiry/delayed-installation, and tab-resume outcomes with source
records and image hashes. If that produces no code changes, obtain a fresh
Astra-low review of the evidence at exact head `7208ae4`. Until then the D2
implementation remains a draft with `changes_requested` disposition.
