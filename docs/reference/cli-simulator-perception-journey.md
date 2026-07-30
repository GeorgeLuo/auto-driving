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

Run these commands from the repository root:

```sh
./cli/automa vehicles status --chase-url http://localhost:5050

./cli/automa vehicles update perception \
  --id chase-sim-chaser \
  --algorithm lightweight_observer

./cli/automa vehicles automation run \
  --id chase-sim-chaser \
  --observe-only \
  --frames 0 \
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

`update perception` stages the packaged observer and safe idle decision
activation. It does not start a worker or apply movement. For the primary Chase
path it rechecks the same environment and passive-capture prerequisites used by
automation, then reports whether the run command is ready.

`automation run --observe-only` preserves the current scenario, playback
state, control source, and input. It succeeds only after one camera frame, the
perception result for that frame, and the loopback view health all agree on the
same live worker generation. `--open-view` is explicit. A browser-launch
failure leaves the healthy worker running and prints the URL for manual use.

`--frames 0` runs until the explicit `automation stop` command. Stopping the
worker keeps its local deployment staged and makes its previous view
unavailable or stale, never current.

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

An absent or malformed evaluator reference does not block camera perception.
It is excluded from perception, observation, memory, and decision inputs.
Reference-dependent scoring remains unavailable until a valid reference is
present.

## Timeout Semantics

`--timeout-s N` is one wall-clock budget for the command's Chase readiness
operation. Registration, state, debug, atomic capture, and preservation checks
receive only the remaining time; the budget is not restarted for each phase.
JSON includes `timeout_s`, total `elapsed_ms`, and per-phase `duration_ms`
diagnostics. A timeout names the last incomplete phase and layer.

The shared default is five seconds. A disconnected frontend commonly consumes
most of that budget because the WebSocket server is reachable and Automa waits
for the frontend-owned response until the deadline. This is different from an
immediate connection refusal and is reported as `frontend_disconnected`.

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
| Deployment absent | Run the printed `vehicles update perception` command |
| Worker stopped | Run the printed observation-only `automation run --open-view` command |
| View stale/unavailable | Use the printed worker recovery; a recorded URL is not treated as healthy |

Simulator preparation is deliberately outside passive attachment:

```sh
./cli/automa simulators ensure --scenario chaser-depth-obstacles
```

That command may launch a browser, select Play, and select a scenario. Status,
staging, observation-only automation, viewing, and cleanup never invoke it
implicitly.

Metrics UI's minimum passive-observation capability request is tracked in
[metrics-ui#150](https://github.com/GeorgeLuo/metrics-ui/issues/150). When that
external contract is insufficient, Automa reports
`simulator_capability_missing` with protocol evidence and the minimum requested
change instead of silently selecting a scenario or taking control.
