# Realistic CLI scenario continuity evidence

Status: **Machine-first complete after re-review repairs; HITL pending for live-config visual.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session (machine-only)

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Machine preflight | **pass** |
| Continuity verdict | **incomplete** (live_config_swap partial — visual HITL pending) |
| US-04 full staged restore | **ok** (activations + autonomy/implementations cache; finally path) |
| Finalizer | **ok** (start vs end + post-hoc `finalize-session`) |
| Offline lineage | content-bound ordered frame digests + manifest |
| Behavior head | committed repair `165ef9a` (+ evidence package commit) |

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Capture + two exclusive applies; lineage re-verified |
| `continuity.live_config_swap` | **partial** | Machine green; HITL visual still required |
| `continuity.memory_lifecycle` | **passed** | Memory check PASS |

## Re-review repairs addressed

1. Per-leaf flag allowlist (parser-valid is not enough; automation `--record` rejected)
2. Strong family topology (recorded capture, `{src_dir}` lineage, visual_required, unique IDs, human cues)
3. US-04 fail-stop precondition; full staged tree cache; remove trial-created absences
4. Post-hoc `finalize-session` entrypoint + expanded product surface digests
5. Content-bound offline lineage (no mtime fallback); recheck on apply
6. Single continuity verdict writer before human-notes / result.json

## Remaining for Met

Interactive HITL pass on `live-swap-stage` visual cue, then re-package session with finalizer.

## Verdict

`incomplete` — machine-first contract exercised; HITL outstanding.
