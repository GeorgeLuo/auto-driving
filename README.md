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
PYTHONDONTWRITEBYTECODE=1 python3 cli/run_tests.py
```

Include the live Chase simulator smoke test:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 cli/run_tests.py --live-sim
```

## Project Layout

- `autonomy/` contains sensor- and environment-agnostic vehicle, perception,
  decision, and runtime contracts plus generic orchestration. It contains no
  perception algorithms.
- `implementations/` contains concrete vehicle adapters, perception plugins,
  runtime hosts, and bounded operations.
- `cli/` contains the `automa` command and its scenario test harness.
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
| `vehicles info ...` | Reads locally staged perception or decision configuration. |
| `vehicles perception ...` | Runs perception experiments and manages production or lab plugins. |
| `vehicles automation ...` | Runs or inspects the local Chase controller worker. |
| `vehicles stream perception` | Displays the worker's rolling latest perception output. |
| `vehicles update core` | Deploys DonkeyCar framework and physical harness code to the Pi. |
| `vehicles update autonomy` | Deploys a versioned autonomy release and activation metadata to the Pi. |
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
./cli/automa vehicles stream perception --id chase-sim-chaser
```

Stop or restart the worker:

```sh
./cli/automa vehicles automation stop --id chase-sim-chaser
./cli/automa vehicles automation restart --id chase-sim-chaser
```

Useful run options:

- `--frames N` makes a bounded smoke run.
- `--interval-s 0` removes the delay between bounded frames.
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
replay an existing image sequence through a production algorithm:

```sh
./cli/automa vehicles perception run
./cli/automa vehicles perception run --id piracer --algorithm lightweight_observer
./cli/automa vehicles perception replay path/to/images --algorithm visual_observer
```

Experimental candidates are isolated under `lab/plugins/perception/`. Inspect
their readiness, provision declared dependencies once, and compare every ready
candidate on the same frames:

```sh
./cli/automa vehicles perception candidates
./cli/automa vehicles perception setup fastsam
./cli/automa vehicles perception compare path/to/images
```

No captures or reports are retained by default. Add `--record` when overlays,
per-frame JSON, and the generated review page are wanted.

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

Probe the vehicle first:

```sh
./cli/automa vehicles active
```

For a fresh setup, sync the core harness without restarting, then deploy and
restart the autonomy release:

```sh
./cli/automa vehicles update core --id piracer
./cli/automa vehicles update autonomy --id piracer --restart
```

`update autonomy` packages `autonomy/` and `implementations/`, verifies the
archive hash on the Pi, installs a versioned release, transfers perception and
decision manifests, and restarts only when requested. Post-restart verification
requires both the selected decision engine and perception algorithm to load
while Donkey drive mode remains `user`; the deployment check does not command
movement.

Use the deploy commands according to what changed:

- DonkeyCar vendor patch or `deploy/targets/donkeycar/app/`: `update core`.
- `autonomy/`, `implementations/`, or staged activations: `update autonomy`.
- Run both commands for a fresh Pi or when both layers changed.

Core deployment preserves remote autonomy releases and runtime activation
state. To inspect planned writes, add `--dry-run`.

If the HTTP server is down but SSH is available, bypass discovery explicitly:

```sh
./cli/automa vehicles update core --id piracer \
  --skip-discovery --ssh-target piracer@piracer.local
./cli/automa vehicles update autonomy --id piracer \
  --skip-discovery --ssh-target piracer@piracer.local --restart
```

The handheld controller is not enabled by default. Enable it only when it
should become an active command source:

```sh
./cli/automa vehicles update core --id piracer --restart --drive-args=--js
```

### Physical Activation State

The first physical autonomy deployment creates the default
`lightweight_observer` perception activation and `idle` decision activation
when none exist. The Pi loads both activations: the lightweight frame and floor
boundary mapper runs before observation, while the idle decision engine keeps
movement at zero.

Decision changes are local until the next autonomy deployment:

```sh
./cli/automa vehicles update decision --id piracer --engine idle
./cli/automa vehicles update autonomy --id piracer --restart
```

`vehicles info perception --id piracer` inspects the staged mapper, enabled
plugins, machine-readable input contract, and release metadata. Local staging
does not require the Pi to be online; the subsequent autonomy deploy does.

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
- [`docs/milestones/002-perception-hardening/plan.html`](docs/milestones/002-perception-hardening/plan.html)
  is the active perception-hardening milestone.

Dependency direction is intentional:

```text
autonomy contracts       -> never import implementations
implementations          -> satisfy and compose autonomy contracts
CLI/runtime entrypoints  -> select implementations and execute the cycle
```

Perception follows a component-query model. The stable stage wraps a generic
`SensorSnapshot` and runs configured plugins without knowing which sensor or
meaning any plugin uses. Each plugin declares component ids, attempts to
resolve those components from the snapshot, and emits evidence or an explicit
unavailable result. Concrete camera decoding and every meaning-making
algorithm live under `implementations/perception/`; unpromoted candidates live
under `lab/plugins/perception/`.

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
