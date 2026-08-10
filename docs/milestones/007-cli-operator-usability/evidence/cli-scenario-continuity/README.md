# Realistic CLI scenario continuity evidence

Status: **Machine-first complete at behavior head `28701cc`; visual HITL pending.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session (machine-only)

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Machine preflight | **pass** |
| Continuity verdict | **incomplete** (live_config_swap partial — visual HITL pending) |
| US-04 restore | **ok** (snapshot meta `staged_trees` + restore compare) |
| Finalizer | **ok** (dirty identity includes symlink targets + exec modes) |
| Product identity | **17/17 exact keys** (launcher + whole CLI package + runtime trees) |
| Behavior head | `28701cc` |

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
5. Machine-only exit is fail-closed across required-step completeness, family/safety preflight, restore, cleanup, and finalizer
6. Required nonvisual skips cannot be hidden by passing family siblings
7. Freshness binds the launcher, rendered/runtime CLI surface, and whole autonomy/implementation trees
8. Continuity pass requires a trimmed named operator; README now documents the executable HITL/finalizer handoff
9. Validation counts reconciled to **97** milestone tests / **575** discovered

## Remaining for Met

Interactive HITL on `live-swap-stage` visual cue, then re-package. The machine
preflight and post-hoc finalizer are current-head green; this evidence remains
explicitly non-pass until a named operator records the visual result.

## Verdict

`incomplete` — machine-first contract exercised and finalized at `28701cc`;
HITL outstanding.
