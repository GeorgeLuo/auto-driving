# Automa Vehicle Automation Workspace

This repository is the local source of truth for a vehicle-agnostic automation
framework, a PiRacer/DonkeyCar target, and a Chase simulator adapter. Each frame
runs one decision cycle of six steps: perception, observation, memory, proposal,
plan, and action. Every step runs the plugins selected for it in its own
activation (`runtime/<step>/active.json`). The default action plugin, `selected`,
authorizes the plan's selected command. A shared execution runtime owns movement
mode, delivery, freshness, and stopping on both Chase and PiCar. Starting
`vehicles automation run --id <vehicle>` enables autonomous movement;
`--observe-only` runs the same plugins without delivering their output.
Without a proposal, the cycle produces idle control.

## Setup

From the repository root, create and activate a local environment, then install
runtime and analysis dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Activate the existing environment with `source .venv/bin/activate` in each new
terminal. The CLI uses that terminal's `python3`; it is a repository executable,
so no package installation or PATH change is needed. Without activation, use
`.venv/bin/python cli/automa help`.

Use the CLI from the repository root:

```sh
./cli/automa help
./cli/automa vehicles help
./cli/automa simulators help
```

Run the offline test harness:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
```

Include the live Chase simulator smoke test:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --live-sim
```

Check a powered-on Pi without moving it:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --live-pi
```

The Pi check reads autonomy status only. It does not send drive or mode changes,
restart the runtime, or connect over SSH. See [`tests/README.md`](tests/README.md)
for prerequisites and endpoint overrides.

## First-Time Navigation

After setup, use this path instead of reading the repository directory by
directory:

1. Run `./cli/automa help` to see what the tool can do, then descend one command
   level at a time with commands such as `./cli/automa vehicles help`. Use
   `--help` only when you reach the final command you intend to run.
2. Choose the [Chase simulator workflow](#chase-simulator-workflow) for local
   development or the [physical PiRacer workflow](#physical-piracer-workflow)
   when deploying to hardware.
3. Read [`tests/README.md`](tests/README.md) before changing behavior. It explains
   test ownership, focused module runs, and the explicit simulator and Pi
   boundaries.
4. Use [`docs/README.md`](docs/README.md) for documentation navigation,
   architecture references, and research notes. Optional change inspection:
   `python3 -m qca`. Historical delivery files are under `docs/deprecated/`;
   do not use them.
5. Treat `runtime/` and `lab/` as generated state. Start from tracked source and
   the CLI rather than using files in those directories as an API.

## Project Layout

- `autonomy/` contains sensor- and environment-agnostic vehicle, perception,
  memory, decision, and runtime contracts plus generic orchestration. It contains no
  perception algorithms.
- `implementations/` contains concrete vehicle adapters, perception plugins,
  runtime hosts, and bounded operations.
- `cli/` contains the `automa` command and its implementation.
- `tests/` mirrors production ownership and contains deterministic, integration,
  CLI, and explicitly opt-in live validation.
- `deploy/targets/donkeycar/` contains the physical harness, pinned DonkeyCar
  source manifest, and local vendor patch.
- `frontend/donkeycar/` contains the optional local DonkeyCar control frontend.
- `runtime/` contains generated controller bundles and process state. It is
  ignored by Git.
- `lab/` contains captures, reports, and experimental artifacts. It is ignored
  by Git.
- `scripts/` contains transitional calibration and inspection tools plus the
  internal DonkeyCar restart helper. User-facing deployment and runtime
  workflows belong in `automa`.

The documentation entrypoint is [`docs/README.md`](docs/README.md). The
canonical directory contract is
[`docs/reference/directory-structure.contract.json`](docs/reference/directory-structure.contract.json).

## CLI Model

The command groups intentionally distinguish different kinds of state:

The live worker's local browser entrypoint is **Automa**, with links to
**Perception** at `/perception` and **Memory** at `/memory`. The automation
command's `--open-view` option opens this shared entrypoint. Both pages use the
same worker lifetime and publication APIs; perception owns its image buffer,
while `RuntimeViewServer` owns the listener, routes, and shutdown.

| Command | Reads or changes |
|---|---|
| `vehicles active` | Probes discoverable PiCar and Chase endpoints; does not imply deployment, worker, or view state. |
| `vehicles status` | Reads the complete Chase simulator, vehicle, deployment, worker, capture, and view state without changing it; other local deployments are listed separately with their inspection command. |
| `vehicles update perception` | Packages code and stages a vehicle perception activation locally. |
| `vehicles update observation\|proposal\|plan\|action` | Packages code and stages that step's plugins locally (`--plugin`, repeatable). |
| `vehicles update memory` | Packages code and stages a vehicle memory preset or plugin selection locally (`--preset`, or `--plugin` repeatable; default preset `recency_ledger`). |
| `vehicles plugins upload` | Stores a local file in an existing live catalog; `--arm` also requests adoption through the same operation as `plugins arm`. |
| `vehicles plugins arm` | Adds or updates perception, memory and proposal plugins before a subsequent cycle, without restarting automation. Repeat `--plugin` with `--step`, or use a JSON `--selection` map to request several steps together. |
| `vehicles plugins status` | Reads available, requested and applied revisions, plus downstream loading failures. `list` is an alias. See the [simulator prototype flow](docs/reference/cli-simulator-perception-journey.md#arm-on-an-existing-runtime-host). |
| `vehicles info perception\|memory\|proposal\|plan\|action` | Reads that step's staged activation, enabled and available plugins, bundle, and runner schema (inputs, output, composition and failure policy). Perception and memory also show their preset. Each reports its view; memory, proposal, plan and action include live runner status. Proposal, plan and action also show the decision generation, each decision step's plugins, and the action authority. |
| `vehicles proposal\|plan\|action inspect` | Offline: replays an image, a directory of images, or a recorded automation or inspect run through perception, observation, memory and the decision steps, and prints that step's record for every frame. A recording restores the selections it recorded for each frame; `--plugin` replaces the inspected step's. `--frame N` reports one 0-based frame after replaying the ones before it. `--record` also saves the report and source frames under `runtime/<step>-inspections/`; that run replays as a source. |
| `vehicles perception ...` | Inspects packaged perception plugins and measures their viability. |
| `vehicles automation ...` | Runs or inspects the local Chase controller worker. |
| `vehicles stream perception` | Displays rolling latest perception. Chase uses the local automation worker; PiCar polls onboard `/autonomy/observation/latest` and serves a local frame-matched `/perception` view (link to Memory map) whose URL the terminal shows. |
| `vehicles stream memory` | Inspects live memory as a key→value ledger. The terminal shows health and counts; on PiCar it also serves a local `/memory` map page whose URL the terminal shows. Keys are `record_id`s; click a key to see the retained value. |
| `vehicles stream proposal\|plan\|action` | Prints that step's record from the latest cycle the vehicle's runtime host published, with its age, generation and plugins; action also shows how the host applied control. `--once` prints one and exits 2 with the reason when none is current; `--json` returns `vehicle_<step>_stream_v1`. |
| `vehicles memory reset` | Clears live retained evidence on Chase or PiCar and starts a new empty epoch (visible via info/stream/Memory map). Does not move the vehicle. |
| `vehicles memory inspect` | Offline: runs an image, a directory of images, or a recorded perception or memory run through perception, observation and memory, and reports each memory plugin's health, record count and epoch after every frame. A recording restores the executable step selections and configs it contains; `--preset` or `--plugin` overrides memory. Otherwise each step uses its default. The report prints to the terminal; `--record` also saves the source frames, timing, both step selections and report under `runtime/memory-inspections/`. Record live frames with `vehicles perception inspect --record`, then inspect that run. |
| `vehicles memory viability` | Memory health check: 60s poll of the live memory step on a PiCar (update cadence, duration, failures, health, epoch stability); Chase returns a stub pass. PiCar measurements save `report.json` under `lab/runs/memory-viability/` unless `--no-record`. |
| `vehicles perception viability` | Perception health check: 60s onboard cadence/freshness measurement on a PiCar (RSS when the vehicle supplies an `ssh_target`); Chase returns a stub pass. PiCar measurements save `report.json` and `summary.md` under `lab/runs/perception-viability/` unless `--no-record`. |
| `vehicles update core` | Deploys DonkeyCar framework and physical harness code to the Pi. |
| `vehicles update autonomy` | Deploys a versioned autonomy release and activation metadata (perception, decision, memory) to the Pi. With `--restart`, verifies the live memory step; if activation is present but the step is missing, update core (manage.py harness) then re-run autonomy. |
| `vehicles operation ...` | Runs a bounded, explicitly requested vehicle operation. |
| `simulators ...` | Finds or prepares the SimEval and Metrics UI environment. |

`stream perception` and `stream memory` take the same flags. By default each
refresh redraws the terminal view, and on PiCar updates the local view whose
URL it shows. `--json` prints one probe per refresh in place of both, for
scripts: `vehicle_perception_live_v0` for perception and
`vehicle_memory_live_v1` for memory.
`--once` exits 2 unless the probe's `status` is `live`. Any other status
(`stopped`, `stale`, `absent`, `error`, `unavailable`) comes with an `error`.
Discovery failures also emit one `unavailable` JSON probe and exit 2, even
without `--once`. Terminal streams show the same probe verdict and reason;
perception labels the worker's state and the onboard publication's health
separately from that verdict.
On Chase, both steps are live only while this vehicle's automation worker is
running and its state is under 30s old
(`AUTOMA_CHASE_WORKER_PROBE_MAX_AGE_MS`). This is the capture-loop heartbeat,
not the completion time of each step. Perception additionally requires a
result from the current automation run and reports its frame identity and
`age_ms`; memory reports the retained step's lifecycle and plugin state.

On PiCar, perception reads `/autonomy/observation/latest`: a healthy publication
with a perception payload is live, and `age_ms` comes from the Pi's clock.
Memory reads the retained step in `/autonomy/status`: the step's presence is
live. Its `plugins[]` entries retain each applied plugin's `state` and expose
that plugin's `health`, `epoch_id`, `record_count`, and `bounds` alongside it;
`evidence_publisher` names the plugin whose evidence is published.
`last_error` and counters report the step's update health.
Use the nested perception result and plugin reports to inspect plugin outcomes;
`live` describes availability rather than promising that every plugin succeeded.

Worker probe overrides are `AUTOMA_CHASE_WORKER_PROBE_MAX_AGE_MS` (default
30000) and `AUTOMA_CHASE_WORKER_PROBE_CLOCK_SKEW_MS` (default 2000). These
replace the former memory-only `AUTOMA_CHASE_MEMORY_PROBE_MAX_AGE_MS` and
`AUTOMA_CHASE_MEMORY_PROBE_CLOCK_SKEW_MS` names.

Both viability commands exit 0 for a passed measurement or Chase stub, 1 for
failed measurement gates, and 2 for a preflight failure. Under `--json`, preflight
failures return `vehicle_step_viability_error_v0` with `vehicle_id`, `step`,
`error` (`unknown_vehicle`, `unsupported_provider`, or `missing_connection`), and
the diagnostic in `message`. PiCar reports are also saved in JSON mode; use
`--json --no-record` for a report printed only to stdout. Chase stubs produce
terminal or JSON output only.

Every `vehicles update <step>` stages only for a known vehicle. A `chase-sim-*`
id, or a vehicle with matching identity metadata in any staged step, is known
without discovery. Every successful step update records the vehicle id,
provider, kind, and connection in the activation's metadata. Offline staging
uses the newest valid identity matching the exact requested id. A directory
name alone does not identify a vehicle.

Otherwise the id must be discoverable; `--timeout-s` bounds each discovery
probe. `--dry-run` checks resolution without writing an activation or making a
new id known offline. Existing perception identity metadata remains usable.
Older memory or decision activations without provider metadata require a
matching identity in another step or discovery on the next update.

Every step update records `metadata.controller_bundle` with `root_dir`,
`autonomy_dir`, `implementations_dir`, `runtime_dir`, and `release`.
`vehicles info perception|memory|proposal --json` exposes those same bundle keys.
The paths identify the staged code; `release` identifies the packaged source
and archive. Plugin selections and constructor configs remain in `plugins`,
`plugin_specs`, and `plugin_configs`.

Perception additionally reports Chase readiness after staging. Its
`--timeout-s` also bounds each live readiness check and simulator operation;
it is not a deadline for the entire update command. Perception `--restart`
requires live discovery even when a local identity exists.

For every step, an unknown-vehicle failure under `--json` returns
`vehicle_step_update_error_v0` with `vehicle_id`, `step`, `error: unknown_vehicle`,
and the discovery diagnostic in `message`; the command exits 2 and stages
nothing. This resolution error is shared even though successful update payloads
contain different step-specific results, such as perception readiness.

Use `help` at a command-group level and `--help` for final command options:

```sh
./cli/automa vehicles update help
./cli/automa vehicles update autonomy --help
```

## Chase Simulator Workflow

The supported passive path starts from the Metrics UI URL and preserves the
operator's current simulator session:

```sh
./cli/automa vehicles status --chase-url http://localhost:5050
./cli/automa vehicles update memory \
  --id chase-sim-chaser \
  --preset recency_ledger
./cli/automa vehicles update perception \
  --id chase-sim-chaser \
  --preset lightweight_observer
./cli/automa vehicles automation run \
  --id chase-sim-chaser \
  --observe-only \
  --num-decisions 0 \
  --open-view
./cli/automa vehicles status --id chase-sim-chaser
./cli/automa vehicles automation stop --id chase-sim-chaser
./cli/automa vehicles status --id chase-sim-chaser
```

The memory update stages the Memory view's ledger; without a memory activation,
the live cycle has no memory plugins. Each update replaces only its named
step's selection/configs. For an existing tuned deployment, use your intended
selection instead of copying these default presets; a preset/plugin selection
uses its packaged configs.

`runtime/` survives Git branch changes. Old per-step schemas, including
`automa_memory_activation_v0`, are incompatible with the shared
`automa_step_activation_v0` documents. Status reports an invalid deployment and
the affected step's `vehicles update <step>` command. Restage that step with
your intended selection, then check status again. Every command that reads
staged documents checks them first and prints the same step, path, reason, and
restage command: startup and restart before launching or stopping a worker,
`vehicles update perception` and `vehicles update autonomy` before packaging or
writing, and `vehicles info`, `vehicles stream proposal|plan|action`, and
`vehicles perception inspect` before reporting. An invalid optional step blocks startup;
an absent optional step keeps its built-in or empty behavior.

See the
[Chase simulator-to-perception CLI journey](docs/reference/cli-simulator-perception-journey.md)
for the layer vocabulary, timeout semantics, and exact recovery behavior.

`vehicles status`, staging, observation-only startup, viewing, and cleanup do
not select a scenario, change playback or control/input state, or apply vehicle
movement. To explicitly prepare the repository's known demonstration
environment, opt into the configuration-changing command:

```sh
./cli/automa simulators ensure --scenario chaser-depth-obstacles
```

Inspect the machine-readable contracts declared by the staged code:

```sh
./cli/automa vehicles info perception --id chase-sim-chaser
./cli/automa vehicles info proposal --id chase-sim-chaser
```

Run the same staged plugins with movement enabled:

```sh
./cli/automa vehicles automation run --id chase-sim-chaser
./cli/automa vehicles automation status --id chase-sim-chaser
./cli/automa vehicles info perception --id chase-sim-chaser
./cli/automa vehicles stream perception --id chase-sim-chaser
./cli/automa vehicles stream perception --id piracer
```

`automation run` waits for a camera frame, the completed perception result for
that frame, and a healthy view belonging to the same worker generation. It
returns a nonzero result and preserves the exact capture code/path when those
gates fail; a spawned PID alone is not reported as success.

While the automation worker is running, `vehicles info perception` reports a
loopback URL for a live frame-and-data view. The page polls the worker's
in-memory publication. Camera capture runs independently from perception, so a
slow plugin consumes the newest available frame instead of accumulating a
backlog. The page displays the latest camera frame with the newest available
overlay and reports its source frame, frame lag, and elapsed result age. It can
independently hide regions, labels, or finding kinds. Only image-coordinate
findings are drawn over the camera frame; findings in other coordinate systems
remain available in the data panel.

Stop or restart the worker:

```sh
./cli/automa vehicles automation stop --id chase-sim-chaser
./cli/automa vehicles automation restart --id chase-sim-chaser --observe-only --num-decisions 0 --open-view
```

Useful run options:

- `--num-decisions N` bounds the number of completed decision cycles and stops
  movement afterward. `--num-decisions 0` starts an unbounded
  background worker; the launch command returns after readiness. Use
  `vehicles automation stop` to stop it. Ctrl-C in a terminal stream stops
  that stream, not the worker.
- `--interval-s` sets the camera capture cadence; it defaults to `0.25` seconds on both hosts.
- `--interval-s 0` captures as quickly as the vehicle interface allows.
- Decisions consume the newest pending capture as soon as the previous cycle finishes.
  `skipped_since_previous` counts captures superseded between frames passed through
  the decision cycle; `skipped_count` is the sum of those per-frame counts. Drive ticks
  outside the capture cadence and pending frames discarded on stop are not skips.
- `--observe-only` applies no decision output on either vehicle. Starting passive observation in Chase preserves its current simulator session.
- `--open-view` opens the browser only after the correlated view is healthy.
- `--record` keeps every completed decision and its exact camera image, capture
  time, and applied step configurations. Both vehicles commit these artifacts
  before counting the decision. PiCar recordings are copied from the onboard
  run in order and drained on completion or stop; later manual observations
  stay outside the recording. A successful recorded run reports matching
  `Decisions recorded` and `Decisions completed` counts.
- `--log` persists worker output to `automation.log`.

After changing perception or shared autonomy code, stage a fresh bundle before
restarting the worker:

```sh
./cli/automa vehicles update perception --id chase-sim-chaser --preset sim_debug
./cli/automa vehicles automation restart --id chase-sim-chaser --observe-only --num-decisions 0 --open-view
```

### Perception Plugins

`--plugin` selects packaged perception plugins by catalog key, in order, with
their default configs from `implementations/decision_cycle/perception/catalog.py`.
`--preset` selects a named preset from
`implementations/decision_cycle/perception/presets.py`. The two are exclusive,
and a plugin list is recorded as the preset it equals, else `custom`.

`vehicles info perception` reports the staged preset, its enabled plugins and
the available ones, and the staged runner's `perception_schema_v3` contract.
`--json` returns `vehicle_perception_info_v0` with that contract under
`perception_schema`. Staging replaces the selection. A running worker applies a
changed plugin list at its next frame and changed plugin configs when it
restarts; a stopped one uses the selection the next time it starts:

```sh
./cli/automa vehicles info perception --id chase-sim-chaser
./cli/automa vehicles update perception --id chase-sim-chaser --plugin frame --plugin floor_plane
./cli/automa vehicles update perception --id chase-sim-chaser --preset visual_observer
```

`lightweight_observer` is the production-oriented frame and floor-boundary
chain. `visual_observer` adds feature-motion tracks and is intentionally much
slower. Artifact-only VLM preprocessing remains an optional diagnostic plugin,
not part of either observer.

### Perception Inspection

Inspect what a perception selection detects: observe five frames from a usable
vehicle without taking movement control, or apply a preset or a plugin
selection to an image, an image directory, or a recorded perception or memory
run:

```sh
./cli/automa vehicles perception inspect
./cli/automa vehicles perception inspect --id piracer --preset lightweight_observer
./cli/automa vehicles perception inspect path/to/frame.jpg --plugin frame --plugin floor_continuity
./cli/automa vehicles perception inspect path/to/images --preset visual_observer
```

The report prints to the terminal: the source, the perception selection,
aggregate statuses, latency, representation health and each plugin's status
counts. `--json` includes each frame's identity, timing and plugin outputs.
`--record` also saves the selection, timing, per-frame plugin outputs and the
report as `report.json` under `runtime/perception-inspections/<run>/`, and
prints that directory after `Recorded:`. A live recording keeps its captured
frames in `frames/`; an image recording refers to the source images where they
are.

A recording restores its perception selection, frame identity and timestamps;
`--preset` or `--plugin` overrides the selection. Without a recording, a live
read uses the vehicle's staged selection and images use the default preset.

Live inspection refreshes the local code bundle when workspace source changes.
It rebuilds a staged named perception preset from the current catalog, including
its configs; a `custom` activation keeps its selection, specs and configs.
An explicit inspection selection runs from that bundle without saving the
override to the vehicle's activation.

Both inspection commands read recorded frames the same way: equal timestamps
are allowed at millisecond resolution, frame indices preserve ordering and
timestamps cannot go backwards. Camera manifests (`camera_frames`) and
inspection reports (`frames`) preserve their declared order and timing. Without
a manifest, images are ordered by filename, ignoring case, and assigned times
of 0, 1000, 2000, ... milliseconds; file mtimes are not capture times. Both
commands use the shared adapter's generated frame IDs and validate images
before running plugins or recording a report. Recordings preserve those IDs.
Operation reports preserve their before/after capture order through the same
source adapter, including archived copies in `frames/`.

Dropout frames remain in the report with their identity, time and absence
reason. Perception inspection reports them as `unavailable`. Both inspection
commands and workbench replay reset perception's temporal state without
invoking its plugins at these positions. A completed perception
inspection exits 1 when any frame is partial, unavailable or in error; otherwise
it exits 0. Source, plugin-loading and execution exceptions exit 2.

Image directories and recordings default to a 512-frame limit in both inspection
commands and workbench replay. Pass `--max-frames N` to read a larger source.
This bound does not limit live perception capture (`--frames` controls that).

For a physical vehicle, `vehicles perception inspect --id piracer` currently fetches
Pi camera frames and runs perception on them on the development machine.
It does not prove that the Pi executed or published the perception result.

### Memory Plugins

`--plugin` selects packaged memory plugins by catalog key, in order, with their
default configs from `implementations/decision_cycle/memory/catalog.py`.
`--preset` selects a named preset from
`implementations/decision_cycle/memory/presets.py`. The two are exclusive,
and a plugin list is recorded as the preset it equals, else `custom`.

`vehicles info memory` reports the staged preset, its enabled plugins and the
available ones, and the staged runner's `memory_schema_v1` contract.
`--json` returns `vehicle_memory_info_v1` with that contract under `memory_schema`.
Perception, memory, and proposal (`vehicles info proposal`) describe inputs,
plugins, output, composition and failure policy;
memory's contract also names the ledger fields the CLI and viewers project from
each plugin's status. The report itself preserves that status, including absent
ledger keys. Staging replaces the selection. A running worker applies a
changed plugin list at its next frame and changed plugin configs when it
restarts; a stopped one uses the selection the next time it starts:

```sh
./cli/automa vehicles info memory --id chase-sim-chaser
./cli/automa vehicles update memory --id chase-sim-chaser --plugin bounded_evidence
./cli/automa vehicles update memory --id chase-sim-chaser --preset recency_ledger
```

`recency_ledger`, the default, keeps a bounded ledger of observation things and
signals with age expiry and oldest-first eviction.

### Memory Inspection

Inspect what a memory selection retains: run an image, an image directory, or
a recorded perception or memory run through perception, observation and memory:

```sh
./cli/automa vehicles memory inspect path/to/images
./cli/automa vehicles memory inspect path/to/images --plugin bounded_evidence
```

Memory inspection reads its source only. To inspect memory over frames from a
vehicle, record them with perception inspection, then inspect the directory it
prints after `Recorded:`. Memory runs the same perception selection over the
same frames:

```sh
./cli/automa vehicles perception inspect --id chase-sim-chaser --frames 20 --record
./cli/automa vehicles memory inspect runtime/perception-inspections/<run>
```

The report prints to the terminal: the source, both step selections, and each
memory plugin's health, record count and epoch after every frame, together with
the evidence publisher. `--record`
also saves the source frames, timing, both step selections and the report as
`report.json` under `runtime/memory-inspections/<run>/`, and prints that
directory after `Recorded:`. Memory recordings copy their images with relative
paths, so replay still works after moving the recording or removing the
original source.

A recording restores its perception and memory selections, frame identity and
timestamps; `--preset` or `--plugin` overrides memory. Without a recording each
step uses its default. Source ordering, timing, validation and `--max-frames`
follow perception inspection and workbench replay. On a dropout frame memory
receives an empty observation carrying the absence metadata and can age
retained evidence. A completed memory inspection exits 0, including these
frames; source, plugin-loading and execution exceptions exit 2.

Older memory recordings contain only summary fields. They use default step
selections and image-directory ordering and timing, because those reports did
not save executable configs or an image inventory.

### Proposal Plugins

`--plugin` selects packaged proposal plugins by catalog key, in order, with
their default configs from `implementations/decision_cycle/proposal/catalog.py`.
Proposal has no presets; without `--plugin` it stages its default plugins.

`vehicles info proposal` reports the staged plugins and the available ones,
the staged runner's `proposal_schema_v1` contract, and the plan and action
plugins that act on them. `--json` returns `vehicle_proposal_info_v1` with that
contract under `proposal_schema`, and the decision generation, each decision
step's plugins and the action authority under `decision`; `vehicles info plan`
and `vehicles info action` report the same for their step. Like memory info, it
probes the running autonomy engine and reports its proposal step under `live`
(`vehicle_proposal_live_v1`): the plugins it runs and its run and failure
counts, from the runtime host's `/autonomy/status`. That is the engine's step, not the proposals any view last
rendered. A worker loads the staged proposals from the controller bundle when
it starts, as it loads memory, and reports them under `proposal` in its state
and `proposal_plugin_report` in each frame. Staging replaces the selection. A
running worker applies a changed plugin list at its next frame and changed
plugin configs when it restarts; a stopped one uses the selection the next time
it starts. Proposals are part of the decision generation: once the worker runs
the new selection it publishes under the restaged generation, and an open
decision view follows it. The generation changes after the cycle confirms
which plugins were applied. A plugin load or removal-reset failure keeps the
old applied selection and generation, and publishing is refused while they
differ from the staged decision. After a config restage it publishes no
decision frames until it restarts:

```sh
./cli/automa vehicles info proposal --id chase-sim-chaser
./cli/automa vehicles update proposal --id chase-sim-chaser --plugin avoid_recent_obstruction
```

## Decision Playback Workbench

The workbench reads the same recorded frame order and timing. `vehicles
workbench replay` starts each step from `--perception-preset` or
`--perception-plugin`, `--memory-preset` or `--memory-plugin`, and
`--proposal-plugin`, as the inspect and update commands take them. With no
flag, a step uses its default preset; proposal has no presets and uses its
default plugins. The CLI and page show each step's preset, where it has one,
and ordered plugins.
The page's catalog lists available plugins; its separate **Run order** shows
execution order. Newly checked plugins run last, and retained plugins keep
their order. Unchecking every plugin disables its plugins.
Recorded dropout positions remain seekable. The page labels perception as
absent and shows the reason while memory can still show retained records.

```sh
./cli/automa vehicles workbench replay path/to/images --perception-preset multi_obstruction --memory-preset recency_ledger --serve
```

A preset keeps its plugin configs until that step's ordered selection changes.
A changed selection uses catalog defaults for every selected plugin; it does
not restore a recording's step configs or a tuned preset just because its ids
match. Start with the named preset again to restore its tuning. The other
steps keep their selections and configs. Submitting the same ordered list again
keeps the current configs and pass.

Perception tracks and memory evidence carry state across frames, and proposals
read them, so changing any step's selection during a running or paused replay
rebuilds every step's pipeline with a fresh shared map and runs from the first
frame to the displayed frame before the action returns. This includes the last frame still displayed
between loop passes.

For workbench API integrations, `GET /api/state` reports schema
`workbench_image_replay_state_v3`. Read `<step>_plugin_catalog` for availability
and default configs, `active_<step>_plugin_ids` for the selected execution order,
and `machine_detail.pipeline.<step>_preset` for the selection's preset name or
`custom` (null for proposal, which has no presets). The pipeline's `<step>_plugin_report.applied_plugin_ids` reports the
plugins that were applied. Each frame's `memory_plugins` lists every applied
memory plugin's health, record count, and epoch, and
`memory_evidence_publisher` names the plugin whose evidence the step published.
`steps.memory` is the complete memory step report, with its `plugins[]` and
`evidence_publisher`. Replace v1's generic perception `plugin_catalog` and
`active_plugin_ids` with the step-named fields; use the catalog's `digest` in
place of `catalog_digest` / `run_catalog_digest`, and the selected or applied ids
in place of `plugin_order` / `run_plugin_order` / `run_active_plugin_ids`.
Cleanup now names `perception` instead of `mapper`. The removed CLI flags
`--plugin`, `--active-plugin`, and `--active-plugin-id` become
`--perception-plugin`; memory uses `--memory-plugin`.

Send `POST /api/action` with an explicit step for any selection; omitting
`step` is a 400 input error. `active_plugin_ids` remains the common request field
and may be empty. Include the current state's `run_id` during playback:

```json
{"action": "select_plugins", "step": "memory", "active_plugin_ids": [], "run_id": "<current run_id>"}
```

## Physical PiRacer Workflow

The default target is `piracer@piracer.local`, with the Donkey server at
`http://piracer.local:8887`.

For a previously prepared Pi, power it on and probe the supervised runtime:

```sh
./cli/automa vehicles active
```

For a fresh setup, install the core harness and boot service, then deploy and
restart the autonomy release:

```sh
./cli/automa vehicles update core --id piracer
./cli/automa vehicles active
./cli/automa vehicles update autonomy --id piracer --restart
```

`update core` installs and enables `automa-donkey.service`. The first install
starts it automatically and waits for `/autonomy/status` to report manual
`user` mode. Later Pi boots start the same service without another CLI command,
and systemd restarts the runtime if its process exits. If HTTP discovery is
down, core update falls back to the configured `piracer` SSH target and reports
that fallback before connecting.

`update autonomy` packages `autonomy/` and `implementations/`, verifies the
archive hash on the Pi, installs a versioned release, transfers every staged
step activation and the runtime identity, and restarts the supervised service
only when requested. Post-restart verification requires every deployed step to
run its staged plugins while Donkey drive mode remains `user`; the deployment
check does not command movement.

Use the deploy commands according to what changed:

- DonkeyCar vendor patch or `deploy/targets/donkeycar/app/`: `update core`.
- `autonomy/`, `implementations/`, or staged activations: `update autonomy`.
- Run both commands for a fresh Pi or when both layers changed.

Core deployment preserves remote autonomy releases and runtime activation
state. It installs the boot service on every update but does not restart an
already-running service unless `--restart` is present. To inspect planned
writes, add `--dry-run`.

For a non-default physical id or SSH target, bypass discovery explicitly when
the HTTP server is down:

```sh
./cli/automa vehicles update core --id piracer \
  --skip-discovery --ssh-target piracer@piracer.local
./cli/automa vehicles update autonomy --id piracer \
  --skip-discovery --ssh-target piracer@piracer.local --restart
```

The handheld controller is not enabled by default. Enable it only when it
should become an active command source. Drive arguments persist across service
restarts and Pi boots:

```sh
./cli/automa vehicles update core --id piracer --restart --drive-args=--js
```

Pass `--drive-args=` with `--restart` to return to the default controller-free
startup.

### Physical Activation State

The first physical autonomy deployment creates the default
`lightweight_observer` perception activation, built-in observation/plan/action
activations, and `bounded_evidence` memory activation when none exist. No
proposal is staged by default, so the cycle holds. A named perception preset
is rebuilt from the current catalog, including its configs; custom perception
activations and all other staged steps keep their selection, specs and configs,
including named memory presets. Every deployed step records the same release
and bundle paths. The Pi loads those activations. The Donkey assembly runs the
shared autonomy cycle independently of `run_pilot`. The drive loop samples its
camera memory at the configured capture cadence and publishes it on
`/autonomy/camera/latest` without waiting for decisions. The native camera and
drivetrain retain their loop rate (`DRIVE_LOOP_HZ`, 20 Hz); `--interval-s` controls
which samples enter the autonomy pipeline. Before a run starts, the capture
cadence defaults to `AUTONOMY_CAPTURE_INTERVAL_S` (0.25 s). The background
decision worker takes the newest pending capture immediately after each cycle,
dropping superseded captures rather than accumulating a backlog. The matched result remains
`/autonomy/observation/latest`. While mode remains `user`, pilot outputs stay
zero and Donkey DriveMode keeps manual input authoritative.

**Deploy split:** autonomy packages ship the controller tree and activation
files (`runtime/<step>/active.json` for each staged step). The code path that
*loads* the steps into the Donkey loop lives in `manage.py` from **core**. After
harness changes, run core then autonomy with `--restart`. Autonomy `--restart`
verification fails if a step was shipped but `/autonomy/status` does not report
it running the staged plugins.

Step selections are local until the next autonomy deployment:

```sh
./cli/automa vehicles update proposal --id piracer
./cli/automa vehicles update action --id piracer --plugin selected
./cli/automa vehicles update memory --id piracer
./cli/automa vehicles update autonomy --id piracer --restart
```

`vehicles info perception|memory|proposal --id piracer` inspects staged
activation and release metadata. Local staging does not require the Pi to be
online; the subsequent autonomy deploy does.

### Shared Movement Workflow

Once the selected plugins are staged (and deployed for PiCar), the same commands
control both vehicles. Change only the vehicle ID:

```sh
./cli/automa vehicles automation run --id chase-sim-chaser --open-view
./cli/automa vehicles automation stop --id chase-sim-chaser
./cli/automa vehicles automation run --id piracer --open-view
./cli/automa vehicles automation stop --id piracer
```

`automation restart --id <vehicle>` stops movement, recreates the host, and
starts it with the requested options. `--observe-only`, `--num-decisions`, and
`--interval-s` have the same control semantics on both vehicles. The simulator
acquires WS input automatically; the onboard host acquires its drivetrain
output automatically. A separate mode HTTP request is unnecessary.

Perception presets can differ: for example, `sim_debug` supplies image-based
simulator evidence, while `obstruction_observer` supplies PiCar evidence. Memory,
proposal, plan, and action retain their shared contracts. The existing
`avoid_recent_obstruction` proposal requests throttle `0.60` and steering away
from lateral obstruction evidence; without qualifying evidence it requests idle.
It does not implement target chasing just because the perception preset detects
an evader.

Existing staged action activations remain explicit selections. To migrate an
old `hold` activation, stage `selected` on that vehicle. `hold` remains available
as a deliberately idle plugin; a custom action plugin can change the requested
command but cannot bypass runtime movement authority.

Capture status uses `interval_s` and `frames_captured` on both vehicles. The PiCar
startup setting is `AUTONOMY_CAPTURE_INTERVAL_S`; rename any custom
`AUTONOMY_OBSERVATION_INTERVAL_S` override when updating.
Automation `run` and `restart` use `--num-decisions` in place of `--frames`.
Run configuration and automation status use `num_decisions` (zero means
unbounded); host session status reports `processed_decisions`.

This change updates the physical harness and vendor HTTP API. Install both
layers before using the shared commands on PiCar:

```sh
./cli/automa vehicles update core --id piracer
./cli/automa vehicles update autonomy --id piracer --restart
```

### Control Ownership and API

`AutonomyCycleHost` runs the six steps, then passes their result to
`ControlExecution`. A `ControlTarget` provides only `acquire`, `write`, and
`release`. Chase's `ChaseControlTarget` pushes each command to the simulator;
Donkey's `DonkeyControlTarget` leaves it for the drivetrain loop to pull. Vehicle
code cannot introduce a second launch command, partial-autonomy mode, or
throttle multiplier.

The local host and `OnboardRuntimeClient` accept the same `RunConfiguration`
through `start(configuration)` and expose `stop()`. The CLI supplies these
operations for either hosting location. Implementers using the vehicle boundary
can use `implementations.vehicle.access.create_vehicle_access(vehicle, timeout_s=...)`
with the discovered vehicle descriptor; transport preparation belongs to
`ChaseControlTarget`, not plugin code.

The shared runtime rejects results from before a mode transition or from an
invalidated in-flight cycle. Commands expire two seconds after their source
frame; a watchdog stops held output when capture or computation stalls. Cycle
errors, bounded-run completion, and shutdown also stop autonomous output.
The timeout is shared infrastructure, not a vehicle-specific CLI flag.

Cycle records distinguish authorized `control` from actual `application`.
`application.applied` means delivery was acknowledged at the receipt's named
command boundary; it does not assert measured physical motion. The action
record's legacy `proposed_applied` field describes plugin authorization only.
Chase's current WS transport supports directional throttle at fixed scenario
speed, so its receipt declares `directional_fixed_speed`; equal throttle values
do not imply equal simulated and physical speed.

## Bounded Startup Check

The startup check captures a frame before and after each basic action
combination and scores whether the command produced a visible change. A live
check acquires that vehicle's control target and sends movement pulses, so
raise the vehicle or clear its path first. `--dry-run` only captures frames:
it does not acquire control or send pulses, and it does not switch simulator
playback.

```sh
./cli/automa vehicles operation startup-check --id piracer
./cli/automa vehicles operation startup-check --id chase-sim-chaser
```

Results are written under `lab/runs/startup-check/<run-id>/`, including the
plan, report, summary, before/after frames, diffs, and contact sheet.

## Generated Runtime State

The local controller layout is generated under:

```text
runtime/vehicles/<vehicle-id>/
  bundle/
    autonomy/
    implementations/
    releases/
    runtime/
      <step>/active.json   # perception, observation, memory, proposal, plan, action
      automation/
  deploy/
```

The physical target stores versioned releases under
`/home/piracer/mycar/runtime/controller-releases/` and exposes the active
packages through `/home/piracer/mycar/autonomy` and
`/home/piracer/mycar/implementations`.

## Camera and Optional Frontend

The Donkey server exposes:

- `http://piracer.local:8887/drive`
- `http://piracer.local:8887/frame.jpg`
- `http://piracer.local:8887/frame-highres.jpg`
- `http://piracer.local:8887/autonomy/status`
- `http://piracer.local:8887/autonomy/observation/latest`
- `http://piracer.local:8887/autonomy/observation/latest/frame.jpg`

`/frame.jpg` is the live camera feed. The `/autonomy/observation/latest*`
endpoints publish the exact onboard-processed frame and its matching
findings (in-memory only; no default history or disk writes). The JPEG
response includes `X-Frame-Id` so clients can pair image and JSON.

Run the optional local DonkeyCar frontend with:

```sh
./frontend/donkeycar/start.sh
```

Then open `http://localhost:8088/`. Chase simulator UI is owned by Metrics UI
and is prepared through `./cli/automa simulators ensure`.

## Architecture and Planning

- [`docs/README.md`](docs/README.md) is documentation navigation and the
  reading order.
- [`docs/reference/onboard-autonomy-flow.html`](docs/reference/onboard-autonomy-flow.html) explains
  the onboard perception, decision, and action flow.
- [`docs/reference/donkey-server-functionality.html`](docs/reference/donkey-server-functionality.html)
  describes the physical Donkey server boundary.
- [`docs/deprecated/`](docs/deprecated/) is historical delivery record. Do not
  use it.

Dependency direction is intentional:

```text
autonomy contracts       -> never import implementations
implementations          -> satisfy and compose autonomy contracts
CLI/runtime entrypoints  -> select implementations and execute the cycle
```

Perception follows a feed-injection model. The stable step wraps a
generic `SensorFrame` and runs configured plugins without knowing which
sensor or meaning any plugin uses. Each plugin declares named feed inputs
and returns only structured signals, spatial evidence, and measurements. The
generic runner resolves and caches those inputs, then owns missing-input and
warm-up status, error isolation, timing, source attribution, text rendering,
and optional diagnostic persistence. The surrounding cycle owns the sensor
frame, so perception output does not duplicate it. Concrete camera decoding
and every meaning-making algorithm live under
`implementations/decision_cycle/perception/`.

Both current vehicle adapters expose only the generic `front_camera` sensor
through `CarInterface.read_sensors()`.

## Transitional Research Tools

The scripts below are useful for inspection and calibration but are not part of
the automation runtime or stable API:

- `scripts/perception/`: still processing, feature tracking, scene motion, and
  relative landmark analysis.
- `scripts/calibration/`: PiRacer visual depth and step/turn experiments.

Run a script with `--help` for its current inputs. Keep scene-specific
assumptions in these tools until they have a validated operation contract.

## Current Pi Configuration

The active physical overrides live in
`deploy/targets/donkeycar/app/myconfig.py`:

- steering left/right/center PWM: `470 / 640 / 555`
- throttle forward/stopped/reverse PWM: `-1200 / 0 / 1200`
- camera: `PICAM`, `640x480`, horizontal and vertical flip enabled

Remote recordings, logs, PIDs, generated controller bundles, lab runs, Python
bytecode, and the generated DonkeyCar vendor checkout are excluded from Git.
