# Native simulator stack review

Reviewed the combined diff from the merge base of `origin/main` and
`codex/native-sim-assets` (`6c321229`). The consolidated branch starts from
`origin/main` at `60775e19`, including the replay-manifest changes. The review
covers PRs #571, #590, and #591. References below point into the consolidated tree.

## High

1. **Fixed · glTF buffer ranges and missing position data.** The glTF validator
   computes byte ranges using unsigned arithmetic and can inspect indices before
   the importer reads them. An overflowing view offset or accessor count can
   bypass the library's range check. A position accessor with no buffer view
   could also silently produce zeroes and import an invisible mesh. The
   importer checks buffer presence and ranges without overflow before handing
   the data to the validator. Imported positions and UVs require buffer views.
   The scene test covers both overflowing ranges and missing positions.
   [gltf_asset_loader.cpp:19](../../simulator/native/src/scene/gltf_asset_loader.cpp#L19)

## Medium

2. **Fixed · JPEG error cleanup.** On a libjpeg error after initialization,
   `longjmp` could leave the changed automatic compressor and destination values
   indeterminate. Cleanup could leak memory or act on an invalid pointer.
   The compressor and destination now have one owner outside the jump's stack
   frame, which also cleans up if allocating the result fails.
   [jpeg.cpp:12](../../simulator/native/src/sensor/jpeg.cpp#L12)
3. **Fixed · Simulator deletion from a path containing spaces.** When `ps` prints
   the absolute native executable path without quotes, splitting the command as
   shell tokens truncates the path. Deletion could discard a live simulator's
   record without stopping the process. The launcher recognizes its full path
   and exact port at the start of the command before tokenization.
   [process.py:140](../../cli/automa_cli/simulators/process.py#L140)
4. **Fixed · FPS changes used two step intervals.** After a live change such as
   60 Hz to 4 Hz, the scheduler could debit an old 1/60-second tick while the
   first step under the new configuration advanced simulated time by 1/4
   second. The scheduler and the world now use the pending configuration's
   interval for the same next step.
   [server.cpp:160](../../simulator/native/src/server/server.cpp#L160)
   [world.cpp:56](../../simulator/native/src/server/world.cpp#L56)
5. **Fixed · Configuration at wall contact.** Swept contact can place the
   vehicle one rounding unit beyond the exact perimeter. A camera-only change
   could then be rejected as an invalid pose. Perimeter validation allows the
   same small tolerance used for obstacle contact. The motion calculation and
   reference poses stay the same.
   [vehicle.cpp:60](../../simulator/native/src/physics/vehicle.cpp#L60)
6. **Fixed · Unsupported glTF transparency.** A material with `MASK` or `BLEND`
   could import with every texel opaque, adding surfaces and shadows where the
   author left holes. The opaque-only importer now rejects those modes.
   [gltf_asset_loader.cpp:99](../../simulator/native/src/scene/gltf_asset_loader.cpp#L99)
7. **Fixed · Native CI coverage and systems test cost.** The final stack had
   no job that built the C++ targets. A native-only change now runs a separate
   path-filtered build and CTest on Ubuntu. The loopback systems suite remains
   separate. Its status, discovery, and cleanup commands use the in-process
   CLI harness. The `hosts run` calls still exercise the executable and launch
   real workers.
   [native-simulator.yml:1](../../.github/workflows/native-simulator.yml#L1)
   [test_native.py:44](../../tests/systems/commands/simulators/test_native.py#L44)
8. **Deferred · Stream publication can trail a reset acknowledgment.** A frame
   copied before reset can finish encoding and reach a different connection
   after the reset was acknowledged. It carries the old epoch. The Chase
   consumer checks session identity before using decoded pixels, and the
   stream ends on the next epoch check. A strict ordering guarantee needs
   coordination between epoch changes and frame publication. That should be
   addressed alongside the capture and streaming architecture so serving a slow
   socket stays independent of physics.
   [server.cpp:181](../../simulator/native/src/server/server.cpp#L181)
   [camera_stream.py:199](../../implementations/vehicle/chase_sim/camera_stream.py#L199)
9. **Deferred · The WebSocket payload check runs after reassembly.** A local
   client can make IXWebSocket buffer a large message before the application's
   1 MiB JSON check runs. The loopback prototype keeps its pinned transport.
   A pre-reassembly limit belongs in the transport and needs separate
   compatibility and backpressure testing.
   [server.cpp:44](../../simulator/native/src/server/server.cpp#L44)

## Low

10. **Fixed · An empty unsubscribe ID was accepted.** Before subscription,
    the client's stored ID is empty. An empty request ID matched and returned
    an `ended` receipt for a stream that had never started. The handler now
    requires an active subscription as well as a matching ID.
    [server.cpp:135](../../simulator/native/src/server/server.cpp#L135)
11. **Fixed · Renderer dependency check allowed the whole server to bypass
    the registry.** A direct backend include in the capture or runtime code
    would have passed the include-graph test. The registry is now the sole
    cross-layer exception.
    [include-graph.cmake:41](../../simulator/native/tests/include-graph.cmake#L41)
12. **Fixed · Duplicate JSON readers and a test-only loader path.** Config
    and asset reads had separate parsing and error handling. A valid JSON
    prefix followed by trailing content could be accepted. They now use one
    strict core reader. The motion and image regressions use the same
    asset catalog loader as the simulator, so an unusable packaged asset
    is also caught there.
    [json.cpp:8](../../simulator/native/src/core/json.cpp#L8)
    [asset_loader.cpp:53](../../simulator/native/src/scene/asset_loader.cpp#L53)
13. **Fixed · Unused image decoding API and misleading launcher docs.** The
    vendored image loader exposed floating-point output APIs while every
    caller reads RGB bytes. The implementation omits those APIs and preserves
    byte decoding. The simulator status help now describes the launcher
    location for both backends, and the tour docs identify the boxes-only
    map as the regression input.
    [texture_image.cpp:2](../../simulator/native/src/scene/texture_image.cpp#L2)
    [commands.py:63](../../cli/automa_cli/simulators/commands.py#L63)
14. **Deferred · Paused stream updates require a new frame index.** A paused
    configuration changes the revision at the current frame. Publishing that
    revision immediately on the stream would make the current Chase consumer
    reject the duplicate frame index. The README directs paused callers to
    atomic capture. Sending multiple revisions per frame would require a
    protocol and consumer change.
    [server.cpp:188](../../simulator/native/src/server/server.cpp#L188)
    [camera_stream.py:163](../../implementations/vehicle/chase_sim/camera_stream.py#L163)
15. **Deferred · Capture work grows with map detail and client count.** Camera
    capture copies the configuration JSON under the world lock. Each software
    render builds an instance BVH and starts four scanline tasks. Several
    subscribers or a larger room can increase time spent copying state and
    lower delivered camera rate. The immutable scene and renderer interface
    remain available for the renderer-speed work. Map schema and showcase data
    stay as shipped by the stack.
    [server.cpp:72](../../simulator/native/src/server/server.cpp#L72)
    [renderer.cpp:110](../../simulator/native/src/render/software/renderer.cpp#L110)
16. **Deferred · Shadow occlusion depends on light vector length.** For custom
    lights, scaling all direction components changes the shadow ray's origin
    bias and world-space reach, even though the lighting angle is unchanged.
    Defining those distances in world units could change authored looks. Keep
    the existing shading for this consolidation and evaluate normalization
    with renderer and room fixtures.
    [renderer.cpp:35](../../simulator/native/src/render/software/renderer.cpp#L35)
17. **Deferred · Upstream warnings in the pinned IXWebSocket build.** With
    compression off, AppleClang reports unused parameters, private fields,
    and constants in the dependency. Keep upstream sources intact. The
    checked-in cgltf and stb_image headers matched their recorded SHA-256
    hashes and have licenses and revision provenance. Their include paths
    are marked as system headers.
    [CMakeLists.txt:5](../../simulator/native/CMakeLists.txt#L5)
    [cgltf SOURCE](../../simulator/native/third_party/cgltf/SOURCE)
    [stb_image SOURCE](../../simulator/native/third_party/stb_image/SOURCE)
18. **Fixed · A deterministic test assumed temporary files live outside the
    repository.** With `TMPDIR` inside the worktree, the CLI correctly reports a
    repository-relative recording path and the test compared it to an
    absolute fixture path. The assertion compares the resolved directories.
    This lets the suite run with writes restricted to the worktree.
    [test_inspection_recordings.py:123](../../tests/deterministic/cli/memory/test_inspection_recordings.py#L123)

The legacy map obstacle and material fields serve the default Chase map and
the boxes-only baseline. The WebSocket compatibility messages are exercised
by the unchanged Python consumer. Both surfaces remain in use. Native status
adds the executable fields only for native records to preserve the existing
Metrics UI output shape. The status payload is generated for display and
is separate from the persisted simulator record.
[map_loader.cpp:92](../../simulator/native/src/scene/map_loader.cpp#L92)
[status.py:56](../../cli/automa_cli/simulators/status.py#L56)

The process check keeps the two explicit string comparisons for a launcher
command that ends at the port and one followed by more arguments. The
regression test covers the exact port boundary for an executable path that
contains spaces.
[process.py:151](../../cli/automa_cli/simulators/process.py#L151)

## Final review of PR #592

Reviewed the complete change from `origin/main` at `60775e19`, including the
speed, overscan, tour and run 6 commits through `452ef354`. The comparison
build came from a Git archive of `codex/native-sim-consolidated` at that
exact commit. Paths in the first two tables are relative to `simulator/native/`.

### Ranked findings

| Severity | Location | Failure scenario and disposition |
| --- | --- | --- |
| Medium | `src/server/world.cpp:101` | After driving away from spawn, increasing the radius could fit the current pose and invalidate the saved spawn or tour. A later reset or tour would fail. Fixed in `1819424f`. Both immediate and pending updates check start clearance before accepting the larger radius. |
| Medium | `src/render/software/geometry.cpp:176` | Equal-distance triangles with distinct UVs let BVH visit order choose the visible texel. Standard library partitioning can change that order. Fixed in **image fix `70f48973`**. Mesh ties use the first triangle in asset order. The new one-pixel fixture changed from blue (`d94c37186c0dbb96`) to the specified red (`f921091be4163e9e`). All four existing RGB hashes and both complete JPEG tours matched their baselines. |
| Medium | `tools/fit/geometry.py:113`, `tools/fit/estimate.py:102` | A landmark beyond the pinhole edge can be visible through the overscanned lens. The fitter counted it as outside and could favor a pose that still showed the carton. Fixed in `3e28b7f6`. Both checks use distorted output pixels. |
| Medium | `tools/fit/fit.py:184` | Removing an early JPEG shifted later annotations to different photos even when each photo still had its own matching report. Fixed in `3e28b7f6`. Each annotated ordinal is checked against its frame ID before estimation. |
| Low | `tools/fit/estimate.py:24`, `tools/fit/fit.py:76` | Rounding every short command independently could lose a whole interval of motion. Two nearly equal headings on opposite sides of π could also receive a full-turn penalty during final pose selection. Fixed in `3e28b7f6`. Tick counts telescope between key poses and the final scoring uses the same wrapped heading difference as the estimator. |
| Low | `tools/tour.cpp:51`, `src/server/main.cpp:25` | An argument such as `1junk` started a one-step tour, and a mistyped port could select a real port. Fixed in `9c82c0e2` by sharing the benchmark's whole-token, range-checked integer parsing. |
| Low | `src/server/main.cpp:40` | A buffered JPEG write could fail on close after the one-shot renderer had already decided to exit successfully. Fixed in `9c82c0e2`. The writer checks stream status after close, as the tour already does. |
| Low | `docs/room-run6-fit.md:15`, `docs/rendering-performance.md:80`, `docs/room-run6-gaps.md:27`, `README.md:176` | The fit instructions used a personal path and stale measurements. The performance report described run 6 as absent, and the gaps doc attributed carpet and hinge limitations to the wrong mechanisms. Fixed in `3e28b7f6` and `8658c8f6`. Reproduction names the evidence directories, Python dependencies and required historical Git object. The report identifies its four-map timing cohort. |

### Findings carried forward

| Severity | Location | Disposition |
| --- | --- | --- |
| Medium | `src/server/server.cpp:182` | A capture in flight can publish an old-epoch frame across a reset acknowledgement on another connection. Deferred as in finding 8. The consumer checks identity. A wire-order guarantee needs coordinated publication and backpressure handling. |
| Medium | `src/server/server.cpp:48` | The message size check runs after IXWebSocket reassembly. Deferred as in finding 9. A transport-level resource bound needs work and testing in the pinned dependency. The server binds to loopback. |
| Low | `src/server/server.cpp:188` | A paused stream cannot publish a new revision at the same frame index to the current consumer. Deferred as in finding 14. Atomic capture exposes the current revision, while a stream change requires a consumer protocol change. |
| Low | `src/server/server.cpp:72`, `src/render/software/renderer.cpp:134` | Multiple subscribers still copy configuration JSON under the world lock and rebuild the instance BVH. Finding 15 is partly resolved by the shared tile pool, mesh BVHs and shadow grids. Defer sharing complete frame preparation until subscriber and map workloads justify the ownership change. |
| Low | `src/render/software/renderer.cpp:45` | Rescaling an authored light vector changes shadow bias and reach. Deferred as in finding 16. Normalizing it requires a lighting convention and image migration. |
| Low | `CMakeLists.txt:5` | A clean AppleClang build emits warnings inside the pinned IXWebSocket compression code. Deferred as in finding 17 to an upstream dependency update. |

### Concurrency, reproducibility and merge scope

The renderer mutex covers map preparation, cache replacement and completion of
a tile submission. Each tile owns distinct pixels. Workers record exceptions,
finish the generation and join at shutdown, including partial construction.
The renderer retains one shadow cache. Each directional sample falls back to
BVH traversal above 65,536 grid entries, for grazing directions or extreme
bounds. Expired mesh keys are pruned on render. A camera sampler retains one
size per immutable configuration, and a caller holds its sampling map until
interpolation ends. Load, reset, committed configure and relighting through
map replacement all rebuild the relevant cache. Pending configure preserves
the active cache until the step. Cold comparison and concurrent capture/stream
tests cover captures of both the previous and the current configuration at different sizes.

At a pixel centre the largest radial expansion on an axis occurs at a
corner. The outer margin is one third of the half-axis, so multiplying the
relative corner expansion by three in the margin covers that ray. This
calculation also gives zero extension on a one-pixel axis. FOV scales the
resulting rays after this mapping. Tests exercised the 10° and 150° limits,
distortion 0 and 0.5, singleton axes, odd sizes and 640×480. The bundled run 6
tour advanced at every one of its 420 steps.

The review compared every new blob type and size in the PR history and checked
embedded media and the final changed files for duplicates. The largest blob
was the 283,010-byte stb_image header. The three PNGs were the only image
files. The run 6 rug reproduced from a coordinate pattern, and the carton
matched the procedural drawing pixel for pixel after horizontal reflection.
The earlier six-color case label predates run 6. The glTF data URI contains
552 bytes of positions, UVs and indices, fully accounted for by its buffer
views. The annotations contain IDs, coordinates and labels. Camera JPEGs,
reports, fitted blends and generated summaries stayed in ignored build
directories. Both vendored headers matched their recorded SHA-256 values
and carry license and source notices. The JS reference is an intentional
motion fixture. The wall assets serve the named-asset equivalence test, and
the renderer reads the sky material directly.

The native backend remains opt-in and experimental. The fit uses labelled
training observations, approximate rigid geometry, a fixed camera and a fitted
speed gain. It does not independently validate perception or real-world pose.
The corrected fit measured **160.5 → 13.0 px** over 113 paired marks in 15
evaluation frames, plus two support frames. The three settled contacts each
advanced less than 1 nm in a native forward step. We retained the authored
spawn. The new frame-zero estimate differs by one 0.012 m search increment.

The Ubuntu workflow runs one job with a ten-minute cap, at most two parallel
build tasks, path filters and superseded-run cancellation. The benchmark and
motion fitter are opt-in targets. Keeping the full tours, sanitizer run and
private room fit in the offline review keeps routine CI cost bounded.
The operator reported green Ubuntu CI at the incoming head. Local comparisons
used AppleClang on arm64 and one JPEG library. Rebuilding with floating-point
contraction disabled reproduced the five RGB fixtures and both complete tours.
Libm operations and JPEG library versions can still differ between platforms.
The native CI checks pre-JPEG RGB hashes and compares trajectory components
with a tolerance.

### Validation

| Command or comparison | Result |
| --- | --- |
| Release CMake configure and build, with both FetchContent source directories supplied locally | Built the server, tour, tests, benchmark and motion fitter offline. |
| `ctest --test-dir simulator/native/build --output-on-failure` | 4/4 passed, including the layer dependency check. |
| `native-sim-tour` for `showcase-boxes` and `showcase`, then `cmp` on every JPEG | At the default 420 steps, interval 3 and 640×480, the incoming one-worker build and two four-worker review runs each produced exactly 140 files per map. All matched, including the SHA-256 values in the performance report. |
| ThreadSanitizer build, `ctest -R native_sim_scene`, and tile-pool exception probe | Concurrent captures/streams passed. All 36 throw, empty-generation and reuse cycles completed, including shutdown with 1, 4 and 16 workers. |
| `-ffp-contract=off` regression and tour builds, plus extreme camera spot checks | All five RGB fixtures and 280 JPEGs matched. Twenty FOV, distortion and size combinations also matched between one and four workers. |
| `python simulator/native/tests/test_fit.py` and `python simulator/native/tools/fit/fit.py` | Three synthetic regressions passed. Full offline fit completed on the original recording. |
| `python tests/run.py` | 1,204 passed. |
| `python tests/run.py --systems commands -k simulators` | 12 passed, including all five native tests. The default sandbox denied localhost binds. Both Python suites passed under a loopback-only profile with external networking disabled and writes restricted to this worktree. |
| `python -m scripts.validation.check` | Zero blocking findings. Dispositions for all 32 questions follow. |

### Python quality review questions

Each row gives the check's file and line. Simulator modules are under
`cli/automa_cli/simulators/`. The boundary test is
`tests/deterministic/cli/test_module_boundaries.py`.

| Location | Check | Disposition |
| --- | --- | --- |
| `process.py:151` | Introduced conditional | Retain. Matching either the end of the executable/port string or a following space handles unquoted paths and enforces the port boundary. |
| `status.py:64` | Introduced omitted key | Retain. The command emits backend-specific display fields for native records and preserves the existing Metrics UI status shape. |
| `process.py:43` | Caught exception | Retain. A non-child PID cannot be reaped with waitpid. Execution continues to the liveness check. |
| `process.py:137` | Conditional | Retain. An override needs an interpreter and script before the script prefix can match. |
| `process.py:178` | Conditional | Retain. Both integer checks guard the subsequent process identity test before signalling. |
| `render.py:21` | Conditional | Retain. The game-specific status line needs a connected frontend and a game name. |
| Boundary test line 55 | Conditional | Retain. Match the exact package or a dotted descendant, excluding unrelated prefixes. |
| Boundary test line 79 | Conditional | Retain. Import edges connect known, distinct modules. |
| Boundary test line 193 | Conditional | Retain. This rule rejects a shared module's import outside shared. |
| Boundary test line 195 | Conditional | Retain. The provider-to-deployment rule needs both endpoints. |
| Boundary test line 197 | Conditional | Retain. Only the named run modules trigger the direct JSON-file import rule. |
| Boundary test line 213 | Conditional | Retain. Only a feature's own assembler can reuse its command handlers. |
| Boundary test line 226 | Conditional | Retain. Each diagnosed edge emits its reason once. |
| Boundary test line 242 | Conditional | Retain. The package root has no child component and needs its own name. |
| Boundary test line 305 | Conditional | Retain. A single leading underscore identifies private imports while allowing dunder names. |
| Boundary test line 346 | Conditional | Retain. The group graph uses cross-group edges between known groups. |
| Boundary test line 387 | Conditional | Retain. The explicit AST shapes distinguish permitted metadata from executable package initialization. |
| `tests/deterministic/cli/memory/test_inspection_recordings.py:254` | Text assertion | Retain. The CLI error names the affected frame and explains the missing image or absence reason. |
| `render.py:62` | Complexity | Retain. Distinct create/delete outcomes have distinct user-facing messages. |
| Boundary test line 190 | Complexity | Retain. Each architectural rule yields its own actionable reason. This PR adds one module classification. |
| `process.py:53` | Related exit waits | Retain. The simulator wait also reaps a child it started. |
| `process.py:62` | Related stop paths | Retain. Startup rollback owns the just-created process group. Recorded deletion checks identity again before escalation. |
| `process.py:90` | Related launch functions | Retain. Simulator startup waits for a URL, writes the simulator record and rolls back failed startup. The host launcher returns a worker process. |
| `process.py:127` | Related Metrics UI helpers | Retain. Matching an existing process and checking a checkout's readiness use different inputs and produce different results. |
| `process.py:140` | Related host command matchers | Retain. Native executable/port, supervisor module/host name and automation worker signatures need distinct identity checks. |
| `process.py:167` | Related cleanup paths | Retain. Recorded deletion owns record removal and reports exited, stopped or killed. |
| `render.py:12` | Related status formatting | Retain. Vehicle and simulator commands already reuse these lines. |
| `render.py:50` | Related status blocks | Retain. Shared lines are factored out. Launcher path, configuration and log belong to long simulator status. |
| `service.py:31` | Related create/delete | Retain. Creation checks launcher readiness, resolves configuration and allocates a port. |
| `service.py:65` | Related delete/create | Retain. Deletion normalizes the URL and checks attached hosts before stopping the recorded process. |
| `service.py:105` | Related launcher helpers | Retain. The checkout validator checks filesystem prerequisites and gives startup diagnostics. |
| `process.py:178` | Inline field rule | Retain. The final signal guard accepts only exact integer PID and port values and can discard a stale record without signalling. It is outside the autonomy document parser. |

**Verdict: yes, once PR #592 includes the local review commits and Ubuntu CI
passes on that head.** The tour comparison, experimental label and deferred
transport and fidelity limits apply to this integrated revision.
