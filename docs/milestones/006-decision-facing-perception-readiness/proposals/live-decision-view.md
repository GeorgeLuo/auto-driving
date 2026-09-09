# Proposal: Live decision view (D2)

| Field | Value |
| --- | --- |
| Milestone | M006 Decision-Facing Perception Readiness |
| Unit | D2 live decision URL and retained-source explanation |
| Combined branch | `m006/live-decision-view` |
| Target | `milestone/006-decision-facing-perception-readiness` |
| Inspected target / initial D2 head | `a8c4483dea577afdb78094caa36d650f47882a27` |
| Design author | A1; requested runtime `gpt-6-astra / xhigh` (request, not runtime attestation) |
| Implementer | L1 Luna, constrained by [IMPLEMENTATION-SPEC.md](IMPLEMENTATION-SPEC.md) |
| Status | Authored contract for the explicitly assigned combined job; no separate proposal acceptance, implementation, browser acceptance, or live availability claimed |
| Criterion relationship | Supports later M006-06/07 evidence; changes no criterion status or existing M006-05 acceptance |

## Review Kind

Behavioral feature slice

This is D2's requested review focus. It differs from the current evidence
frontier's `Live or external evidence`; it is not a reclassification of #202.
The workflow accommodation below must resolve this distinction before integration.

## Review Question

Does `info decision` expose a reachable, generation-bound view of the accepted
decision cycle, with spatial evidence on its actual current or retained source
image, and explicit refusal or unavailability when correlation cannot be established?

## Operator Want

- Want: Open the decision URL, inspect why the selected shadow proposal arose,
  and distinguish its intent from authorized idle and independently reported
  host output.
- Reject if: The view substitutes another image, carries old geometry onto an
  unrelated current image, presents a stopped generation as live, or converts
  engine authority into observed host telemetry.

## Operator Override And Workflow Integration Blocker

The assignment explicitly says “this is proposal and implementation in the same
branch” and “Astra xhigh will propose, Luna max will implement.” It authorizes
the D2-only combined branch/PR and internal A1 design → L1 implementation → A2
independent review sequence. L1 does not need renewed approval of that choice.
It does not make this artifact a separately accepted proposal, complete #202,
authorize a merge, or authorize canonical capture. The operator also authorized
isolated deterministic fixtures and local test servers for later implementation.

The [canonical contract](../../README.md#git-branch-model) permits a narrow
parallel exception in `Milestone Decisions`, but its separate-phase and
single-current machinery has no combined D2 route. Read-only GitHub inspection
on 2026-09-09 found:

| Record | Observed state |
| --- | --- |
| Target and this worktree at `a8c4483…` | Current is Cross-environment shadow proposal evidence, `ready_for_implementation`; #201 accepted at `9e2a353c736a04fed22c1ce5d456c6115fbfbddc` |
| Open draft [#202](https://github.com/GeorgeLuo/auto-driving/pull/202), head `5ab8394bfa74ae2f3a87cac54c6ad808ae489f6a` | Same current frontier, `implementation_in_review`, branch `m006/shadow-proposal-evidence`, target base `a8c4483…` |
| Local branch named `milestone/006-decision-facing-perception-readiness` | Resolves to `1646a583a2c2e5509f1f9037c62448cf9f088baa`; not the observed remote target. Do not use this local alias as D2's exact base. |

The target has not received #202's unmerged transition. Preserve that distinction
and #202's active evidence ownership. D2 must not take over its current pointer,
accepted #201 proposal, map, workflow history, or completion receipt.

An in-memory trial appended a decision-log row while preserving current/history:
standalone `validate_plan_text` passed. `validate_review_unit_transition` then
refused with the exact error:

```text
review-unit PR must append exactly one workflow-history transition
```

An in-memory trial of the standard evidence implementation transition, still
using D2's branch, refused with:

```text
implementation PR must use m006/shadow-proposal-evidence, not m006/live-decision-view
```

An attempted D2 current/history replacement refused during plan validation:

```text
Workflow History can change frontier only after accepted or while idle/ready_for_proposal selecting from the work order
```

These are design diagnostics, not proposed edits. The owning code is
`docs/milestones/workflow.py::validate_review_unit_transition` (around lines
4150–4425 at the inspected base), reached by `validate_review_unit_git_diff`.
It requires a single current, frozen branch identity and review kind, one
history transition, and the current accepted proposal. A normal proposal route
also admits only its one proposal artifact, plan, and generated HTML, excluding
this combined implementation and companion specification. At an
`implementation_in_review` base it has no new review-unit transition at all.
Neither prose nor a PR template overrides these gates.

The canonical plan can honestly **record the exception as a decision**, while
honestly reporting that it cannot represent its delivery state. The smallest
decision-log entry for the root to reconcile, without a state transition, is:

> 2026-09-09 — Operator authorized D2 proposal and implementation together on
> `m006/live-decision-view`, targeting the M006 milestone while #202 remains the
> active evidence implementation. A1 constrains design, Luna implements, and A2
> independently reviews. No separate D2 acceptance or criterion promotion is
> asserted; integration is blocked by the single-current/separate-phase tooling.

A1 leaves `plan.md` and generated HTML unchanged; root owns this pending entry
and its reviewable integration. Recording it alone cannot make the PR green.
For the requested topology, the minimum required accommodation is a separately
reviewed, explicit D2 exception route identifying its exact branch/target,
proposal/spec, behavioral review kind, operator decision, and combined review
receipt while preserving #202's current and history. Its integration receipt
must record D2 only, without invoking #202's `advance` handoff or resetting its
current. This is a process review unit, not D2 product scope. This document does
not authorize implementing that accommodation, adding a generic bypass,
weakening checks, mislabeling D2 as #201 implementation, or using an adjunct
targeting #202. Root can continue the authorized reviewable draft and report
this blocker; it must not manufacture standard workflow compliance.

## Proposed Contract

### Existing boundaries and selected route

| Boundary at the inspected base | Existing fact | D2 change |
| --- | --- | --- |
| `decision.py::get_vehicle_decision_info` | `vehicle_decision_info_v0`, `combined_view.view_id=decision-combined-v0`, unconditional null URL and template path | Preserve existing keys; expose a probed generation-bound URL or structured unavailable state |
| `publish_shadow_decision_frame` / `accept_decision_stream_frame` | Atomic `latest_decision.json`, complete strict `vehicle_decision_stream_frame_v0`, activation/run/PID/age and cycle-summary checks | Reuse acceptance on every decision request; leave v0 exports and policy unchanged |
| `automation.py::process_frame` | Same pending context connects capture, observation/memory and shadow cycle; capture and perception run at different rates | Feed the view immutable image/observation association from these existing producer events |
| `PerceptionViewServer` | Worker-owned loopback HTTP server, run/PID identity, start/stop, 8-frame perception buffer, `/`, `/memory`, `/api/latest`, `/frame?v=…` | Add `/decision` and isolated decision data/image routes to the same server; bound decision image retention independently |
| Exact-frame apply renderer | Current recorded image plus textual refs/authority; no live serving or retained spatial overlay | Preserve CLI, digest bytes, record layout and renderer behavior; it remains an offline supporting artifact |
| Physical observation | Donkey observation/memory/frame/control/mode exports; matched JPEG via `X-Frame-Id` | No physical integration. These exports alone are insufficient for D2 live acceptance |

The existing automation worker owns serving startup, bind fallback, publication,
and shutdown. Add a decision view component under `cli/automa_cli/decision_view.py`
to that server; no extra daemon, dashboard, command, port configuration or
third-party dependency. The component is optional for other engines and for
the physical observation server, where D2 returns `producer_unavailable` until
a separately contracted producer supplies the full decision boundary.

`info` performs bounded read/probe work only, never starts a server or worker,
stages an engine, captures a frame or writes a runtime file. Reuse the existing
view record's loopback origin, validate it, and probe the actual decision JSON
route with a 250 ms total HTTP budget and 8 MiB response ceiling. Disable
redirect following; require the expected loopback HTTP origin, vehicle, run,
worker and activation. Do not follow a record-supplied arbitrary API URL.

The worker's server ends on its existing normal, error, interrupt and stop
paths. A browser already open loses its live badge and clears live content when
fetch fails or the source is rejected. Cached files, a listening server, or a
live PID alone never prove an accepted cycle. Restart creates a new run; an old
URL must not silently attach to it, including after port reuse.

```text
same captured frame ──> existing decision producer ──> strict live decision read
        │                         │                           │
        └── immutable image ──> source association store ─────┤
                                                            v
info decision ──> existing loopback /decision ──> cycle + current/source images
```

### Endpoint and data schema

New names in this section are D2 view contracts, not extensions of the strict
v0 decision/authority/replay objects. `canonical_json_utf8` is the existing
sorted, compact, finite JSON byte serializer; `canonical_json_bytes` is a size,
not bytes. All hashes below are lowercase 64-character SHA-256 hex.

| Surface | Contract |
| --- | --- |
| `combined_view` in existing info payload | Keep `view_id` and `path_template`; add `available`, `status`, `reason`, `api_url`, `generation_id`, `identity`. `url` is the reachable page URL only after a successful current decision probe; otherwise null. A partial image/host explanation can still expose a URL. Human output prints the same URL or reason. Existing missing/invalid activation exits remain unchanged; a valid activation with unavailable view remains successful info with explicit unavailable details. |
| `GET /decision?generation=<generation_id>` | Existing `decision_view.html`, transformed into the live page; missing/invalid generation displays unavailable, never auto-selects a current worker. The static file opened directly reports “live source unavailable.” |
| `GET /api/decision/latest?generation=<generation_id>` | `automa_decision_view_v1` response, as below. Re-read activation/state/latest frame and reuse strict acceptance for each request; compare requested vehicle with frame and owner identity explicitly. Recheck generation after assembly. |
| `GET /api/decision/images/<image_id>?generation=<generation_id>` | Immutable registered PNG/JPEG bytes, content type and `X-Automa-Image-Sha256`; validate generation and live source again. Never accepts file paths, remote URLs, latest-image fallback, or a bare frame ID as an image identity. |
| Methods and framing | GET/HEAD only; HEAD uses identical status/headers. Other methods rejected. Use existing loopback transport's `no-store`, `nosniff`, CSP and no directory listing. Routes have no writes or control actions. |
| Failures | Malformed/duplicate/missing required query or invalid hash: 400. Unknown route/image: 404. Generation mismatch: 409. Malformed decision publication: 422. Absent/stopped/stale/unreachable source or wrong engine: 503. JSON data failures have the view schema, `status=unavailable`, machine `reason`, `decision=null`, no images or geometry. A reachable HTML shell is not data acceptance. |

Every JSON view response has the following keys. Successful payloads never
rewrite or regenerate the embedded cycle to fill missing input.

| Key | Type / rule |
| --- | --- |
| `schema` | `automa_decision_view_v1` |
| `view_id` | `decision-combined-v0` |
| `status`, `reason` | `current`, `partial`, or `unavailable`; reason null when current, stable code otherwise. `partial` retains an accepted live cycle with a missing image, geometry or host witness; it is not successful live evidence acceptance. |
| `served_at_ms`, `max_age_ms`, `expires_at_ms` | Actual read time, existing stream age ceiling (default 30000 ms), accepted `published_at_ms + max_age_ms`; null expiry on unavailable |
| `generation_id`, `identity` | Generation described below, or null when unestablished; never copy requested identity into an unverified producer claim |
| `decision`, `decision_sha256` | Full, unchanged accepted `vehicle_decision_stream_frame_v0` and SHA-256 of its canonical bytes; null on rejected decision |
| `current_image` | Image descriptor for **the decision cycle's** frame, not the fastest camera frame; explicit unavailable descriptor when absent |
| `evidence` | One entry per candidate source reference in candidate/ref order, including unselected/stale candidates. Exact raw ref plus association, source descriptor, provenance and supported geometry. No source refs means an empty array, not invented evidence. |
| `host_observation` | View-level independent observation status/value described below; raw authority envelope remains in `decision` |
| `limits` | Nonnegative integer fields `max_images=64`, `max_image_bytes=8388608`, `max_total_image_bytes=33554432`, `max_pixels_per_image=16000000`, `max_metadata_bytes=8388608`, `max_response_bytes=8388608`, `image_count`, `image_bytes`, `metadata_bytes`, `retention_refusals`, `size_refusals`. Usage and refusal counts describe this generation's view store, not memory policy. |

Successful JSON responses use HTTP 200. `combined_view.available` is true for
both `current` and `partial`; its status/reason mirror the probed response.
Unavailable info has null `url` and `api_url`; any identity it retains must
have been independently established. Cap both the decision-file read and
serialized view response at 8 MiB. An otherwise stream-valid cycle exceeding
either D2 bound yields HTTP 503 / `view_payload_too_large`, with no truncated
cycle or invented summary. This is a view limitation, not a new stream limit.
Bound refusal bookkeeping as counters, not an ever-growing rejected-ID log.

For a partial response, top-level reason is the first unavailable component's
reason in current-image, candidate/ref, then host order; each component keeps
its own reason. On unavailable responses, `decision`/`decision_sha256` and
expiry are null, `current_image` is an unavailable descriptor, `evidence=[]`,
and `host_observation` is unavailable with null value. No old data survives.

`identity` contains exact `vehicle_id`, `run_id`, `worker_pid`,
`activation_engine_id`, `activation_activated_at_ms`, and
`activation_sha256`. The activation hash covers canonical
`{engine_id, engine_spec, engine_config, activated_at_ms}` from the activation
used at worker construction. `generation_id` hashes canonical `identity`.
The server stores this immutable startup identity and compares both generation
fields and configuration hash to the current activation. A config edit with
an unchanged timestamp is a mismatch, not a relabeling opportunity. Frame
identity remains the tuple of generation plus `frame_id` and `frame_index`;
source/cycle timestamps are additional facts, not interchangeable identifiers.

Each available image descriptor contains `status=available`, `reason=null`,
`image_id`, `sha256`, `content_type`, `byte_length`, decoded `width_px` and
`height_px`, `generation_id`, `frame_id`, `frame_index`, `captured_at_ms`,
`observation_id`, and a server-generated relative `url`. `image_id` hashes the
canonical descriptor fields other than `status`, `reason`, `image_id` and `url`.
`sha256` hashes the exact served compressed bytes, not pixels, a path, or a
re-encoded approximation. Unavailable descriptors retain only established
source identities; image/hash/dimensions/URL fields are null and a reason is
required. Preserve Chase `simulation_epoch` and `simulator_frame_index` when
provided in the matching capture as an optional `environment_frame` object;
when present it participates in the image ID hash. Never import evaluator or
reference-decision records into the view's decision input.

The image store receives bytes from `publish_frame`'s existing captured input,
before transient source files can disappear. It seals an association to the
processed frame's observation when that frame's accepted cycle arrives. It
must compare that cycle's frame identity, observation ID, and sensor reading
association to the same capture event, not search for a plausible filename.
Repeated frame identity with different bytes or observation association is a
conflict, not an overwrite. Same image bytes from different frames may share
storage but keep distinct descriptors. Do not expose filesystem paths as fetch
parameters or reread arbitrary observation artifact paths during HTTP requests.

### Current versus retained geometry and retention

For `kind=memory_record`, resolve the ref's `id` against the complete
`cycle.source.memory.value.records`, not the 12-row memory summary. Require a
unique record and exact match of ref `frame_id`, `observation_id`, and
`plugin_id` with its provenance (including explicit nulls where permitted).
Carry `evidence_id`, `source_plugin_id`, `observed_at_ms`, `updated_at_ms`,
`coordinate_frame` and `record_id` through the explanation. For spatial
acceptance, require an archived source observation from this same generation,
with matching `observation_id`, evidence `thing_id=evidence_id`, plugin,
location, and image association. Missing archive means `source_unavailable`;
do not attach a fresh run identity to inherited memory.

Use the existing reducer's plugin attribution: the thing's non-null
`source_plugin_id`, otherwise the observation's `perception_plugin_id`,
including null only when both are null. Require one matching thing, its full
`location` equal to the record's location, and `provenance.observed_at_ms`
equal to that observation's `created_at_ms`. `updated_at_ms` is the source
memory-update time; it is not the capture-completion or observation time.
Recover source `frame_index` only from the sealed capture association, since
the existing `MemoryProvenance` has no frame-index or generation field.

Each `evidence` entry has `proposal_id`, `selected` (derived by equality with
the accepted selected proposal ID), `source_ref` (unchanged), `status`,
`reason`, `association` (`current`, `retained`, or `unavailable`),
`record_id`, `provenance`, `source_image`, and `geometry`. Ambiguous archived
source matches or conflicting archived geometry are refused locally, preserving
the accepted raw cycle for diagnosis. Unsupported ref kinds are explicit `unsupported_ref`;
D2 does not invent a resolver for new proposal types.

Here `provenance` is the unchanged `MemoryProvenance` export or null when
unresolved. Entry status is `available` only when source association, image and
geometry all validate with reason null; otherwise `unavailable` with the first
failing association, image, then geometry reason. A proven association stays
`current` or `retained` even if its image or
geometry is unavailable. Image absence must not erase known raw provenance.
Geometry status is `available` or `unavailable`; absent bbox/polygon values are
null. On refusal its transform and both coordinate arrays are null; preserve
an established coordinate-frame label for diagnosis. `host_observation.status`
likewise uses `available` or `unavailable`, distinct from the raw envelope's
`ready` status. No references is an ordinary empty explanation, not a failure.

Strict stream validation precedes all component projection. If duplicate
memory record IDs, malformed source refs, invalid envelopes or geometry already
fail the strict decoder, refuse the entire publication with HTTP 422 and
`decision=null`. Partial component refusal applies only to a stream-valid
cycle with unavailable/ambiguous image attribution or unsupported view geometry.
Never relax the decoder to exercise a partial-state fixture.

| Geometry / association | Rendering |
| --- | --- |
| Exact current source identity | Draw supported geometry on the matching current-cycle image |
| Retained or stale ref to an older frame in this generation | Separate source-image pane with original frame/observation IDs, image hash, record age and raw freshness/lifecycle; draw only there |
| `location.frame=image`, provenance coordinate frame `image`, valid normalized bbox and/or polygon, verified original source image | Use supplied geometry. If both are present, polygon is the outline and bbox may be shown as a labeled bounding box. |
| Zone only (`left`/`right` etc.) | Show the original zone as text. `geometry.status=unavailable`, reason `geometry_missing`; do not fabricate a half-image region. Such a case can exercise intent but cannot satisfy spatial overlay acceptance. |
| Missing source, mixed identity, unsupported coordinate frame, malformed geometry | No overlay; visible explanation of the missing/mismatched component |

`geometry` is `{status, reason, coordinate_frame, bbox_xyxy_norm,
polygon_xy_norm, transform}`. Accept only finite non-boolean numbers in [0,1],
four bbox coordinates with x1<x2 and y1<y2, and polygons of at least three
points with nonzero area. Reject invalid supplied geometry rather than clipping
or coercing it. Cap a rendered polygon at 256 vertices; excess is
`geometry_unsupported`. Do not simplify it into a new shape.

The only supported transform is `normalized_source_image_to_display`: multiply
normalized coordinates by the decoded source width/height, then uniformly scale
and translate with that same image's displayed rectangle. An SVG `viewBox`
matched to the image bounds is suitable. Preserve aspect ratio and account for
letterboxing; test resize. No crop, mirror, rotation, EXIF orientation change,
camera-motion estimate, homography or old-frame-to-current-frame transform is
supported. Non-identity image orientation is `image_orientation_unsupported`.
Old geometry never appears on a new frame even if dimensions, zone, object ID,
or compressed image hash happen to match.

Decision retention is volatile and separate from the existing 8-frame
perception cache: at most 64 images, 32 MiB compressed image bytes total, 8 MiB
per image and 16 million decoded pixels per image; PNG/JPEG only. Keep the
matching source observations with those images, at most 8 MiB aggregate
canonical metadata. Pin the current accepted decision image and all source
images referenced by records in its complete memory snapshot, including stale
records, until no longer referenced or the generation ends. Evict unpinned
oldest entries first. If bounds cannot accommodate a new image/association,
publish the cycle with explicit `retention_limit` for that component; never
evict a pinned source silently, block decision execution, or tune memory bounds.
Reject an oversized incoming item before allocation/decoding where possible.
Once evicted, a source is unavailable; no background disk scan or latest-image
substitution. Clear the store on stop or generation invalidation. This cache
is not durable capture or a retention promise across restarts.

The same finite store also owns pending capture associations; they count toward
all bounds and may be evicted before their slower cycle completes, yielding
explicit unavailability. Recompute pins atomically from each newly accepted
snapshot before admitting its association, releasing the preceding snapshot's
obsolete pins. HTTP render transactions acquire no lasting cache pins; eviction
between JSON and image GET is an explicit image failure. Archive only the
capture/sensor and source-observation fields needed for these joins; do not
copy `capture_record.shadow_reference` or other evaluator siblings into D2.

### Lifecycle, selection, authority and display behavior

Render the complete accepted cycle, not a newly run decision policy. Show
candidate plugin/ID, lifecycle, freshness, confidence, reason, command including
gear, all refs, `selected_proposal_id`, selector, and complete contributions.
Retained age is `cycle.source.timestamp_ms - provenance.updated_at_ms`; show it
beside, not instead of, the producer's freshness. Do not advance proposal
lifecycle merely because browser wall time passes. Wall time can only make
the publication unavailable. Inactive, missing-input, incompatible-input and
engine-error states remain visible with the actual null plan/command as emitted.

Show four separately labeled facts:

1. Proposed intent from `cycle.authority.proposed` / accepted plan.
2. Engine-authorized idle from `cycle.authority.authorized_output` including
   `shadow-only-idle`.
3. `proposed_applied=false`, read from the accepted authority, prominently.
4. Independently observed host output, or explicit unavailable and its reason.

The existing `ComponentEnvelope` named `authority.host_application` is retained
verbatim. It is not a typed physical telemetry contract today. For a numeric
view-level host claim, this proposal requires the proposed D1 value shape
`automa_host_observation_v1` below, in a ready envelope with exact generation,
frame and timestamp correlation. Other ready payloads may be inspected as raw
reported data but `host_observation.status=unavailable` with
`host_observation_unsupported`; do not guess fields. Missing, malformed,
future-dated, cross-generation or cross-frame reports also remain unavailable.

The proposed value contains `schema`, full `identity` (as above), `frame_id`,
`frame_index`, `observed_at_ms`, `observer` (named host output boundary),
`mode`, `user_input`, `pilot_output`, and `host_output`. Input/output values are
either null or `{steering, throttle}` with finite numbers; no invented defaults.
All listed keys are required. `observer` is a nonempty string, `mode` a string
or null, and timestamps/indices are nonnegative non-boolean integers. Control
numbers exclude booleans and numeric strings; reject rather than coerce them.
The envelope `updated_at_ms` equals `observed_at_ms`, which must fall between
this decision's source timestamp and publication timestamp. Ready host output
requires non-null observed `host_output`; a Pi zero-output witness additionally
requires mode `user`, idle observed user inputs and zero observed pilot values.
`host_observation` is `{status, reason, value}`; value is this exact validated
report only when usable. Nonzero telemetry is displayed as observed and cannot
be accepted as a zero-output witness. One frame never proves a whole interval.
D2 defines consumption and fixture coverage only; it creates no real host report.

### Missing, malformed, stale, stopped and mismatched state

| Condition | Required public result |
| --- | --- |
| No activation / invalid activation | Preserve existing info/stream errors; HTTP decision data unavailable |
| Valid activation, no worker/view/physical producer | Info URL null, `available=false`, actionable `producer_unavailable` or `view_unreachable`; no startup side effect |
| Missing or malformed latest decision | 503 missing / 422 invalid; no previous cycle presented as current |
| Wrong vehicle, run, PID, activation/configuration, or requested generation | Refuse data and image association. View generation mismatch is 409; expose stable reason, do not adopt the other generation. |
| Stopped/completed/error worker, dead PID, future publication or age above ceiling | Reuse stream refusal, HTTP 503; no cached live success |
| Accepted cycle, missing current or retained image | `partial`, authoritative cycle remains visible, affected descriptor unavailable, no spatial success claim |
| Stream-valid cycle, unresolved ref/ambiguous source attribution/unsupported view geometry | `partial`, affected entry refused, raw refs visible, no guessed geometry; strict decoder failures instead reject the whole cycle with 422 |
| Host unavailable, unsupported, malformed or mismatched | `partial`, separate host panel unavailable; engine idle never fills the panel |
| Missing/expired memory or no selected proposal | Show producer's inactive/idle state and empty refs honestly; do not retain a prior selected overlay |
| New camera capture while decision is still on an older frame | Show the image paired with the accepted decision; never silently switch to `/frame` latest |
| Browser fetch/image decode failure or expiry | Clear rejected live images/overlays and badge; display unavailable. Never combine fields/images from different responses. |
| Out-of-order reply | Discard it, including a superseded failure; it must neither overwrite nor clear a newer accepted transaction. |

Poll no faster than every 500 ms with a 2 s request timeout. Treat each response
as one render transaction: preload its immutable images, then install matched
image/geometry/text together; use response sequence tokens to discard older
in-flight results. Failed image fetch marks that component unavailable. Remove
the live badge and content at the declared expiry even if no new response
arrives. On tab visibility regain, invalidate the old display and fetch before
showing live content. A retained source image is historical evidence *inside*
an accepted current cycle, not itself a fresh camera claim.

A render transaction's deadline includes JSON transfer, image loading and
decoding, and is at most 2 s from request start. Calculate remaining freshness
from server `expires_at_ms - served_at_ms`, subtract elapsed transaction time
using the browser monotonic clock, and check it again immediately before
installing the transaction. Do not reset the deadline when an image completes
or rely on browser/server wall clocks agreeing. Clear at zero remaining budget
(conservatively at the stream's inclusive boundary); unchanged publication
timestamps cannot extend its life. Test an image arriving after expiry and a
superseded failure arriving after newer success.

### D1 publication requirements (consumer request, not accepted D1 scope)

“Existing” means present in the inspected repository boundary, not verified on
a deployed Pi. D1's eventual proposal must accept, revise with A1, or explicitly
leave unavailable the proposed requirements. D2 does not implement D1's
transport, physical shadow cycle, remote liveness adapter or telemetry sampling.

| Required field / capability | Existing vs proposed | Owner / provenance | D2 behavior and validation |
| --- | --- | --- | --- |
| Full `vehicle_decision_stream_frame_v0`, cycle, plan, authority and summaries | Existing local decision publication; absent from inspected physical observation route | Shadow adapter + automation; genuine host cycle | Reuse strict decoder/acceptance. Observation-only publication cannot satisfy it. |
| Vehicle, real run and producer liveness; activation engine/time | Existing local run/PID/state predicate; physical equivalent **proposed** | D1 physical lifecycle owner and its separately reviewed Automa integration | Never synthesize a local PID/state to impersonate Pi. Current D2 physical result stays unavailable until a real accepted adapter exists. |
| Activation configuration digest and D2 generation binding | **Proposed view addition**; config/time inputs already exist locally | Worker constructor / active activation; D2 hashes local inputs | Refuse changed config even with unchanged activation timestamp; fixture tests must mutate both independently. |
| Frame ID/index, capture/completion times | Existing Chase and Donkey observation publication | Camera/cycle snapshot, atomic capture boundary | Match exact decision/source tuple; preserve time meanings and environment frame identity. |
| Current observation, full memory snapshot, record/source IDs and provenance | Existing decision/physical observation structures | Observation and memory owners, unchanged | Match raw refs uniquely against full records and archived source observation; do not use summary truncation or recurring IDs as identity. |
| Actual image bytes with matched frame metadata | Existing Chase captured path; physical matched latest JPEG and `X-Frame-Id` | Camera publisher and existing matched-fetch boundary | Seal bytes at ingress. Existing latest-only physical API does not promise retained images or activation binding. |
| Image SHA-256, decoded dimensions, immutable frame/source access and retention | **Proposed** D2 image descriptor/store; physical supply/retention requirement **proposed** | D2 local store; future D1 genuine image ingress/retention | Verify exact bytes and source association; eviction/oversize/missing image is explicit unavailable. No filename/hash-only provenance claim. |
| Supported source geometry and coordinate frame | Existing `ViewLocation` bbox/polygon/zone and memory provenance | Original image-relative evidence | Render only supported geometry on matching source; zone-only is not a spatial overlay witness. |
| Lifecycle, selection/contributions, full proposed command, authorized idle, `proposed_applied=false` | Existing accepted proposal/plan/authority exports | PR #74 runner and selector | Render verbatim accepted meanings; no policy or authority changes. |
| `authority.host_application` envelope | Existing generic envelope; adapter defaults unavailable when not supplied | Actual host output owner | Preserve raw envelope; absence cannot establish zero output. |
| Ready value `automa_host_observation_v1`, identity/frame/time/observer, mode, user/pilot/final host output | **Proposed consumer shape**, not implemented/accepted D1 telemetry | Future D1 samples real host mode/input/output boundaries; never derives from authorized command | Require exact correlation; malformed/missing fields unavailable. Observe nonzero as nonzero; no interval inference. D2 positive coverage is explicitly fixture-only. |
| Deployed bundle revision, endpoint/session and interval provenance | Evidence requirement already named by #201; availability unverified | Device operator and later capability/evidence owner | Required in later live receipt; repository hashes or a URL do not prove deployment or throughout-zero control. |

## Ownership

| Concern | Owner |
| --- | --- |
| Acceptance and info contract | `cli/automa_cli/decision.py`, reusing its strict live predicate |
| Serving lifecycle and image ingestion | Existing `automation.py` + `PerceptionViewServer` |
| D2 projection, source association and image retention | New focused `cli/automa_cli/decision_view.py`, called by the existing server |
| Visual transaction and spatial explanation | `cli/automa_cli/decision_view.html` |
| Existing decision policy/authority/replay | Existing accepted modules, consumed unchanged |
| Physical generation, liveness and host publication | D1, separately contracted and verified |
| Workflow/PR exception integration | Root orchestrator; separate process review when necessary |
| Live capability receipts and canonical evidence | Separately authorized operator/evidence owner, including #202 |

## Affected Paths

| Journey | Observable result |
| --- | --- |
| Existing stage → info | Same command and schemas, now reachable decision URL or honest reason |
| Existing automation → stream → browser | Same accepted decision identity in JSON and page; no browser-owned producer |
| Current → retained → stale → inactive | Actual source image and supported overlay while referenced; producer lifecycle/selection and idle authority visible |
| Stop/restage/new run | Old view refuses and never reassociates its retained evidence |
| Offline apply/replay/record | Existing output and digest meanings remain intact |
| Physical observation-only view | Continues its existing behavior; no claim of physical D2 availability |

## Adversarial Matrix

These IDs define the closed D2 acceptance scope and map to commands/tests in
[the implementation spec](IMPLEMENTATION-SPEC.md#contract-to-test-map).

| ID | Case | Observable acceptance / reject condition |
| --- | --- | --- |
| D2-01 | Info before/after controlled producer/server availability | Public CLI exposes actual generation URL only after probe; no side effects; null/template/unreachable URL cannot satisfy ready case |
| D2-02 | Current left/right bbox and polygon | Actual decoded source images and overlays match raw refs; intent signs from existing fixtures/runner agree; changing camera latest cannot change the decision image |
| D2-03 | Fresh → retained → stale → inactive plus unselected stale ref | Source images survive beyond 8 captures while pinned; stale command-null/idle and later empty refs match cycle; no retained overlay on current frame |
| D2-04 | Wrong vehicle/run/PID, stop/death, restage/config edit, same frame ID in new run, reused port | Data refuses; old URL cannot attach to new owner; stopped image requests cannot succeed as live |
| D2-05 | Missing/corrupt cycle, summary mismatch, future timestamp, just-inside/just-outside age bound | Same production predicate refuses; page clears on expiry/failure; no republish or browser-generated lifecycle |
| D2-06 | Missing image, conflicting bytes, deleted transient file, changed source hash, eviction/size limit, oversized response | Immutable registered bytes remain correct after source file removal; conflicts/limits unavailable; pending captures are bounded, released pins permit admission, oversized view refuses without truncation; no latest/path fallback |
| D2-07 | Missing/duplicate ref target, mismatched observation/plugin/time, unknown ref, non-image/zone-only/invalid geometry | Strict-invalid cycle gives 422; stream-valid unresolved source gives partial. Exact plugin fallback resolves; raw refs visible only for accepted cycles; summary truncation does not hide a valid 13th record |
| D2-08 | Proposed nonzero versus idle; host missing, unsupported, ready zero/nonzero, wrong-frame/time/generation host report | Four authority facts remain separate; no false zero or interval claim |
| D2-09 | Encoded path traversal, URL-shaped image ID, malformed/duplicate generation, redirected/non-loopback view record, script-shaped labels | Fixed routes refuse; no arbitrary read/fetch; raw text remains inert; no request/control writes |
| D2-10 | Browser resize, reversed response/image completion, delayed image past expiry, superseded failure, network loss, visibility regain | Correct overlay scaling and atomic image/text association; no expired install, stale live badge, mixed cycles, or clearing newer content because of an old reply |
| D2-11 | Existing decision/perception/memory/automation and apply/record callers | Regression checks pass, byte-equal replay still meaningful, no default disk writes added to info/stream/apply |
| D2-12 | Physical observation without full producer; deterministic test PID or ready host fixture | Explicit physical unavailability; fixture labels never become D1 or live-environment receipts |

## External Assumptions

Existing image-relative geometry describes the original camera orientation;
only validated PNG/JPEG and identity orientation are supported. The local
publisher is trusted to connect its actual capture and cycle; hashes establish
byte consistency and bounded provenance, not authenticated hardware origin or
protection from a malicious same-user process rewriting all inputs.

The current local PID-based stream predicate is not a remote physical liveness
contract. D1 must settle that owner before physical D2 verification. Neither
device connectivity nor a deployed Pi bundle nor a live Chase decision stream
was inspected in A1. Browser capability is not assumed from tool availability;
L1 must actually inspect the implemented page or report that gate outstanding.

## Non-Goals

- D1 physical integration, endpoint recovery, remote PID emulation, deployment,
  device/simulator operation, canonical capture or changing #202.
- Decision/perception/memory policy, new engine/plugins, movement, authority
  semantics, tracking, prediction, camera motion or cross-frame transforms.
- Remote/LAN hosting, authentication product, arbitrary URL/file serving,
  a second dashboard/server process or a new CLI command.
- Changing existing strict cycle/stream/replay schema keys, exact-frame record
  layout or canonical replay digest meaning.
- Persistent capture by default, hardware authenticity, physical-object identity,
  navigation safety, criterion promotion, milestone closeout, merge or workflow
  tooling changes.

## Evidence rendering

- Derived HTML: yes for sealed implementation verification records.
- Stable evidence directory:
  `docs/milestones/006-decision-facing-perception-readiness/evidence/live-decision-view/`.
- Source records, image hashes and browser receipts are authoritative for their
  stated fixture/live scope. Commit a derived HTML view next to any sealed
  result JSON. Screenshots corroborate actual rendering; they do not replace
  source records. The live page is not this durable evidence rendering.
- A1 creates no sealed runtime signal, fixture, screenshot or acceptance result,
  so this proposal-only commit needs no derived evidence page. No skip is sought.

The combined implementation uses bounded deterministic evidence and actual
browser inspection of controlled fixtures. Later Chase and Pi live capability
receipts remain separate: exact source/deployed versions, info URL/JSON,
vehicle/run/activation/frame identities, source images/hashes, host observation
coverage, stop/stale/mismatch output, browser screenshots and inspection
disposition, and authorization. Pi depends on D1. Canonical #202 capture remains
separately gated by #201; neither this design nor fixture acceptance opens it.

## File Impact

A1 creates only this proposal and adjacent `IMPLEMENTATION-SPEC.md`. No plan,
tooling, product, test, PR or live-state change is part of the A1 commit.
The pending decision-log text and accommodation belong to root as described
above. L1's explicit file/component allowance is in the spec; expansion to D1,
accepted schemas, authority or workflow requires returning the issue to A1/root.

## Validation Plan

A1 runs read-only source/GitHub metadata inspection, workflow status, standalone
plan validation, generated-document check, proposal structure/handoff-template
validation, local link checks and `git diff --check`. In-memory validator trials
above modify no files. The actual standard `validate-pr` failure must be retained
as a blocker, not converted into a passing check.

L1's exact deterministic and public-interface commands, browser cases and
expected failures are in [IMPLEMENTATION-SPEC.md](IMPLEMENTATION-SPEC.md).
A1 does not run product tests, launch a server, inspect a live browser, stage a
vehicle or claim D2 acceptance.

## Expected Handoff

A1 hands this written contract to L1 under the explicit combined-job override.
After L1/A2, root should have a reviewable combined draft with exact head/base,
checks, review disposition and unresolved workflow/live gates. The standard
template below describes D2's eventual scoped success only. It is **not
executable against the current M006 plan**: `advance` there would incorrectly
complete/reset #202's evidence frontier. The separately reviewed accommodation
must preserve that current and record D2 independently. No criterion changes,
acceptance fact or invented successor are encoded here.

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "D2 correlated live decision view and bounded deterministic/browser verification in PR #{pr}; environment availability and M006 evidence acceptance remain separate. Apply only through a reviewed D2 integration accommodation preserving the current evidence frontier.",
  "criterion_updates": {},
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "D2 contracts no successor and does not complete the active evidence unit.",
    "revisit_when": "The root reconciles the D2 integration route and separately authorized live capability verification."
  }
}
```
