# M008 assessment: perception-memory workbench

| Field | Value |
| --- | --- |
| Milestone | 008 Perception-Memory Workbench Feasibility |
| Frontier | Perception-memory workbench journey |
| Review kind | Behavioral feature slice |
| Accepted proposal | [PR #172](https://github.com/GeorgeLuo/auto-driving/pull/172) |
| Accepted merge | 09687f19acd61b286378fb65f3db915ce5e50d51 |
| Implementation branch | m008/perception-memory-workbench |
| Assessment status | Implementation slice ready for focused review; operator POC acceptance remains separate |

## Selected composition

The selected bounded journey is:

~~~text
ordered image directory
  -> existing packaged perception mapper
  -> autonomy.decision.observation.observation_from_perception
  -> existing DecisionCycle memory stage
  -> server-owned structured state and local page
~~~

The sequence identity is workbench.image_replay.v1. The first adapter is
image_directory; it accepts supported JPEG, PNG, WebP, or BMP files, honors a
declared manifest order when present, and otherwise uses lexical filename
order. lightweight_observer and bounded_evidence are fixed for this slice.
The CLI and HTTP server call the same ImageReplayRunner; the browser never
constructs a perception, observation, or memory value.

## Capability assessment

| Surface / composition | Inputs and signals | Side effects and cleanup | Workbench fit | Disposition |
| --- | --- | --- | --- | --- |
| Ordered image directory -> lightweight_observer -> Observation -> bounded_evidence | Read-only image path, stable source/frame identity, ordered index, timestamp, optional absence annotation; server state exposes structured perception things/signals, observation, memory records, and per-frame effect | Mapper and isolated memory stage are reset at terminal cleanup; no vehicle, simulator, worker, Metrics, movement, or recording path | Directly answers the selected operator question and is deterministic | Selected as workbench.image_replay.v1 |
| automa vehicles perception apply | One image or existing image sequence; returns perception reports and optional artifacts | Existing command can record reports and is perception-only, but it does not own the observation-to-memory journey or long-lived controls | Useful implementation/reference seam, not a second workbench authority | Reuse request shape and mapper catalog; keep command semantics unchanged |
| automa vehicles memory replay | automa_memory_observation_sequence_v0 JSON; returns memory snapshots and digest | Offline bounded memory replay; optional explicit recording | Proves memory determinism but has no image capture or overlay | Keep as a diagnostic/reference input; do not make it the visual workbench source |
| Existing perception / page | Published frame/perception record | Live view publication owned by the vehicle automation surface | Existing overlay semantics inform the workbench, but live publication is outside the replay authority | Adapt meanings through server-produced state; do not copy its lifecycle |
| Existing memory /memory page | Published memory snapshot | Live worker/host state | Existing record and selected-value presentation is useful, but it depends on a live vehicle worker | Adapt ledger presentation only; no live dependency |
| M007 live continuity / automation | Vehicle or simulator, active worker, current view, memory lifecycle | May involve worker, deployment, simulator, or vehicle boundaries | Not deterministic for this first POC and violates the observation-only replay boundary | Later feed/evidence path; no implementation change here |
| Video file | No accepted repository-owned timestamp/order/decoder contract for this slice | Would add decoder and source-lifetime policy | Same page could eventually consume a normalized feed, but semantics are not selected | Later product proposal |

## Implemented boundary

The workbench owns the following normalized source fields before pipeline
execution:

- source_id, frame_id, frame_index, ordered position, and timestamp_ms;
- supported image path, content type, byte size, dimensions, and optional
  absence reason; and
- manifest/source identity and adapter metadata.

Source validation fails before mapper or memory creation for empty input,
unsupported formats, unreadable or undecodable images, symlink/non-regular
paths, traversal, duplicate frame identity, non-increasing sequence metadata,
and configured frame/byte limits. The ImageReplayRunner then creates the same
SensorSnapshot and perception request boundary used by existing perception
application. Non-absent frames pass through the fixed mapper, Observation, and
DecisionCycle memory stage. An absent frame follows the no-perception
observation path and cannot fabricate image evidence.

The structured state includes server identity, sequence/run/source identity,
adapter, current frame/position, phase, progress, allowed actions, summary,
machine detail, perception, observation, memory, timeline effects, failure
boundary, recovery action, and terminal cleanup. The loopback server remains
available after completion, failure, cancellation, or reset and can start the
next declared run. Actions are typed and allow-listed; unknown fields and raw
argv are rejected, and stale run identifiers fail closed.

The page provides source selection and validation, start/pause/resume/step/
cancel/reset, cadence, overlay visibility, timeline, server-produced overlays,
perception text, and an identity-linked memory ledger. JavaScript is a
presentation client: it does not read the source directory, decode images,
derive observations, mutate memory, or invoke a command.

## POC-completion envelope

| Addition or deferred request | Why it is present or deferred | Decision |
| --- | --- | --- |
| validate and set_cadence actions | Small support actions make source failure and controlled replay usable through the same page/CLI-owned runner; they do not alter fixed pipeline semantics | Admitted under the four proposal envelope conditions |
| cancel action | Gives a bounded recovery path for a long-lived local server and ensures isolated stage cleanup | Admitted under the four proposal envelope conditions |
| --max-frames, --host, and --port CLI options | Expose source and loopback safety limits already enforced by the runner; they do not add a source or semantic choice | Admitted under the four proposal envelope conditions |
| Video, live ingestion, arbitrary algorithm selection, candidate comparison, recording, simulator/vehicle control, or external hosting | Each changes source, semantic, authority, or operator goal | Deferred to a later proposal; no follow-up in this implementation unit |

No new product frontier, catalog entry, external authority, or alternate
execution path was added. No generated runtime report or recording artifact is
part of this implementation.

## Validation evidence

Focused deterministic coverage is in
[tests/cli/test_workbench.py](../../../../tests/cli/test_workbench.py). It covers:

- manifest order, identity, bounds, traversal, unsupported input, and absence;
- the real packaged perception -> Observation -> bounded-memory path;
- no fabricated perception for absence;
- pause/resume/step/reset, stale-run refusal, and terminal cleanup;
- shared loopback state, frame transport, persistence after completion, and
  rejection of raw argv; and
- the public automa vehicles workbench replay --json entry point.

The implementation validation command is:

~~~text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.cli.test_workbench
~~~

The required broader repository suite and milestone documentation/workflow
checks remain part of implementation handoff. The current repository-wide
suite also exposes an integration boundary: M007 is closed and its CLI audit
inputs are digest-pinned, while this accepted M008 slice adds the public
`vehicles.workbench` parser leaves. The unchanged M007 audit therefore reports
those two new leaves as a membership mismatch; its historical artifacts must
not be rewritten in this implementation. A separately reviewed CLI-audit
maintenance unit is required before the normal repository-wide suite can be
green. The separate Replay workbench POC acceptance evidence unit owns the
operator judgment for M008-03, M008-05, and M008-06; this document does not
claim that judgment.

## Residual gaps

| Gap | Evidence needed | Owner / disposition |
| --- | --- | --- |
| Operator finds the first-use page useful and legible | One guided POC acceptance session against the selected journey | Separate M008 evidence unit |
| Video or live source semantics | A later source proposal defining ordering, timestamps, identity, and lifecycle | Later product decision |
| Browser-level visual interaction across supported browsers | The POC acceptance session may identify a bounded repair; browser compatibility beyond the local page is not claimed here | Evidence unit first; later proposal if scope expands |
| Closed M007 CLI audit remains pinned while the accepted M008 CLI adds two parser leaves | The implementation must preserve historical M007 evidence rather than silently changing its inventory or digests | Separate CLI-surface maintenance review before repository-wide CI can pass; no M007 artifact rewrite here |
