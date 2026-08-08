# Realistic CLI scenario continuity evidence

Status: **Machine-first complete after untracked Git-material identity + CI gate; HITL pending.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session (machine-only)

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Machine preflight | **pass** |
| Continuity verdict | **incomplete** (live_config_swap partial — visual HITL pending) |
| US-04 restore | **ok** (snapshot meta `staged_trees` + restore compare) |
| Finalizer | **ok** (dirty identity includes symlink targets + exec modes) |
| Behavior head | `c2aca34` |

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Content-bound lineage |
| `continuity.live_config_swap` | **partial** | HITL visual still required |
| `continuity.memory_lifecycle` | **passed** | Memory check PASS |

## Latest re-review repairs

1. Untracked dirty identity binds symlink **targets** (mode 120000) and file **exec modes** (100644/100755)
2. US-04 interrupt matrix mutates then interrupts; asserts exact activation/tree rollback
3. `tests/milestones/` package + `tests/run.py` discovery so GitHub check runs the matrix
4. PR validation counts reconciled to **90** milestone tests

## Remaining for Met

Interactive HITL on `live-swap-stage` visual cue, then re-package.

## Verdict

`incomplete` — machine-first contract exercised; HITL outstanding.
