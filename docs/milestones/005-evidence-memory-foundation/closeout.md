# Milestone 005 Closeout: Evidence Memory Foundation

Status: closed 2026-07-28

The post-merge `complete-implementation` handoff for PR #69 set plan Status
`closed`, marked M005-13 Met, and recorded the accepted closeout unit.

## Outcome

Milestone 005 delivered bounded, inspectable decision-cycle memory: typed
memory snapshots, a framework-owned activation path, dual-host wiring (Chase and
Donkey), operator stage/inspect/stream/reset/replay with opt-in provenance
recording, a packaged `BoundedEvidenceLedger`, deterministic recurrence and
conflict policy, and live observe-only proofs on Pi and Chase. Action remained
idle for the entire milestone. Memory retains attributed observation evidence; it
does not claim a world model, semantic identity, or movement authority.

Whole-milestone acceptance is the durable judgment in this file plus the
accepted review-unit ledger in the plan. The post-merge handoff applied the
terminal plan mutations: M005-13 Met, risk-table clear, Status closed, and empty
frontiers.

## Durable Decisions

- Memory is **retained attributed evidence**, not a complete world model.
  Perception remains uncertain and coordinate-scoped; stronger models must be
  earned later.
- **Action stays idle** for the entire milestone so memory lifecycle can be
  evaluated without movement safety coupling.
- Memory is **bounded and process-local by default** (capacity, age, detachable
  snapshots, explicit reset). Restart continuity is an explicit non-goal.
- Do **not** promote perception track IDs into durable object identity.
- The **same memory interface** runs on Chase and Donkey hosts; neither receives
  privileged simulator map state as candidate input.
- Visual **provenance** is required on Pi and Chase: retained image-space
  evidence must trace to exact source frames; retained is not current geometry.
- Chase’s built-in model is a **shadow reference only**; evaluator state stays
  outside rewritten controller inputs and retained memory provenance.
- Same-slot **structural conflict policy**
  (`conflict_policy=bounded_evidence_structural_v1`, PR #64): fail-closed
  invalidation without semantic fusion or sticky tombstones; conflict counters
  are disjoint from capacity eviction and age expiry.
- Proposal and implementation remain **separate review units**; implementation
  may not invent policy outside an accepted proposal.

## What Was Demonstrated

| Claim | Evidence |
| --- | --- |
| Typed memory I/O at the decision-cycle boundary | M005-01; `MemorySnapshot` / activation contracts |
| Stage ownership distinct from perception and action | M005-02; idle action policy |
| Finite capacity/age, detach, reset, provenance, isolated failure | M005-03; #52, #53 |
| Packaged ledger without world-model claims | M005-04; `BoundedEvidenceLedger` |
| Same activation/lifecycle on Chase and Donkey | M005-05; host wiring |
| Operator stage / inspect / stream / reset / replay | M005-06; Automa memory CLI |
| Defaults write no history; recording opt-in and bounded | M005-07; #53 ceilings |
| Deterministic recurrence, dropout, expiry, capacity, reset, failure, replay, and same-slot conflict | M005-08; #52–#53, #57, **#64** matrix + per-prefix replay |
| Live Pi present/dropout/expiry/reset without movement | M005-09; `evidence/physical-memory-lifecycle/` |
| Guided Chase shadow alignment, observe-only, evaluator isolation | M005-09–M005-11; `evidence/chase-shadow-memory/` (#51) |
| Guided Chase max-age expiry without reset | M005-08–M005-09; `evidence/chase-max-age-expiry/` (#57) |
| Provenance distinguishes current vs retained | M005-12; physical and Chase extracts |
| Closeout usefulness, residual risk, 006 activate decision | This file; M005-13 Met only after Phase B handoff |

### Completion usage (still supported)

| Workflow | Notes |
| --- | --- |
| Stage memory | `vehicles update memory --id <id> --implementation bounded_evidence` |
| Inspect / stream | Memory map and stream; health, epoch, retained keys |
| Reset | Empty state with new epoch |
| Replay | Stable digest; offline sequences including conflict fixtures |
| Record provenance | Explicit `--record` only; bounded extract |
| Lifecycle check (Pi / Chase) | Stationary / observe-only; zero unapplied candidate control on Chase |

## Failures And Residual Limits

| Residual | Why it remains |
| --- | --- |
| Process-local memory only | Restart continuity was an explicit non-goal |
| Chase live probe process identity | Host command inspection can fail closed; not a security boundary |
| Metrics UI atomic capture dependency | Chase evidence quality depends on sibling capture contract |
| Transitional cumulative PR shape | Pre-contract M005 work targeted `main`; the cumulative PR is a remaining-work delta from the milestone branch, not a full rewrite of history |
| Physical perception quality for movement | 004 residual (side misses / clear-floor false positives) was not re-solved in 005 |
| Semantic fusion, identity, non-idle action | Explicit 005 non-goals |

These residuals are restated here so Phase B may remove matching Open Risks rows
from the plan without concealing unfinished product work.

## Validation

- Deterministic suite at this closeout implementation tip:
  `PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py` → **434** tests, **2** named
  live skips (Pi / live sim), zero failures.
- Live Pi lifecycle and Chase shadow/max-age proofs remain tracked under
  `evidence/`; this closeout does **not** re-run live vehicles or reopen #64’s
  conflict matrix.
- No closeout step commands movement or grants non-idle action authority.
- Plan integrity: Phase A left Exit Criteria, Open Risks, Accepted Review Units,
  and Status Active unchanged; Phase B applied the reviewed terminal handoff.

## Deferred Work

- **Activate** the existing pre-plan
  [Milestone 006 — Decision-Facing Perception Readiness](../006-decision-facing-perception-readiness/plan.html)
  only through its separate cross-milestone activation step after cumulative
  closeout review. Activation is not 005 product scope.
- Do **not** implement 006 packages under `m005/closeout`.
- No competing pre-plan is introduced by this closeout.

## Cumulative PR identity

| Field | Value |
| --- | --- |
| Cumulative PR | **#68** — https://github.com/GeorgeLuo/auto-driving/pull/68 |
| Base | `main` |
| Head | `milestone/005-evidence-memory-foundation` |
| Shape | Transitional remaining-work delta (post-baseline milestone tip) |
| Readiness | Phase B complete; this PR is the Phase C whole-milestone review surface. |

## References

- Milestone plan: [plan.md](plan.md) · [plan.html](plan.html)
- Accepted closeout proposal: [#66](https://github.com/GeorgeLuo/auto-driving/pull/66) at `0bd2920e15b2dc022428ca40a99cd2b3c29b43e5`
- Completed ledger: [completed.md](../completed.md)
- Tracked evidence: [evidence/](evidence/)
- Key implementation PRs (ledger): [#51](https://github.com/GeorgeLuo/auto-driving/pull/51),
  [#52](https://github.com/GeorgeLuo/auto-driving/pull/52),
  [#53](https://github.com/GeorgeLuo/auto-driving/pull/53),
  [#57](https://github.com/GeorgeLuo/auto-driving/pull/57),
  [#64](https://github.com/GeorgeLuo/auto-driving/pull/64)
- Conflict proposal (not a ledger unit): [#61](https://github.com/GeorgeLuo/auto-driving/pull/61)
- Cumulative PR: [#68](https://github.com/GeorgeLuo/auto-driving/pull/68)
