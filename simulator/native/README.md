# Native Chase prototype

A C++17 simulator for Automa's existing `chase-sim` vehicle adapter. It runs
without Chrome, Node, or a Metrics UI checkout. Metrics UI remains the default
backend; this prototype is selected explicitly.

## Build and run

Requires CMake 3.24+, a C++17 compiler, and libjpeg development files
(`brew install cmake jpeg-turbo` on macOS; `libjpeg-dev` on Debian/Ubuntu).
The first configure downloads hash-pinned IXWebSocket and nlohmann/json sources.

From the auto-driving repository root:

```sh
cmake -S simulator/native -B simulator/native/build -DCMAKE_BUILD_TYPE=Release
cmake --build simulator/native/build -j 4
./cli/automa simulators create --backend native
```

If JPEG is not found on Apple Silicon, add `-DCMAKE_PREFIX_PATH=/opt/homebrew`
to the configure command. Use the URL printed by `create` below:

```sh
./cli/automa simulators status --long
./cli/automa hosts create --name native --provider chase-sim --url http://127.0.0.1:5051/
./cli/automa hosts run --name native --observe-only --num-decisions 10
./cli/automa hosts status --name native --long
./cli/automa hosts stop --name native
./cli/automa hosts delete --name native
./cli/automa simulators delete --url http://127.0.0.1:5051/
```

Omit `--observe-only` for a movement-enabled run. The simulator uses the
existing host registration, passive capture, control acquisition, camera
stream, and deletion protection. Existing Automa status calls describe the
compatible execution endpoint as `frontend connected`; no browser is running.
The HTTP-shaped URL identifies the vehicle; the prototype serves WebSocket
control at `/ws/control` and does not serve a dashboard.

## Maps, materials, and runtime changes

Copy `scenarios/chaser-depth-obstacles.json` to author a map. The schema contains
map dimensions and rotated box obstacles, spawn position and heading, vehicle
properties, camera calibration, and procedural material settings. Launch it with:

```sh
./cli/automa simulators create --backend native --config /absolute/path/to/map.json
```

Coordinates and distances use Chase world units. `spawn.headingRadians` is
`atan2(z, x)`: `-pi/2` faces north. Obstacle rotations use Chase's original
convention. Perimeter boxes marked `boundary` are visual geometry; physical
perimeter contact is determined by map dimensions and vehicle radius.
Obstacle dimensions and spawn clearance are validated. Only one chaser is
supported; no evader or internal autonomy is carried into this prototype.

`materials.floor`, `obstacle`, and `wall` are RGB integer triples. `seed` and
`textureScale` change deterministic procedural texture. Arbitrary texture-file
loading is not implemented.

The existing Python control client can configure the running simulator:

```python
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWsClient

sim = MetricsUiWsClient("ws://127.0.0.1:5051/ws/control")
receipt = sim.play_game_command("configure", {
    "vehicle": {"speedUnitsPerFrame": 0.05, "wheelbase": 0.46},
    "camera": {"height": 0.18, "verticalFovDegrees": 65},
    "materials": {"floor": [190, 180, 160], "seed": 7},
})
print(receipt["payload"]["result"])
print(sim.play_game_query("native-state"))
```

While playing, configuration patches merge into one pending update applied
before the next physics step. The acknowledgment gives its configuration
revision and effective frame index. While paused, updates apply immediately.
Every image carries its configuration revision. Changes preserve pose and
epoch, and invalid patches leave the current and pending configurations intact.
Camera captures render an immutable copy of state; observation never advances
the simulation. Simulation continues independently of camera encoding.

For map replacement or reproducible stepping:

```python
import json
sim.command({"type": "pause"}, wait_for_frontend_ack=True)
with open("simulator/native/scenarios/chaser-depth-obstacles.json") as source:
    sim.play_game_command("load-map", json.load(source))
sim.play_game_command("set-chaser-input", {"motion": "forward", "steering": 0.25})
sim.play_game_command("step", {"frames": 60})
capture = sim.play_game_query("atomic-evaluation-capture")
sim.play()
```

`load-map` requires pause, resets pose/input/frame count, creates a new epoch,
and leaves playback paused. `reset` resets the current map and preserves its
play/pause mode. Both end active camera subscriptions; reconnect the host's
run afterwards. Runtime map geometry edits are intentionally expressed as map
replacement, not as a partially applied update.

`native-state` is evaluator/debug information, never a vehicle sensor input.
The optional legacy evaluator reference is reported unavailable rather than
invented. Unsupported commands, actors, formats, and settings fail explicitly.

## Translation and fidelity

Vehicle motion integration, steering sign, swept padded-box collision, and
contact-limited yaw are translated from Metrics UI commit
`0854fd07030ac42fb82dd04f20e66384ed71df58`. Its MIT license is retained in
`METRICS-UI-LICENSE`. `tests/reference.json` contains 470 frames generated by
the original JavaScript simulator, covering straight travel, steering,
rotated obstacles, boundary contact, reverse motion, and idle input.

The default geometry and RC-indoor camera mount/projection parameters match
that source. The software renderer is an approximation: it uses procedural
grain and simple face shading, with no Three.js PBR, shadow maps, exact texture
generation, antialiasing, or ACES tone mapping. Camera/perception equivalence
has **not** been established. This is a motion/protocol prototype, not a
drop-in visual replacement for existing experiment baselines.

The compatibility path retains JSON/base64 JPEG, at most 640x480 and 30 Hz.
Slow readers drop newly sampled frames before encoding when their socket is
backed up; outgoing buffers are bounded. Only `latest-frame` delivery is
supported. There is no lossless-stream mode or binary/shared-memory transport.

Legacy Automa throttle remains directional fixed-speed because its current
adapter quantizes throttle. Direct native commands additionally accept
`throttle` magnitude in `[0,1]`. `speedUnitsPerFrame` and `fps` retain Chase's
semantics: changing FPS changes wall-clock travel speed. This prototype does
not claim a more realistic tire, acceleration, or actuator model.

## Verification and measurement

```sh
ctest --test-dir simulator/native/build --output-on-failure
.venv/bin/python tests/run.py --systems commands -k commands.simulators
.venv/bin/python tests/run.py -k simulators -k RecordedProcessStopTests
.venv/bin/python -m scripts.validation.check
simulator/native/build/automa-native-sim --benchmark
simulator/native/build/automa-native-sim --render /tmp/native-camera.jpg
```

The native systems tests use ephemeral ports and disposable runtime roots.
They exercise real JPEG decoding, passive preservation, steering through the
unchanged adapter, live updates, epoch termination, map replacement, discovery,
host attachment, both run modes, and process deletion. They skip if the native
binary has not been built. CTest uses the checked-in JavaScript reference and
does not require Node. To regenerate that reference from a Metrics UI checkout:

```sh
node --import /path/to/metrics-ui/node_modules/tsx/dist/loader.mjs \
  simulator/native/tests/generate-reference.mjs /path/to/metrics-ui
```

`--benchmark` measures 100,000 physics steps and 30 render-plus-JPEG frames,
without WebSocket transport or Automa. Compare these separately from delivered
camera rate and end-to-end decision latency. Removing internal chaser reasoning
contributes to the physics improvement; this benchmark cannot isolate language
speedup or establish an end-to-end advantage over Metrics UI.
