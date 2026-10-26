# Chase Simulator-to-Perception CLI Journey

This is the supported operator path from a local Metrics UI URL to a browser
view of Automa's camera and perception output. The primary path attaches to the
Chase vehicle already exposed by the frontend. It does not select a scenario,
start playback, change the control source or input, or apply vehicle control.

Use `help` to descend through command groups and `--help` for the final command:

```sh
./cli/automa help
./cli/automa vehicles help
./cli/automa hosts help
./cli/automa hosts run --help
```

## Primary Journey

Run these commands from the repository root with the environment from
[README Setup](../../README.md#setup) activated. This example selects packaged
perception and memory presets. For an existing tuned deployment, stage your
intended selections instead: each update replaces its named step's configs.
`runtime/` persists across branch changes; incompatible activations must be
explicitly restaged using the recovery that `hosts status --long` prints.

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

`vehicles status` is read-only. It lists the default simulator, created
simulators and discovered vehicles, with each environment's provider, URL and
reachability. Use the provider and URL with `hosts create`.

`hosts status` prints a block for each created host: its state, its vehicle
and that vehicle's URL, whether its simulator lists it, and the host URL and
pid. `--long` adds each host's deployment, run and view under its block. `--name`
selects one host; `--json` emits `automa_hosts_status` with a `hosts` array
whose entries carry the host's `run` detail. Host names are chosen at creation
and do not identify the vehicle environment.

To look through a host, pass the URL on its last status line to `hosts viewer`:

```sh
./cli/automa hosts viewer --host-url http://127.0.0.1:54665
```

It serves the runtime viewer fed from that host (Perception, Memory, Action and
Plugins) and opens it in the browser until Ctrl-C. It exits 2 when no host has
that URL or the host does not answer. While the host is not live, for example
after its run stopped, it prints `Not live:` with the reason, and the Perception
and Memory pages show that reason instead of a live frame.

Status always looks at the default simulator: `CHASE_UI_WS_URL` when set,
otherwise `http://localhost:5050`. To look at another simulator or a PiCar,
list it once:

```sh
./cli/automa vehicles discover --provider chase-sim --url http://localhost:5051
./cli/automa vehicles discover --provider picar --url http://piracer.local:8887
./cli/automa vehicles remove --url http://localhost:5051
```

`vehicles discover` adds the vehicle to `runtime/hosts/discovered.json` when
it can take a host: a simulator whose frontend is connected with the Chase game
loaded, or a PiCar whose machine answers. Otherwise it exits 2 and says why;
`--force` lists it anyway. The provider is required because a PiCar without its
harness answers nothing that names it. `vehicles remove` stops listing a
discovered vehicle; the default simulator is always listed. An HTTP(S) Metrics
UI origin is resolved to the same origin at `/ws/control`, and an explicit
non-root WS(S) path is preserved, so either form names the same simulator.

To run another simulator without opening a browser, create one from a Metrics
UI checkout whose packages are installed:

```sh
export AUTOMA_METRICS_UI_DIR=~/path/to/metrics-ui
./cli/automa simulators create
./cli/automa simulators delete --url http://127.0.0.1:5051/
```

`simulators create` runs Metrics UI's headless launcher
(`scripts/play-chase-headless.mjs`) with its default configuration on the first
free port from 5051, and prints `Created Simulator (chase-sim) at <URL>` once
the launcher reports it ready. A launcher that exits or is not ready within
45 s is ended, and the command exits 2 with the end of its log
(`runtime/hosts/simulators/<port>/simulator.log`). The simulator outlives
the command: status lists it, with `created by simulators create, pid <pid>`,
until its process ends or `simulators delete` ends it. Delete refuses while a
host was created at that simulator.

`update memory` stages the memory preset and `update perception` the observer.
Without them, an otherwise fresh cycle runs no memory or perception plugins,
and `hosts run` starts the same way. Each update writes only its own step; absent observation, plan and action activations run
their built-in plugins. Neither update command starts a worker, applies
movement, or requires a live simulator. `hosts status --long` reports
invalid staged steps, and `hosts run` checks them before it starts.

`hosts run --observe-only` preserves the current scenario, playback
state, control source, and input. It succeeds only after one camera frame, the
perception result for that frame, and the loopback view health all agree on the
same live worker generation. `--open-view` is explicit. A browser-launch
failure leaves the healthy worker running and prints the URL for manual use.

`--unbounded` starts an unbounded background worker in either mode and overrides
`--num-decisions`. The launch command returns once the correlated view is ready
and prints `hosts stop`, which stops the worker. Without `--unbounded`,
a movement-enabled run requires a positive `--num-decisions N`.
Observation-only runs also retain `--num-decisions 0` as an unbounded option.
Ctrl-C in a terminal stream stops that stream, not the worker. For a passive
restart, keep `--observe-only` explicit:

```sh
./cli/automa hosts restart --name chase-sim-chaser --observe-only --num-decisions 0 --open-view
```

Stopping the worker keeps its local deployment staged and makes its previous
view unavailable or stale, never current.

## Upload to an Existing Catalog

With a Chase worker and its viewer open, upload a local plugin file separately
from selecting it:

```sh
./cli/automa hosts plugins upload \
  --name chase-sim-chaser \
  --file ./prototype.py \
  --step perception \
  --plugin-id prototype \
  --entrypoint prototype:Prototype

./cli/automa hosts plugins status --name chase-sim-chaser --step perception
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

Use `--url <viewer-or-workbench-url>` instead of `--name` to address a catalog
directly. The same command and `/api/plugins` endpoint apply to Chase, PiCar,
and the replay workbench. Workbench plugin panels refresh their catalogs even
before a replay starts or after it finishes. Dependent files can be uploaded
in sequence without resolving each other during registration.

Uploaded source lives outside `implementations`. Re-uploading an ID makes a
new source revision available while previous selections retain their source.
Registrations belong to the live catalog; restoration after a host restart is
not guaranteed. A Chase worker currently ends its catalog and viewer when the
worker exits. Keeping that host alive between runs is separate work.

## Arm on an Existing Runtime Host

After uploading, request the available revision without restarting the worker:

```sh
./cli/automa hosts plugins arm --name chase-sim-chaser --step perception --plugin prototype
./cli/automa hosts plugins status --name chase-sim-chaser --step perception
./cli/automa hosts stream perception --name chase-sim-chaser --once
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
./cli/automa hosts plugins arm --name chase-sim-chaser --selection ./selection.json
./cli/automa hosts plugins status --name chase-sim-chaser
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

`vehicles status --json` emits `automa_vehicles_status`, which lists vehicle
environments in `vehicles` with provider, URL, state and error. Simulator
entries also include frontend, game and created-process information.
`hosts status --json` reports hosts separately, with their host names, the http
URLs of their vehicles, their runtime URLs and their simulator attachment.

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
| Deployment absent | Run the printed `hosts stage perception` command; stage memory separately when you want its ledger |
| Staged step invalid (including a retired schema) | `hosts status --long`, startup, `hosts info`, and the autonomy update identify its step, activation path, and restage command. Run its `hosts stage <step>` command with your intended selection, then rerun `hosts status --long`. Checking never rewrites configs, and known invalid documents prevent worker startup, restart, and physical deployment |
| Worker stopped | Run the printed observation-only `hosts run --open-view` command |
| View stale/unavailable | Use the printed worker recovery; a recorded URL is not treated as healthy |

`vehicles status` exits 1 when no listed vehicle is reachable. `hosts status
--name <host>` exits 1 when that host is not found, not running, not answering,
or in error. Invalid arguments exit 2.

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
