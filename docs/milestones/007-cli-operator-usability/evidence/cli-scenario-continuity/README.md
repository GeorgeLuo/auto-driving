# Realistic CLI scenario continuity evidence

Status: **Machine-first complete after dirty-identity + archive-clean matrix repairs; HITL pending.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session (machine-only)

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Machine preflight | **pass** |
| Continuity verdict | **incomplete** (live_config_swap partial — visual HITL pending) |
| US-04 restore | **ok** (snapshot meta `staged_trees` + restore compare) |
| Finalizer | **ok** (exact product keys; shared dirty git identity) |
| Behavior head | `cad6119` |

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Content-bound lineage |
| `continuity.live_config_swap` | **partial** | HITL visual still required |
| `continuity.memory_lifecycle` | **passed** | Memory check PASS |

## Latest re-review repairs

1. Dirty Metrics UI: exact `diff_identity` match required (no linked_pr None escape)
2. Shared `collect_git_identity` hashes untracked **contents** for record and post-hoc
3. US-04 runner matrix is archive-clean temp fixtures only (no ROOT runtime)
4. Snapshot meta persists `staged_trees` digests; restore receipt compared mechanically

## Remaining for Met

Interactive HITL on `live-swap-stage` visual cue, then re-package.

## Verdict

`incomplete` — machine-first contract exercised; HITL outstanding.
