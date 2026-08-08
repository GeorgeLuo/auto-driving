# Realistic CLI scenario continuity evidence

Status: **Machine-first complete; shared git-identity wiring closed; HITL pending.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session (machine-only)

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Machine preflight | **pass** |
| Continuity verdict | **incomplete** (live_config_swap partial — visual HITL pending) |
| US-04 restore | **ok** (snapshot meta `staged_trees` + restore compare) |
| Finalizer | **ok** |
| Behavior head | `74a4a4a` |

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Content-bound lineage |
| `continuity.live_config_swap` | **partial** | HITL visual still required |
| `continuity.memory_lifecycle` | **passed** | Memory check PASS |

## Latest repair

`_git_identity` now **delegates** to shared `collect_git_identity` (no inline duplicate).

## Remaining for Met

Interactive HITL on `live-swap-stage` visual cue, then re-package.

## Verdict

`incomplete` — machine-first contract exercised; HITL outstanding.
