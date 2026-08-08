# Realistic CLI scenario continuity evidence

Status: **Machine-first complete after re-review restore/finalizer repairs; HITL pending.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session (machine-only)

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Machine preflight | **pass** |
| Continuity verdict | **incomplete** (live_config_swap partial — visual HITL pending) |
| US-04 full staged restore | **ok** (verified tree digests; trial absences removed) |
| Finalizer | **ok** (exact product keys incl. autonomy/implementations trees) |
| Offline lineage | content-bound ordered frame digests + apply re-verify |
| Behavior head | `3fc5e9a` |

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Capture + two exclusive applies; lineage re-verified |
| `continuity.live_config_swap` | **partial** | Machine green; HITL visual still required |
| `continuity.memory_lifecycle` | **passed** | Memory check PASS |

## Re-review repairs (latest)

1. US-04: remove trial-created absent trees; verify cache + restored tree_sha256
2. Post-hoc finalizer: exact product key set; autonomy/ + implementations/ trees; no reuse of recorded Metrics UI as current; dirty UI needs named diff/linked PR
3. Runner-level restore matrix tests (success / command-fail / interrupt / restore-fail)

## Remaining for Met

Interactive HITL on `live-swap-stage` visual cue, then re-package.

## Verdict

`incomplete` — machine-first contract exercised; HITL outstanding.
