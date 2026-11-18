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
