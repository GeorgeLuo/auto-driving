# Realistic CLI scenario continuity evidence

Status: **Machine-first complete after review repairs; HITL pending for live-config visual.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session (machine-only)

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Machine preflight | **pass** |
| Continuity verdict | **incomplete** (live_config_swap partial — visual HITL pending) |
| US-04 full staged restore | **ok** (perception/decision/memory bundle; finally path) |
| Finalizer | **ok** (start vs end identities; product digests non-empty) |
| Offline lineage | exact `src_dir` + `manifest_sha256` |

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Capture + two exclusive applies |
| `continuity.live_config_swap` | **partial** | Machine green; HITL visual still required |
| `continuity.memory_lifecycle` | **passed** | Memory check PASS |

## Review repairs addressed

1. Parser-level unregistered flag rejection
2. Continuity precondition worker stop; US-04 restore in `finally` over full staged activations
3. Finalizer compares session-start identity bundle to end-of-session tree; refuses missing/empty digests
4. Family topology markers + authoritative continuity verdict
5. Exact capture lineage (stdout path + manifest digest)
6. Compare JSON retains `error_detail` full text

## Remaining for Met

Interactive HITL pass on `live-swap-stage` visual cue, then re-package session with finalizer.

## Verdict

`incomplete` — machine-first contract exercised; HITL outstanding.
