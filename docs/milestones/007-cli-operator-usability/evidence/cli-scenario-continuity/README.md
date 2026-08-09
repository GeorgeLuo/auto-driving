# Realistic CLI scenario continuity evidence

Status: **Machine-first complete at behavior head `63f402c`; visual HITL pending.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session (machine-only)

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Machine preflight | **pass** |
| Continuity verdict | **incomplete** (live_config_swap partial — visual HITL pending) |
| US-04 restore | **ok** (snapshot meta `staged_trees` + restore compare) |
| Finalizer | **ok** (dirty identity includes symlink targets + exec modes) |
| Behavior head | `63f402c` |

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Content-bound lineage |
| `continuity.live_config_swap` | **partial** | HITL visual still required |
| `continuity.memory_lifecycle` | **passed** | Memory check PASS |

## Latest re-review repairs

1. Untracked dirty identity binds symlink **targets** (mode 120000) and file **exec modes** (100644/100755)
2. Quoted Unicode and delimiter-containing untracked paths use raw NUL-delimited Git output and length-framed identity material
3. US-04 interrupt matrix mutates then interrupts; asserts exact activation/tree rollback
4. `tests/milestones/` package + `tests/run.py` discovery so GitHub check runs the matrix
5. PR validation counts reconciled to **92** milestone tests / **570** discovered

## Remaining for Met

Interactive HITL on `live-swap-stage` visual cue, then re-package. The machine
preflight and post-hoc finalizer are current-head green; this evidence remains
explicitly non-pass until a named operator records the visual result.

## Verdict

`incomplete` — machine-first contract exercised and finalized at `63f402c`;
HITL outstanding.
