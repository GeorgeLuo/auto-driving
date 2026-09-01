# Proposal Amendment: Operator-selected perception plugins for image replay

## Review Kind

Behavioral feature slice

## Review Question

Can an operator point the local workbench at a supported frame-set directory,
inspect every manifest-backed perception plugin discovered from a declared
plugin directory, toggle the active plugin set before playback, and review the
selected plugins' server-produced overlays, observations, and memory effects
through the same CLI/API replay contract?

## Reason For Amendment

The accepted proposal in PR #172 makes `lightweight_observer` the fixed
perception choice and explicitly lists algorithm selection as a non-goal. That
boundary is sufficient for a pipeline smoke test but not for the operator
review that M008 is intended to support: a visual approval cannot establish
that the workbench is useful when the operator cannot choose which perception
composition produced the displayed design.

The implementation review confirms the gap. The current implementation head
`f17bbcc` constructs the replay runner with the fixed catalog algorithm and
the page exposes no active-plugin control. Meanwhile, the repository already
has a server-side plugin composition owner in
`autonomy/perception/mappers/plugin_runner.py` and manifest-backed candidate
directories under `lab/plugins/perception/`. The missing piece is a bounded,
operator-visible catalog and selection contract, not a browser-side algorithm
implementation.

The direct operator requirement is recorded here as the durable review
evidence: the primary workflow is “point at a frame set for playback, then
toggle which plugins are applied.” Without this correction, the delivered
visual surface cannot answer the operator's stated question. This is a
contract change—not a visual-polish adjunct—because it changes the accepted
configuration authority, structured state, CLI/API actions, validation
surface, and an explicit non-goal.

The original proposal, its acceptance, its expected handoff, and every prior
artifact remain immutable. This amendment is additive and does not authorize
implementation in the amendment PR.

## Evidence Requiring Amendment

| Evidence | Finding |
| --- | --- |
| Accepted proposal PR #172, `perception-memory-workbench.md`, “One shared sequence and structured state” | Fixes `lightweight_observer` and rejects algorithm selection, so the operator cannot vary the displayed perception composition. |
| Implementation review head `f17bbcc`, `cli/automa_cli/workbench_runner.py` and `cli/automa_cli/workbench.html` | The runner reports/constructs the fixed catalog algorithm and the page has no plugin catalog or active-plugin control. |
| Existing `autonomy/perception/mappers/plugin_runner.py` and `lab/plugins/perception/*/plugin.json` | The repository already has composition/provenance and manifest-backed directory patterns that can support a bounded selection seam. |
| Operator review requirement recorded in this amendment request | The required journey is a frame-set playback in which the operator toggles which plugins are applied; a fixed composition cannot receive meaningful visual approval. |

## Contract Delta

### Preserve the existing replay and safety boundary

The amendment keeps `workbench.image_replay.v1`, the normalized ordered
image-directory feed, the server-owned perception → `Observation` → bounded
memory pipeline, the long-lived loopback page, and the observation-only
policy. The source remains read-only; the workbench still starts no vehicle,
worker, simulator, movement/control, recording, Metrics operation, or remote
authority. The browser remains a presentation and action client, not a source
decoder, plugin loader, observation builder, or memory reducer.

`bounded_evidence` remains the fixed memory implementation. The existing
`Observation` schema and `PerceptionText` output contract remain authoritative;
selected plugin output is composed through the existing server-side mapper and
retains per-plugin provenance in `plugin_runs`, signals, things, measurements,
and artifacts.

### Declared plugin directory and catalog

The runner accepts a server-owned `plugin_dir` in addition to the existing
frame-set source. The CLI exposes it as a bounded `--plugin-dir PATH` option;
the loopback API may accept the same path only while the runner is idle or in a
terminal state. A page may request a new path, but it never receives a file
handle or executes code itself. The default when the option is omitted is the
packaged catalog that preserves the existing `lightweight_observer` behavior.

The declared directory is read-only and is canonicalized before discovery.
Its plugin packages are directories below the root containing a `plugin.json`
manifest; discovery walks that tree without following a path outside the
canonical root. Discovery is deterministic by normalized plugin ID and then
manifest-relative path.
Each manifest must declare, at minimum:

- a unique safe plugin ID, display name, description, and entrypoint;
- the plugin configuration owned by the manifest, with no browser-supplied
  entrypoint or arbitrary configuration override;
- its supported input/component contract and `perception_text_v2` output
  compatibility; and
- enough runtime/readiness metadata for the server to report whether the
  plugin can be instantiated without installing dependencies or downloading
  a model during replay.

The loader discovers every manifest-backed package under the root. A malformed,
unreadable, duplicate, incompatible, or not-ready package is retained in the
catalog with an explicit `unavailable` reason; it is never silently omitted.
Duplicate IDs make the catalog invalid because an ID-only selection would be
ambiguous. A package is loaded for catalog inspection before playback, but its
entrypoint is instantiated and invoked only when it is selected. This makes
“all plugins under the directory are available for use” observable without
running unselected code or causing unrequested plugin side effects.

The catalog response includes the canonical root, a stable digest of the
normalized manifest metadata and entrypoint identities, each plugin's ID,
description, output kind, input requirements, readiness/error status, and the
current default/active state. Existing manifest-backed lab candidates may be
adapted when their declared runtime is ready; packages requiring an unsupported
isolated runtime are visible as unavailable rather than being silently run in
the core process. Dependency installation, network model fetches, and
automatic setup are outside this amendment.

### Operator selection and replay lifecycle

The page adds a plugin-catalog pane next to the frame-set source controls. The
operator can:

1. declare/validate a frame-set directory;
2. declare/refresh a plugin directory and inspect every discovered package;
3. toggle one or more ready plugin IDs; and
4. start playback with that selection.

The active set is normalized in catalog order (sorted plugin ID) and becomes
immutable for a run. Toggling while a run is active is rejected or staged for
the next run; it can never change the mapper midway through a frame sequence.
At least one ready plugin is required. Selecting an unavailable, unknown, or
duplicate ID fails before the first frame is processed and identifies the
catalog boundary. If no `plugin_dir` or selection is supplied, the default
active set is the existing `lightweight_observer` composition (`frame`,
`floor_plane`) so the original CLI invocation remains compatible. When an
explicit plugin directory is supplied, the operator must choose the active
IDs from that catalog before starting; the workbench does not silently run
every discovered plugin or fall back to a different root.

The API accepts only declarative fields such as `plugin_dir` and
`active_plugin_ids`; it never accepts a Python spec, argv, module path,
unreviewed plugin config, source adapter, or memory implementation. The CLI
and API invoke the same runner and validate the same catalog and selection.
The declared source identity, plugin-root identity, catalog digest, active
plugin IDs, and deterministic plugin order are included in structured state,
human output, and `--json` output. Every frame's perception state shows the
selected plugin runs and their status/provenance, so the visual review can
compare a run with a different selection without guessing which composition
was applied.

The state machine gains only the bounded catalog/configuration actions needed
for this workflow: inspect or validate the source and plugin root while idle,
select the active IDs while idle or terminal, then start/pause/resume/step/
reset the existing replay. A root or selection change resets pending run
configuration and cannot mutate an in-progress or completed run's historical
state. A refresh returns the last terminal state together with its immutable
plugin selection and catalog digest.

### Composition and acceptance boundary

The selected IDs are translated by the server into the existing
`PluginPerceptionMapper` (or an owned adapter that presents the same mapper
contract). Plugins share the normal resolved inputs and reset lifecycle;
plugin-specific outputs remain attributed by `source_plugin_id`. The selected
composition changes which packaged perception evidence is produced, but it
does not create a new observation or memory semantic, a second execution
authority, or a plugin-specific browser path.

The implementation and the queued POC acceptance must demonstrate the concrete
operator journey: choose a real frame set, choose a plugin directory, run once
with the default set, run again with a different valid set, and see the active
IDs and plugin-attributed visual/memory state in both runs. A visual approval
that cannot identify or change the active plugin set is not sufficient for the
amended review question.

The amendment does not add video/live ingestion, arbitrary upload, a generic
workflow builder, algorithm-training controls, plugin editing, dependency
installation, or a new external authority. Those requests remain later
frontiers.

## Operator Want

- **Want:** point the workbench at a frame-set directory, point it at a
  plugin-directory tree, inspect all discovered plugin entries, toggle the
  active ready IDs, start playback, and see those IDs and their attributed
  overlays/memory effects in the resulting run.
- **Reject if:** the page only shows a fixed algorithm, silently omits a
  discovered package, accepts arbitrary code/configuration from the browser,
  or cannot prove which selected plugins produced the displayed state.

## Ownership

| Concern | Owner | Required result |
| --- | --- | --- |
| Plugin-root canonicalization, manifest discovery, readiness, duplicate handling, and catalog digest | Workbench replay runner / perception plugin-directory adapter | Every package under the declared root is represented deterministically; invalid packages are visible and never silently executed. |
| Entrypoint resolution and plugin execution | Existing `PluginPerceptionMapper` seam or a bounded adapter owned by the perception implementation | Only server-selected, validated IDs are instantiated; reset, input sharing, output provenance, and failure status use existing contracts. |
| Selection, source, and replay configuration API | Workbench loopback server and shared runner | API accepts declarative paths and IDs only; configuration is locked for each run and is identical for CLI and HTTP. |
| CLI parse and human/machine output | `cli/automa_cli/app.py` plus workbench command module | `--plugin-dir`, selection, catalog digest, active IDs, and failures are exposed without a second execution path. |
| Plugin catalog pane and visual attribution | `cli/automa_cli/workbench.html` | The operator can inspect, toggle, and identify the active set without browser business logic or raw JSON. |
| Operator acceptance and durable gap disposition | M008 assessment and queued replay-workbench POC acceptance unit | The demonstration compares at least two valid selections and records whether the amended visual surface is minimally useful. |

## Affected Paths

### Contract and shared runtime paths

- `cli/automa_cli/app.py` and the workbench command module for the optional
  plugin-root flag and declarative selection boundary.
- `cli/automa_cli/workbench_runner.py`, `workbench_server.py`, and
  `workbench_contract.py` for catalog, selection, immutable run state, API
  actions, CLI/API parity, and failure/refusal contracts.
- `cli/automa_cli/workbench_source.py` only if source/plugin-root validation
  helpers must share the existing local read-only boundary.
- `autonomy/perception/mappers/plugin_runner.py` and the owned perception
  activation/catalog adapter for manifest-to-plugin composition, reset, input
  compatibility, and provenance.
- `implementations/perception/catalog.py` and/or a new
  `implementations/perception/plugins/` manifest directory for the packaged
  default catalog and backward-compatible `lightweight_observer` selection.
- `cli/automa_cli/workbench.html` for the catalog pane, toggles, locked-run
  state, active-plugin attribution, and accessible error/recovery text.

### Focused proof and durable documentation

- `tests/cli/test_workbench.py`, focused plugin-directory/manifest tests, and
  perception mapper tests for discovery, deterministic selection, CLI/API
  parity, negative cases, and repeated-run isolation.
- `docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md`
  for the amended operator journey and any unavailable/deferred package
  disposition.

No implementation, test, runtime artifact, accepted proposal, or existing
assessment result is changed by this amendment PR.

## Adversarial Matrix

| Attempted bypass or failure | Required behavior |
| --- | --- |
| Browser sends an entrypoint, Python module, argv, raw mapper config, or memory implementation | Reject before lifecycle work; only server-discovered IDs and manifest-owned configuration are accepted. |
| Browser changes `active_plugin_ids` while a run is running or after a frame has been processed | Reject or stage for the next run; the current run's selection and catalog digest remain immutable. |
| Plugin root is missing, not a directory, unreadable, a symlink escape, or contains a manifest/entrypoint outside the declared boundary | Refuse catalog activation with a named path boundary; never execute a path that was not proven inside the declared root or approved packaged catalog. |
| Two manifests expose the same ID, or an ID is malformed/unsafe | Mark the catalog invalid and refuse start until the ambiguity is resolved. |
| A manifest is malformed, output-incompatible, missing its entrypoint, not ready, or requires dependency/model setup | Show it as unavailable with a bounded reason; do not silently omit it, install anything, or execute it. Starting with another valid selection remains explicit and does not claim the unavailable plugin ran. |
| Selection is empty, unknown, duplicated, or includes an unavailable plugin | Refuse before the first frame and name the offending selection/catalog entry. |
| Selection order differs between CLI and browser, machines, or refreshes | Normalize by the catalog's deterministic ID order and record the resulting ordered IDs and catalog digest. |
| A valid plugin throws, times out, returns an invalid batch, or emits undeclared artifacts | Preserve existing plugin-run error/unavailable semantics, attribute the failure to that plugin, and do not report an all-success perception or fabricated observation. |
| Unselected plugin has state, diagnostics, or a model | Do not instantiate or invoke it; a catalog inspection has no perception/memory side effect. |
| Reset or a second run reuses plugin state from the prior selection | Reset/discard the isolated mapper and memory; the next run starts with its own source identity, selection, generation, and empty prior history. |
| Source frame set is empty, malformed, over-limit, or contains an absence annotation | Preserve the existing source refusal/no-input path; plugin selection never fabricates a frame or perception result. |
| Page refreshes or a terminal run is followed by a new selection | Return the terminal run with its original IDs/digest, then allow a separately validated next run without relaunching the server. |
| A plugin-selection run starts a worker, simulator, movement/control, recording, Metrics request, network install, or model download | Reject as out of contract; the replay remains observation-only and local. |
| Browser computes overlays, plugin status, observations, or memory records | Fail review: all values and provenance are rendered from server-produced structured state. |
| A visual screenshot shows overlays but not the source, active IDs, or selection status | Treat the operator review as incomplete; the page must make the composition and run identity legible without raw JSON. |

## External Assumptions

- The operator can name a readable local frame-set directory and a readable
  local plugin root; those paths remain available for the duration of a run.
- Plugin packages use the repository's supported manifest/entrypoint contract
  and are trusted local code. A future sandbox for arbitrary third-party code
  is a separate safety/product decision.
- The host's core Python environment can import the selected packaged plugins.
  An isolated runtime or model-dependent package is usable only when an
  already-supported adapter reports it ready; this amendment authorizes no
  setup, network access, or dependency installation.
- The existing `PerceptionPluginContract`, `PluginPerceptionMapper`,
  `PerceptionText`, `Observation`, and bounded-memory interfaces remain
  available and continue to own input resolution, reset, provenance, and
  semantic conversion.
- The loopback server remains local and the operator is the authority for
  selecting the declared directory. The browser is not granted general local
  filesystem access.

## Non-Goals

- Rewriting the accepted proposal or its reviewed `Expected Handoff`, changing
  the queued frontier, or modifying prior amendment artifacts.
- Video/live adapters, arbitrary upload, simulator/vehicle integration,
  movement/control, worker orchestration, Metrics UI, remote hosting, or
  authentication.
- A generic plugin marketplace, package installer, model downloader, runtime
  sandbox, plugin editor, training/configuration UI, or browser-side code
  execution.
- Executing every discovered package regardless of readiness or running
  unselected plugins “for comparison”; discovery and active execution are
  deliberately separate.
- Changing `Observation` or bounded-memory semantics, inventing plugin-specific
  memory, or claiming that a plugin's current evidence is a durable world
  fact.
- A new algorithm-comparison product surface beyond selecting the active
  plugins for this one frame-set replay journey.
- The operator-acceptance verdict itself; the amended implementation supplies
  the selectable workbench and the queued evidence unit records usefulness.

## File Impact

This amendment PR changes only:

- this additive amendment artifact;
- canonical `docs/milestones/008-cli-decision-workbench/plan.md`; and
- generated `docs/milestones/008-cli-decision-workbench/plan.html`.

After exact-head amendment review and acceptance, the implementation PR may
change only the shared runner/API/CLI/page, manifest/catalog adapter, focused
tests, and assessment paths listed above. It must link PR #172 and this
amendment, preserve the original expected handoff, and reconcile any package
that remains unavailable without hiding it from the catalog.

## Validation Plan

### Amendment PR

1. Run `python3 docs/milestones/workflow.py validate
   docs/milestones/008-cli-decision-workbench/plan.md` and confirm the state is
   `proposal_amendment_in_review`.
2. Run `python3 docs/milestones/workflow.py validate-pr` for the amendment
   transition. Confirm the changed paths are limited to this artifact, the
   canonical plan, and generated plan HTML; the accepted proposal and its
   reviewed handoff have no diff.
3. Run `python3 docs/render_markdown.py --check` and `git diff --check`.
4. Obtain the required exact-head contract review receipt before merging. No
   implementation branch may start while this amendment is in review.

### Implementation PR after amendment acceptance

Deterministic coverage must prove:

- CLI and HTTP discover the same root/catalog, validate the same IDs, invoke
  the same runner, and expose the same ordered selection, catalog digest, and
  failure semantics;
- every manifest-backed child package appears exactly once or is reported with
  an explicit unavailable/error status, with duplicate IDs and path escapes
  rejected before execution;
- default selection preserves the existing `lightweight_observer` run, while a
  second valid selection changes the active plugin IDs and is visible in CLI,
  API, page, and per-frame provenance;
- the browser can send paths and IDs but cannot send entrypoints, argv, raw
  configs, or generated perception/observation/memory values;
- toggling is available before start, locks for an active run, and does not
  leak mapper or memory state across reset and repeated runs;
- selected plugin failures, invalid outputs, unavailable runtimes, malformed
  sources, stale actions, and empty selections fail or degrade through named
  server-owned states without claiming success or fabricating evidence; and
- the full amended workflow remains observation-only, source read-only, local,
  and free of worker/simulator/movement/recording/Metrics/setup side effects.

The focused workbench/plugin suite, affected adjacent tests, normal default
suite, plan validation, Markdown rendering check, and `git diff --check` are
required before implementation review. The queued replay-workbench POC
acceptance unit—not this amendment PR—performs the guided visual demonstration
with two valid plugin selections and records the operator's usefulness verdict.

## Independence Check

- [ ] No accepted proposal or prior amendment was modified.
- [ ] No product or runtime implementation changed.
- [ ] No implementation tests or generated runtime artifacts were added.
- [ ] The new amendment, plan transition, and generated plan HTML are the only
  changes.
- [ ] The accepted proposal's reviewed `Expected Handoff` is unchanged.

## Repair Cycle Ledger

| Cycle | Review receipt | Classification | Highest severity | Repair revision | Contract impact |
| --- | --- | --- | --- | --- | --- |
| None | None | None | None | None | None |

## Review Notes

- This amendment intentionally replaces the accepted proposal's fixed-plugin
  clause only as described above; all other accepted behavior remains in force.
- The visual approval requirement is now testable: the operator can identify
  the source, plugin root, active IDs, catalog digest, and resulting evidence
  without relying on shell commands or raw JSON.
- The `147` change is historical ancestry, not the canonical M008 branch. The
  amendment uses the required `m008/amend-plugin-selection` branch from
  `origin/milestone/008-cli-decision-workbench`; proposal and implementation
  remain separate review phases.
