# Proposal Amendment: Lag-bounded live CLI view correlation

## Review Question

Does lag-bounded correlation make the accepted M007 live CLI journey realistic
under continuous Chase publication while still proving camera/perception
lineage, failing closed on unproven lag, and exposing a concise operator verdict?

## Reason For Amendment

The accepted proposal in PR #86 requires one exact-current publication:
`overlay.status=current` and equal camera/source frame ids. Guided live sessions
during PR #92 showed continuous Chase alternating between `current` and `stale`
with observed frame lag commonly in the 12–17 range. The camera publisher can
advance while perception finishes an earlier frame, so exact-current is not a
stable health criterion for the continuous view.

That evidence is already sufficient to show that the accepted gate is too
strict; this amendment does not require another known-failure run. It does not,
however, lower the proof required for a later positive acceptance result. The
rejected PR #92 experiment also demonstrated why a self-reported lag is
insufficient: a payload could claim `frame_lag=1` while its current/source
indexes were 100 and 1. Positive acceptance therefore requires independently
derived index proof.

Durable review context:

- PR #92 live observation and initial bounded-lag experiment:
  <https://github.com/GeorgeLuo/auto-driving/pull/92#issuecomment-5195269421>
- PR #92 adversarial false-pass finding:
  <https://github.com/GeorgeLuo/auto-driving/pull/92#issuecomment-5195354070>
- PR #92 final exact-current restoration before merge:
  <https://github.com/GeorgeLuo/auto-driving/pull/92#issuecomment-5195594646>

## Contract Delta

PR #86 and its proposal artifact remain immutable and authoritative except for
the correlation clauses replaced below. All other startup, layer, authority,
recording, preservation, human-view, cleanup, and handoff requirements remain
unchanged.

The `correlation` machine gate passes exactly one of these cases:

1. **Exact current**
   - `overlay.status == "current"`;
   - `frame.frame_id` and `overlay.source_frame_id` are nonempty and equal; and
   - the reported lag is the integer `0` when present.
2. **Proven bounded stale**
   - `overlay.status == "stale"`;
   - `frame.frame_id` and `overlay.source_frame_id` are nonempty;
   - `frame.frame_index`, `overlay.source_frame_index`, and
     `overlay.frame_lag` are type-strict integers (booleans and floats fail);
   - derived lag is
     `frame.frame_index - overlay.source_frame_index`;
   - derived lag equals `overlay.frame_lag`; and
   - derived lag is in `1..MAX_FRAME_LAG`, inclusive.

`MAX_FRAME_LAG` is **24** for this acceptance contract. The value must live on
the pinned/versioned acceptance surface, with the catalog digest updated in the
same implementation change. It must not be an unreviewed local or runner-only
override. Twenty-four covers the established 12–17-frame live observations
with bounded headroom; changing it requires another reviewed amendment.

`pending`, an unknown status, missing perception, empty ids, missing indexes,
non-integer indexes or lag, a negative/zero stale lag, a claimed/derived
mismatch, reverse index order, or lag above 24 fails closed. Polling until an
exact-current sample happens is diagnostic only and cannot replace validation
of the captured publication.

Frame count is the freshness dimension accepted here because the review
question is source lineage in a continuously advancing simulator stream, not
wall-clock performance qualification. `frame_lag_ms` and `result_age_ms` remain
recorded diagnostics and must be preserved in evidence, but this amendment does
not invent an unmeasured latency SLO. Obviously malformed or nonnumeric timing
diagnostics remain findings; a future time budget requires measured evidence
and a separate reviewed amendment.

The human screenshot still must show a nonblank, intelligible camera and
perception presentation. A proven bounded-stale overlay is no longer a blocker
by itself. A blank/misleading display, `pending`, unproven lag, or lag above the
accepted bound remains an acceptance blocker even when other gates pass.

Operator output must make the decision scannable without requiring raw JSON:

- pass: correlation mode (`current` or `bounded_stale`), derived lag, and the
  accepted `MAX_FRAME_LAG`;
- fail: the specific missing, malformed, inconsistent, or over-budget value;
- machine evidence: the current/source ids and indexes, claimed and derived
  lag, bound, mode, and verdict.

The successful M007-05 handoff evidence should describe a
“lag-bounded correlated camera/perception publication (exact current or proven
bounded stale)” rather than claiming that the accepted run necessarily sampled
an exact-current frame.

## Ownership

The M007 live CLI session runner owns correlation validation and concise gate
reporting. The pinned `m007-acceptance.yaml` catalog owns the reviewed threshold
surface and digest. PR #88 remains the sole owner of the formal live session,
human judgment, evidence artifacts, and M007-05 verdict.

## Affected Paths

- Exact-current `/api/latest` publications.
- Stale publications with current/source frame identities and indexes.
- The pinned M007 acceptance catalog and its digest.
- The runner's human gate summary and structured result evidence.
- The human-view judgment bound to the validated publication.

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| `current`, equal nonempty ids, lag absent or integer zero | Pass as `current` |
| `current`, unequal ids or nonzero claimed lag | Fail with the conflicting fields |
| `stale`, derived lag 1 | Pass lower boundary as `bounded_stale` |
| `stale`, derived lag 12 or 17 with equal claimed lag | Pass established live range |
| `stale`, derived lag 24 | Pass inclusive upper boundary |
| `stale`, derived lag 25 | Fail and report `25 > 24` |
| Missing current/source index | Fail closed; name the missing index |
| Boolean, float, string, or null index/lag | Fail closed; name the malformed field and type |
| Claimed lag differs from derived index difference | Fail and report claimed plus derived values |
| Source index exceeds current index | Fail reverse lineage; do not clamp to zero |
| `pending` or unknown overlay status | Fail with the status |
| Missing perception, authority violation, or applied control | Preserve the existing independent blocker |
| Modified catalog threshold without matching reviewed digest | Refuse formal acceptance before commands run |
| Healthy screenshot with unproven or over-budget machine lag | Machine gate wins; record a blocker |
| Proven bounded-stale machine gate with blank/misleading screenshot | Human gate wins; record a blocker |

## External Assumptions

- Current and source frame indexes are monotonically increasing and comparable
  within the current worker generation.
- Existing worker-generation, vehicle-id, perception-presence, and authority
  gates keep the compared frames inside one accepted live session.
- The view publication continues to expose current/source frame ids and indexes
  plus its claimed lag; absence or type drift fails rather than skipping.
- The established 12–17-frame observations justify opening this amendment, but
  do not themselves prove a later acceptance pass.

## Non-Goals

- Rewriting the accepted PR #86 proposal artifact or its merge receipt.
- Re-running a known failing exact-current gate merely to authorize amendment.
- Changing product camera, perception, publication, or Metrics UI behavior.
- Wall-clock latency or perception-performance qualification.
- Poll-until-green, retry-based pass manufacture, or an unpinned local override.
- Producing the #88 live evidence, human visual judgment, or M007-05 verdict.
- Changing any non-correlation gate or the proposal's Expected Handoff outcome.

## File Impact

This amendment PR changes only:

- this additive amendment artifact;
- canonical M007 `plan.md`; and
- generated M007 `plan.html`.

After acceptance, a new child PR targeting #88 may modify only the existing
session-runner implementation, its pinned acceptance catalog/digest, focused
tests, and directly corresponding runner documentation. Product CLI and view
publication code are outside this amendment.

## Validation Plan

1. Validate this PR as a `proposal_amendment` transition with the milestone
   workflow gate and confirm the accepted PR #86 proposal has no diff.
2. In the later child implementation PR, exercise every adversarial row with
   focused unit tests, including type-strict booleans/floats and claimed versus
   derived mismatch.
3. Prove that the threshold is pinned on the reviewed acceptance surface and a
   catalog edit without its matching digest is rejected before command
   execution.
4. Verify concise pass/fail summaries expose mode, derived lag, bound, and exact
   reason while structured evidence retains all correlation fields.
5. Run the full deterministic suite before merging the child implementation.
6. Only after the amendment and implementation are accepted, run #88's formal
   session once under the new pin. A pass requires all original gates plus this
   amended positive proof; a failure needs only one explicit blocking gate.
