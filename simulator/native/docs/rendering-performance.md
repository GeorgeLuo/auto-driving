# Native simulator rendering performance

## Lens overscan

The lens now samples scene content across the full distorted frame. Each
axis keeps its original pitch in the middle two thirds and spreads its outer
samples up to the corner ray required by `1 + barrelDistortion * radiusSquared`.
This keeps the centre projection and bilinear filter and still traces one
primary ray per output pixel. A uniform, native-pitch overscan at the bundled
distortion of 0.12 would trace about 54% more rays per frame. Across all 140
legacy and showcase poses, every pre-JPEG RGB byte matched the previous
renderer in the central 384×288 pixels of each 640×480 frame.

The current 140-frame checksums below use `native-sim-bench --full-only --hash`.
The last two columns give whole-frame median / p90 milliseconds on the same
toolchain and poses, before (`d86c1f91`) and after the lens fix.

| Map | RGB FNV-1a | JPEG FNV-1a | Before (ms) | After (ms) |
| --- | --- | --- | ---: | ---: |
| chaser-depth-obstacles | `94563b7e631b441c` | `ab83fb261b0bb4c5` | 16.85 / 18.07 | 16.25 / 17.13 |
| showcase-boxes | `64e0861e166eadaf` | `e6e27482efa1b349` | 17.89 / 19.37 | 17.92 / 19.71 |
| showcase | `ed3620d7cb95affb` | `200c346636e39e8b` | 24.23 / 28.37 | 24.56 / 29.19 |
| stress | `600406374178b2ec` | `8fca7f5f7348050e` | 28.88 / 33.10 | 29.22 / 33.72 |

| Tour | SHA-256 of 140 JPEGs concatenated in frame order |
| --- | --- |
| showcase-boxes | `fd45a0869755bb4451f4792f36f06b0ad69a5d20d68c713d5298c8f3d9afc2e9` |
| showcase | `6ca0b1ba0b77a7251272f596f08f8f4473385daec8115ea67b019194300df431` |

## Historical consolidated measurements

The paired measurements use an arm64 Release build with Apple Clang
21.0.0 and libjpeg-turbo 3.1.1 on an eight-core Apple M1. Each invocation
warms up with 12 frames and measures all 140 poses per map at 640×480.
The reference uses four scanline workers and the optimized build uses the
default eight workers. Both use the same poses, assets, camera calibration,
and JPEG settings. The timings exclude physics and WebSocket delivery.

Whole-frame time includes rendering, lens sampling, and JPEG encoding.
Each cell gives median / p90 milliseconds.

| Map | Before 1 | After 1 | Before 2 | After 2 |
| --- | ---: | ---: | ---: | ---: |
| chaser-depth-obstacles | 67.03 / 68.75 | 16.74 / 17.38 | 67.18 / 69.64 | 17.30 / 18.66 |
| showcase-boxes | 88.49 / 96.53 | 17.83 / 19.43 | 94.72 / 104.02 | 17.94 / 19.58 |
| showcase | 128.16 / 161.39 | 24.26 / 28.50 | 129.29 / 161.52 | 24.38 / 28.65 |
| stress | 158.01 / 196.76 | 28.89 / 32.79 | 159.09 / 194.63 | 28.81 / 33.20 |

The renderer stage alone took the following median / p90 milliseconds.

| Map | Before 1 | After 1 | Before 2 | After 2 |
| --- | ---: | ---: | ---: | ---: |
| chaser-depth-obstacles | 59.32 / 60.87 | 12.49 / 12.85 | 59.48 / 61.23 | 12.81 / 13.84 |
| showcase-boxes | 79.85 / 87.49 | 13.30 / 14.79 | 85.81 / 94.22 | 13.43 / 14.95 |
| showcase | 120.43 / 153.67 | 19.31 / 23.39 | 122.16 / 153.72 | 19.43 / 23.67 |
| stress | 149.51 / 188.95 | 23.85 / 27.80 | 151.36 / 186.71 | 23.84 / 28.16 |

The four invocations ran sequentially with builds, tours, and tests stopped.
Two `top` samples immediately before the first run showed 94.4% and 94.6%
idle CPU. The 1-, 5-, and 15-minute load averages include the benchmark's own
work once it starts.

| Invocation | Load at start | Load at end |
| --- | --- | --- |
| Before 1 | 4.20, 7.14, 7.48 | 5.47, 7.01, 7.41 |
| After 1 | 5.47, 7.01, 7.41 | 6.14, 7.09, 7.43 |
| Before 2 | 6.14, 7.09, 7.43 | 5.34, 6.69, 7.26 |
| After 2 | 5.34, 6.69, 7.26 | 6.38, 6.86, 7.30 |

The four existing RGB regression fixtures and their renderer IDs match.
On both benchmark repeats, all four maps reproduced the reference's RGB
and JPEG FNV-1a checksums listed in the historical prototype results below.
For both complete JPEG tours, `cmp` matched each frame against PR #592.

| Tour | Matching JPEGs | SHA-256 of JPEGs concatenated in frame order |
| --- | ---: | --- |
| showcase-boxes | 140 | `9a5e12dbd5e2215cfa763a78ae63138adc626f7ea1e8efd996f8fc097c8a9cb8` |
| showcase | 140 | `2850631072f769ba314cd273c4e70711888a6df8b529a52c8fc904b0356c30ff` |

These four-map measurements predate the run 6 merge. The default benchmark
now also runs `maps/room-run6.json`. Use `--map` to select one map.

## Reproduce the consolidated comparison

The consolidated reference is `9b08eda7` (PR #592). Benchmark commit
`3ba83ee6` adds the harness to that renderer. The original research report
came from `0a5c3134` and its measurements are preserved below.

Build `native-sim-bench` and `native-sim-tour` in Release for the benchmark
commit and the consolidated revision (`d86c1f91`), using the same compiler and JPEG library.
For offline CMake configuration, use the `FETCHCONTENT_SOURCE_DIR_*` paths
in the [native README](../README.md). Point `JPEG_INCLUDE_DIR` and
`JPEG_LIBRARY_RELEASE` to the same libjpeg installation if CMake finds
headers and the library in different installations.

```sh
build=simulator/native/build
unset AUTOMA_NATIVE_RENDER_WORKERS
for run in 1 2; do
  for bench in "$build/baseline/native-sim-bench" "$build/native-sim-bench"; do
    uptime
    "$bench" --frames 140 --warmup 12 --full-only --hash || exit
    uptime
  done
done
simulator/native/build/native-sim-bench --map /absolute/path/to/map.json --full-only
```

Clear `AUTOMA_NATIVE_RENDER_WORKERS` to measure the default worker count.
The baseline uses four scanline workers. The current renderer shares one
persistent tile pool across captures and streams. It caches projected shadow
candidates by immutable map identity and lens sampling by configuration and
image size. Shadow samples still use the original bias, ray limit and exact
intersection tests. Grazing lights, extreme bounds and oversized candidate
lists use the original shadow BVH. The [native README](../README.md#verification-and-measurement)
describes when cache entries are replaced.

For JPEG byte comparisons, run both builds from the repository root using the
same map file and empty output directories. The default tour writes 140 frames
in each directory.

```sh
native=simulator/native
build=$native/build
for map in showcase-boxes showcase; do
  before=$build/identity/before-$map
  after=$build/identity/after-$map
  "$build/baseline/native-sim-tour" "$native/maps/$map.json" "$before" || exit
  "$build/native-sim-tour" "$native/maps/$map.json" "$after" || exit
  for frame in "$before"/frame_*.jpg; do
    cmp "$frame" "$after/${frame##*/}" || exit
  done
done
```

## Historical measurement method

The reference is native simulator revision `6c321229` with the benchmark
target added. Measurements used an arm64 Release build (`-O3 -DNDEBUG`),
Apple Clang 21.0.0 and libjpeg-turbo 3.1.1 on a fanless Apple M1 MacBook Air
with four performance cores, four efficiency cores, 16 GB RAM and macOS
27.0.1. CMake used existing local IXWebSocket and nlohmann/json source
directories. No dependencies were downloaded.

```sh
cmake -S simulator/native -B simulator/native/build -DCMAKE_BUILD_TYPE=Release
cmake --build simulator/native/build --target native-sim-bench
simulator/native/build/native-sim-bench --frames 140 --warmup 12 --hash
```

For offline configuration, use the two `FETCHCONTENT_SOURCE_DIR_*` overrides
in the [native README](../README.md). On this machine the JPEG include path
was explicitly set to the same Homebrew installation as the linked library
because CMake also found Mono's JPEG headers.

The harness uses forward throttle 1, steering 0.57 and captures after physics
steps 1, 4, 7, …, 418, like `native-sim-tour`. Short runs select poses evenly
throughout that circuit. Each pass warms up and then starts measuring at the
first pose again. Loading, physics, hashing, file I/O, JSON/base64 and WebSocket
delivery are outside the timed region. Camera output is 640×480, JPEG quality
80 with the library defaults. `--map` selects one scene and `--full-only`
runs only the original rendering path. The output is CSV with wall-clock
medians and nearest-rank p90s.

The stress map keeps the showcase room and full-size props and adds four
groups of the same props scaled to 22% in its corners. It preserves asset
sharing, both lights, the materials and the full tour pose at every step.
It has 5× as many props, 4.7× as many visual parts and 5× as many
instanced triangles. The object count includes four fixed boundary walls
in both maps, so the total is 104 versus 24. It tests more traversal and more overlapping bounds
in the same room. A cluttered room with full-size props around the camera,
larger meshes, and more pixels covered by expensive materials can cost more.

The first pass directly measures the whole frame and its render, sensor and
JPEG stages. Two further passes turn shadow strength to zero and then also
remove textures, including embedded ones. The residual is primary rays,
flat shading and frame setup. The shadow and texture columns are differences
at matching poses. They include changes in scanline load balance and are
sensitive to frequency and scheduling changes between passes. Small negative
texture deltas show the resolution of this method. Independent percentiles
do not add up to the frame percentile.

## Historical baseline

Each cell gives median / p90 milliseconds over 140 timed frames.

| Map | Objects / parts / triangles | Total | Render | Rays and flat shading | Shadows, delta | Textures, delta | Sensor | JPEG |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| chaser-depth-obstacles | 7 / 7 / 0 | 70.3 / 75.3 | 62.4 / 66.5 | 17.9 / 19.0 | 44.2 / 48.2 | 0.2 / 1.4 | 7.3 / 7.7 | 1.03 / 1.20 |
| showcase-boxes | 14 / 14 / 0 | 91.8 / 96.6 | 83.1 / 87.9 | 23.1 / 25.7 | 59.6 / 65.3 | 0.1 / 2.0 | 7.7 / 7.8 | 1.18 / 1.23 |
| showcase | 24 / 54 / 12 | 135.5 / 168.7 | 127.6 / 160.8 | 28.1 / 37.7 | 99.0 / 127.0 | 0.1 / 2.9 | 6.7 / 7.6 | 1.10 / 1.20 |
| stress | 104 / 254 / 60 | 172.3 / 210.1 | 164.0 / 202.2 | 46.4 / 55.1 | 121.2 / 152.6 | −1.0 / 3.7 | 6.7 / 7.8 | 1.11 / 1.28 |

The baseline delivered compute capacity of 7.4 fps on the showcase and 5.8
fps on stress by median, and 5.9 and 4.8 fps at p90. The investigation measured
the full tour, which includes different views and lighting work at each pose.
The earlier 103/217 ms observations are useful as individual observations
without a matched build and sampling method. A separate full showcase pass
in this session measured 141/184 ms.

## Profile

`sample` could not attach to the local process. `xctrace` required a full
Xcode installation. The stage timings above and thread-local operation
counters from a separate 32-pose instrumented renderer provided the profile.
The following are mean counts per frame. BVH nodes include mesh BVH visits.
Primitive calls count the dispatch to a visual part, including parts that
miss and calls into the mesh kernel.

| Work | Showcase | Stress |
| --- | ---: | ---: |
| Camera rays | 307,200 | 307,200 |
| Shadow rays tested | 1.74 million | 1.74 million |
| Shadow BVH node tests | 23.20 million | 32.84 million |
| Shadow primitive calls | 6.20 million | 5.64 million |
| Primary BVH node tests | 4.27 million | 6.73 million |
| Primary primitive calls | 1.52 million | 1.73 million |
| Primary / shadow triangle tests | 32k / 81k | 32k / 81k |
| Texture calls / carpet calls | 288k / 144k | 288k / 144k |

1. **Shadow any-hit traversal and primitive tests** take about 99 ms on the
   showcase and 121 ms on stress. There are four rays per enabled soft
   directional light at surfaces facing it. In the showcase that is up to
   eight per hit, averaging six per shaded hit. Each traverses the top-level
   BVH from scratch even when directions and scene geometry are static.
2. **Primary rays and flat shading** take 28 ms and 46 ms. Binary slab tests,
   leaf candidate dispatch and world-to-local transforms dominate the work
   count. The BVH tests a leaf's group of up to four primitives after a
   single group bound check.
3. **Sensor distortion and vignette** take about 7 ms in a serial pass.
   It truncates the double-color buffer to 8-bit, computes radial addresses
   and gain, bilinearly samples and rounds. After the traversal changes it
   becomes a much larger fraction of frame time.

Textures are a small part of this workload. Half the shaded hits already
use the carpet, and removing all textures saves too little to distinguish
reliably from run-to-run drift. JPEG averages about a millisecond in the
median frame and is a lower priority.

## Options

Short experiments below sampled 32 evenly spaced poses per map with six
warmups. Each row is a trial against a 146/178 ms showcase and 177/211 ms
stress short-run reference unless it names a different parent. Numbers are
whole-frame median / p90 in ms except where marked. Sensor and JPEG timings
sometimes moved even when their implementations were identical. Small
differences and the ranking among close alternatives are inconclusive.
The combined prototype was tested over the full 140-frame tour.

| Option | Result and speed-up | Byte identity | Effort |
| --- | --- | --- | --- |
| Eight persistent workers and 64×16 tiles, with per-worker queues and stealing | 134/174 showcase, 147/178 stress alone. About 1.1×/1.2× versus the fixed four-band `std::async` renderer in that trial. A four-worker pool trial gave 215/317 and 277/420 during a slower run. See the combined test for the attainable throughput. | Independent pixel writes. No material or ray calculations change. | Low–medium. Bound concurrency across captures and test mixed core counts. |
| Keep flat part BVH, one world-space bound per part, and shared mesh BVHs | Current structure. Fine-grained leaf bounds plus the shadow index improved the tiled trial from 32.6/41.4 to 25.4/32.1 ms on showcase and from 39.1/45.6 to 32.9/39.4 ms on stress. Extra checks did not pay on their own on the baseline. | Verified for the combined prototype. Preserve nearest-hit range and original part order on equal hits. | Low–medium. Cache prepared parts and primary BVH by immutable map revision if rebuild time grows. |
| Top BVH over instances and second BVH per set of parts | A world-space two-level proxy gave 141/161 and 188/207. No consistent gain. It adds a bounds traversal that may only help when large repeated assets amortize it. A local-space per-asset tree can share memory and build work, but adds an instance transform and may visit looser world bounds. | Moved bounds and different traversal order need tests at grazing and coincident hits. Preserve mesh triangle tie behavior as well as the part tie-breaker. | Medium for a reusable local-space tree. |
| More expensive surface-area splits | An exhaustive axis/split proxy, built every frame, gave 175/206 and 216/396. No win here. A cached build with binned splits might help denser or larger meshes. | Mesh traversal order can change equal-distance UV/normal selection. | Medium. Measure node counts with preparation moved out of frames. |
| Analytic shapes versus tessellation | Analytic boxes/strips use one slab test. Cylinders and ellipsoids solve quadratics and compute UVs only for accepted primary hits. Meshes already reuse a triangle BVH. Tessellating each box or strip into 12 triangles raised the boxes-only median from 94 to 120 ms and showcase from 141 to 150 ms. Geometry winding was preserved and mesh UV fallback was used. | Triangulation changes starting-inside and edge rules, normals on curved parts, and UVs unless explicitly rebuilt. | Low to keep analytic shapes, medium to build compatible GPU meshes. |
| Directional projected candidate grids | Grid alone 71/99 and 61/92. Grid + eight tiles 33/41 and 39/46. The final version also caches the projected grids and checks individual part bounds. It projects conservatively along each of the original shadow directions and still calls the original any-hit kernels. | All original offsets, distances and four samples are kept. Grid cells contain candidate indices, with no cached lit/occluded bit. Use the BVH fallback for extreme coordinates and slopes. | Medium. Cache by ordered bounds and sample direction. Add boundary tests before production. |
| One hard shadow ray per light | 60/71 and 72/82, about 2.4× faster than the short reference. Median pre-JPEG PSNR 41.2 dB on showcase, 4.3% of pixels changed. | Removes penumbra levels and changes which thin features cast shadows. Requires a separate image baseline. | Low. |
| Shadow maps or cached visibility | Static orthographic depth maps make a sample a lookup. Single-depth or filtered maps have an estimated 5–15 ms potential in the tuned CPU path, depending on filtering and build amortization. Try only for a new visual backend. | Quantization of light-space coordinates, bias and thin/coplanar casters change visibility. A continuous hard visibility cache would need conservative cells and exact fallback near edges. | Medium–high. |
| Float, branchless slabs and NEON | Float BVH bounds 140/176 and 276/549 in noisy trials. Scalar finite-ray slabs without per-axis exits 169/198 and 262/362. Two-lane NEON FP64 XY slab tests with scalar Z 140/216 and 153/185. None demonstrated a repeatable standalone improvement. Float tested the BVH slab math only. | Sampled hashes matched for these variants. Float loses small padding at room scale and can cull a real hit. Retain double intersections, existing FMA policy, and partial-NaN/infinite or parallel-ray behavior. | Low for scalar prototypes, medium–high for a portable SIMD path. |
| Data layout and wider BVHs | Estimate 5–15% of tuned render time if profiling identifies leaf/gather stalls. Store hot shape data or four child bounds together and try coherent ray packets with an exact scalar fallback. | Layout alone is compatible. Changing order of arithmetic or tie resolution needs RGB validation. | Medium. |
| Bake the carpet at load | A 256 texels/world-unit nearest lookup of carpet variation used 22.5 MiB and did not show a frame-time gain (152/230 versus the paired 139/178). It changed 28.7% of pixels, median pre-JPEG PSNR 35.0 dB. | Finite resolution samples the procedural grain at different positions. Leather sheen and cardboard tape require separate view-dependent terms. | Medium for multiple materials and image UV domains. |
| Cache the exact lens map and parallelize output rows | Sensor median fell from 6.7 ms to 3.9 ms with a serial map and to 1.8 ms in the four-worker trial. The table is 11.7 MiB at 640×480 and keyed by dimensions, distortion and vignette. | Same byte truncation, indices, double interpolation order and final rounding. Verified in the combined prototype. | Low–medium. |
| Fuse distortion into primary rays and apply vignette on write | Sensor copy fell to 0.20 ms. The 151.6 ms whole-frame median versus 139.3 ms in the visual control did not show a gain. Distortion math moved into ray generation and edge clamping can repeatedly trace the same edge rays. | A single fractional ray replaces a four-sample interpolation of already truncated RGB. It changes edges, noise sampling and the order of quantization. 39.2% of pixels changed, median pre-JPEG PSNR 32.3 dB. | Medium to wire camera effects through the renderer. |
| libjpeg-turbo settings | Keep default integer DCT, 4:2:0, quality 80, default Huffman tables. On the showcase the stage was 1.10 ms, versus 1.04 with fast DCT, 1.75 with 4:4:4 and 2.67 with optimized Huffman tables. No meaningful frame-time gain. | Fast DCT changed JPEG bytes with identical input RGB, decoded PSNR 48.7 dB against default. Optimized coding changes bytes even when decoded pixels match. Quality and subsampling also change decoded pixels. | Low. |
| 320×240 and bilinear upscale after camera processing | 42.5/55.3 and 45.3/53.6 on legacy software, about 3.3×/3.9× against the short controls. | Small parts and texture frequencies blur or alias. Median pre-JPEG PSNR 30.6 dB and 63.4% of pixels changed on showcase. | Low. Reserve for opt-in fallback. |

The visual comparisons use matching poses and final RGB before JPEG unless
the row specifies decoded JPEG. PSNR uses the 8-bit range and reports the
median across the 32 showcase frames. A large value does not replace exact
RGB and JPEG checks.

## Other backends

These ranges are estimates of warmed end-to-end 640×480 time including
readback or CPU buffer conversion, the sensor and JPEG. GPU estimates assume
static buffers, a few draws or instanced draws, low-cost materials, two
directional depth maps and asynchronous transfer where the capture contract
permits it. They exclude shader compilation, startup, physics and WebSocket
delivery. The saved 135.5 ms showcase baseline is the comparison.

| Backend | Estimated 640×480 time and speed-up | Integration and dependency weight | Repeatability | Headless Linux CI |
| --- | --- | --- | --- | --- |
| Metal rasterization | 9–22 ms, about 6–15× the baseline. Rough budget of 2–10 ms for rasterization, shadows and submission, 1–4 ms for readback and RGB conversion, and 3–8 ms for the camera and JPEG. | Medium–high. Objective-C++ adapter, shader build, tessellated analytic parts, UVs, static mesh uploads and depth maps. System Metal framework and SDK on macOS, no vendored renderer. | A pinned GPU, driver and shader build can be stable across runs. Raster coverage, depth ties, tessellation and shader FP differ from software and can vary across machines. | Metal is macOS-only. Keep software on Linux. |
| Portable rasterization with OpenGL or Vulkan | 9–27 ms on M1-class GPU hardware, about 5–15× the baseline. Headless software Vulkan or GL can be substantially slower. | High. Same geometry, shading and readback work plus platform/device setup and shader packaging. bgfx adds bx, bimg and shader tooling. A header-first layer such as sokol has a smaller footprint, with backend and context support to verify for the selected release. | Expect repeatable results on a pinned runtime. Raster rules, precision, filtering and drivers make cross-device byte identity unsuitable as a contract. | Feasible with offscreen Vulkan images without a window surface, or desktop GL with an appropriate EGL context. Need to verify that the chosen wrapper supports this setup and that CI has a GPU driver or Mesa software driver. |
| Embree CPU ray tracing | Estimate roughly 1.0–1.3× over the tuned software path on these maps, about 25–38 ms on showcase if traversal improves. A mesh-heavy scene with much more time in triangle tests could improve 1.3–3× over tuned software. | Medium–high. Bridge scene geometry and instancing, keep software camera and shading, and use callbacks for analytic intersections or change their representation. Apache-2.0. The local Blender Embree 4 arm64 build is 14 MiB and links TBB. Package an optional system library and its build dependencies. | Immutable geometry and fixed builds are repeatable candidates. Float BVH/ray data, triangle edge/tie rules, SIMD dispatch and ISA differences do not preserve existing RGB or guarantee cross-machine byte identity. Double-precision analytic callbacks still depend on float broad-phase conservatism. | Yes in principle on supported Linux architectures. The observed binary exports CPU ray queries and has no window-system link dependency. Use the Linux build and a smoke test for the selected ISA. |
| Filament | Estimate 10–30 ms, about 5–14× the baseline with a warmed GPU. Generic material and render-graph overhead and synchronous readback can limit the win. | High. Separate engine, backend and shader/material tools. `gltfio` is optional if existing scene assets are uploaded directly. Use unlit/custom shader materials to reproduce the intended simple lighting or accept PBR appearance as a separate mode. | Backend, driver, tone mapping, shadows, sampling and coverage influence results. Expect a new image baseline, pinned for a given test device. | Conditional. Verify a selected build with a headless EGL or Vulkan configuration and offscreen rendering plus readback. Keep software for image regression and fallback. |

The local Metal SDK headers describe texture allocation, shared storage and
blit-to-buffer readback. Blender's local Embree library exports
`rtcIntersect1`, `rtcOccluded1`, `rtcCommitScene` and user geometry callback
registration. No local bgfx, sokol or Filament API docs were available.
Their integration and headless assessments are conditional on the selected
version and are based on the underlying render APIs. No backend was installed,
vendored or timed.

## Historical recommendations and prototype

### A. Software renderer now

1. **Index the four shadow directions for each static light.** Build a
   conservative 2D grid of projected world-space part bounds per sample.
   Traverse each candidate list with the existing double-precision any-hit
   test. Keep the original light samples, bias, range and visibility
   arithmetic. The standalone trial was about 2.1× faster on showcase
   and 2.9× on stress. Medium effort. Verify grazing, occlusion beyond the
   far limit, coplanar geometry and invalidation on map or light changes.
2. **Replace four fixed bands with bounded tiled work.** Eight tiled workers
   plus the index yielded 33 ms showcase and 39 ms stress in the short
   trial, about 4.5× faster than the short reference on both. Keep any
   scene state immutable while tiles run. Low–medium effort. Use one
   bounded executor across concurrent captures and measure the server
   path with a real capture and physics loop.
3. **Cull individual parts before exact intersection.** Keep the flat
   primary BVH and existing mesh BVHs. The tiled short run with both
   shadow and primary leaf bounds reached 25/32 ms on showcase and
   33/39 ms on stress. This is about 1.3× and 1.2× on top of the
   tiled index trial by median. Low–medium effort. Keep the current
   tie-breakers and conservative bounds.
4. **Cache exact sensor addresses, weights and gain.** Preserve both
   quantizations. Parallel sensor sampling reduced its median by about
   5 ms in the short trial. Low–medium effort. A bounded executor can
   serve both stages and cap the number of calibration tables. Keep
   libjpeg-turbo settings and procedural materials as they are.
5. **Measure the real room and close the showcase tail.** Set the acceptance
   targets at 33 ms p90 for the showcase and 67 ms p90 for stress, then
   include concurrent capture and physics. The full quiet run needs about
   11 ms of showcase p90 improvement. Measure worker completion and
   scene preparation first, then try narrower candidate lists or wider
   exact BVH traversal against that profile. Keep the same double
   intersection arithmetic and check image fixtures after each change.

The separate local prototype combines steps 1–4. Results are whole-frame
compute capacity before transport and the 30 Hz stream cap.

| Map | Full prototype median / p90 | Approx. fps at median / p90 | Median speed-up |
| --- | ---: | ---: | ---: |
| chaser-depth-obstacles | 20.6 / 26.0 ms | 49 / 39 | 3.4× |
| showcase-boxes | 24.6 / 33.7 ms | 41 / 30 | 3.7× |
| showcase | 35.4 / 44.4 ms | 28 / 23 | 3.8× |
| stress | 33.1 / 39.4 ms | 30 / 25 | 5.2× |

These are 140-frame results from the run without concurrent test commands.
An isolated 140-frame showcase repeat took 30.9/44.9 ms, or 32/22 fps.
An earlier run alongside validation took 43.1/59.0 ms on showcase and
56.0/78.8 ms on stress. The comparable short experiments demonstrate
about **39 fps median / 31 fps at p90 on showcase** and **30 fps median /
25 fps at p90 on stress**. Budget **28–32 fps median and 22–23 fps at p90
for showcase**, and **30 fps median and 25 fps at p90 for stress**, based on
the quiet full tours. The showcase meets 30 fps by median in one repeat and
misses at p90 in both full runs. A 32-pose differential check on the combined
prototype measured 15.6 ms for primary rays and flat shading and 13.5 ms
incremental for shadows, versus 28.1 and 99.0 ms in the full baseline.

The prototype preserves the four pre-JPEG regression hashes and all 140
boxes-only JPEGs against both the current renderer and the archived
pre-refactor tour. The concatenated boxes JPEG SHA-256 was
`9a5e12dbd5e2215cfa763a78ae63138adc626f7ea1e8efd996f8fc097c8a9cb8`.
Both full prototype runs also match the unmodified renderer's RGB and JPEG
tour checksums on all four maps.

| Map | RGB FNV-1a | JPEG FNV-1a |
| --- | --- | --- |
| chaser-depth-obstacles | `dba7f701d097500d` | `351278cbd08f2f35` |
| showcase-boxes | `3a44bbc9795d56b0` | `221824c342f1081c` |
| showcase | `4f3b16470c633f12` | `f4b2d7ac69947964` |
| stress | `fe63c55eaed48a6e` | `598b233761cca581` |

The local tests establish equality with this toolchain and JPEG library.
Keep the RGB fixture separate from encoding and check the matched JPEG
tour with the same encoder build. Bounds changes still need adversarial
geometry tests and checks on supported Linux builds before adoption.

### B. Alternate renderer timing

Keep software as the reference and default. Once the actual room is
available, repeat the 640×480 benchmark with its assets, capture concurrency
and the same camera contract. If the software misses 33/67 ms p90 or the
room needs significantly higher frame rates, prototype a **Metal renderer**
behind the existing interface. Budget 9–22 ms, about **45–110 fps** compute
capacity, subject to readback and profiling. Give it a new renderer ID and
separate image baselines. Rasterized shadows and analytic silhouettes are
acceptable only for users who opt into that visual mode.

Add **Embree** only if a room profile shows triangle intersection or mesh
traversal dominating after these changes, particularly when headless Linux
throughput matters. Measure with realistic mesh sizes and with analytic parts
in a hybrid scene. Defer the portable raster layer until there is a concrete
Linux GPU requirement. Filament is worth revisiting if physically based
materials become a requirement and justify its material tooling and engine
dependencies.
