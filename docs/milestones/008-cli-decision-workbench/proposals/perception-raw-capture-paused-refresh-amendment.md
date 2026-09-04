# Proposal Amendment: Raw-capture empty selection and paused held-frame refresh

## Review Kind

Behavioral feature slice

## Review Question

Can an operator use an empty plugin set as raw-capture and, when paused, see a
valid plugin toggle applied to the held frame as a server result, while a
running toggle still takes effect on the next processed feed frame?

## Reason For Amendment

Operator review of implementation PR #174 settled a visible-control rule that
#179 and #181 do not allow together:

- empty selection is normal usage (raw capture without perception overlays);
- a paused plugin toggle must change the still on screen;
- a running toggle still applies at the next feed frame, and that still must
  show the frame's real server perception rather than a client preview.

#179 requires at least one ready plugin and refuses an empty selection before
the first frame. #181 applies live changes at a frame boundary and forbids
recomputing completed frames, including the held paused frame. Those rows make
the operator's paused-toggle and raw-capture journey a contract failure even
though the rest of the workbench question remains true.

This is a contract correction, not a HITL adjunct: it replaces an accepted
matrix row and an explicit non-goal. The original proposal, #179, #181, their
acceptance receipts, and the reviewed Expected Handoff remain immutable.

## Contract Delta

A plugin toggle takes effect the next time this view will process a frame. The
in-view still must be that server result, not a browser-substituted preview.

- An empty `active_plugin_ids` list is a valid explicit raw-capture selection.
  Replay still advances through the image, observation, and memory lifecycle;
  perception plugins are not invoked; the server reports empty perception
  (`status=empty`, no plugin runs or things). Unknown, duplicate, or
  unavailable IDs remain refused atomically. When no plugin directory and no
  selection are supplied, the packaged `frame` + `floor_plane` default remains.
- While **running**, a valid selection change, including empty, applies to the
  next unprocessed feed frame. The current still is not recomputed. The page
  renders that still's server-produced perception, overlays, and provenance.
- While **paused**, a valid selection change, including empty, reprocesses the
  held in-view frame now through the existing perception → `Observation` →
  memory seam so the operator sees the new result immediately. Other already
  processed timeline frames are not recomputed.
- Invalid selections leave the effective set and the held frame unchanged.
- Plugin-root discovery/refresh remains idle-or-terminal only. The browser
  remains a presentation and action client.

This replaces #179's "at least one ready plugin" / empty-selection refuse row
and #181's "completed frames are never recomputed" / "retroactive
recomputation" non-goal for the held paused frame only. All other #172, #179,
and #181 behavior remains unchanged: server-owned catalog and execution,
observation-only local replay, existing `PerceptionText` / `Observation` /
bounded-memory contracts, and safety.

## Ownership

The existing workbench runner/API owns selection validation, raw-capture empty
selection, running next-frame application, and paused held-frame reprocess. The
existing mapper owns plugin execution/reset. The existing page renders the
in-view frame's server state. No ownership boundary changes.

## Affected Paths

Only the existing workbench runner/server/page, focused workbench/plugin tests,
and the M008 assessment named by #179. No new adapter, schema, runtime, or
external capability is introduced.

## Adversarial Matrix

| Attempt | Required behavior |
| --- | --- |
| Empty selection at start, idle, or terminal | Accept as raw-capture; do not invoke perception plugins; report `perception.status=empty`. |
| Empty or valid change while running | Apply at the next unprocessed feed frame; do not recompute the current still; render that still's server perception. |
| Empty or valid change while paused | Reprocess the held in-view frame now; replace that frame's server perception/overlays; leave other timeline frames unchanged. |
| Invalid, unknown, duplicate, or unavailable IDs | Reject atomically; keep the prior set; do not reprocess. |
| Page drops leftover `plugin_runs` because the pending selection is empty | Fail review: render the in-view frame's server-produced perception. |
| Recompute a prior timeline frame that is not the held paused frame | Preserve history; do not recompute it. |
| Root refresh or unselected plugin during replay | Reject root refresh while active; never invoke unselected code. |
| Reset or later replay | Reset mapper/memory and prevent state leakage. |

## External Assumptions

Replay remains incremental; selected packages are ready in the core runtime;
existing `plugin_runs`/provenance identifies the set that produced each
processed frame; and a paused held frame can be reprocessed through the same
pipeline without a new memory semantic.

## Non-Goals

Recomputing completed frames while running; recomputing historical frames other
than the held paused frame; browser-side overlay substitution; live root
mutation; dependency/model installation; arbitrary browser code; new
observation or memory semantics; vehicle/simulator control; Metrics UI; or any
other #172/#179/#181 non-goal except the paused held-frame reprocess and empty
raw-capture rows replaced above.

## File Impact

This PR changes only this artifact, the canonical M008 plan, and generated HTML.
After acceptance, the existing implementation PR updates the runner/API/page,
focused tests, and assessment paths above.

## Validation Plan

Validate the amendment plan/rendering and exact changed paths. After acceptance,
prove through the public API/page: empty selection as raw-capture; paused
held-frame reprocess for valid changes including empty; running next-feed-frame
application without rewriting the current still; in-view rendering of server
perception rather than a client hide; atomic invalid-selection rejection; reset
isolation; and unchanged observation-only behavior. Run focused and canonical
suites.
