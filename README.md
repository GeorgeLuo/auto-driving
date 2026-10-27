# Automa Vehicle Automation Workspace

This repository is the local source of truth for a vehicle-agnostic automation
framework, a PiRacer/DonkeyCar target, and a Chase simulator adapter. Each frame
runs one decision cycle of six steps: perception, observation, memory, proposal,
plan, and action. Every step runs the plugins selected for it in its own
activation (`runtime/<step>/active.json`). The default action plugin, `selected`,
authorizes the plan's selected command. A shared execution runtime owns movement
mode, delivery, freshness, and stopping on both Chase and PiCar. Starting
`hosts run --name <host>` enables autonomous movement;
`--observe-only` runs the same plugins without delivering their output.
`--host-url <host>` runs that existing host instead of starting one, and the host reports its host name.
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

Run the deterministic test suite. GitHub runs this same command. It calls code in-process and does not start another program:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
```

Run the systems suite, the tests that start another program:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --systems
```

Before pushing, run only the tests your change reaches. [`tests/README.md`](tests/README.md#validating-a-change) explains how to choose them with a group and `-k`.

Include the live Chase simulator smoke test, or check a powered-on Pi without moving it. Both commands run the systems suite:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py --live-sim
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
- `tests/` holds the in-process `deterministic/` suite that CI runs and the
  local-only `systems/` suite, which starts other programs and holds the
  explicitly opt-in live checks.
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
| `vehicles status` | Shows the default simulator, created simulators, and discovered vehicle environments with their reachability and endpoints. |
| `vehicles discover` | Lists the vehicle given by `--provider` (`chase-sim` or `picar`) and `--url` in `vehicles status` once it can take a host; `--force` lists it regardless. The default simulator (`CHASE_UI_WS_URL`, else `http://localhost:5050`) is always listed. |
| `vehicles remove` | Stops listing the discovered vehicle at `--url`. The default simulator cannot be removed. |
| `hosts viewer` | Serves the runtime viewer (Perception, Memory, Action and Plugins) fed from the host named by `--name`, or at `--host-url` (the URL `hosts status` shows for it), and opens it in the browser until interrupted. Exits 2 when no such host exists, it is not running, or it does not answer. |
| `hosts stage perception\|memory\|proposal\|plan` | Packages code and stages that step's preset or plugin selection locally (`--preset`, or repeatable `--plugin`; default presets `lightweight_observer`, `recency_ledger`, `obstruction_avoidance` and `most_confident`). |
| `hosts stage observation\|action` | Packages code and stages that step's plugins locally (repeatable `--plugin`; without it, the step's default plugins and configs). |
| `hosts plugins upload` | Stores a local file in an existing live catalog, refusing source that does not compile; `--arm` also arms it through the same operation as `plugins arm`. |
| `hosts plugins arm` | Loads plugins for any step on the host and applies them at its next cycle, without restarting automation. On observation, plan and action, which run one plugin, the armed plugin replaces the current one. An import or construction error fails the command (exit 2) and keeps the previous selection. Repeat `--plugin` with `--step`, or use a JSON `--selection` map to arm several steps together. |
| `hosts plugins disarm` | Deselects named plugins on an existing host; observation, plan and action keep their one plugin (arm a replacement instead). Use `--step` with repeatable `--plugin`, a JSON `--selection` map for grouped removals, or `--all` to clear one step (with `--step`) or perception, memory and proposal. The next cycle resets disarmed instances; catalog entries remain available. |
| `hosts plugins status` | Reads available, requested and applied revisions; exits 2 while the last selection request failed. `list` is an alias. See the [simulator prototype flow](docs/reference/cli-simulator-perception-journey.md#arm-on-an-existing-runtime-host). |
| `hosts info perception\|memory\|proposal\|plan\|action` | Reads that step's staged activation, enabled and available plugins, bundle, and runner schema (inputs, output, composition and failure policy). Perception, memory, proposal and plan also show their preset. Each reports its view; memory, proposal, plan and action include live runner status. Proposal, plan and action also show the decision generation, each decision step's plugins, and the action authority. |
| `plugins proposal\|plan\|action inspect` | Offline: replays an image, a directory of images, or a recorded automation or inspect run through perception, observation, memory and the decision steps, and prints that step's record for every frame, with that step's and each of its plugins' time and the cycle's (`--json` carries every step's in `timing`). A recording restores the selections it recorded for each frame; `--plugin` replaces the inspected step's; proposal and plan also accept `--preset`. `--frame N` reports one 0-based frame after replaying the ones before it. `--record` also saves the report and source frames under `runtime/<step>-inspections/`; that run replays as a source. |
| `plugins perception inspect` | Shows what a perception selection detects from an image, a directory of images, or a recorded run. |
| `hosts create` | Creates a named host for the vehicle given by `--provider` (`chase-sim` or `picar`) and `--url`, installs its release, and starts it. `vehicles status` names each vehicle's provider and URL. |
| `hosts delete` | Stops the run and host, then removes its local files. PiCar harness files remain onboard. |
| `hosts update` | Packages the staged steps as a versioned release for the vehicle's host: a PiCar receives it over SSH, a simulator vehicle keeps it locally. With `--restart`, the host restarts onto it and verifies every step runs its staged plugins. |
| `hosts run\|stop\|restart\|status` | Starts, stops, restarts or inspects a run on the host. Status includes the vehicle endpoint, host URL and simulator attachment. `stop` ends the run; the host stays attached until `hosts delete`. |
| `hosts stream perception` | Displays rolling latest perception from the runtime host's `/host/observation/latest` and serves a local frame-matched `/perception` view (link to Memory map) whose URL the terminal shows. |
| `hosts stream memory` | Inspects live memory as a key→value ledger. The terminal shows health and counts and serves a local `/memory` map page whose URL it shows. Keys are `record_id`s; click a key to see the retained value. |
| `hosts stream proposal\|plan\|action` | Prints that step's record from the latest cycle the host published, with its age, generation, plugins, and the step's and each plugin's time; action also shows how the host applied control. `--once` prints one and exits 2 with the reason when none is current; `--json` returns `host_<step>_stream`. A running terminal stream also serves the local action view below. |
| `hosts memory reset` | Clears live retained evidence on the runtime host and starts a new epoch. Exits 0 once the host's answer shows every plugin on a new epoch or empty, even while a running session refills memory. Does not move the vehicle. |
| `plugins memory inspect` | Offline: runs an image, a directory of images, or a recorded perception or memory run through perception, observation and memory, and reports each memory plugin's health, record count and epoch after every frame, with the memory step's and each plugin's time. A recording restores the executable step selections and configs it contains; `--preset` or `--plugin` overrides memory. Otherwise each step uses its default. The report prints to the terminal; `--record` also saves the source frames, timing, both step selections and report under `runtime/memory-inspections/`. A recorded perception inspect run is a source. |
| `hosts memory viability` | Memory health check: 60s poll of the runtime host's live memory step (update cadence, duration, failures, health, epoch stability). Saves `report.json` under `lab/runs/memory-viability/` unless `--no-record`. |
| `hosts perception viability` | Perception health check: 60s cadence/freshness measurement of the runtime host's observation publication that also requires that no cycle's control was applied (host RSS/CPU when the vehicle supplies an `ssh_target`). Saves `report.json` and `summary.md` under `lab/runs/perception-viability/` unless `--no-record`. |
| `hosts operation ...` | Runs a bounded, explicitly requested vehicle operation. |
| `simulators create` | Starts a headless Chase simulator from the Metrics UI checkout at `AUTOMA_METRICS_UI_DIR`, with its launcher's default configuration, on the first free port from 5051, and prints its URL. It runs until `simulators delete --url` ends it, which refuses while a host was created at it. |
| `simulators ...` | Finds or prepares the SimEval and Metrics UI environment. |

With `--plugin`, each `stage` step keeps the staged config of every plugin that stays selected. When a restage changes a plugin's spec or config, the command names those plugins and prints the `--restart` command that applies them, since a running host only adopts a changed plugin list live.

The runtime viewer's **Plugins** table refreshes every 500 ms. Revision columns
are hidden by default; **Show revisions** reveals them for debugging. **Enabled**
means the plugin is in the host's applied selection; it changes after adoption,
while requested and applied revisions show pending selection changes. A
stopped session can still have enabled plugins. The table reports and
changes selections for all six steps. **Origin** identifies the available entry as packaged or
uploaded, independently of which revision is applied. Uploads add rows or
replace an available revision without editing the packaged catalog files.
Enabled uses green and red dots, with an amber **Unknown** dot when selection
data is unavailable. A polling failure marks the last rows as a stale snapshot
until a successful refresh. Messages above the table appear only for errors.
Above the table, the page lays out the six steps left to right in run order,
each listing its applied plugins top to bottom in the order they run. It
refreshes with the table, dims with it on a polling failure, and shows
**Unknown** for a step without selection data.

Uploaded plugins stay in the host's catalog until that host restarts; `plugins
upload` and `plugins status` print the host run beside the catalog version. A
restart onto a new release clears them: `hosts update --restart` on a PiCar,
and on a simulator vehicle the next `hosts run` after a restage, which
moves an idle host onto the latest release. Upload them again after either.

`plugins arm` and `plugins disarm` change the existing host's selections without
starting or stopping automation or changing control mode. They leave staged
activations unchanged; a host restart restores staged selections. Both accept
`--name <host>` or `--url <runtime-url>`. Workbench catalogs support upload only.

```sh
./cli/automa hosts plugins disarm --name chase-sim-chaser --step proposal --plugin avoid_recent_obstruction
./cli/automa hosts plugins disarm --name chase-sim-chaser --step memory --all
./cli/automa hosts plugins disarm --name chase-sim-chaser --all
./cli/automa hosts plugins disarm --name chase-sim-chaser --selection removals.json --json
```

For grouped disarming, `removals.json` is a step-to-ID-list map, for example
`{"perception": ["floor_plane"], "proposal": ["avoid_recent_obstruction"]}`.
Empty lists remove nothing; omitted steps stay selected. `--all` is the explicit
clear operation and cannot be combined with `--plugin` or `--selection`.
Unknown plugin IDs fail the whole request; already inactive catalog IDs are
successful no-ops. Disarmed instances follow the existing reset lifecycle,
which can clear their tracking or memory state. Re-arming creates fresh
instances, while plugins that remain selected keep their state.

Human output reports `Disarming: requested`, `applied`, or `failed`, followed by
requested and applied selections and a status command. `--json` returns
`automa_plugin_disarm`, retaining the shared `arming` selection state.
Exit 0 acknowledges the request; the next cycle applies it. Exit 2 reports a
failure. When no session is running, changes remain requested until a cycle
runs; status never presents a pending deselection as applied.

`stream perception` and `stream memory` take the same flags. By default each
refresh redraws the terminal view and updates the local view whose URL it
shows. `--json` prints one probe per refresh in place of both, for
scripts: `host_perception_live` for perception and
`host_memory_live` for memory.
`--once` exits 2 unless the probe's `status` is `live`. Any other status
(`stopped`, `stale`, `absent`, `error`, `unavailable`) comes with an `error`.
When no runtime host can be addressed, both emit one `unavailable` JSON probe
and exit 2, even without `--once`; the error names the command that starts one.
Terminal streams show the same probe verdict and reason, and perception labels
the publication's health separately from that verdict.

Every running terminal stream (perception, memory, proposal, plan, action)
also serves a `/decision` page and prints its URL as `action view:`. The page
pairs the host's latest accepted cycle with its frame, the retained evidence its
proposals read, the candidates, the plan's selection, the action's authorized
command and how the host applied it. When no matched cycle can be read, the
line gives the reason, and the `/perception` and `/memory` pages still follow
the observation.

Every vehicle's runtime host serves the same routes; the CLI finds a Chase
host from its host record and a PiCar's from its staged `base_url`.
Perception reads `/host/observation/latest`: a healthy publication with a
perception payload is live, and `age_ms` comes from the host's clock.
Memory reads the retained step in `/host/status`: the step's presence is
live. Its `plugins[]` entries retain each applied plugin's `state` and expose
that plugin's `health`, `epoch_id`, `record_count`, and `bounds` alongside it;
`evidence_publisher` names the plugin whose evidence is published.
`last_error` and counters report the step's update health.
Use the nested perception result and plugin reports to inspect plugin outcomes;
`live` describes availability rather than promising that every plugin succeeded.

Both viability commands exit 0 for a passed measurement, 1 for failed
measurement gates, and 2 when no runtime host can be addressed. Under `--json`,
that failure returns `host_step_viability_error` with `host_name`,
`step`, `error` (`no_runtime_host`), and the diagnostic in `message`. Reports
are also saved in JSON mode; use `--json --no-record` for a report printed only
to stdout. The `control_never_applied` gate fails if any sampled cycle applied
control, so measure a running host in manual or observe-only mode.

Every `hosts stage <step>` requires a host created with `hosts create --name`.
Its configuration record identifies the host and the vehicle endpoint; staging
reads that record without probing the vehicle. A directory or an old activation
alone does not create a host. `--dry-run` validates staging without writing it.

Every step update records `metadata.controller_bundle` with `root_dir`,
`autonomy_dir`, `implementations_dir`, `runtime_dir`, and `release`.
`hosts info perception|memory|proposal --json` exposes those same bundle keys.
The paths identify the staged code; `release` identifies the packaged source
and archive. Plugin selections and constructor configs remain in `plugins`,
`plugin_specs`, and `plugin_configs`.

Replay (`workbench` and every step's `inspect` on a recording)
runs each recorded selection on the release its frames name. It extracts that
archive from `runtime/hosts/<host-name>/bundle/releases/` and ignores whatever is
staged now. If the archive is gone, replay exits 2 naming the release and
`hosts update`.

Every step's update returns the same `host_step_update` payload under
`--json`: `step`, `preset`, `plugins`, the staged `manifest`, and the `apply`
commands. Staging does not check readiness; `hosts run` does before
it starts, and `hosts status --long` reports invalid staged steps. An
unknown-host failure under `--json` returns `host_step_update_error`
with `host_name`, `step`, `error: unknown_host`, and the diagnostic in `message`; the command exits 2 and stages nothing.

Use `help` at a command-group level and `--help` for final command options:

```sh
./cli/automa hosts stage help
./cli/automa hosts update --help
```

## Chase Simulator Workflow

A host exists only after `hosts create` writes its `host-configuration.json` record.
Choose any name; every later `--name` refers to that name. Existing directories
without a record can be adopted, preserving staged steps. The passive path
preserves the operator's simulator session:

```sh
./cli/automa vehicles status
./cli/automa hosts create --name chase-sim-chaser --provider chase-sim --url http://localhost:5050
./cli/automa hosts stage memory \
  --name chase-sim-chaser \
  --preset recency_ledger
./cli/automa hosts stage perception \
  --name chase-sim-chaser \
  --preset lightweight_observer
./cli/automa hosts run \
  --name chase-sim-chaser \
  --observe-only \
  --num-decisions 0 \
  --open-view
./cli/automa hosts status --name chase-sim-chaser
./cli/automa hosts stop --name chase-sim-chaser
./cli/automa hosts status --name chase-sim-chaser
```

The memory update stages the Memory view's ledger; without a memory or
perception activation, the live cycle runs no plugins for that step. Each update replaces only its named
step's selection/configs. For an existing tuned deployment, use your intended
selection instead of copying these default presets; a preset/plugin selection
uses its packaged configs.

`runtime/` survives Git branch changes. Old per-step schemas, including
`automa_memory_activation_v0`, are incompatible with the shared
`automa_step_activation_v0` documents. `hosts status --long` reports an
invalid deployment and the affected step's `hosts stage <step>` command.
Restage that step with your intended selection, then check it again. Every command that reads
staged documents checks them first and prints the same step, path, reason, and
restage command: startup and restart before launching or stopping a worker,
`hosts update` before packaging or writing, and `hosts info`, `hosts stream proposal|plan|action`, and
`plugins perception inspect` before reporting. An invalid optional step blocks startup;
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
./cli/automa hosts info perception --name chase-sim-chaser
./cli/automa hosts info proposal --name chase-sim-chaser
```

Run the same staged plugins with movement enabled:

```sh
./cli/automa hosts run --name chase-sim-chaser
./cli/automa hosts status --name chase-sim-chaser
./cli/automa hosts info perception --name chase-sim-chaser
./cli/automa hosts stream perception --name chase-sim-chaser
./cli/automa hosts stream perception --name piracer
```

`hosts run` waits for a camera frame, the completed perception result for
that frame, and a healthy view belonging to the same worker generation. It
returns a nonzero result and preserves the exact capture code/path when those
gates fail; a spawned PID alone is not reported as success.

While a host's run is active, `hosts info perception` reports a
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
./cli/automa hosts stop --name chase-sim-chaser
./cli/automa hosts restart --name chase-sim-chaser --observe-only --num-decisions 0 --open-view
```

Useful run options:

- `--num-decisions N` bounds the number of completed decision cycles and stops
  movement afterward. Movement-enabled `run` and `restart` require a positive
  count unless `--unbounded` is set. Observation-only runs still accept zero.
- `--unbounded` runs until stopped, overriding `--num-decisions` on either
  vehicle and in either mode. For example:
  `./cli/automa hosts run --name chase-sim-chaser --unbounded --open-view`.
  The launch command returns after readiness and prints
  `hosts stop`. Ctrl-C in a terminal stream stops that stream,
  not the worker.
- The next capture starts as soon as the previous cycle finishes: the loop is
  not shut down, no decision is in flight, and no frame is pending. There is no
  configured capture interval.
- Each capture goes straight to the decision worker, so no capture waits and none
  is dropped. Drive ticks during an in-flight cycle are not captures, and
  `/host/camera/latest` updates once per capture.
- `--observe-only` applies no decision output on either vehicle. Starting passive observation in Chase preserves its current simulator session.
- `--open-view` opens the browser only after the correlated view is healthy.
- `--record` keeps every completed decision and its exact camera image, capture
  time, and applied step configurations. Both vehicles commit these artifacts
  before counting the decision. The host owns its history under
  `runtime/automation/host-runs/`; the CLI exports it under
  `runtime/automation/runs/`. Separate directories keep the local Chase host
  and CLI from overwriting each other's manifests. PiCar recordings are copied
  from the onboard run in order and drained on completion or stop; later manual observations
  stay outside the recording. A successful recorded run reports matching
  `Decisions recorded` and `Decisions completed` counts.
- `--log` persists worker output to `automation.log`.

After changing perception or shared autonomy code, stage a fresh bundle before
restarting the worker:

```sh
./cli/automa hosts stage perception --name chase-sim-chaser --preset sim_debug
./cli/automa hosts restart --name chase-sim-chaser --observe-only --num-decisions 0 --open-view
```

### Perception Plugins

`--plugin` selects packaged perception plugins by catalog key, in order, with
their default configs from `implementations/decision_cycle/perception/catalog.py`.
`--preset` selects a named preset from
`implementations/decision_cycle/perception/presets.py`. The two are exclusive,
and a plugin list is recorded as the preset it equals, else `custom`.

`hosts info perception` reports the staged preset, its enabled plugins and
the available ones, and the staged runner's `perception_schema_v3` contract.
`--json` returns `host_perception_info` with that contract under
`perception_schema`. Staging replaces the selection. A running worker applies a
changed plugin list at its next frame and changed plugin configs when it
restarts; a stopped one uses the selection the next time it starts:

```sh
./cli/automa hosts info perception --name chase-sim-chaser
./cli/automa hosts stage perception --name chase-sim-chaser --plugin frame --plugin floor_plane
./cli/automa hosts stage perception --name chase-sim-chaser --preset visual_observer
```

`lightweight_observer` is the production-oriented frame and floor-boundary
chain. `visual_observer` adds feature-motion tracks and is intentionally much
slower. Artifact-only VLM preprocessing remains an optional diagnostic plugin,
not part of either observer.

### Perception Inspection

Inspect what a perception selection detects: apply a preset or a plugin
selection to an image, an image directory, or a recorded perception or memory
run:

```sh
./cli/automa plugins perception inspect path/to/frame.jpg --plugin frame --plugin floor_continuity
./cli/automa plugins perception inspect path/to/images --preset visual_observer
```

Start perception, memory, proposal, plan, or action
inspection with `--shared-memory FILE`. The file is a `shared_memory_v1` JSON
document. For example, save this as `seed.json` to continue cardboard object 7:

```json
{
  "schema": "shared_memory_v1",
  "values": {
    "perception.sim_cardboard_permanence.history": {
      "objects": [{
        "object_id": 7,
        "bbox_xyxy_norm": [0.11, 0.0, 0.76, 0.77],
        "seen_frames": 9,
        "missed_frames": 0
      }],
      "next_object_id": 8
    }
  }
}
```

```sh
./cli/automa plugins perception inspect path/to/next-frame.png --preset chase-v1-detect-by-color --shared-memory seed.json --json
```

The `chase-v1-detect-by-color` preset selects `sim_cardboard_obstacle` and then
`sim_cardboard_permanence`. `sim_cardboard_obstacle` detects visible cardboard
regions, including smaller distant boxes, and separates touching silhouettes
when their lower edges have distinct steps. `sim_cardboard_permanence` runs
after it and associates the nearest detection/history pairs within a normalized
center distance of 0.25, using each detection and identity at most once per
frame. Its
`detection_plugin_id` option names the detection plugin it reads, by default
`sim_cardboard_obstacle`.
A match is labeled `known cardboard 007`; a detection
without a match receives a new identity and is labeled `new cardboard`.
The two plugins emit an overlapping detection/identity pair for every current
obstacle. Detection IDs identify this frame's regions; the permanence plugin
owns identities across frames. Remembered
objects stay in shared memory through three missed frames by default, and a
frame with no detection draws no box. Large jumps and expired history start
new identities. This association uses image geometry and cannot establish
physical identity after arbitrary motion or long disappearances.

Each invocation decodes a fresh map. Plugin state keys follow
`<step>.<plugin_id>.<name>`. Records use
`{"$record": "module.path:ClassName", "data": {...}}`. Seed retained evidence
at `memory.bounded_evidence.ledger` with the record spec
`implementations.decision_cycle.memory.shared.evidence_ledger.ledger:EvidenceLedger`;
the bounded evidence entry's `final.plugins[].state` from memory inspection can
be pasted as its `data`. The published `memory.evidence` value is rebuilt from
that ledger on every update. Keys no selected plugin reads are silently unused;
tuples decode as lists, so uploaded plugins must accept that representation.

Absent frames reset perception history, and restages reset the affected plugins'
history keys, including seeded history. JSON reports and recorded `report.json`
include the original file path and document under `shared_memory_seed`. This is
provenance: replaying a recording does not reapply its seed. Pass the flag again
to start another invocation from that history.

The report prints to the terminal: the source, the perception selection,
aggregate statuses, latency, representation health and each plugin's status
counts. `--json` includes each frame's identity, timing and plugin outputs.
`--record` also saves the selection, timing, per-frame plugin outputs and the
report as `report.json` under `runtime/perception-inspections/<run>/`, and
prints that directory after `Recorded:`. An image recording refers to the source
images where they are.

A recording restores its perception selection, frame identity and timestamps;
`--preset` or `--plugin` overrides the selection. Without a recording, the step
uses its default preset.

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

### Memory Plugins

`--plugin` selects packaged memory plugins by catalog key, in order, with their
default configs from `implementations/decision_cycle/memory/catalog.py`.
`--preset` selects a named preset from
`implementations/decision_cycle/memory/presets.py`. The two are exclusive,
and a plugin list is recorded as the preset it equals, else `custom`.

`hosts info memory` reports the staged preset, its enabled plugins and the
available ones, and the staged runner's `memory_schema_v1` contract.
`--json` returns `host_memory_info` with that contract under `memory_schema`.
Perception, memory, and proposal (`hosts info proposal`) describe inputs,
plugins, output, composition and failure policy;
memory's contract also names the ledger fields the CLI and viewers project from
each plugin's status. The report itself preserves that status, including absent
ledger keys. Staging replaces the selection. A running worker applies a
changed plugin list at its next frame and changed plugin configs when it
restarts; a stopped one uses the selection the next time it starts:

```sh
./cli/automa hosts info memory --name chase-sim-chaser
./cli/automa hosts stage memory --name chase-sim-chaser --plugin bounded_evidence
./cli/automa hosts stage memory --name chase-sim-chaser --preset recency_ledger
```

`recency_ledger`, the default, keeps a bounded ledger of observation things and
signals with age expiry and oldest-first eviction.

### Memory Inspection

Use `--shared-memory FILE` to start with a seeded ledger; see
[Perception Inspection](#perception-inspection) for the format and producer keys.

Inspect what a memory selection retains: run an image, an image directory, or
a recorded perception or memory run through perception, observation and memory:

```sh
./cli/automa plugins memory inspect path/to/images
./cli/automa plugins memory inspect path/to/images --plugin bounded_evidence
```

Memory inspection reads its source only. A recorded perception inspect run is a
source, and memory runs the same perception selection over the same frames:

```sh
./cli/automa plugins perception inspect path/to/images --record
./cli/automa plugins memory inspect runtime/perception-inspections/<run>
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
Proposal follows perception and memory: `--preset obstruction_avoidance`
selects its default recipe; without either flag it stages that default preset.
Its default business logic is reached through
[`proposal/presets.py`](implementations/decision_cycle/proposal/presets.py),
[`proposal/catalog.py`](implementations/decision_cycle/proposal/catalog.py), and
[`plugins/avoid_recent_obstruction/plugin.py`](implementations/decision_cycle/proposal/plugins/avoid_recent_obstruction/plugin.py).

`hosts info proposal` reports the staged plugins and the available ones,
the staged runner's `proposal_schema_v1` contract, and the plan and action
plugins that act on them. `--json` returns `host_proposal_info` with that
contract under `proposal_schema`, and the decision generation, each decision
step's plugins and the action authority under `decision`; `hosts info plan`
and `hosts info action` report the same for their step, plan with its
`plan_schema_v1` contract under `plan_schema` and action with none. Like memory
info, it probes the running autonomy engine and reports its proposal step
under `live` (`host_proposal_live`): the plugins it runs and its run and failure
counts, from the runtime host's `/host/status`. That is the engine's step, not the proposals any view last
rendered. A worker loads the staged proposals from the controller bundle when
it starts, as it loads memory, and reports them under `proposal` in its state
and `proposal_plugin_report` in each frame. Staging replaces the selection. A
running worker applies a changed plugin list at its next frame and changed
plugin configs when it restarts; a stopped one uses the selection the next time
it starts. Proposals are part of the decision generation: once the worker runs
the new selection it publishes under the restaged generation, and an open
action view follows it. The generation changes after the cycle confirms
which plugins were applied. A plugin load or removal-reset failure keeps the
old applied selection and generation, and publishing is refused while they
differ from the staged decision. After a config restage it publishes no
decision frames until it restarts:

```sh
./cli/automa hosts info proposal --name chase-sim-chaser
./cli/automa hosts stage proposal --name chase-sim-chaser --plugin avoid_recent_obstruction
```

## Decision Playback Workbench

The workbench reads the same recorded frame order and timing. `hosts
workbench replay` starts each step from `--perception-preset` or
`--perception-plugin`, `--memory-preset` or `--memory-plugin`, and
`--proposal-preset` or `--proposal-plugin`, as the inspect and update commands
take them. With no flag, a step uses its default preset: proposal uses
`obstruction_avoidance`. The CLI and page show each step's preset, where it has one,
and ordered plugins. Each frame's detail and timeline entry carry the cycle's
`timing`: each step's and plugin's time for that frame.
The page's catalog lists available plugins; its separate **Run order** shows
execution order. Newly checked plugins run last, and retained plugins keep
their order. Unchecking every plugin disables its plugins.
Recorded dropout positions remain seekable. The page labels perception as
absent and shows the reason while memory can still show retained records.

```sh
./cli/automa hosts workbench replay path/to/images --perception-preset multi_obstruction --memory-preset recency_ledger --serve
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
`custom`. The pipeline's `<step>_plugin_report.applied_plugin_ids` reports the
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

Create the host once; its name is independent of the Pi's hostname:

```sh
./cli/automa vehicles discover --provider picar --url http://piracer.local:8887
./cli/automa vehicles status
./cli/automa hosts create --name piracer --provider picar --url http://piracer.local:8887
./cli/automa hosts stage perception --name piracer --preset lightweight_observer
./cli/automa hosts update --name piracer --restart
./cli/automa hosts run --name piracer --observe-only --num-decisions 10
```

Creation syncs the DonkeyCar harness, installs and enables `automa-donkey.service`,
deploys every step's activation, and restarts and verifies the host in manual mode.
Later boots start that service automatically. `hosts update` packages and deploys a
new controller release; add `--restart` to load changed plugin specs or configs.
Use `--dry-run` on create or update to inspect the commands without writing or connecting.

Creation records the SSH target from `--ssh-target`, `PI_HOST`, or the URL hostname
(with user `piracer`), and the home from `--pi-home`, `PI_HOME`, or `/home/piracer`.
Updates and deletion use that recorded connection. If creation fails after syncing,
the Pi keeps the files already transferred.

The handheld controller is disabled by default. Persist its drive arguments at
creation with `--drive-args=--js`, or on a later update:

```sh
./cli/automa hosts update --name piracer --restart --drive-args=--js
```

Pass `--drive-args=` with `--restart` to restore controller-free startup.
`hosts delete --name piracer` stops and disables the service and removes the
local host directory. Harness files stay in `~/mycar` on the Pi. A failed SSH stop
keeps the local record so deletion can be retried.

### Physical Activation State

The first physical autonomy deployment stages each step's default where
nothing is staged: the `lightweight_observer` perception, `recency_ledger`
memory and `obstruction_avoidance` proposal presets, and the built-in
observation/plan/action plugins. A selection staged as a named preset is
rebuilt from the current catalog, including its configs; any other staged
selection keeps its plugins, specs and configs. Every deployed step records the
same release and bundle paths. The Pi loads those activations. The Donkey assembly runs the
shared autonomy cycle independently of `run_pilot`. The drive loop still runs at
`DRIVE_LOOP_HZ` (20 Hz) and submits a detached sample every tick, so
`/host/camera/latest` updates at the drive-loop rate. Each background
decision takes the newest waiting sample; received samples increment
`frames_received` and waiting samples replaced by newer arrivals increment
`frames_dropped`. No backlog builds. A memory halt keeps camera inspection
available without accumulating pending work or overload drops. The matched result remains
`/host/observation/latest`. While mode remains `user`, pilot outputs stay
zero and Donkey DriveMode keeps manual input authoritative.

**Deploy split:** autonomy packages ship the controller tree and activation
files (`runtime/<step>/active.json` for each staged step). The code path that
*loads* the steps into the Donkey loop lives in `manage.py` from **core**. After
harness changes, run core then autonomy with `--restart`. Autonomy `--restart`
verification fails if a step was shipped but `/host/status` does not report
it running the staged plugins.

Step selections are local until the next autonomy deployment:

```sh
./cli/automa hosts stage proposal --name piracer
./cli/automa hosts stage action --name piracer --plugin selected
./cli/automa hosts stage memory --name piracer
./cli/automa hosts update --name piracer --restart
```

`hosts info perception|memory|proposal --name piracer` inspects staged
activation and release metadata. Local staging does not require the Pi to be
online; the subsequent autonomy deploy does.

### Shared Movement Workflow

Once the selected plugins are staged (and deployed for PiCar), the same commands
control both vehicles. Change only the host name:

```sh
./cli/automa hosts run --name chase-sim-chaser --open-view
./cli/automa hosts stop --name chase-sim-chaser
./cli/automa hosts run --name piracer --open-view
./cli/automa hosts stop --name piracer
```

`hosts restart --name <host>` stops movement, recreates the host, and
starts it with the requested options. `--observe-only` and `--num-decisions`
have the same control semantics on both vehicles. The simulator
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

Capture status uses only `frames_received` and `frames_dropped` on both vehicles
and the Chase stream. Received frames are distinct captures accepted at
runtime ingress; dropped frames are received captures replaced before processing.
Counters accumulate for the host process; run summaries show deltas. Intentional
halt/stop discards and uncaptured simulator frame-index gaps are excluded.
Automation `run` and `restart` use `--num-decisions` in place of `--frames`.
Run configuration and `hosts status` use `num_decisions` (zero means
unbounded, explicitly requested with `--unbounded` for movement-enabled runs); host session status
reports `processed_decisions`.

This change updates the physical harness and vendor HTTP API. Install both
layers before using the shared commands on PiCar:

```sh
./cli/automa hosts create --name piracer --provider picar --url http://piracer.local:8887
./cli/automa hosts update --name piracer --restart
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
playback. A dry run reports the captured frame pairs unscored and exits 0; a
live check exits 1 when a check fails.

```sh
./cli/automa hosts operation startup-check --name piracer
./cli/automa hosts operation startup-check --name chase-sim-chaser
```

Results are written under `lab/runs/startup-check/<run-id>/`, including the
plan, report, summary, before/after frames, diffs, and contact sheet.
`report.json` lists the control `acquire` and `release` events of a live check.

## Generated Runtime State

The local controller layout is generated under:

```text
runtime/hosts/<host-name>/
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
- `http://piracer.local:8887/host/status`
- `http://piracer.local:8887/host/observation/latest`
- `http://piracer.local:8887/host/observation/latest/frame.jpg`
- `http://piracer.local:8887/host/observation/transaction`

`/frame.jpg` is the live camera feed. The `/host/observation/latest*`
endpoints publish the exact onboard-processed frame and its matching
findings (in-memory only; no default history or disk writes). The JPEG
response includes `X-Frame-Id` so clients can pair image and JSON.
The CLI viewer uses `/host/observation/transaction`, which returns the
publication and its JPEG together so a newer frame cannot split the pair
between requests over Wi-Fi.

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
