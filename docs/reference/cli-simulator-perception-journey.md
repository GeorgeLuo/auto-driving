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
explicitly restaged using the recovery that status prints.

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

./cli/automa vehicles status --id chase-sim-chaser

./cli/automa vehicles automation stop --id chase-sim-chaser

./cli/automa vehicles status --id chase-sim-chaser
```

`vehicles status` is read-only. An HTTP(S) Metrics UI origin is resolved to the
same origin at `/ws/control`; an explicit non-root WS(S) path is preserved.
Use either `--chase-url` or the compatibility option `--chase-ws-url`, not
both. When neither is supplied, Automa uses `CHASE_UI_WS_URL` when set and
otherwise connects to `http://localhost:5050`.

`update memory` stages the memory preset. Without it, an otherwise fresh cycle
has no memory plugins. `update perception` stages the observer and fills absent
observation, plan and action activations with their built-in plugins. Existing
selections are preserved; proposals are staged separately. An incompatible
existing observation, plan, action or proposal activation must be restaged
before updating perception, which refreshes their release metadata.
Neither update command starts a worker or applies movement. For the primary
Chase path, the perception update rechecks the same environment and
passive-capture prerequisites used by automation, then reports whether the run
command is ready. Memory staging does not require a live simulator.

`automation run --observe-only` preserves the current scenario, playback
state, control source, and input. It succeeds only after one camera frame, the
perception result for that frame, and the loopback view health all agree on the
same live worker generation. `--open-view` is explicit. A browser-launch
failure leaves the healthy worker running and prints the URL for manual use.

`--num-decisions 0` starts an unbounded background worker; the launch command returns
once the correlated view is ready. Use `automation stop` to stop the worker.
Ctrl-C in a terminal stream stops that stream, not the worker. For a passive
restart, keep `--observe-only` explicit:

```sh
./cli/automa vehicles automation restart --id chase-sim-chaser --observe-only --num-decisions 0 --open-view
```

Stopping the worker keeps its local deployment staged and makes its previous
view unavailable or stale, never current.

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
```

The command reports success after the source is stored and its revision is
registered. Open **Plugins** in the runtime viewer to see the available
revision. Upload does not change the selected or running plugins. The file is
not imported, constructed, or checked for dependencies during upload; invalid
code can be registered and fail when loaded downstream.

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
Its JSON remains `automa_vehicle_discovery_v0`; aggregate status JSON is
`automa_vehicle_status_v1`.

Aggregate status evaluates Chase cards only. Other local deployments, such as
PiRacer, are listed separately with their `vehicles automation status` command
instead of being assigned Chase simulator, camera, or frontend state.

An absent or malformed evaluator reference does not block camera perception.
It is excluded from perception, observation, memory, and decision inputs.
Reference-dependent scoring remains unavailable until a valid reference is
present.

When Metrics UI returns `protocol.passiveObservation` and an atomic
`passiveObservation.preservation` receipt, Automa validates the advertised
actor, camera, preserved fields, equal before/after fingerprints, and matching
sensor identity. The receipt is authoritative because it is produced inside
the same frontend query as the camera capture. Older payloads fall back to
separate read-only state/debug checks and remain fail-closed when required
fields are unavailable.

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

Targeted status prints exactly one `Next action:` when a required layer is
blocked. Run the command as printed, or perform the named external change.

| Condition | Recovery |
| --- | --- |
| Server unreachable | `./cli/automa simulators ensure --scenario chaser-depth-obstacles` is an explicit configuration-changing option |
| Frontend disconnected | Open or reload the exact Metrics UI HTTP URL, then rerun status |
| Wrong game | Preserve it unless you explicitly choose the configuration-changing `simulators ensure --scenario ...` command |
| Capture identity/image invalid | Repair the exact field named by `capture_identity_invalid` or `capture_image_invalid` |
| Passive proof missing | Metrics UI must expose the missing fingerprint field or a fail-closed `preserveSession` receipt; Automa does not work around it |
| Deployment absent | Run the printed `vehicles update perception` command; stage memory separately when you want its ledger |
| Staged step invalid (including a retired schema) | Status, startup, `vehicles info`, and the perception and autonomy updates identify its step, activation path, and restage command. Run its `vehicles update <step>` command with your intended selection, then rerun status. Checking never rewrites configs, and known invalid documents prevent worker startup, restart, and physical deployment |
| Worker stopped | Run the printed observation-only `automation run --open-view` command |
| View stale/unavailable | Use the printed worker recovery; a recorded URL is not treated as healthy |

Status exits nonzero for failed simulator, capture, worker-error, and view-error
gates. Normal lifecycle next steps—an undeployed bundle or an intentionally
stopped worker—remain successful status reads and print the command that
advances the journey.

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
