# Realistic CLI scenario continuity evidence

Status: **Interactive HITL complete at behavior head `04258e1`.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Execution | `interactive_live` |
| Named operator | `GeorgeLuo` |
| Machine preflight | **pass** (6/6 required steps) |
| Continuity verdict | **pass** |
| Visual gate | **pass** — nonblank Automa view with live/current status, intelligible floor-boundary overlays, frame/status details, and observation-only readiness |
| US-04 restore | **ok** (full staged snapshot/restore verification) |
| Finalizer | **ok** against the auto-driving tree and Metrics UI checkout |
| Metrics UI | `m002/04-passive-observation` at `722e070fdc9f4ee89d13f947bf3996e62dcb2783`, clean |
| Evidence | `runner-session/browser-view.png` and `human-notes.md` |

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Content-bound capture lineage and two exclusive applies |
| `continuity.live_config_swap` | **passed** | Named visual confirmation, stop cleanup, and staged-state restoration |
| `continuity.memory_lifecycle` | **passed** | Present/dropout/expiry/reset memory check PASS |

## Review repair coverage

The session exercises the repaired boundaries: parser-valid and semantically
allowlisted commands, fail-closed required-step/family aggregation, exact
capture lineage, full US-04 transaction restore, current-tree freshness
finalization, Git-significant dirty identity, and named-operator enforcement.

The tracked session contains **55** inner digest entries; the outer evidence
receipt covers the selected session artifacts. All paths are repository-redacted
and the detached manifests were regenerated after packaging.

## Verdict

`pass` — machine-first continuity, named-operator visual HITL, cleanup,
restoration, and finalization all passed on the same fresh session.
