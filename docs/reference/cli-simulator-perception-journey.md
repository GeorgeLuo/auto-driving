# Chase Simulator-to-Perception CLI Journey

This is the supported operator path from a local Metrics UI URL to a browser
view of Automa's camera and perception output. The primary path attaches to the
Chase vehicle already exposed by the frontend. It does not select a scenario,
start playback, change the control source or input, or apply vehicle control.

Use `help` to descend through command groups and `--help` for the final command:

```sh
./cli/automa help
./cli/automa vehicles help
./cli/automa vehicles automation help
./cli/automa vehicles automation run --help
```

## Primary Journey

Run these commands from the repository root with the environment from
[README Setup](../../README.md#setup) activated. This example selects packaged
perception and memory presets. For an existing tuned deployment, stage your
intended selections instead: each update replaces its named step's configs.
`runtime/` persists across branch changes; incompatible activations must be
explicitly restaged using the recovery that `vehicles automation status` prints.

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

./cli/automa vehicles automation status --id chase-sim-chaser

./cli/automa vehicles automation stop --id chase-sim-chaser

./cli/automa vehicles automation status --id chase-sim-chaser
```

`vehicles status` is read-only and is the one status overview. It prints a
heading for the simulator and for each vehicle host, with details indented
below it:

```text
Simulator  running at http://localhost:5050/
  frontend connected, game chase

Host chase-sim-chaser  running, observe only
  120 decisions, 0 errors, last frame 0.3s ago
  attached since 2026-10-23 16:27:55
  http://127.0.0.1:54665, pid 50057

Host chase-sim-follower  not answering
  no answer within 2.0s
  not attached to this simulator (local host record)
  http://127.0.0.1:54701, pid 50122
```

The simulator is running when its Metrics UI server answers, whether or not a
browser has it open; `frontend not connected` (or `not answering`) says no
browser is serving it.
When it is unreachable, the next line says why. The hosts are those Metrics UI
lists as attached agents (`list_agents`), every local host record whose process
is alive, and each staged PiCar connection. Status reads every host's
`/autonomy/status` in parallel, within the one `--timeout-s` deadline, so a
host that never answers does not hold up the others. A host's state is its
session state (`not started`, `starting`, `running`, `stopping`, `finished run`,
`error`), followed by the mode of a run that has control (`observe only`,
`autonomy`). `finished run` (JSON `finished_run`) means its last run was stopped
or reached its limit. Otherwise the state is `not answering` with the reason, or
`error` when its reply is not a runtime host status; its last error prints
below. When the simulator cannot list agents, the line `cannot list attached
hosts` says why and local hosts are still shown. `--id` shows only that
vehicle's host, or `not found`. The JSON is `automa_simulator_status_v1`.

An HTTP(S) Metrics UI origin is resolved to the
same origin at `/ws/control`; an explicit non-root WS(S) path is preserved.
Use either `--chase-url` or the compatibility option `--chase-ws-url`, not
both. When neither is supplied, Automa uses `CHASE_UI_WS_URL` when set and
otherwise connects to `http://localhost:5050`.

`update memory` stages the memory preset and `update perception` the observer.
Without them, an otherwise fresh cycle runs no memory or perception plugins,
and `automation run` starts the same way. Each update writes only its own step; absent observation, plan and action activations run
their built-in plugins. Neither update command starts a worker, applies
movement, or requires a live simulator. `vehicles automation status` reports
invalid staged steps, and `automation run` checks them before it starts.

`automation run --observe-only` preserves the current scenario, playback
state, control source, and input. It succeeds only after one camera frame, the
perception result for that frame, and the loopback view health all agree on the
same live worker generation. `--open-view` is explicit. A browser-launch
failure leaves the healthy worker running and prints the URL for manual use.

`--unbounded` starts an unbounded background worker in either mode and overrides
`--num-decisions`. The launch command returns once the correlated view is ready
and prints `automation stop`, which stops the worker. Without `--unbounded`,
a movement-enabled run requires a positive `--num-decisions N`.
Observation-only runs also retain `--num-decisions 0` as an unbounded option.
Ctrl-C in a terminal stream stops that stream, not the worker. For a passive
restart, keep `--observe-only` explicit:

```sh
./cli/automa vehicles automation restart --id chase-sim-chaser --observe-only --num-decisions 0 --open-view
```

Stopping the worker keeps its local deployment staged and makes its previous
view unavailable or stale, never current.

## Chase v1 Forward Baseline

Perception, memory, proposal and plan each provide `chase-v1-detect-by-color`. Together
they detect and retain visible simulator cardboard boxes, find the walls,
report sustained visual stagnation and image-space clearance, and drive
straight forward into whatever is ahead. A collision (a static view with
cardboard or a wall in sight) starts the `avoid_obstacle` recovery: reverse
until no near box reaches the right of the image, then turn right until no near
box is ahead, then drive forward again. If the car cannot reverse (the view
stays static for more than 8 cycles), it turns right anyway, and a stalled turn
reverses again, so a corner becomes a multi-point turn. A static view with
nothing in sight still reverses with `drive_backwards`. The straight commands
are steering `0.0` with normalized throttle `0.60` or `-0.60`; the turn is
steering `1.0` with throttle `0.60`.

Memory's `stop_row` learns to start that recovery before contact. The first
collision sets a hypothesis: start reversing once the lowest lower edge of a
box ahead (`clearance_ahead`'s `nearest_bottom_y`) reaches image row `0.79`,
halfway between `min_stop_row` `0.6` and `blind_row` `0.98`. Each stop at the
hypothesis tests it. The car keeps rolling briefly after it starts reversing,
so the lower edge keeps descending; if it reaches `blind_row` (the base has
left the image, which happens just before contact) the row was too close, and
if it stays above until the car starts turning, or for `settle_ms` (one second)
of reversing, the car stopped short. Rows seen while turning do not count: the
turn sweeps other boxes into the ahead band. A head-on approach crosses the
stop row long before the view is static, so a later collision was not head-on;
it is counted but does not move the row. The next row is the
midpoint between the deepest row that stopped short and the shallowest that
came too close, until they are within `resolution` (`0.02`); then the car keeps
the deepest row that stopped short. A row that fails after passing, or passes
after failing, discards the older bound, so the hypothesis can move back when
conditions change. The hypothesis lives in shared memory, so restarting the
host or resetting memory forgets it.

Memory's `turn_model` changes no command. It learns, from ordinary driving,
how the car's own turn moves the boxes `sim_cardboard_permanence` tracks: each
landmark's bearing is its box centre's offset from the middle of the image, in
image widths, and each new camera frame contributes the median shift of the
landmarks seen at both ends, filed by the applied right turn or straight
drive over that interval. A left turn is not learned. A box touching the image
edge, or one that changes size by more than 1.5×, does not count, because the
detector splits a box leaving the image and identities follow the pieces. Two
slow turning intervals in a row are counted as a stall instead of learned.
Landmarks that leave the view keep a bearing carried by the learned speed, so
one seen again measures how far off it was. It knows no degrees, camera or
map, only widths per second and its own errors, so it should transfer to
another car and camera unchanged.

Memory's `approach_model` also changes no command. While the car drives
straight, the lower edge of a box ahead steps down the image one row at a
time, slowly near the horizon and quickly near the car; it times each
one-row step, on 120 rows and the camera's capture clock, and keeps each row's
median. It also times each straight run that ended with `avoid_obstacle`
backing up, and keeps their median as `run_ms`, how long the car usually
drives before it must stop. Perception's `closeness` adds the learned row
times from a box's lower edge down to `stop_row`'s row: that is the box's
reach. It emits `closeness_ahead`, `closeness_left` and `closeness_right`,
`near` when the nearest box in that band would be reached within one usual run
and `far` otherwise, or no value until the rows between are learned. A row
nobody has timed borrows its learned neighbours. Both numbers are the car's
own, so nothing is tuned for this arena. When the box ahead is near,
`avoid_obstacle` turns toward the side whose nearest box has more reach (an
empty side has the most), right on a tie, until ahead has as much reach as
that side.

Perception's `sim_wall` plugin finds wall pixels by saturation, which lighting
does not change: the walls are less saturated than the wood floor and more than
the gray sky. It splits the image into 16 vertical strips and emits one box per
strip over that strip's longest run of wall rows, so each box's lower edge is
where the wall meets the floor.

Perception's `clearance` plugin emits `clearance_ahead` and `clearance_right`:
each is true when no `sim_cardboard_obstacle` or `sim_wall` box overlaps that
horizontal band of the image with its lower edge at or below the band's
`*_bottom_y`. The ahead band also counts farther boxes, so the turn continues
until the obstacle has left the centre. Each signal also reports
`nearest_bottom_y`, the lowest lower edge of any box overlapping the band,
which is the row `stop_row` learns. It has no depth or vehicle width, so a
future version could remember how much clearance a turn actually needed.

Perception's `view_stagnation` plugin emits `view_static` and publishes its
timestamped result at `shared_memory["perception.view_stagnation"]`. The default
requires at least three fresh captures spanning one second below the visual
change threshold. Missing/repeated input clears the cue, and the plan drives
forward. See the
[plugin contract](../../implementations/decision_cycle/perception/plugins/view_stagnation/README.md).

With the running simulator exposed at `http://localhost:5050`, stage the four
steps and start a bounded movement run:

```sh
./cli/automa vehicles update perception --id chase-sim-chaser --preset chase-v1-detect-by-color
./cli/automa vehicles update memory --id chase-sim-chaser --preset chase-v1-detect-by-color
./cli/automa vehicles update proposal --id chase-sim-chaser --preset chase-v1-detect-by-color
./cli/automa vehicles update plan --id chase-sim-chaser --preset chase-v1-detect-by-color
./cli/automa vehicles automation run --id chase-sim-chaser --num-decisions 60 --record
```

If the game's pause-before-actions debug setting is active, enable **Playback**
under **Game → Simulation** to let frames advance during the movement run.

For a simulator on another port, set `CHASE_UI_WS_URL` in the command
environment, for example `ws://localhost:5054/ws/control`, before staging and
running. `vehicles status --chase-url` only inspects that endpoint; it does not
persist a different runtime endpoint.

The car always hits its first obstacle; only later obstacles can be avoided
before contact. Clearance and the stop row are image rows, not distances: they
know nothing of the car's speed or width, and the stop row does not cover
contact during the turn. Once the bisection
converges the hypothesis does not explore deeper rows again unless a stop comes
too close.

## Upload to an Existing Catalog

With a Chase worker and its viewer open, upload a local plugin file separately
from selecting it:

```sh
./cli/automa vehicles plugins upload \
  --id chase-sim-chaser \
  --file ./prototype.py \
  --step perception \
  --plugin-id prototype \
  --entrypoint prototype:Prototype

./cli/automa vehicles plugins status --id chase-sim-chaser --step perception
```

The command reports success after the source is stored and its revision is
registered, and prints the next verification command. `plugins status` reads the
live catalog and reports its version, plugin IDs, filenames, and revisions;
use `--json` for the complete receipt or catalog snapshot, including the target,
outcome, and recovery action. `plugins list` remains an alias for `status`. `plugins help`
describes the file identity arguments and the existing-host precondition.
Open **Plugins** in the runtime viewer to see the available
revision. Upload does not change the selected or running plugins. Source that
does not compile is refused with its file and line. The file is not imported,
constructed, or checked for dependencies during upload; arming loads it.
Add `--config '<JSON object>'`, for example `--config '{"throttle": -0.6}'`,
to store constructor settings with the revision; arming constructs the plugin
with them, and `plugins status --json` reports them.

Use `--url <viewer-or-workbench-url>` instead of `--id` to address a catalog
directly. The same command and `/api/plugins` endpoint apply to Chase, PiCar,
and the replay workbench. Workbench plugin panels refresh their catalogs even
before a replay starts or after it finishes. Dependent files can be uploaded
in sequence without resolving each other during registration.

Uploaded source lives outside `implementations`. Re-uploading an ID makes a
new source revision available while previous selections retain their source.
Registrations belong to the live catalog; restoration after a host restart is
not guaranteed. A Chase worker currently ends its catalog and viewer when the
worker exits. Keeping that host alive between automation runs is separate work.

## Arm on an Existing Runtime Host

After uploading, request the available revision without restarting the worker:

```sh
./cli/automa vehicles plugins arm --id chase-sim-chaser --step perception --plugin prototype
./cli/automa vehicles plugins status --id chase-sim-chaser --step perception
./cli/automa vehicles stream perception --id chase-sim-chaser --once
```

The host imports and constructs the selection before answering. The receipt
reports `requested`, and the host applies it at its next cycle. An import or
construction error fails the arm with exit 2 and the error, keeps the previous
selection, and `plugins status` exits 2 until an arm succeeds.
Status and the viewer's **Plugins** page distinguish available, requested and
applied revisions. New IDs append in command order. An existing ID updates in
place; other plugins retain their definitions and configurations. Uploading a
newer revision does not change a pending or applied selection until another arm
request names that ID. Re-arming an unchanged definition preserves its instance
and history.

For one-command registration and arming, add `--arm` to the upload command.
It uses the same arm operation and reports upload and arming outcomes separately;
the upload remains available if arming fails.

For dependent plugins, upload each file first, then arm the group with a JSON
selection document:

```json
{"perception": ["prototype"], "memory": ["prototype-memory"], "proposal": ["prototype-proposal"]}
```

```sh
./cli/automa vehicles plugins arm --id chase-sim-chaser --selection ./selection.json
./cli/automa vehicles plugins status --id chase-sim-chaser
```

The host loads every changed step before publishing any of the group. The
next cycle sees the adopted selections together. Every step arms the same way;
observation, plan and action run one plugin, so arming one replaces it. Arming
neither starts automation nor changes its control mode. An idle or completed
host can accept a request while its catalog exists; it stays requested until the
next cycle. A Chase worker exiting ends that catalog and its uploaded selections.
The replay workbench continues to use its existing selection controls and
supports upload only through this command group.

Uploads currently support self-contained plugin files using installed imports.
Uploading Python modules that import other uploaded files needs a later loader
extension; upload does not resolve or validate those dependencies.

## State Vocabulary

The commands keep these layers separate:

| Layer | States | Meaning |
| --- | --- | --- |
| `simulator_server` | `reachable`, `unreachable` | Metrics UI HTTP/WS service |
| `simulator_frontend` | `connected`, `disconnected` | Registered browser frontend |
| `chase_game` | `ready`, `wrong_game`, `unavailable` | Frontend currently exposes Chase |
| `vehicle` | `discoverable`, `undiscoverable` | Front-camera-capable Chase vehicle endpoint |
| `passive_capture` | `available`, `unavailable`, `unsupported` | Read-only atomic capture with session preservation proof |
| `automation_deployment` | `deployed`, `not_deployed`, `invalid` | Local bundle and activations |
| `automation_worker` | `running`, `stopped`, `error` | PID and recorded worker state agree |
| `perception_view` | `available`, `unavailable`, `stale` | Loopback view belongs to the current worker generation |
| `evaluator_reference` | `available`, `unavailable`, `invalid` | Optional frame-scoped scoring reference |

The compatibility command `vehicles active` means only “discoverable vehicle.”
It does not imply a deployed bundle, running worker, or healthy browser view.
Its JSON remains `automa_vehicle_discovery_v0`.

An absent or malformed evaluator reference does not block camera perception.
It is excluded from perception, observation, memory, and decision inputs.
Reference-dependent scoring remains unavailable until a valid reference is
present.

Metrics UI must return an atomic `passiveObservation.preservation` receipt.
Automa validates its declared fields, nested fingerprints, and agreement with
the captured sensor identity. The receipt is authoritative because it is
produced inside the same frontend query as the camera capture. A missing
receipt requires updating Metrics UI; no surrounding-read fallback is accepted.

## Timeout Semantics

`--timeout-s N` is one wall-clock budget for the command's Chase readiness
operation. Registration, state, debug, atomic capture, and preservation checks
receive only the remaining time; the budget is not restarted for each phase.
JSON includes `timeout_s`, total `elapsed_ms`, and per-phase `duration_ms`
diagnostics. A timeout names the last incomplete phase and layer.

The shared default is five seconds. A server-reported
`frontend_not_connected` response fails immediately as
`frontend_disconnected`. A stale registered frontend socket that no longer
answers can still consume the remaining budget before Automa can distinguish
it from a slow frontend.

## Recovery

The automation commands and `simulators ensure` print the recovery when a
required layer is blocked. Run the command as printed, or perform the named
external change.

| Condition | Recovery |
| --- | --- |
| Server unreachable | `./cli/automa simulators ensure --scenario chaser-depth-obstacles` is an explicit configuration-changing option |
| Frontend disconnected | Open or reload the exact Metrics UI HTTP URL, then rerun the command |
| Wrong game | Preserve it unless you explicitly choose the configuration-changing `simulators ensure --scenario ...` command |
| Capture identity/image invalid | Repair the exact field named by `capture_identity_invalid` or `capture_image_invalid` |
| Passive proof missing | Metrics UI must expose the missing fingerprint field or a fail-closed `preserveSession` receipt; Automa does not work around it |
| Deployment absent | Run the printed `vehicles update perception` command; stage memory separately when you want its ledger |
| Staged step invalid (including a retired schema) | `vehicles automation status`, startup, `vehicles info`, and the autonomy update identify its step, activation path, and restage command. Run its `vehicles update <step>` command with your intended selection, then rerun `vehicles automation status`. Checking never rewrites configs, and known invalid documents prevent worker startup, restart, and physical deployment |
| Worker stopped | Run the printed observation-only `automation run --open-view` command |
| View stale/unavailable | Use the printed worker recovery; a recorded URL is not treated as healthy |

`vehicles status` exits 1 when the simulator is not reachable, or when `--id`
names a host that is `not found`, `not answering` or in `error`; otherwise it
exits 0, including when no host is found or the browser is closed. Invalid
arguments exit 2.

Simulator preparation is deliberately outside passive attachment:

```sh
./cli/automa simulators ensure --scenario chaser-depth-obstacles
```

That command may launch a browser, select Play, and select a scenario. Status,
staging, observation-only automation, viewing, and cleanup never invoke it
implicitly.

Metrics UI's passive-observation capability request is tracked in
[metrics-ui#150](https://github.com/GeorgeLuo/metrics-ui/issues/150), with the
protocol implementation under review in
[metrics-ui#151](https://github.com/GeorgeLuo/metrics-ui/pull/151). When the
running external contract is insufficient, Automa reports
`simulator_capability_missing` with protocol evidence and the minimum requested
change instead of silently selecting a scenario or taking control.
