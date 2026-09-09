# M006: Prepare cross-environment shadow proposal evidence with a fail-closed capture gate

## Milestone Context

- Milestone: M006 — Decision-Facing Perception Readiness
- Base branch: `milestone/006-decision-facing-perception-readiness`
- Implementation branch: `m006/shadow-proposal-evidence`
- Frontier: Cross-environment shadow proposal evidence
- Exit criteria: M006-06, M006-07
- Status: **Draft; canonical evidence incomplete**

## Accepted Proposal

- Proposal PR: [#201](https://github.com/GeorgeLuo/auto-driving/pull/201)
- Proposal merge: `9e2a353c736a04fed22c1ce5d456c6115fbfbddc`
- Reviewed head: `f9705785e62ba6c7d193cee8dcd86d0052ed6508`
- Proposal artifact:
  `docs/milestones/006-decision-facing-perception-readiness/proposals/shadow-proposal-evidence.md`
- Implementation content commit: `779a04a3b79cc8d111bed181471ab861114c49d9`
- Preserved remote implementation-start commit: `af02f318010947612bf8ec9f25e42e01581288e1`
- Draft implementation PR: [#202](https://github.com/GeorgeLuo/auto-driving/pull/202)
- Branch tip at publication: `a0e11c6d5c29fc3f1f75c2b22588696600fc9a9c`

## Review Kind

Live or external evidence

## Review Question

Does the staged `avoid_recent_obstruction` path produce provenance-complete
shadow action plans and the same correlated visual explanation on Chase and
stationary PiRacer inputs while applied control remains zero and privileged
simulator state stays outside controller inputs?

## Deliverable

This draft implementation supplies the smallest evidence-local preparation
slice and preserves a truthful fail-closed boundary:

- frozen Chase/PiRacer procedure and C1-C7 case map;
- stable evidence-root `result.json` with explicit D1/D2 receipts and unmet
  criteria;
- derived `result.html` rendered from that authoritative JSON;
- evidence-local verifier with bounds, blocked-readiness, no-fabricated-success,
  and derived-HTML checks;
- one preparatory offline public-door replay receipt, clearly ineligible for
  live evidence.

No product policy, decision surface, proposal artifact, milestone criterion,
vehicle, simulator, or physical state was changed. No canonical Chase/PiRacer
package is claimed.

## Readiness and remaining work

- D1 is blocked: the current automation worker discovers Chase only
  (`include_picar=False`) and rejects non-Chase providers, so no supported
  physical shadow-cycle publication/liveness route is available.
- D2 is blocked: staged decision info reports `combined_view.url: null` and
  only a local path template; the CLI exposes offline apply and terminal
  stream, not a correlated live decision URL/retained overlay.
- Operator capture authorization is pending and is not valid while those
  receipts are blocked.
- M006-06 and M006-07 remain `Unmet`.
- The branch preserves the concurrent implementation-start transition; the
  canonical plan is now `implementation_in_review`.

The separate D1/D2 capability or recovery route must be proposed and reviewed
if an operator later assigns it. This PR does not create an external issue,
implement the gap, or rewire the frontier.

## Validation

- Packet verifier, derived HTML, and fabricated-success rejection: pass.
- Focused decision surfaces:
  `pytest -q tests/cli/decision/test_commands.py tests/cli/decision/test_shadow_decision_surfaces.py`
  — 33 passed.
- Workflow status/validation, generated documentation check, and diff check:
  pass.
- Preparatory public-door replay: deterministic digest equality, digest SHA-256
  `63369a158af3198a44e145ed11aa71dbb62097e46a39e3e458909fe08a53e54b`.
- Broader `pytest -q`: `1048 passed, 2 skipped, 4 failed` in unrelated
  pre-existing M007 coverage/audit tests; exact-base checks reproduce the
  historical audit failure. Full suite not green; keep this PR draft.
- No live/external capture run because the readiness gate is blocked.

See [orchestration validation ledger](../../../../synthesis/artifacts/orchestration/2026-09-08-m006-frontier-implementation/VALIDATION.md)
and [evidence result](../../evidence/shadow-proposal-evidence/result.json).

## File Impact

- `docs/milestones/006-decision-facing-perception-readiness/evidence/shadow-proposal-evidence/`
  — procedure, blocked result, derived report, verifier, and preparatory receipt.
- `docs/synthesis/artifacts/orchestration/2026-09-08-m006-frontier-implementation/`
  — pinned packet, state, unit receipts, validation ledger, and this body.

## Repair Cycle Ledger

| Cycle | Review receipt | Classification | Highest severity | Repair revision | Contract impact |
| --- | --- | --- | --- | --- | --- |
| None | None | None | None | None | No review cycle has occurred. |

## Handoff

This draft is ready for independent review of the evidence-local preparation
and the truthful readiness disposition only after the final head is recorded.
It is not ready to mark M006-06/M006-07 `Met`, merge, self-accept, or advance
the frontier. The next permitted external action is to resolve or separately
assign the blocked D1/D2 capability routes, rerun the readiness gate, and only
then obtain explicit operator authorization for bounded canonical capture.
