# Proposal Amendment: Live plugin selection during image replay

## Review Kind

Behavioral feature slice

## Review Question

Can an operator change the ready perception-plugin set while replay is running
or paused and see server-produced evidence attributed per processed frame?

## Reason For Amendment

Hands-on review found that #179 made plugin controls unavailable during
playback. Replay processes plugins frame-by-frame; it does not precompute their
output. Run-level immutability was an unnecessary lifecycle choice, not a
safety or authority requirement.

## Contract Delta

- A valid ready-plugin selection may change before or during replay and takes
  effect at a frame boundary; completed frames are never recomputed.
- The server validates IDs atomically; invalid, unknown, duplicate, or
  unavailable selections leave the effective set unchanged.
- Each processed frame exposes the plugin runs/provenance that produced it.
  Mapper swap/reset mechanics remain implementation-owned; no new browser
  execution path or memory semantic is introduced.
- Plugin-root discovery/refresh remains idle-or-terminal only; selection does
  not change the root or catalog digest during replay.

All other behavior in #172 and #179 remains unchanged: server-owned catalog and
execution, local/read-only/observation-only boundaries, existing
`PerceptionText`/`Observation`/bounded-memory contracts, defaults, and safety.

## Ownership

The existing workbench runner/API owns selection validation and frame-boundary
application; the existing mapper owns plugin execution/reset; the existing
page renders server state. No ownership boundary changes.

## Affected Paths

Only the existing workbench runner/server/page, focused workbench/plugin tests,
and the M008 assessment named by #179. No new adapter, schema, runtime, or
external capability is introduced.

## Adversarial Matrix

| Attempt | Required behavior |
| --- | --- |
| Valid change during running/paused replay | Apply at the next frame boundary and expose per-frame provenance. |
| Invalid/unavailable IDs | Reject atomically; keep the prior effective set. |
| Race with frame processing | Serialize the boundary; never mix sets within a frame. |
| Change after prior frames | Preserve history; do not recompute it. |
| Root refresh or unselected plugin | Reject root refresh while active; never invoke unselected code. |
| Reset or later replay | Reset mapper/memory and prevent state leakage. |

## External Assumptions

Replay remains incremental; selected packages are ready in the core runtime; and
existing `plugin_runs`/provenance identifies the effective set per frame.

## Non-Goals

Retroactive recomputation, live root mutation, dependency/model installation,
arbitrary browser code, new memory semantics, vehicle/simulator control,
Metrics UI, or any #172/#179 non-goal.

## File Impact

This PR changes only this artifact, the canonical M008 plan, and generated HTML.
After acceptance, the existing implementation PR updates the runner/API/page,
focused tests, and assessment paths above.

## Validation Plan

Validate the amendment plan/rendering and exact changed paths. After acceptance,
prove live selection through the public API/page: frame-boundary application,
per-frame provenance, atomic invalid-selection rejection, reset isolation, and
unchanged observation-only behavior; run focused and canonical suites.
