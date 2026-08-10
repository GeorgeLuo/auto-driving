# Realistic CLI scenario continuity evidence

Status: **Machine-first validation complete; human HITL pending.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Execution | `machine_only_live` |
| Behavior head | `c0ffed7` |
| Human operator | **None recorded** |
| Machine preflight | **pass** (6/6 required steps) |
| Continuity verdict | **incomplete** |
| Visual gate | **pending** — no human visual confirmation was recorded |
| US-04 restore | **ok** (full staged snapshot/restore verification) |
| Finalizer | **ok** against auto-driving identities; Metrics UI identity recorded but not independently freshness-gated in machine-only mode |
| Metrics UI | `m002/04-passive-observation` at `722e070fdc9f4ee89d13f947bf3996e62dcb2783`, clean; reserved for independent HITL finalization |
| Evidence | `runner-session/result.json`, `human-notes.md`, and machine transcripts |

## Freshness repair

The product-tree identity now binds the root type and symlink target bytes and
uses length-framed raw relative-path bytes plus Git-relevant leaf identities.
This closes root-symlink retarget and newline/colon path-collision bypasses at
the aggregate tree owner. The refreshed package was collected from behavior
head `c0ffed7` and finalized against the current auto-driving tree.

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
The machine-only finalizer records the Metrics UI identity but does not claim
an independent Metrics UI freshness comparison; the eventual named-human HITL
finalizer must supply and require the exact reviewed Metrics UI checkout.

## Verdict

`incomplete` — deterministic commands, safety preflight, cleanup, restoration,
and auto-driving freshness finalization passed; Metrics UI identity was
recorded but not independently freshness-gated, and a named human must still
inspect the live view and record the visual result before continuity can pass.
