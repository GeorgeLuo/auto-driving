# Automa Vehicle Automation Workspace

This repository is the local source of truth for a vehicle-agnostic automation
engine, a PiRacer/DonkeyCar target, and a Chase simulator adapter. The current
decision engine is intentionally idle: the framework can capture sensors, run
perception, produce an inspectable cycle, and select a controller without yet
implementing autonomous navigation.

## Setup

Install the local runtime and analysis dependencies:

```sh
python3 -m pip install -r requirements.txt
```

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
4. Use [`docs/README.md`](docs/README.md) for the active milestone, current
   architecture references, completed milestone context, and research notes.
5. Treat `runtime/` and `lab/` as generated state. Start from tracked source and
   the CLI rather than using files in those directories as an API.

## Project Layout

- `autonomy/` contains sensor- and environment-agnostic vehicle, perception,
  decision, and runtime contracts plus generic orchestration. It contains no
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

| Command | Reads or changes |
|---|---|
| `vehicles active` | Probes live PiCar and Chase endpoints. |
| `vehicles update perception` | Packages code and stages a vehicle perception activation locally. |
| `vehicles update decision` | Packages code and stages a decision activation locally. |
| `vehicles update memory` | Packages code and stages a vehicle memory activation locally (default `bounded_evidence`). |
| `vehicles info ...` | Reads staged perception, decision, or memory configuration; perception info also reports the live view URL. |
| `vehicles perception ...` | Runs perception experiments and manages production or lab plugins. |
| `vehicles automation ...` | Runs or inspects the local Chase controller worker. |
| `vehicles stream perception` | Displays rolling latest perception. Chase uses the local automation worker; PiCar polls onboard `/autonomy/observation/latest` and opens a local frame-matched perception view (link to Memory map). |
| `vehicles stream memory` | Inspects live memory as a key→value ledger (terminal + local `/memory` map page on PiCar). Keys are `record_id`s; click a key to see the retained value. |
| `vehicles memory reset` | Clears live retained evidence on Chase or PiCar and starts a new empty epoch (visible via info/stream/Memory map). Does not move the vehicle. |
| `vehicles perception check` | Guided stationary PiCar placement check (clear/left/center/right/removed by default); never moves the car. Use `--record` for review artifacts. |
| `vehicles perception qualify` | Offline common-frame compare of packaged control vs one lab candidate on labeled physical-check frames; emits promote/reject. |
| `vehicles perception viability` | 60s onboard cadence/freshness/RSS measurement for a physical PiCar. |
| `vehicles update core` | Deploys DonkeyCar framework and physical harness code to the Pi. |
| `vehicles update autonomy` | Deploys a versioned autonomy release and activation metadata (perception, decision, memory) to the Pi. With `--restart`, verifies the live memory stage; if activation is present but the stage is missing, update core (manage.py harness) then re-run autonomy. |
| `vehicles operation ...` | Runs a bounded, explicitly requested vehicle operation. |
| `simulators ...` | Finds or prepares the SimEval and Metrics UI environment. |

Use `help` at a command-group level and `--help` for final command options:

```sh
./cli/automa vehicles update help
./cli/automa vehicles update autonomy --help
```

## Chase Simulator Workflow

Prepare the simulator and verify that the Play/Chase frontend is connected:

```sh
./cli/automa simulators ensure
./cli/automa simulators ensure --scenario chaser-depth-obstacles
./cli/automa vehicles active
```

`simulators ensure` succeeds only after the selected scenario and Chase debug
state remain reachable through a short post-setup stability probe.

Stage the simulator color-control algorithm. A fresh controller bundle also
receives the explicit idle decision activation:

```sh
./cli/automa vehicles update perception --id chase-sim-chaser --algorithm sim_debug
```

Use `vehicles update decision` when deliberately changing the selected engine.

Inspect the machine-readable contracts declared by the staged code:

```sh
./cli/automa vehicles info perception --id chase-sim-chaser
./cli/automa vehicles info decision --id chase-sim-chaser
```

Start the controller worker in the background. It takes Chase WS control by
default, but the current decision engine emits only idle control:

```sh
./cli/automa vehicles automation run --id chase-sim-chaser
./cli/automa vehicles automation status --id chase-sim-chaser
./cli/automa vehicles info perception --id chase-sim-chaser
./cli/automa vehicles stream perception --id chase-sim-chaser
./cli/automa vehicles stream perception --id piracer
```

`automation run` and `automation restart` wait until the worker has captured
its first camera frame and published the view. They return a nonzero result and
persist the startup reason in `state.json` when discovery, model loading, or
camera startup fails; a spawned PID alone is not reported as success.

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
./cli/automa vehicles automation restart --id chase-sim-chaser
```

Useful run options:

- `--frames N` makes a bounded capture run.
- `--interval-s` sets the camera capture cadence; it defaults to `0.25` seconds.
- `--interval-s 0` captures as quickly as the vehicle interface allows.
- `--observe-only` leaves movement authority with the simulator.
- `--record` keeps timestamped frame and perception artifacts.
- `--log` persists worker output. No worker log is written by default.

After changing perception or shared autonomy code, stage a fresh bundle before
restarting the worker:

```sh
./cli/automa vehicles update perception --id chase-sim-chaser --algorithm sim_debug
./cli/automa vehicles automation restart --id chase-sim-chaser
```

### Perception Experiments

Observe five frames from a usable vehicle without taking movement control, or
apply an algorithm to one existing image or an image directory:

```sh
./cli/automa vehicles perception run
./cli/automa vehicles perception run --id piracer --algorithm lightweight_observer
./cli/automa vehicles perception apply path/to/frame.jpg --candidate floor_continuity
./cli/automa vehicles perception apply path/to/images --algorithm visual_observer
```

Candidate parameters come from the candidate manifest. Override one or more for
a bounded experiment without editing that manifest; the effective configuration
is retained in a recorded report:

```sh
./cli/automa vehicles perception apply path/to/images \
  --candidate floor_continuity \
  --set minimum_boundary_confidence=0.7 \
  --record
```

Guided stationary physical placement check (PiCar only; never commands movement):

```sh
./cli/automa vehicles perception check --id piracer --record
```

Results land under `lab/runs/perception-check/<run-id>/` with `review.html` when `--record` is set.

Offline strategy qualification on a recorded check run:

```sh
./cli/automa vehicles perception qualify \
  --from-check-run lab/runs/perception-check/<run-id> \
  --candidate floor_continuity \
  --extra-frame right=path/to/extra-right.jpg
```

Reports land under `lab/runs/perception-qualify/`. Promotion is explicit and offline-only; packaged floor-plane remains the operational fallback unless Pi viability is also proven.


`--set` is candidate-only, repeatable, and accepts JSON values. Invalid or
unknown parameter names fail explicitly rather than being ignored.

Experimental candidates are isolated under `lab/plugins/perception/`. Inspect
their readiness, provision declared dependencies once, and compare every ready
candidate on the same frames:

```sh
./cli/automa vehicles perception candidates
./cli/automa vehicles perception setup fastsam
./cli/automa vehicles perception compare path/to/images
```

A ready candidate can also drive the local simulator worker without being
copied into the controller bundle or imported into the core process:

```sh
./cli/automa vehicles update perception --id chase-sim-chaser --candidate fastsam
./cli/automa vehicles automation restart --id chase-sim-chaser
./cli/automa vehicles info perception --id chase-sim-chaser
```

This candidate activation path is simulator-only. The isolated worker returns
the stable perception contract, including normalized polygons when available;
the live view draws those polygons and falls back to normalized boxes for
plugins that do not emit outlines.

No captures or reports are retained by default. Add `--record` when overlays,
per-frame JSON, and the generated review page are wanted.

For a physical vehicle, `vehicles perception run --id piracer` currently fetches
Pi camera frames and processes them through a mapper on the development machine.
It does not prove that the Pi executed or published the perception result.

`lightweight_observer` is the production-oriented frame and floor-boundary
chain. `visual_observer` adds feature-motion tracks and is intentionally much
slower. Artifact-only VLM preprocessing remains an optional diagnostic plugin,
not part of either observer. Lab candidates remain local until a measured
promotion decision moves them into `implementations/`.

### Perception Plugins

The staged perception schema reports the available and enabled plugins. Enable
or disable one plugin at a time, then restart the worker so it imports the new
chain:

```sh
./cli/automa vehicles info perception --id chase-sim-chaser
./cli/automa vehicles perception enable --id chase-sim-chaser floor_plane
./cli/automa vehicles perception disable --id chase-sim-chaser sim_color_targets
./cli/automa vehicles automation restart --id chase-sim-chaser
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
archive hash on the Pi, installs a versioned release, transfers perception and
decision manifests, and restarts the supervised service only when requested.
Post-restart verification requires both the selected decision engine and
perception algorithm to load while Donkey drive mode remains `user`; the
deployment check does not command movement.

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
`lightweight_observer` perception activation, `idle` decision activation, and
`bounded_evidence` memory activation when none exist. The Pi loads those
activations. The Donkey assembly runs the shared autonomy cycle independently of
`run_pilot`, so manual `user` mode executes onboard perception at
`AUTONOMY_OBSERVATION_INTERVAL_S` (default 0.5 s) using the newest camera frame.
While mode remains `user`, pilot outputs stay zero and Donkey DriveMode keeps
manual input authoritative. The Pi publishes the exact latest frame/result on
`/autonomy/observation/latest` for Automa stream, guided check, and viability
measurement.

**Deploy split:** autonomy packages ship the controller tree and activation
files (including `runtime/memory/active.json`). The code path that *loads*
memory into the Donkey loop lives in `manage.py` from **core**. After harness
changes that add stages, run core then autonomy with `--restart`. Autonomy
`--restart` verification fails if a memory activation was shipped but no live
memory stage appears in `/autonomy/status`.

Decision and memory selection are local until the next autonomy deployment:

```sh
./cli/automa vehicles update decision --id piracer --engine idle
./cli/automa vehicles update memory --id piracer
./cli/automa vehicles update autonomy --id piracer --restart
```

`vehicles info perception|decision|memory --id piracer` inspects staged
activation and release metadata. Local staging does not require the Pi to be
online; the subsequent autonomy deploy does.

## Bounded Startup Check

The startup check captures a frame before and after each basic action
combination and scores whether the command produced a visible change. It sends
movement pulses unless `--dry-run` is provided, so raise the vehicle or clear
its path first.

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
      perception/active.json
      decision/active.json
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

- [`docs/README.md`](docs/README.md) identifies the active milestone and the
  reading order for current work.
- [`docs/reference/onboard-autonomy-flow.html`](docs/reference/onboard-autonomy-flow.html) explains
  the onboard perception, decision, and action flow.
- [`docs/reference/donkey-server-functionality.html`](docs/reference/donkey-server-functionality.html)
  describes the physical Donkey server boundary.
- [`docs/milestones/completed.md`](docs/milestones/completed.md) is the concise
  append-only history of closed work.
- [`docs/milestones/004-physical-perception-parity/plan.html`](docs/milestones/004-physical-perception-parity/plan.html)
  is the active physical-perception milestone.
- [`docs/milestones/005-evidence-memory-foundation/plan.html`](docs/milestones/005-evidence-memory-foundation/plan.html)
  is the queued evidence-memory milestone.

Dependency direction is intentional:

```text
autonomy contracts       -> never import implementations
implementations          -> satisfy and compose autonomy contracts
CLI/runtime entrypoints  -> select implementations and execute the cycle
```

Perception follows a component-injection model. The stable stage wraps a
generic `SensorSnapshot` and runs configured plugins without knowing which
sensor or meaning any plugin uses. Each plugin declares named component inputs
and returns only structured signals, spatial evidence, and measurements. The
generic runner resolves and caches those inputs, then owns missing-input and
warm-up status, error isolation, timing, source attribution, text rendering,
and optional diagnostic persistence. The surrounding cycle owns the sensor
snapshot, so perception output does not duplicate it. Concrete camera decoding
and every meaning-making algorithm live under `implementations/perception/`;
unpromoted candidates live under `lab/plugins/perception/`.

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
