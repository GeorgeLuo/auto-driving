# Realistic CLI scenario continuity evidence

Status: **Draft — continuity session not completed.** This scaffold makes no
M007-10 acceptance claim.

Accepted contract:
[cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md)
([PR #99](https://github.com/GeorgeLuo/auto-driving/pull/99)).

## Required families

| Family ID | Minimum contract |
| --- | --- |
| `continuity.offline_perception` | Capture once + apply/compare with lineage |
| `continuity.live_config_swap` | Restage, observe-only view, transactional restore |
| `continuity.memory_lifecycle` | Concise memory check PASS/FAIL + cleanup |

## Procedure (summary)

1. Continuity safety preflight + family validation (fail closed before CLI).
2. Machine-first catalog run (`m007-continuity.yaml`).
3. HITL where visual primary confirmation is declared.
4. Evidence freshness finalizer against final product/runner/catalog bytes.
5. Overall `pass` only if every required **family aggregate** is `passed`.

## Artifact ledger

| Artifact | State |
| --- | --- |
| `result.json` | Present; incomplete scaffold until session finishes |
| Continuity session dir | Pending live run |
| Catalog digests | Pending |
| Bounded repairs | See implementation PR |

## Verdict

`incomplete` — tracked continuity session has not finished.
