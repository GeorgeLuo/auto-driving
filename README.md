# PiRacer Source Workspace

This directory is the local source of truth for the Raspberry Pi car.

## Layout

- `deploy/targets/donkeycar/` contains the physical PiRacer deployment bundle.
  `deploy/targets/donkeycar/app/` mirrors `/home/piracer/mycar` on the Pi, excluding
  runtime data and logs.
- `deploy/targets/donkeycar/donkeycar-vendor.json` and `deploy/targets/donkeycar/patches/`
  describe the DonkeyCar source checkout generated for the Pi.
- `autonomy/` contains stable vehicle, perception, decision, and runtime
  primitives.
- `implementations/` contains concrete vehicle adapters, perception plugins,
  decision engines, runtime hosts, and bounded operations.
- `cli/` contains the `automa` command-line access point for local and
  vehicle-facing interactions.
- `lab/` contains unstable generated work: run captures, analysis artifacts,
  and frozen experiment folders.
- `runtime/` contains local runtime state for active controllers, services, and
  generated controller bundles.
- `scripts/` contains sync and restart helpers.

Script entrypoints are grouped by role:

- `scripts/deploy/donkeycar/` contains remote helpers used by CLI deploy flows.
- `scripts/perception/` contains reusable local image-analysis tools.
- `scripts/decision/` contains decision-memory inspection utilities.
- `scripts/calibration/` contains calibration-specific tools.

Deployment commands should go through `./cli/automa`; scripts under
`scripts/deploy/donkeycar/` are implementation helpers.

The CLI access point is:

```sh
./cli/automa
```

Install the local analysis/runtime dependencies into the active Python
environment with:

```sh
python3 -m pip install -r requirements.txt
```

Show the top-level command summary:

```sh
./cli/automa help
```

List active vehicles discovered from configured endpoints:

```sh
./cli/automa vehicles active
```

For simulator vehicles, active means the WS server is reachable, the Play/Chase
frontend is connected, Chase is loaded, and front-view capture responds. The
default output also shows inactive candidates and partial readiness, such as WS
server up but frontend not connected. Use `--active-only` for a terse active
vehicle list, or `--json` for the full machine-readable payload.

Sync the physical PiCar core Donkey/harness bundle using a discovered vehicle
id:

```sh
./cli/automa vehicles update core --id piracer
```

The CLI prepares `deploy/targets/donkeycar/vendor/donkeycar/` automatically from the
pinned DonkeyCar manifest and local patch file before syncing. Use `--dry-run`
to inspect the exact vendor, SSH, and rsync plan before writing anything.

Deploy the separately versioned autonomy controller bundle after core setup:

```sh
./cli/automa vehicles update autonomy --id piracer --restart
```

This verifies a hashed controller archive on the Pi, installs it under a
versioned release directory, and activates the selected perception and decision
manifests. Core updates preserve these autonomy release paths.

Activate the current perception plugin chain for the WS-controlled Chase
simulator controller:

```sh
./cli/automa vehicles update perception --id chase-sim-chaser
```

This stages the local controller bundle under `runtime/vehicles/` and does not
require the simulator frontend to be online unless `--restart` is requested.
Inspect the planned files without writing them:

```sh
./cli/automa vehicles update perception --id chase-sim-chaser --dry-run --json
```

The active algorithm is explicit and defaults to the current mapper:

```sh
./cli/automa vehicles update perception --id chase-sim-chaser --algorithm current
```

Activation writes:

```text
runtime/vehicles/chase-sim-chaser/bundle/runtime/perception/active.json
```

Inspect the active algorithm and the schema it declares for translating sensor
inputs into perception output:

```sh
./cli/automa vehicles info perception --id chase-sim-chaser
```

Run the active automation loop for a simulator vehicle:

```sh
./cli/automa vehicles automation run --id chase-sim-chaser
```

`vehicles active` probes live vehicle/controller endpoints. `vehicles automation
status` is different: it reads the local runtime bundles under `runtime/vehicles`
and reports which automation deployments exist and whether their recorded worker
process is currently running:

```sh
./cli/automa vehicles automation status
./cli/automa vehicles automation status --id chase-sim-chaser
```

For now this loop starts in the background, takes over simulator WS control,
sends idle actions, captures the front-camera sensor, and runs perception every
automation tick. By default it only keeps rolling latest output, overwriting the
latest camera frame and perception files each iteration. It does not persist a
worker log unless `--log` is passed. Use `--frames` for a bounded smoke test:

```sh
./cli/automa vehicles automation run --id chase-sim-chaser --frames 5 --interval-s 0
```

Stream the rolling latest perception view:

```sh
./cli/automa vehicles stream perception --id chase-sim-chaser
```

This redraws the terminal with the current automation control state, perception
cadence/timing, latest frame metadata, and full latest perception text instead
of appending a tail.

Stop or bounce the background automation worker:

```sh
./cli/automa vehicles automation stop --id chase-sim-chaser
./cli/automa vehicles automation restart --id chase-sim-chaser
```

After editing `implementations/perception/` or shared `autonomy/` primitives, restage
the perception bundle and restart the worker so the mapper is imported fresh:

```sh
./cli/automa vehicles update perception --id chase-sim-chaser
./cli/automa vehicles automation restart --id chase-sim-chaser
```

Use `--observe-only` only when you want perception to watch the simulator
without becoming the movement authority.

Use `--record` only when you want a per-frame trace with images and perception
artifacts:

```sh
./cli/automa vehicles automation run --id chase-sim-chaser --record
```

Use `--log` only when you want background worker stdout/stderr appended to
`automation.log`:

```sh
./cli/automa vehicles automation run --id chase-sim-chaser --log
```

It also stages a local controller bundle so the simulator runtime has the same
data shape as a deployed vehicle. The vehicle runtime directory should contain
only that bundle:

```text
runtime/vehicles/chase-sim-chaser/bundle/
runtime/vehicles/chase-sim-chaser/bundle/autonomy/
runtime/vehicles/chase-sim-chaser/bundle/implementations/
runtime/vehicles/chase-sim-chaser/bundle/runtime/perception/
```

It does not modify the simulator source tree. Add `--restart` when the Metrics
UI Play/Chase frontend is open; this re-prepares WS control and writes a sample
`perception.txt` / `perception.json` under the same runtime directory.

Current Pi target defaults to:

```sh
piracer@piracer.local
```

Override it with `--ssh-target` or `PI_HOST` if needed:

```sh
./cli/automa vehicles update core --id piracer --ssh-target piracer@192.168.0.168
```

For first setup or recovery when the Donkey HTTP endpoint is not discoverable
yet, deploy over SSH without discovery:

```sh
./cli/automa vehicles update core --id piracer --skip-discovery --ssh-target piracer@piracer.local
```

## Workflow

Edit files locally, then sync the physical PiCar core harness and restart the
drive server:

```sh
./cli/automa vehicles update core --id piracer --restart
```

If you only want to sync core files without restarting the controller:

```sh
./cli/automa vehicles update core --id piracer
```

The drive server now starts in API/web-control mode by default. Enable the
handheld joystick only when you explicitly want it to be an active command
source:

```sh
./cli/automa vehicles update core --id piracer --restart --drive-args=--js
```

Run the optional static DonkeyCar frontend:

```sh
./frontend/donkeycar/start.sh
```

Open:

```sh
http://localhost:8088/
```

This is only for the static DonkeyCar frontend in `frontend/donkeycar/`.
Simulator UI is handled by Metrics UI through `./cli/automa simulators ensure`.

The static UI uses the Pi as a backend by default:

```sh
http://piracer.local:8887
```

The controller server exposes the latest camera frame:

```sh
http://piracer.local:8887/frame.jpg
```

It also exposes an on-demand sensor-resolution still for mapping:

```sh
http://piracer.local:8887/frame-highres.jpg
```

Convert one image into the current decision-memory frame representation:

```sh
python3 scripts/decision/image_to_memory.py path/to/frame.jpg
```

Capture one Chase simulator front-view frame and inspect it through the same
memory path:

```sh
python3 scripts/decision/image_to_memory.py --sim-current
```

Process one camera still locally:

```sh
python3 scripts/perception/process_still.py
```

This writes debug artifacts under `lab/runs/stills/<timestamp>/`:

- `frame.jpg`
- `floor_mask.png`
- `overlay.png`
- `topdown_rgb.jpg`
- `occupancy.png` as a top-down visibility cone (`dark=unknown`, `green=visible floor`, `red=first blocking non-floor`)
- `occupancy.npy` with numeric cells (`0=unknown`, `1=visible floor`, `2=blocked/occupied`)
- `summary.json`

Track stable visual features between two stills:

```sh
python3 scripts/perception/track_features_between.py image_a.jpg image_b.jpg \
  --bbox x0,y0,x1,y1
```

This writes `matches.jpg` and `summary.json` under `lab/runs/tracks/<timestamp>/`.

Track whole-frame features and group motion-consistent regions without assuming a box:

```sh
python3 scripts/perception/track_scene_motion.py image_a.jpg image_b.jpg
```

This writes `scene_motion.jpg` and `summary.json` under `lab/runs/scene_motion/<timestamp>/`.

Run the phase-1 visual depth calibration entrypoint locally against a saved burst:

```sh
python3 scripts/calibration/piracer_visual_depth_calibration.py analyze-images \
  --image-dir lab/runs/steps/<run>/images-only \
  --pattern 'step_*.jpg'
```

This writes:

- `scene_depth_summary.json`
- `calibration.json`
- `backtest/backtest.json`
- `index.html`
- pairwise `scene_motion.jpg` overlays
- held-out feature prediction overlays under `backtest/*/backtest_prediction.jpg`

Run the same phase-1 calibration from this workspace against the already-running
Pi controller server:

```sh
python3 scripts/calibration/piracer_visual_depth_calibration.py run-full \
  --base-url http://piracer.local:8887 \
  --frame-endpoint /frame.jpg
```

The current phase-1 calibration treats one pulse as the step unit. It estimates visual motion consistency and depth-layer candidates; it does not estimate physical meters, trusted camera intrinsics, or turn radius yet.

The calibration score includes a held-out feature backtest. For each adjacent image pair, the script fits motion hypotheses on most feature matches, predicts held-out feature matches, and scores the pixel residuals. This is a same-run fit check, not an independent validation run.

## Autonomy Layering

Autonomy is organized outside DonkeyCar. Donkey is only the device adapter for
camera capture and drive pulses.

High-level onboard autonomy diagram:

```text
docs/onboard-autonomy-flow.html
```

Layering:

- `autonomy/vehicle/` owns black-box vehicle input/output contracts and
  capabilities.
- `autonomy/perception/` owns stable sensor-to-evidence contracts and mapper
  assembly.
- `autonomy/decision/` owns the staged controller cycle and observation shapes.
- `autonomy/runtime/` owns loadable engine contracts and lifecycle management.
- `implementations/vehicle/` owns concrete PiCar and Chase simulator adapters.
- `implementations/perception/` owns concrete perception algorithms.
- `implementations/operations/` owns bounded procedures such as startup action
  checks.

Dependency direction:

```text
autonomy contracts       -> never import implementations
implementations          -> satisfy and compose autonomy contracts
CLI/runtime entrypoints  -> select implementations and execute the cycle
```

The car boundary uses:

- `VehicleAction`: chase-style executable shape, `forward`, `reverse`, `steering`
- `VehiclePulse`: timed real-world envelope around a `VehicleAction`
- `SensorReadRequest` / `SensorSnapshot`: generic vehicle sensor reads
- `CarInterface`: `stop`, `execute_action`, `execute_pulse`, `read_sensors`
- `DonkeyPiCar`: PiCar/PiRacer implementation backed by the Donkey web server

The only current sensor exposed by both the PiCar and Chase simulator adapters
is `front_camera`. All vehicle sensor access goes through `read_sensors`.

The one reusable controller cycle lives in `autonomy/decision/cycle.py`.
Vehicle-specific entrypoints adapt their sensor and actuator transports to its
generic inputs and outputs.
Use the CLI for the canonical vehicle discovery snapshot:

```sh
./cli/automa vehicles active
./cli/automa vehicles active --json
```

Run the current automation path against the Chase simulator:

```sh
./cli/automa simulators ensure
./cli/automa vehicles update perception --id chase-sim-chaser
./cli/automa vehicles automation run --id chase-sim-chaser --frames 3
```

The simulator automation path stages a controller bundle under `runtime/`,
takes over WS control by default, and uses the active perception mapper.

Run the modular startup action-registration check against the physical PiRacer:

```sh
./cli/automa vehicles operation startup-check \
  --id piracer \
  --throttle 0.22 \
  --duration-s 0.3 \
  --settle-s 0.35
```

The same startup plan can run against the Chase simulator:

```sh
./cli/automa vehicles operation startup-check --id chase-sim-chaser
```

This writes a run under `lab/runs/startup-check/<run-id>/` with:

- `plan.json` containing the vehicle-agnostic calibration instructions
- `report.json` containing command metadata and image-change scores
- `summary.md` containing the compact pass/fail table
- `frames/` containing before/after images for every action check

Run the seeded box-face step verifier against a forward-only burst:

```sh
python3 scripts/calibration/step_calibration_box_consistency.py \
  --image-dir lab/runs/calibration/YOUR_RUN_ID \
  --pattern 'frame_*.jpg' \
  --seed-provider manual \
  --seed-quad '[[347,153],[411,153],[413,211],[347,211]]'
```

This tracks one rectangular box face and fits apparent size against pulse index. A VLM can provide only the initial quadrilateral seed; all tracking and fitting after that are classical CV. The preferred VLM provider is MuleRouter with Qwen VL Max:

```sh
export MULEROUTER_API_KEY=...

python3 scripts/calibration/step_calibration_box_consistency.py \
  --image-dir lab/runs/calibration/YOUR_RUN_ID \
  --pattern 'frame_*.jpg' \
  --seed-provider mulerouter \
  --vlm-model qwen-vl-max \
  --vlm-base-url https://api.mulerouter.ai/vendors/openai/v1 \
  --target-description 'the most trackable visible planar surface near the center'
```

For another VLM, use `--seed-provider command`; the command receives `image_path width height target_description` and must print JSON with `quad_uv`, `label`, `confidence`, and `notes`. Use `--seed-json` to rerun the deterministic CV pipeline from an inspected or edited seed without calling a VLM again.

Estimate distance to an automatically selected visual landmark in step units:

```sh
python3 scripts/perception/estimate_landmark_distance.py lab/runs/steps/<run>/summary.json
```

This discovers an expanding motion group from the first step, tracks that landmark across later frames, and writes debug tracks under `lab/runs/landmarks/<timestamp>/`.

Analyze an already-captured step or turn run with pairwise feature tracking:

```sh
python3 scripts/perception/analyze_tracked_sequence.py lab/runs/steps/<run>/summary.json
```

## Runtime Data

The sync scripts intentionally exclude remote Donkey runtime data and the
matching local app data paths:

- `/home/piracer/mycar/data/`
- `/home/piracer/mycar/logs/`
- `deploy/targets/donkeycar/app/data/`
- `deploy/targets/donkeycar/app/logs/`
- `*.pid`
- `*.bak.*`
- Python cache files

That keeps driving recordings and runtime process files from being treated as source.

## Current Calibration

The active car config is in `deploy/targets/donkeycar/app/myconfig.py`, which deploys
to `/home/piracer/mycar/myconfig.py` on the Pi.

- Steering left PWM: `470`
- Steering right PWM: `640`
- Steering center: `555`
- Throttle forward PWM: `-1200`
- Throttle stopped PWM: `0`
- Throttle reverse PWM: `1200`
- Camera type: `PICAM`
- Camera resolution: `640x480`
- Camera frame duration limits: `33333-50000us`
- Camera flip: vertical and horizontal enabled
