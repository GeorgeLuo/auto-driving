# Realistic CLI scenario continuity evidence

Status: **Machine-first complete; HITL pending for live-config visual step.**

Accepted contract:
[cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md)
([PR #99](https://github.com/GeorgeLuo/auto-driving/pull/99)).

## Session

| Field | Value |
| --- | --- |
| Mode | `machine_only_live` |
| Catalog | `m007-continuity` (track `continuity`) |
| Machine preflight | **pass** (6 required steps machine-green) |
| US-04 snapshot/restore | **ok** (restorable bytes + verify) |
| Finalizer | **ok** (product/runner/catalog digests match at session time) |
| Overall M007-10 pass | **not yet** — `continuity.live_config_swap` aggregate is `partial` until interactive visual confirmation of `live-swap-stage` |

## Family ledger

| Family ID | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Capture + two exclusive applies (same `src_dir` lineage) |
| `continuity.live_config_swap` | **partial** | Machine commands green; visual HITL still required on stage step |
| `continuity.memory_lifecycle` | **passed** | Memory check PASS (present/dropout/expiry/reset) + stop |

## Confirmation standard

- Offline/memory: concise CLI verdicts (`Memory check: … PASS`, apply review path first).
- Live swap visual: primary cue is Automa view nonblank with perception overlay — **operator HITL required**.
- Raw JSON/path is never the sole human success signal.

## Artifacts

| Path | Role |
| --- | --- |
| `result.json` | Formal continuity result (incomplete until HITL) |
| `runner-session/` | Full machine-only session (commands, digests, US-04 receipts) |
| `runner-session/continuity-preflight.json` | Safety + family preflight |
| `runner-session/us04-activation-*.json` | Restorable snapshot meta + restore verify |

## Remaining for M007-10 Met

1. Interactive HITL pass on `live-swap-stage` visual prompt (same catalog / product head).
2. Re-run finalizer after any behavioral product/runner/catalog change.
3. Optional: LIVE-002 candidates readiness fix if compare family is expanded.

## Verdict

`incomplete` — machine-first contract exercised for all required families; live-config visual HITL outstanding.
