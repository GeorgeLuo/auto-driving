# Native Chase prototype

A C++17 simulator for Automa's existing `chase-sim` vehicle adapter. It runs
without Chrome, Node, or a Metrics UI checkout. Metrics UI remains the default
backend; this prototype is selected explicitly.

## Build and run

Requires CMake 3.24+, a C++17 compiler, and libjpeg development files
(`brew install cmake jpeg-turbo` on macOS; `libjpeg-dev` on Debian/Ubuntu).
The first configure downloads hash-pinned IXWebSocket and nlohmann/json sources.
For an offline build, point `FETCHCONTENT_SOURCE_DIR_IXWEBSOCKET` and
`FETCHCONTENT_SOURCE_DIR_JSON` at existing source copies. The glTF and image
loaders use the pinned headers in `third_party/`.

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

Copy `maps/chaser-depth-obstacles.json` to author a map. The schema contains
map dimensions and rotated box obstacles, spawn position and heading, vehicle
properties, camera calibration, and procedural material settings. Launch it with:

```sh
./cli/automa simulators create --backend native --config /absolute/path/to/map.json
```

Coordinates and distances use Chase world units (roughly metres). `spawn.headingRadians` is
`atan2(z, x)`: `-pi/2` faces north. Obstacle rotations use Chase's original
convention. Perimeter boxes marked `boundary` are visual geometry; physical
perimeter contact is determined by map dimensions and vehicle radius.
Obstacle dimensions and spawn clearance are validated. Only one chaser is
supported; no evader or internal autonomy is carried into this prototype.

`materials.floor`, `obstacle`, and `wall` are RGB integer triples that override
the matching named materials. `seed` and `textureScale` set the grain parameters
for `floor` and `obstacle`. These fields work in map files and in the existing
`configure` command. `native-state` reports the supplied JSON and its patches.

A map can also use `map.instances` to place named objects from
`assets/objects/`. Each entry supplies `id`, `asset` (the filename stem),
`x`, `z`, and `rotationRadians`. An optional `material` selects a named
material for every part. An optional `color` (RGB integers) replaces the base
color of each part's selected material and keeps its texture. A map can mix
`obstacles` and `instances`, with up to 1,000 objects combined. Use
`map.floorMaterial` to select a named floor material.

`map.light` can specify `ambient` (0 to 4) and `directionals` (one to eight
lights). Each light accepts `direction` (an object with `x`, positive `y`,
and `z`), `intensity` (0 to 4), `shadowStrength` (0 to 1), and
`shadowSoftness` (0 to 1). Softness sets the X/Z spread of four shadow rays
around the supplied direction. Zero uses one hard-shadow ray. In this form,
ambient defaults to 0.4 and directional intensity to 1. The original
`map.light` form accepts `direction`, `shadowStrength`, and
`shadowSoftness` without `ambient` or `directionals`. Omitting lighting
keeps the original face shading, direction `(-0.5, 1, 0.25)`, shadow strength
`2.4 / (0.9 + 2.4)`, and softness `0.002`.

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
with open("simulator/native/maps/chaser-depth-obstacles.json") as source:
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
that source. The software renderer ray-casts visual parts and meshes through
bounding volume hierarchies. It shades surfaces using material textures and
the map's lights, with a four-ray soft-shadow approximation. The camera model clamps and truncates linear RGB to
the original native code values, then applies clamped bilinear resampling, the
smoothstep vignette, and JPEG encoding. The source GPU uses a linear render
target with tone mapping and color conversion after sampling. Camera/perception equivalence
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

## Native layout and extension points

```text
simulator/native/
  assets/
    materials.json
    objects/
    textures/
  maps/
    chaser-depth-obstacles.json
    showcase.json
    showcase-boxes.json
  src/
    core/                Shared math, JSON, image types, and seeded noise
    scene/               Map, assets, materials, light, and loaders
    physics/             Vehicle motion and contact
    render/              Renderer interface
      software/          Ray casting and shading
    sensor/              Camera effects, RGB bytes, and JPEG
    server/              Runtime, Chase protocol, renderer registration, and executable
  tests/
  third_party/
  tools/
```

The source includes follow this dependency direction. CTest checks every local
include against these edges and keeps renderer implementations behind the
interface.

| Directory | May include |
| --- | --- |
| `core` | Standard and external headers |
| `scene` | `core` |
| `physics` | `scene`, `core` |
| `render` | `scene`, `core` |
| `sensor` | `core` |
| `server` | `physics`, `render`, `sensor`, `scene` |

- **Add a material.** Add an entry in `assets/materials.json` with a unique
  name, an integer RGB `baseColor`, and a `texture` object. Use `none` for a
  flat color, or `grain` for the original seeded grain. The other procedural
  kinds are `carpet` (pile and mottling), `cardboard` (corrugation),
  `leather` (creases and sheen), `wood` (grain), `fabric` (weave), and
  `patterned_rug` (diamonds and a border). Each procedural kind takes an
  unsigned 32-bit `seed` and a `scale` from 0.01 to 100. Larger scales
  stretch the pattern. The six newer kinds also accept `strength` (0 to 1,
  default 1). Cardboard accepts boolean `print` and `tape` decals.
  For an image, use `{"kind":"image","file":"textures/label.png"}`. The path
  is relative to `assets/`. The image repeats in UV space and multiplies
  `baseColor`. Its optional `scale` defaults to 1. Reference the material
  name on a visual part, on an instance, or in `map.floorMaterial`.
- **Add a map.** Copy a file in `maps/`, give it a new `scenarioId`, and
  set its dimensions, spawn, objects, floor material, and lights. See
  `maps/showcase.json` for named instances, color and material overrides,
  and two directional lights. Use `--config FILE` at startup or pass the
  map document to `load-map` while paused.
- **Add an object.** Add `assets/objects/NAME.json` with a `visuals` array.
  Use `type` of `box`, `cylinder`, `ellipsoid`, or `strip`. Dimensions
  `width`, `height`, and `depth` span X, Y, and Z. A cylinder has an
  elliptical footprint. Use small dimensions on two axes for a long strip,
  or a small height for a floor covering. The local `x`, `y`, `z` offset
  puts the part's base center relative to the instance. `rotationRadians`
  sets yaw. Optional `pitchRadians` and `rollRadians` rotate around local
  X and Z. The part rotates around its base center, in roll, pitch, then yaw
  order. Use pitch or roll on a thin box for a lid flap. Provide a named
  `material` on each part. Image UVs span each box or strip face, wrap the
  curved parts, and map the floor across the room.
  Set `collision` to a separate centered box footprint with
  `{"type":"box","width":0.5,"depth":0.4}` or `null` for a visual-only
  object. Place the object in `map.instances` with `"asset":"NAME"`.
- **Add a triangle mesh.** Place `NAME.gltf` or `NAME.glb` in
  `assets/objects/`. The loader takes rigid, uncompressed triangle lists
  from the default scene (or the first scene), applies node transforms,
  and interpolates `TEXCOORD_0`. Mesh coordinates use Y up and the glTF
  origin is the map instance's placement point. Materials shade with the
  glTF base color factor and its optional base color image, or select a
  named simulator material with `"extras":{"nativeMaterial":"NAME"}` on
  the glTF material. Image overrides work best with authored UVs. Meshes
  without UVs use a local X/Y projection. The loader is for static, opaque
  geometry with geometric normals and base-color shading. Set an optional
  box footprint in `asset.extras.collision` using the same fields as a JSON
  object. See `assets/objects/tool-case.gltf`.
- **Add a renderer backend.** Put its C++ sources in `src/render/NAME/` and
  implement `render/renderer.hpp`'s `Renderer`. Use the scene's visual parts,
  meshes, materials, and lights. Return row-major linear RGB
  with 255 as reference white, and give the backend a stable `id()` for
  captures. Register the backend's name and factory in
  `src/server/renderers.cpp`. CMake discovers its `.cpp` files. Launch the
  native executable with `--renderer NAME`. The default is `software`.

## Verification and measurement

The tour drives a fixed circle and writes deterministic JPEGs using the same
camera pipeline as the server. With the showcase spawn and steering, the
circle radius is about 1.2 m. From `simulator/native/`:

```sh
build/native-sim-tour maps/showcase.json build/tour/showcase
build/native-sim-tour maps/showcase-boxes.json build/tour/boxes
```

The default run advances 420 steps and captures every third step at
640x480. It writes `frame_00000.jpg` through `frame_00139.jpg` in the output
directory and reports mean renderer time with the software renderer's four
scanline workers. Optional arguments after the output directory set steps,
capture interval, steering, width, and height in that order. Use an empty
output directory if a previous run wrote more frames. The box-only map is a
byte-for-byte copy of the original tour input for regression comparisons.

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
binary has not been built. CTest checks the JavaScript motion reference, four
pre-refactor RGB hashes and renderer IDs, and 40 pre-refactor trajectory poses.
It also checks shape and mesh rendering, textures, instance overrides, light
configuration, clearance along the showcase tour, and the camera pipeline.
To regenerate the JavaScript reference from a Metrics UI checkout:

```sh
node --import /path/to/metrics-ui/node_modules/tsx/dist/loader.mjs \
  simulator/native/tests/generate-reference.mjs /path/to/metrics-ui
```

`--benchmark` measures 100,000 physics steps and 30 render-plus-JPEG frames,
without WebSocket transport or Automa. Compare these separately from delivered
camera rate and end-to-end decision latency. Removing internal chaser reasoning
contributes to the physics improvement; this benchmark cannot isolate language
speedup or establish an end-to-end advantage over Metrics UI.
