# Realistic CLI scenario continuity evidence

Status: **Machine-first validation complete; human HITL pending.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Execution | `machine_only_live` |
| Human operator | **None recorded** |
| Machine preflight | **pass** (6/6 required steps) |
| Continuity verdict | **incomplete** |
| Visual gate | **pending** — no human visual confirmation was recorded |
| US-04 restore | **ok** (full staged snapshot/restore verification) |
| Finalizer | **ok** against the auto-driving tree and Metrics UI checkout |
| Metrics UI | `m002/04-passive-observation` at `722e070fdc9f4ee89d13f947bf3996e62dcb2783`, clean |
| Evidence | `runner-session/result.json`, `human-notes.md`, and machine transcripts |

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Content-bound capture lineage and two exclusive applies |
| `continuity.live_config_swap` | **partial** | Machine commands passed; `live-swap-stage` visual confirmation is pending |
| `continuity.memory_lifecycle` | **passed** | Present/dropout/expiry/reset memory check PASS |

## Provenance correction

An earlier agent session inspected a browser screenshot with model vision and
entered a visual `pass` under the repository owner's identity. That was not
human-in-the-loop confirmation, so it is not included as acceptance evidence.
The tracked package intentionally records only the fresh machine-only run.

## Verdict

`incomplete` — deterministic commands, safety preflight, cleanup, restoration,
and freshness finalization passed; a named human must still inspect the live
view and record the visual result before continuity can pass.
