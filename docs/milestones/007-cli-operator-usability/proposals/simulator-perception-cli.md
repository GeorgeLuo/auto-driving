# Proposal: Simulator-to-perception CLI journey

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Simulator-to-perception CLI journey |
| Proposal branch | `m007/simulator-perception-cli-proposal` |
| Implementation branch | `m007/simulator-perception-cli` |
| Exit criteria | M007-01, M007-02, M007-03, M007-04 |

## Review Question

Can a Chase operator move from a local Metrics UI URL to a healthy
observation-only perception browser view through discoverable Automa commands
that distinguish every runtime layer and return exact, bounded recovery when
the frontend, capture contract, worker, or view is unavailable?

For this question, “move from” means passively attaching to the currently
exposed Chase vehicle. It does not mean selecting a scenario, changing
playback/control/input state, or otherwise commandeering the simulator session.

This proposal is ready for implementation only if the operator journey can be
implemented without weakening sensor frame identity, admitting evaluator-only
data into controller inputs, applying vehicle movement, redesigning unrelated
CLI groups, requiring a live simulator in default CI, silently selecting a
scenario or taking simulator control, or treating a deployed bundle,
discoverable vehicle, running worker, and healthy view as the same state.

## Reproduced Operator Failures

The proposal is grounded in one live session on 2026-07-29:

1. `vehicles active` initially reported the WebSocket server reachable but the
   visibly open frontend disconnected, with no direct recovery URL or command.
2. The term “active” was naturally read as an enabled Automa worker even though
   it meant only that the simulator vehicle endpoint and front camera were
   discoverable.
3. `info perception` reported a stale loopback URL as connection refused while
   the simulator vehicle remained active; the worker had stopped days earlier.
4. `automation run --observe-only` and the normal control-taking form both
   failed before their first frame with the collapsed error
   `invalid identity or control reference`.
5. A direct live atomic evaluation capture proved that the image,
   `contractVersion`, actor, simulation epoch, and frame index were valid while
   the optional `evaluator.reference` object was absent.
6. Deterministic fixtures assumed `evaluator.reference` was always present, the
   live smoke was opt-in, and neither simulator readiness nor vehicle discovery
   exercised the automation capture contract.
7. A code-path audit found that adjacent commands currently use different
   readiness mechanisms and default budgets: `simulators ensure` uses SimEval
   UI verification and stability checks, discovery uses independent Metrics UI
   WebSocket state/debug/front-view calls, automation then repeats discovery
   before using atomic evaluation capture, and view status uses a separate
   short HTTP health probe. An earlier success can therefore be weaker than the
   next command's preflight even when both describe the same operator layer.
8. The installed SimEval interface exposes atomic evaluation capture as a
   `play_game_query`, requires `playback.advanced=false`, and takes no scenario
   option, so current Chase capture is structurally read-only. It exposes only
   fixed `chaser|evader` actor options, requires a connected frontend, and does
   not advertise a machine-readable passive-observation capability or
   before/after preservation receipt. With the frontend disconnected,
   capabilities, game-usage, and debug queries time out instead of returning a
   structured unsupported state.

The current payload is therefore usable for sensor-only observation but not for
shadow-reference scoring. Those are separate capabilities and must be reported
as such.

## Proposed Contract

### One operator state vocabulary

Automa owns these distinct states and exact meanings:

| Layer | State | Meaning |
| --- | --- | --- |
| `simulator_server` | `reachable` / `unreachable` | The Metrics UI HTTP/WS service answers. |
| `simulator_frontend` | `connected` / `disconnected` | A browser frontend is registered with the server. |
| `chase_game` | `ready` / `wrong_game` / `unavailable` | Play debug reports `gameId=chase`. |
| `vehicle` | `discoverable` / `undiscoverable` | Chase exposes a front-camera-capable vehicle endpoint. |
| `passive_capture` | `available` / `unavailable` / `unsupported` | The current vehicle exposes read-only capture without scenario, playback, control-source, or input mutation. |
| `automation_deployment` | `deployed` / `not_deployed` / `invalid` | A local bundle and activation contract exist. |
| `automation_worker` | `running` / `stopped` / `error` | The recorded PID and state agree with real process liveness. |
| `perception_view` | `available` / `unavailable` / `stale` | The loopback health endpoint responds for the current worker generation. |
| `evaluator_reference` | `available` / `unavailable` / `invalid` | Optional scoring-only reference for the captured frame; never a sensor input. |

“Active” may remain as a compatibility command name, but help and output must
say **discoverable vehicle**, not imply deployment or worker liveness. No
surface may describe a stopped worker as active merely because its bundle or
last state file exists.

### Aggregate status surface

Add:

```sh
./cli/automa vehicles status
./cli/automa vehicles status --id chase-sim-chaser
./cli/automa vehicles status --chase-url http://localhost:5050
./cli/automa vehicles status --id chase-sim-chaser --json
```

Rules:

- `--chase-url` accepts `http`, `https`, `ws`, or `wss`. HTTP(S) input is
  normalized to the same origin at `/ws/control`; an explicit non-root WS path
  is preserved. The default remains `http://localhost:5050`.
- Existing `CHASE_UI_WS_URL` and `--chase-ws-url` remain supported. Conflicting
  explicit values exit 2 rather than choosing silently.
- Without `--id`, status lists discoverable vehicles and locally deployed
  automation ids, then reports the aggregate layer state for each known id.
- With `--id`, output is one concise state card followed by exactly one
  `Next action:` line when any required layer is not ready.
- Human output uses the vocabulary above. JSON uses schema
  `automa_vehicle_status_v1` with required keys `vehicle_id`, `endpoint`,
  `layers`, `capture`, `readiness`, `next_action`, and `checked_at_ms`.
- `layers` contains every state above except `evaluator_reference`, which lives
  under `capture` because it is frame-scoped.
- `next_action` is either `null` or an object with `reason`, `command`,
  `external_change`, and `expected_state`. Exactly one of `command` or
  `external_change` is non-null. A command is directly runnable from repository
  root; an external change names the component, missing capability, protocol
  evidence, and minimum requested contract instead of inventing a local
  workaround.
- `vehicles active` remains read-only and backward compatible, but its human
  heading becomes `Discoverable vehicles`; `--json` retains existing schema and
  keys for compatibility while its help defines the narrower meaning.

The aggregate surface reads state only. It never starts a simulator, launches a
browser, selects a scenario, changes playback/control/input state, or
starts/stops a worker.

### Supported primary journey

The documented path is:

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

`automation run --open-view` contract:

- `--open-view` is explicit; automation without the flag never launches a
  browser.
- The worker remains observation-only when `--observe-only` is present and
  reports the preserved control source, `action_policy=observe_only`, and
  `control_application=not_applied`.
- Observation-only startup does not call `prepare_for_external_control`, select
  a scenario, play/pause/seek, set a control source, or send idle/control input.
- Startup succeeds only after the first camera frame, first completed
  perception result, and view health check all match the same worker
  generation.
- On success, output always prints the view URL. Browser launch is attempted
  only after view health succeeds.
- Browser launch failure is a warning with the URL and manual `open` recovery;
  it does not stop an otherwise healthy worker or falsify view health.
- If a matching worker is already running, `--open-view` validates and opens
  that worker’s current view rather than spawning a duplicate.
- `info perception` and `automation status` use the same view-generation and
  worker-liveness predicate as `vehicles status`.
- `--frames 0` is intentionally paired with the explicit `automation stop`
  cleanup in the primary demonstration; acceptance does not leave a worker
  running.
- The first status after `automation run` must report deployment `deployed`,
  worker `running`, view `available`, and observation-only/no-applied-control
  authority. The final status after `automation stop` must report deployment
  `deployed`, worker `stopped`, and no available current-generation view.

`update perception` in this sequence is idempotent and removes any hidden
precondition that a compatible local bundle already exists. It stages the
packaged `lightweight_observer` path and the existing safe idle decision
activation; it does not start the worker or apply movement.

### Passive attachment and simulator capability boundary

Passive attachment observes the currently exposed `chase-sim-chaser`; it does
not prepare or own the simulator session. The operator may be in any Chase
scenario and may be using programmatic, keyboard, or WS control. Playback may
be playing or paused. Status, staging, observation-only startup, capture,
viewing, and cleanup preserve all of those choices.

The allowed remote operations for the passive path are agent registration and
read-only state/debug/capability queries, including
`play_game_query(queryId="atomic-evaluation-capture")`. The path must not emit
`set_sidebar_app`, scenario-selection actions, `play`, `pause`, `seek`,
`set_chaser_control_source`, `set_chaser_input`, or any other game action. A
deterministic protocol-spy test rejects any mutating message in status,
preflight, observe-only run, or cleanup.

Before the first passive capture and after worker startup/stop, Automa records
the available session fingerprint: game id, scenario id when exposed,
simulation epoch, playback state, control source, and current input. Fields the
simulator does not expose are `unknown`, never inferred. If an unknown field
prevents proof of the required preservation boundary, `passive_capture` is
`unsupported` and the missing field is reported. Acceptance requires every
required field to be proven unchanged and the capture itself to report
`playback.advanced=false`.

The installed simulator currently exposes a read-only atomic query but does not
declare a general passive-observation capability or preservation receipt.
Implementation must first use the documented query without reconfiguration. If
the current Metrics UI cannot expose a compatible actor/camera or enough state
to honor the preservation contract, Automa returns
`simulator_capability_missing` with:

- the exact missing capability or field;
- the installed command/protocol evidence;
- the minimum external Metrics UI change requested; and
- confirmation that no scenario, playback, control, or input mutation was
  attempted.

The preferred small external contract, if needed, is a machine-readable
`passiveObservation` capability (supported query id, actors/cameras, and
preserved fields) or a `preserveSession: true` atomic-capture option that fails
closed and returns a before/after session fingerprint. The implementation may
not compensate with UI automation, scenario selection, control takeover,
synthetic state, undocumented debug scraping, or permissive acceptance. A
missing external capability remains a named blocker/change request rather than
being hidden behind retries.

`simulators ensure --scenario ...` remains an explicit operator-chosen recovery
for preparing the repository's known demonstration environment, but it is not
part of passive attachment and is never invoked implicitly by status, staging,
automation, or view commands.

### Reviewer-driven usability loop

This proposal freezes the command sequence, operator-state meanings, required
content, recovery semantics, and observation-only safety boundary. It
deliberately does not freeze the exact layout, visual styling, or incidental
copy of the Automa-owned CLI and perception-view surfaces before the reviewer
can exercise them.

During proposal and implementation review, the reviewer may run the supported
journey and provide visual or usage feedback. A finding stays in the current
review unit when it asks whether the same journey is discoverable, truthful,
and usable within the ownership and non-goals above. The implementation must
repair that failure class in the same PR and reconcile affected tests and
operator documentation. Qualitative live review may guide those repairs even
though formal tracked live acceptance remains the next frontier.

After a review unit is accepted, later feedback may intentionally revisit one
of its surfaces through a new frontier. The earlier proposal and ledger remain
the historical acceptance receipt; the later proposal names the behavior being
revised and its new review question. A materially different command journey,
another frontend, new authority, or work outside this repository's ownership
also requires a new frontier rather than silently broadening the active one.
The external Metrics UI may be reviewed here for correct launch, integration,
and recovery behavior, but redesigning that external frontend is not owned by
this proposal.

### Help and documentation hierarchy

The primary journey must be discoverable from the CLI itself by descending one
command level at a time. The required audit is bounded to these surfaces:

```text
./cli/automa help
├── ./cli/automa simulators help
│   └── ./cli/automa simulators ensure --help
└── ./cli/automa vehicles help
    ├── ./cli/automa vehicles active --help
    ├── ./cli/automa vehicles status --help
    ├── ./cli/automa vehicles update help
    │   └── ./cli/automa vehicles update perception --help
    └── ./cli/automa vehicles automation help
        ├── ./cli/automa vehicles automation run --help
        └── ./cli/automa vehicles automation stop --help
```

The audit must prove:

- root and group help enumerate the correct immediate children, explain when
  to descend, and do not leak leaf-only flags into parent summaries;
- calling a command group without a child and calling its explicit `help`
  command expose the same current navigation;
- `vehicles help`, `active --help`, and `status --help` distinguish a
  discoverable vehicle from deployment, worker, and view state;
- leaf help exposes the exact primary-journey and explicit-recovery flags at
  their owning command: `--scenario`, `--chase-url`, `--algorithm`, `--id`,
  `--observe-only`, `--frames`, and `--open-view`, with the same meanings and
  defaults used at runtime;
- help text preserves the observation-only/no-applied-control boundary and
  never requires an operator to derive `/ws/control`, inspect runtime files, or
  infer process topology;
- every command printed as `Next action:` or error recovery is accepted by the
  parser and agrees with the durable operator guide;
- the root `README.md`, documentation index, durable operator guide, and
  milestone primary sequence use the same command names, state vocabulary, and
  group-`help`/leaf-`--help` navigation pattern.

Deterministic tests exercise both the human help hierarchy and parser
acceptance of documented and emitted commands without starting a simulator,
worker, or browser. This is not an audit or redesign of unrelated Automa
commands.

### Sensor identity and evaluator-reference separation

Split the current all-or-nothing atomic-capture validation into two contracts.

**Required sensor capture**

- `contractVersion == 1`
- nonempty `captureId`
- nonempty `actorId` matching the selected passive vehicle; the current
  supported mapping is `chase-sim-chaser` → `chaser`
- `playback.advanced is false`
- `frameIdentity.gameId == "chase"`
- nonempty `frameIdentity.simulationEpoch`
- nonnegative integral `frameIdentity.frameIndex`
- valid image dimensions and decodable image data

Any required sensor-capture failure remains fatal. The error must name the
first failed path, such as `frameIdentity.simulationEpoch` or
`sensor.image.dataUrl`, with code `capture_identity_invalid` or
`capture_image_invalid`.

**Optional evaluator reference**

- `evaluator.classification == "non-sensor"`
- `evaluator.reference.kind == "actor-control-reference"`
- valid scenario, source, phase, action frame index, input, and action
- `actionFrameIndex <= frameIdentity.frameIndex`

When `evaluator.reference` is absent and required sensor capture is valid:

- sensor capture and observation-only perception proceed;
- `last_capture_shadow_reference` is `None`;
- capture metadata records
  `evaluator_reference={status:"unavailable", reason:"reference_missing"}`;
- perception, observation, memory, and decision inputs receive no evaluator
  shadow/reference payload;
- any reference-dependent scoring or alignment surface fails closed with
  `evaluator_reference_unavailable`, naming the required procedure;
- absence is not rewritten as an empty or synthetic actor-control reference.

When the reference exists but is malformed, sensor-only perception still
proceeds with `status="invalid"` and exact field diagnostics, while
reference-dependent scoring fails closed. Required frame identity and image
errors always abort the worker.

This separation is owned by the Chase adapter boundary. CLI code consumes its
structured capability result; it must not duplicate the validation rules.

### Error and recovery envelope

New or updated M007 human/JSON errors use these stable categories:

| Code | Owning layer | Required recovery |
| --- | --- | --- |
| `simulator_unreachable` | `simulator_server` | State that no passive session exists; offer `simulators ensure --scenario chaser-depth-obstacles` only as an explicit configuration-changing recovery |
| `frontend_disconnected` | `simulator_frontend` | Open/reload the exact HTTP URL, then rerun status |
| `wrong_game` | `chase_game` | Preserve the current game; state that no Chase vehicle is passively attachable and offer configured `simulators ensure` only as explicit opt-in |
| `front_view_unavailable` | `vehicle` | Preserve the session; name the missing current-vehicle camera/query capability |
| `simulator_capability_missing` | `passive_capture` | Name the exact missing passive capability and minimum Metrics UI contract/flag requested; no fallback mutation |
| `simulator_state_changed` | `passive_capture` | Stop observation, show the changed fingerprint fields, and require a fresh status; never restore state by writing to the simulator |
| `automation_not_deployed` | `automation_deployment` | Name the applicable staging command; never suggest `automation run` alone |
| `worker_stopped` | `automation_worker` | Print the observation-only run command |
| `worker_start_failed` | `automation_worker` | Preserve the exact nested capture/process code and path |
| `capture_identity_invalid` | capture | Name the failing required identity field |
| `capture_image_invalid` | capture | Name the failing image field |
| `evaluator_reference_unavailable` | capture/reference | State sensor view is still usable; name only the blocked reference-dependent procedure |
| `view_unavailable` / `view_stale` | `perception_view` | Distinguish current worker failure from a stale recorded URL |

JSON errors use a shared object with required keys `schema`, `error`, `layer`,
`message`, `details`, `recovery`, and `exit_code`. Existing command-specific
schema ids may wrap or extend this object, but the human and machine categories
must agree.

No caught validation exception may be collapsed to the current generic message
`invalid identity or control reference`.

### Timeout semantics

- A user-facing `--timeout-s N` is one wall-clock deadline for that command’s
  remote operation, not a fresh `N` seconds for each sequential WebSocket
  phase.
- Every nested phase receives only the remaining budget.
- Local file reads and formatting do not consume a separate network timeout.
- Human timeout errors name the last incomplete layer and elapsed time.
- JSON status includes `timeout_s`, `elapsed_ms`, and per-phase
  `duration_ms` diagnostics.
- The default may remain one second for read-only discovery only when tests
  prove a ready local frontend completes reliably. Automation startup keeps its
  existing explicit startup budget but reports capture and view phases
  separately.
- Retries are bounded by the same deadline and never hide a malformed
  contract.

### Sequential readiness and shared gates

An operator-facing success must prove the postcondition it names and must not
advertise a next command using a weaker readiness check than that next command
will enforce. Related commands consume the same structured gate results from
the owning boundary instead of independently translating transport success
into readiness.

The bounded journey has these canonical gates:

| Gate | Required proof | Shared consumers |
| --- | --- | --- |
| `chase_environment` | Metrics UI server registered and the current frontend already exposes a Chase-compatible vehicle/front camera under one deadline, without selecting it | Explicit `simulators ensure` postcondition, `vehicles active`, `vehicles status`, automation preflight |
| `sensor_capture` | Atomic evaluation image and required sensor identity validate; evaluator reference remains a separate capability | Explicit `simulators ensure` postcondition, targeted `vehicles status`, observation-only automation preflight/run |
| `automation_deployment` | Activation manifests resolve to the staged release and required mapper/idle-decision configuration is loadable | `update perception` postcondition, status, automation run |
| `automation_worker` | PID, state, authority, and generation agree | Automation run/status/stop and aggregate status |
| `perception_view` | Loopback health belongs to the live worker generation and reports a current correlated publication | Automation run/status, info perception, aggregate status |

SimEval may remain the explicit configuration mechanism used to launch and
select a known frontend/scenario, but passive commands never call it.
`simulators ensure` may report `usable: true` only after the same canonical
`chase_environment` gate used by vehicle status passes. A legacy front-view
probe may not stand in for the atomic `sensor_capture` gate when the next
command requires atomic capture.

Every primary-journey result includes a structured `readiness` object with
required keys `schema`, `status`, `ready_for`, `checked_at_ms`, `gates`,
and `blocking_layer`. `status` is `ready`, `blocked`, or `unknown`; `ready_for`
names the next primary-journey operation whose non-mutating prerequisites were
checked. The existing top-level `next_action` remains the one recovery owner
and must agree with `readiness`. Human output prints the equivalent `Ready for:`
or `Not ready for:` line and never labels a successfully formatted snapshot as
operationally successful.

The expected sequence is:

| Completed command | Verified next readiness |
| --- | --- |
| `vehicles status --chase-url http://localhost:5050` | The reported next action is ready: stage deployment when absent, otherwise run observation-only automation |
| `update perception` | Deployment validates and the current environment/capture gates are ready for observation-only automation |
| `automation run --observe-only --open-view` | Worker authority, first processed frame, and current-generation view are ready for inspection |
| Post-start `vehicles status` | The same worker/view gates remain ready for inspection and cleanup |
| `automation stop` | Worker is stopped and no current-generation view remains available |

Each command re-evaluates the gates it depends on; a prior result is evidence,
not an unbounded cache lease. With stable deterministic fixtures, adjacent
commands must agree and the complete sequence must not fail because one command
used a different check or shorter default gate budget. If external state
changes between commands, the later command fails at the changed gate with its
current timestamp and recovery, not with a generic timeout. One shared default
Chase readiness budget is used by adjacent commands, and an explicit timeout is
still one wall-clock deadline with remaining budget passed to every gate.

## Ownership

| Contract | Owning boundary |
| --- | --- |
| Operator layer vocabulary and aggregate status payload | `cli/automa_cli/vehicles.py`; all other CLI surfaces consume the same layer/result types |
| Sequential readiness snapshot and gate composition | One shared CLI readiness owner beside `cli/automa_cli/vehicles.py`; simulator, status, update, and automation surfaces consume it |
| HTTP/WS Chase URL normalization | One helper beside Chase defaults or vehicle discovery; simulator and status commands import it rather than reimplementing it |
| Passive capability, allowed-message boundary, and session fingerprint | `implementations/vehicle/chase_sim/metrics_ws.py` and `car.py`; CLI surfaces consume the structured result and never infer support |
| Required sensor identity and optional evaluator-reference validation | `implementations/vehicle/chase_sim/frame_identity.py` and `car.py` |
| Automation startup phases and exact nested failure propagation | `cli/automa_cli/automation.py` |
| Worker-generation and view-health acceptance | One predicate owned by `cli/automa_cli/perception_view.py` and reused by status/info/automation |
| Explicit browser launch | CLI process/browser helper invoked only by `automation run --open-view` after view health succeeds |
| Bounded help hierarchy and command examples | `cli/automa_cli/app.py`, `tests/cli/help/`, and `docs/reference/cli-simulator-perception-journey.md` |
| Durable operator behavior | `docs/reference/cli-simulator-perception-journey.md` |

The Chase adapter owns capture truth. CLI surfaces may format its structured
result but must not infer missing reference fields or repeat capture validation.
The view predicate owns current-generation health; status commands must not
accept a URL merely because a record file exists.

## Affected Paths

- Simulator preparation: `cli/automa_cli/simulators.py`
- Vehicle discovery and aggregate status:
  `cli/automa_cli/vehicles.py`, `cli/automa_cli/app.py`
- Automation lifecycle and view publication:
  `cli/automa_cli/automation.py`, `cli/automa_cli/perception.py`,
  `cli/automa_cli/perception_view.py`
- Chase protocol/adapter boundary:
  `implementations/vehicle/chase_sim/defaults.py`,
  `implementations/vehicle/chase_sim/metrics_ws.py`,
  `implementations/vehicle/chase_sim/frame_identity.py`,
  `implementations/vehicle/chase_sim/car.py`
- Deterministic and opt-in live definitions under `tests/cli/`,
  `tests/implementations/vehicle/`, and `tests/live/chase_simulator/`
- Cross-level help and documented-command coverage under `tests/cli/help/`
- `README.md`, `docs/README.md`, and the new durable operator reference

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| HTTP URL `http://localhost:5050` | Normalize to `ws://localhost:5050/ws/control`; display both operator and resolved endpoints |
| Explicit WS URL with path | Preserve it exactly |
| Conflicting `--chase-url` and `--chase-ws-url` | Exit 2 with no probe |
| Server down | `simulator_unreachable`; any `simulators ensure` recovery is labeled configuration-changing and requires explicit operator choice |
| Server up, browser visibly open but role not registered | `frontend_disconnected`; exact reload URL; no vehicle/worker implication |
| Frontend connected to non-Chase game | `wrong_game`; preserve it, report no passively attachable Chase vehicle, and do not select another game |
| Chase ready, no local bundle | Vehicle `discoverable`; deployment `not_deployed`; worker/view unavailable |
| Bundle deployed, stopped worker, stale view record | Deployment `deployed`; worker `stopped`; view `stale`; observation-only run recovery |
| State says running but PID is dead or generation differs | Worker `error`; view `stale`; never `available` |
| Valid frame/image, missing evaluator reference | Worker and perception view start; reference status `unavailable` |
| Valid frame/image, malformed evaluator reference | Sensor view starts; reference status `invalid`; reference-dependent operation fails |
| Missing epoch, wrong actor, future/invalid frame identity | Worker fails with exact identity field; no view reported healthy |
| Invalid image dimensions or encoding | Worker fails `capture_image_invalid` |
| Slow frontend across three protocol phases | Total wall time respects one command deadline |
| Browser launcher unavailable | Worker/view remain healthy; warning and manual URL |
| Repeated `automation run --open-view` | No duplicate worker; current-generation view opens |
| Human versus `--json` | Same layer states, error category, and recovery |
| Root or group help before the operator knows a leaf command | Show only the correct next level, current state meanings, and how to descend |
| Leaf help for each primary-journey command | Show every required flag at its owner with runtime-accurate meaning and safety/default semantics |
| `vehicles active` versus `vehicles status` help | Define endpoint discoverability separately from deployment, worker, and view state at parent and leaf levels |
| README, durable guide, help, or emitted recovery drifts | Deterministic checks reject missing parser paths, stale vocabulary, mismatched flags, or non-runnable recovery commands |
| Operator explicitly runs configured `ensure`, then targeted `status` | Both consume the same environment/capture gates and agree; the second command cannot time out because of a shorter or different readiness probe |
| Stable status reports ready for observation-only run | Automation preflight consumes the same environment, capture, and deployment gates and proceeds past those prerequisites |
| Status snapshot was collected but a required gate is blocked | Output says `Not ready for`, names the blocking layer and recovery, and never presents collection success as operational readiness |
| External frontend or simulation generation changes between commands | The later command rechecks and reports the changed owning gate with fresh evidence; no stale success or generic timeout |
| Shared gate is consumed by multiple command groups | Equivalent state, error code, timing semantics, and recovery appear everywhere; no command-specific duplicate probe defines a weaker success |
| Existing Chase scenario with programmatic, keyboard, or WS control | Passive status/run/view work without scenario selection or control-source/input changes |
| Existing Chase session is paused | One current frame may be observed without starting or advancing playback |
| Protocol spy sees a game action, playback command, control-source command, or input command in the passive path | Deterministic test fails; there is no allowlisted workaround |
| Atomic query works but passive capability or required preservation field is absent | `simulator_capability_missing`; report exact minimal Metrics UI capability/`preserveSession` change request |
| Session fingerprint changes during passive observation | `simulator_state_changed`; stop observation and report changed fields without trying to restore them |

## External Assumptions

- Metrics UI owns `/ws/control`, Play registration, and atomic evaluation
  capture. This repository can validate the consumed contract but cannot make
  that external server continuously available.
- The installed interface's `play_game_query` and
  `playback.advanced=false` are evidence for read-only capture, not permission
  to infer unadvertised actor support or session preservation. Missing proof is
  surfaced as an external capability request. The minimum passive-observation
  contract is tracked in
  [metrics-ui#150](https://github.com/GeorgeLuo/metrics-ui/issues/150).
- Browser launching is supported only on the local operator host and remains
  explicit/non-fatal.
- PiRacer discovery, deployment, and its local perception view are not changed
  by this frontier.
- The proposal makes no claim that missing evaluator reference is acceptable
  for shadow scoring. It is acceptable only for sensor-only observation and
  perception.
- Long-duration memory growth, remote views, authentication, non-idle control,
  and simulator performance are not evaluated here.

## Non-Goals

- Broad CLI hierarchy redesign or renaming/removal of compatibility commands
- Decision, memory, perception-algorithm, or PiRacer feature work
- Remote/public perception-view hosting or browser authentication
- Applying movement or changing the idle/observation-only safety boundary
- Implicitly selecting a simulator scenario, starting playback, changing
  control source/input, or restoring simulator state after Automa detects drift
- Treating missing/malformed sensor identity or image data as usable
- Synthesizing evaluator references or admitting evaluator shadow data to
  perception, observation, memory, or decision inputs
- Live acceptance evidence in the deterministic implementation review unit
- Long-duration soak, performance, or memory-growth qualification

## File Impact

### Create

- `docs/reference/cli-simulator-perception-journey.md` — durable operator
  state model, supported commands, and recovery table
- focused deterministic tests for aggregate status, URL normalization,
  operation deadlines, startup/view generation, reference-less capture, and
  the bounded cross-level help/documentation contract
- deterministic stable-sequence and between-step state-change fixtures for
  shared readiness gates
- protocol-spy and session-fingerprint fixtures proving passive attachment
  emits no mutating simulator message

### Modify

- `cli/automa_cli/app.py` — register `vehicles status`, `--chase-url`, and
  `automation run --open-view`
- `cli/automa_cli/vehicles.py` — aggregate layer snapshot, compatibility
  wording for `active`, URL normalization, shared readiness gates and deadline
- `cli/automa_cli/automation.py` — startup phase diagnostics, existing-worker
  open-view behavior, exact adapter errors
- `cli/automa_cli/perception.py` / `perception_view.py` — shared worker/view
  generation predicate and actionable unavailable state
- `cli/automa_cli/simulators.py` — expose shared endpoint/recovery facts without
  duplicating Chase capture validation
- `implementations/vehicle/chase_sim/frame_identity.py` — separate required
  sensor identity from optional evaluator reference
- `implementations/vehicle/chase_sim/car.py` — allow valid sensor-only capture,
  publish structured reference/passive capabilities, preserve session state
  and fail-closed identity/image
- `implementations/vehicle/chase_sim/metrics_ws.py` — operation-deadline support
  if required by the shared owner
- `tests/live/chase_simulator/test_automation_smoke.py` — define the bounded
  observation-only first-frame/view contract for the later live evidence unit
- `tests/cli/help/test_help.py` — audit parent/child navigation, state
  vocabulary, leaf flags, and parser-valid documented/recovery commands
- `README.md` and `docs/README.md` — current command journey, navigation
  pattern, and reference link
- milestone `plan.md` / generated `plan.html` only for workflow transitions

### Remove

- None

Implementation may consolidate helpers differently when one existing module is
the clearer owner, but it may not add another state vocabulary, URL normalizer,
capture validator, or view-liveness predicate.

## Validation Plan

### Deterministic

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.cli.help.test_help \
  tests.implementations.vehicle.test_chase_frame_identity \
  tests.live.chase_simulator.test_automation_smoke \
  -v
python3 docs/render_markdown.py --check
git diff --check
```

Focused tests must prove every adversarial row without a live browser. The live
smoke module remains skipped in the default suite; its command definition and
assertions are deterministic review surface, while executing it and accepting
tracked evidence belongs to the next frontier.

### Proposal validation

```sh
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/simulator-perception-cli-proposal \
  --base-sha <merge-base> \
  --head-sha <head>
```

### Live / external

Not required for implementation acceptance. The next frontier executes the
accepted bounded live procedure against the current local Metrics UI and
records whether the external contract conforms. It must either prove passive
capture plus unchanged required fingerprint fields, or record
`simulator_capability_missing`, the exact minimal Metrics UI change request,
and an unmet live criterion. It may not skip, configure a known scenario, or
substitute control-taking evidence.

## Expected Handoff

Post-merge implementation success template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Passive Chase simulator-to-perception CLI journey with aggregate layer status, shared sequential-readiness gates, HTTP/WS normalization, observation-only first-frame view startup, simulator-state preservation, exact capture/reference diagnostics, operation-level deadlines, cross-level help audit, and durable operator documentation in PR #{pr}",
  "criterion_updates": {
    "M007-01": {
      "status": "Met",
      "evidence": "Consistent simulator, vehicle, deployment, worker, view, and evaluator-reference state vocabulary plus shared next-step readiness gates in human and JSON CLI surfaces in PR #{pr}"
    },
    "M007-02": {
      "status": "Met",
      "evidence": "Local HTTP URL normalization, passive Chase discovery, explicit-only configured preparation, and exact frontend/game/camera/capability recovery in PR #{pr}"
    },
    "M007-03": {
      "status": "Met",
      "evidence": "Passive observation-only sensor/perception startup preserves scenario/playback/control/input state, separates optional evaluator reference, and keeps reference-dependent operations fail-closed in PR #{pr}"
    },
    "M007-04": {
      "status": "Met",
      "evidence": "Bounded shared readiness deadlines, stable actionable error categories, cross-command gate agreement, open-view workflow, parser-valid cross-level help and recovery, README, and durable operator guide in PR #{pr}"
    }
  },
  "risk_remove": [
    "Existing “active” terminology is naturally read as “automation running”"
  ],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "Live CLI operator acceptance is promoted from the frozen next-candidate slot after M007-01 through M007-04.",
    "revisit_when": "The live acceptance unit records current Metrics UI conformance or returns a product finding to the accepted implementation boundary."
  }
}
```

If current live Metrics UI support is still unverified at implementation
handoff, the implementation PR must say so and retain the external capability
as an open risk for the live frontier. If deterministic or live inspection
proves the required contract impossible, replace the empty `risk_upsert` with
the exact `simulator_capability_missing` request; do not describe the passive
journey itself as accepted evidence.

### Sequence after this proposal merges

1. Accept and merge this proposal PR into
   `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal`; verify
   `ready_for_implementation`.
3. Start `m007/simulator-perception-cli` and implement only this contract.
4. Review and repair the implementation in its own PR.
5. On implementation acceptance, promote **Live CLI operator acceptance** and
   keep its live judgment in a separate review unit.
