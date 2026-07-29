# Proposal: Automa shadow decision surfaces

| Field | Value |
| --- | --- |
| Milestone | 006 Decision-Facing Perception Readiness |
| Frontier | Automa shadow decision surfaces |
| Proposal branch | `m006/shadow-decision-surfaces-proposal` |
| Implementation branch | `m006/shadow-decision-surfaces` |
| Exit criteria | M006-05 |

Prerequisite (accepted, do not re-open):

- Implementation PR [#74](https://github.com/GeorgeLuo/auto-driving/pull/74) at
  `7830cb0c509eb6c601bf74f707d8caeca177ed8d`
- Proposal artifact:
  `docs/milestones/006-decision-facing-perception-readiness/proposals/shadow-proposals.md`
- Schemas and engine id frozen there: `decision_data_source_v0`,
  `action_proposal_v0`, `action_plan_v0`, `shadow_authority_result_v0`,
  `shadow_decision_cycle_result_v0`, engine `shadow-proposals`, plugin
  `avoid_recent_obstruction`, selector `deterministic_first_active`

Plan context: this unit was split from the oversized combined
surfaces+live-evidence frontier (plan revision #78; superseded proposal #76)
so that deep deterministic Automa surface review is not combined with
substantial Chase/Pi live proof.

## Review Question

Can Automa stage, inspect, replay, and stream the accepted `shadow-proposals`
decision path with concise default output, complete `--json` output,
deterministic offline replay, latest-frame replacement, one combined
frame/evidence/proposal/authority view, and an opt-in exact-frame HTML artifact
with no default disk writes while `proposed_applied=false`,
`authorized_output` is idle with reason `shadow-only-idle`, and
`host_application` is reported only when the host provides it?

This proposal is ready for implementation only if an implementer can wire Automa
surfaces around PR #74 **without inventing decision policy**, changing
proposal/plan/authority semantics, selecting another plugin, applying vehicle
movement, inventing an unconditional applied-control value, consuming privileged
Chase evaluator state, shipping live Chase/Pi evidence packages, or claiming
physical navigation readiness.

Live Chase and stationary PiRacer packages remain the **next frontier**
(M006-06–M006-07) and are out of scope here except that surface contracts must
be consumable unchanged by that later evidence unit.

## Proposed Contract

### Design constants

| Constant | Exact value / rule |
| --- | --- |
| Engine activation id | `shadow-proposals` (PR #74 catalog; no second engine) |
| Catalog factory | `implementations.decision.catalog:create_shadow_proposals_engine` |
| AutonomyManager engine class | `implementations.decision.shadow_adapter:ShadowProposalsAutonomyEngine` (new; see **Runtime adapter**) |
| Activation `engine_spec` for `shadow-proposals` | `implementations.decision.shadow_adapter:ShadowProposalsAutonomyEngine` |
| Reference plugin | `avoid_recent_obstruction` only (no alternate policy) |
| Selector | `deterministic_first_active` (unchanged) |
| Authority | `proposed_applied=false`; `authorized_output` idle reason exact `shadow-only-idle` |
| Decision activation schema | `automa_decision_activation_v0` (extend fields only; do not fork) |
| Info payload schema | `vehicle_decision_info_v0` |
| Update payload schema | `vehicle_decision_update_v0` |
| Stream / latest frame schema | `vehicle_decision_stream_frame_v0` |
| Apply digest schema | `vehicle_decision_apply_digest_v0` |
| Apply result schema | `vehicle_decision_apply_result_v0` |
| Run sequence schema | `automa_decision_apply_sequence_v0` |
| Exact-frame HTML schema | `decision_exact_frame_review_v0` |
| Combined view id | `decision-combined-v0` |
| Serialization (size ceilings) | `canonical_json_bytes` → **int** length only (never equality) |
| Serialization (determinism) | `canonical_json_utf8(value) -> bytes` — see **Canonical bytes** below |
| Default disk writes | **None** (apply/stream write nothing unless `--record`) |
| Stream freshness max age | `30_000` ms (`AUTOMA_DECISION_STREAM_MAX_AGE_MS`) |
| Apply record output root | `AUTOMA_DECISION_APPLY_OUTPUT_ROOT` or `lab/runs/decision-apply/` |

### Scope (M006-05 only)

| In | Out |
| --- | --- |
| Stage / info / apply / stream / view for `shadow-proposals` | Tracked Chase evidence packages (M006-06) |
| Runtime adapter so automation can load and step the shadow engine | Tracked stationary Pi packages (M006-06) |
| Publication of `ShadowDecisionCycleResult` into latest-frame storage | Live environment attestation procedures (M006-07 host proofs beyond deterministic privilege tests) |
| Deterministic offline apply + double-run identity | Perception retune, second policy, applied movement |
| Combined correlated visual explanation template | Live vehicle dependency in CI |
| Opt-in `--record` exact-frame HTML; no default disk writes | |
| Deterministic CLI/unit tests in default CI | |

Product implementation is limited to **operator surfaces, packaging, correlation
view, offline replay recording, and the thin AutonomyManager adapter** around
the already-accepted shadow engine. It must **import and call** PR #74 types and
`run_cycle`; it must not edit plugin selection policy, lifecycle matrix,
selector ranking, authority idle guarantee, or privilege-free DecisionDataSource
rules.

### Runtime adapter (executable live path)

#### Problem frozen here

`AutonomyManager` loads engines via `engine_cls(**engine_config)` and requires
`reset()`, `describe_schema()`, and `step(AutonomySnapshot) -> AutonomyControl`.
Neither PR #74 entry point satisfies that interface:

- `ShadowProposalsEngine` has `run_cycle(...)`, not `reset`/`step`.
- Catalog `create_shadow_proposals_engine` is a factory, not a loadable class.
- Instantiating `autonomy.decision.shadow_runner:ShadowProposalsEngine` with
  config kwargs fails because `plugins` is required.

Today `cli/automa_cli/automation.py` always loads the staged decision engine
through `AutonomyManager` and publishes only generic cycle/control fields. This
unit freezes the missing adapter and publication boundary so stage → automation
→ stream works without inventing architecture during implementation.

#### Adapter class (exact)

| Item | Rule |
| --- | --- |
| Module path | `implementations/decision/shadow_adapter.py` (**create**) |
| Class | `ShadowProposalsAutonomyEngine` |
| Construction | `__init__(self, **engine_config)` where `engine_config` is a plain dict of `ShadowProposalsConfig` fields only (`enabled_plugins`, `accepted_kinds`, `retained_max_age_ms`, `steer_magnitude`). Missing keys use PR #74 defaults. Invalid values **raise** at construction (fail closed). |
| Inner engine | Built exactly once via `create_shadow_proposals_engine(ShadowProposalsConfig(...))`. No second plugin map. |
| `reset()` | Clears `last_cycle_result` to `None`. Does not mutate catalog/plugins. |
| `describe_schema()` | Returns a dict with at least: `schema="autonomy_engine_schema_v0"`, `engine_id="shadow-proposals"`, `engine_spec` of this class, `purpose` text naming shadow-only idle authority, `stages` naming action=`shadow_proposals_run_cycle`, and `output.type="AutonomyControl"` with `movement="always idle"`. |
| `step(snapshot)` | **Always** sets `last_cycle_result = None` first. Then maps snapshot → `run_cycle` (below). On success, stores the returned `ShadowDecisionCycleResult` (including `engine_error` cycles from the runner). On **entry/mapping failure** (invalid `frame_id`, bad types before `run_cycle`, or `ShadowCycleInputError` from the runner): leaves `last_cycle_result = None`, does **not** invent a cycle result, returns `AutonomyControl` idle with reason `shadow-adapter-entry-error` and metadata describing the failure. On any other unexpected exception after entry: same — `last_cycle_result = None`, idle control with reason `shadow-adapter-step-error`. When `run_cycle` returns normally, returns `authorized_idle_control()` always (never proposed command as control). |
| `last_cycle_result` | Public attribute. `None` after `reset`, at the start of every `step`, and after any step that did not obtain a real `ShadowDecisionCycleResult` from `run_cycle`. Never retains a prior cycle across a failed step. |

#### Snapshot → `run_cycle` mapping (exact)

| `run_cycle` kwarg | Source |
| --- | --- |
| `frame_id` | **Required** from `snapshot.cycle["frame_id"]` as a valid ASCII id (PR #74 grammar). If missing or invalid: **entry failure** (`last_cycle_result=None`); do **not** synthesize a frame id. Automation must supply `frame_id` in `DecisionFrameContext` / cycle dict. |
| `frame_index` | `snapshot.cycle["frame_index"]` if present as non-bool int; else entry failure |
| `timestamp_ms` | `snapshot.timestamp_ms` (non-bool int); else entry failure |
| `observation` | `snapshot.observation` when it is `Observation` or `dict`; else `None` |
| `observation_error` | `snapshot.metadata.get("observation_error")` when `str`; else `None` |
| `memory` | `snapshot.memory` when it is `MemorySnapshot`; else `None` (unavailable path) |
| `host_application` | `snapshot.metadata.get("host_application")` when it is `ComponentEnvelope`; else `None` → authority default `unavailable` / `host_did_not_report_application` |
| `prior_host_applied_command` | `snapshot.metadata.get("prior_host_applied_command")` when `ComponentEnvelope`; else `None` |
| `drive_mode_gate` | `snapshot.mode` if `str`, else `"unknown"` |
| `capabilities` | `snapshot.metadata.get("capabilities")` when `ComponentEnvelope`; else default ready capabilities |

`step` never invents a nonzero `AutonomyControl`. Proposed intent lives only on
`last_cycle_result.authority.proposed` when a cycle result exists.

#### Canonical bytes (determinism helper)

`canonical_json_bytes` in this repository returns an **integer length**, not
bytes. Equal lengths must never be treated as equal content.

| Helper | Signature | Rule |
| --- | --- | --- |
| `canonical_json_utf8` | `(value) -> bytes` | **Create** (or re-export) as `json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`. Reject non-JSON / non-finite the same way as the size helper. |
| `canonical_json_bytes` | `(value) -> int` | Existing helper; use **only** for size ceilings (`len` of the same encoding). |

Determinism and digests: compare and SHA-256 **`canonical_json_utf8(digest)`**
byte strings. Implementation may place `canonical_json_utf8` next to
`canonical_json_bytes` in `autonomy/decision/memory.py` (or a shared util) and
define `canonical_json_bytes = lambda v: len(canonical_json_utf8(v))` if desired
without changing existing call sites' return type.

#### Activation wiring

| Item | Rule |
| --- | --- |
| `DECISION_ENGINES["shadow-proposals"]` | Registers description, `engine_spec` = adapter class path above, and default `engine_config` matching `ShadowProposalsConfig()` defaults with `enabled_plugins=["avoid_recent_obstruction"]`. |
| Stage validation | Before writing activation, construct `ShadowProposalsConfig(**engine_config)` and ensure every `enabled_plugins` id is in `KNOWN_PROPOSAL_PLUGIN_IDS`. On failure: exit 2, **no activation write**. |
| `idle` engine | Unchanged (`autonomy.runtime.engine:IdleAutonomyEngine`). |

#### Automation publication + latest-frame atomicity

| Item | Rule |
| --- | --- |
| Owning file | `cli/automa_cli/automation.py` (**modify**) |
| When | After each `cycle_host.run(context)` when staged `engine_id == "shadow-proposals"`. |
| Read cycle result | From `cycle_host.manager.engine.last_cycle_result`. |
| Publish gate (all required) | (1) `last_cycle_result is not None`; (2) `last_cycle_result.frame_id == context.frame_id` (exact string match); (3) staged activation still `shadow-proposals`. If any gate fails: **do not** write/replace `latest_decision.json` (leave previous file untouched **only** until generation rules below invalidate it — see stream acceptance). Log/count a non-fatal publish skip; do not invent a partial shadow frame. |
| Generation identity | Every published payload includes `run_id` (current automation run id), `worker_pid` (`os.getpid()`), `activation_engine_id` (`shadow-proposals`), and `activation_activated_at_ms` (from the staged decision activation's `activated_at_ms`). These must match the live automation `state.json` / activation at publish time. |
| On automation start | Before first frame: delete or atomically replace `latest_decision.json` with nothing usable — either remove the file or write a non-stream placeholder that fails stream schema validation. Stale files from prior workers must not remain valid. |
| On restage of decision engine | Next automation start (or immediate invalidation if automation is running) must clear `latest_decision.json` so a previous shadow frame cannot satisfy stream after idle/restage. |
| Latest frame path | `{vehicle_runtime}/automation/latest_decision.json` |
| Write protocol | Write temp file in the same directory, `fsync`, then `os.replace` onto `latest_decision.json` (atomic replace). |
| Payload | Exactly one `vehicle_decision_stream_frame_v0` object (schema below), built from `ShadowDecisionCycleResult.to_dict()` plus stream envelope fields including generation identity. |
| Not written by default on apply | Offline apply does not write `latest_decision.json`; apply is offline-only. |
| Stream reader | `stream decision` reads **only** `latest_decision.json` (replacement UX). Does not append history. Acceptance rules in the stream section. |

#### Fixture-backed stream for CI

Live Chase is not required for this unit. Deterministic tests may:

1. Construct the adapter, call `step`/`run_cycle` with fixture observation+memory, write a latest-frame payload through the same publication helper automation uses, then assert `stream decision --once` (or the pure helper) returns the schema; **or**
2. Call the pure `build_decision_stream_frame(cycle_result) -> dict` helper and the pure apply path without a live worker.

Both paths must produce the **same** `vehicle_decision_stream_frame_v0` shape. The
live automation path remains the production owner; fixtures exercise the same
helpers.

### Operator workflow

```text
# 1) Stage shadow decision engine
./cli/automa vehicles update decision --id <vehicle> --engine shadow-proposals

# 2) Inspect contract
./cli/automa vehicles info decision --id <vehicle>
./cli/automa vehicles info decision --id <vehicle> --json

# 3a) Live stream path (automation worker publishes latest_decision.json)
./cli/automa vehicles automation run --id <vehicle>
./cli/automa vehicles stream decision --id <vehicle>
./cli/automa vehicles stream decision --id <vehicle> --once --json

# 3b) Offline deterministic apply (--id required)
./cli/automa vehicles decision apply --id <vehicle> --from-run <dir>
./cli/automa vehicles decision apply --id <vehicle> --from-run <dir> --json
./cli/automa vehicles decision apply --id <vehicle> --from-run <dir> --record
```

Flag names above are frozen for validation. `--id` is **required** on apply
(same vehicle identity as stage/info). Implementation may add optional flags
only if they do not change the required outcomes.

### Exit codes (all decision surfaces)

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `2` | Operator/config/input error (unknown engine, missing activation, invalid config, malformed run, bounds exceeded, wrong engine for shadow stream, non-deterministic apply) |
| `130` | Interrupted (stream Ctrl-C); empty or minimal message |

Stdout on success with `--json` is exactly one JSON document (or one JSON object
per refresh line for continuous stream `--json`). Errors with `--json` emit a
single JSON object:

```json
{
  "schema": "vehicle_decision_error_v0",
  "exit_code": 2,
  "error": "<stable short code>",
  "message": "<human remediation text>",
  "vehicle_id": "<id or null>",
  "details": {}
}
```

Stable `error` codes (extend only with proposal change):

| `error` | Surfaces |
| --- | --- |
| `unknown_engine` | update |
| `invalid_engine_config` | update |
| `activation_missing` | info, stream, apply |
| `activation_invalid` | info, stream, apply |
| `wrong_engine` | stream **and** apply (activation exists but `engine_id ≠ shadow-proposals`) |
| `latest_frame_missing` | stream |
| `latest_frame_invalid` | stream |
| `latest_frame_stale` | stream (age / dead worker / generation mismatch) |
| `run_missing` / `run_invalid` / `run_bounds_exceeded` | apply |
| `apply_non_deterministic` | apply |
| `record_bounds_exceeded` / `record_write_failed` | apply `--record` |
| `missing_vehicle_id` | apply when `--id` omitted |

Without `--json`, errors are multi-line human text including remediation (e.g.
point at `update decision`).

### Surface contracts (exact schemas)

Omission rule: keys listed as required are always present. Optional keys may be
omitted only when marked optional. `null` is used explicitly where noted; do not
omit a required nullable key.

#### Stage (`update decision`) → `vehicle_decision_update_v0`

Required keys:

| Key | Type | Rule |
| --- | --- | --- |
| `schema` | string | exact `vehicle_decision_update_v0` |
| `vehicle_id` | string | request id |
| `engine_id` | string | staged id (`idle` or `shadow-proposals`) |
| `dry_run` | bool | |
| `activation` | string | display path of `active.json` |
| `manifest` | object | full `automa_decision_activation_v0` body that was/would be written |
| `release` | object \| null | release summary or null on dry-run without sync |

Human default (no `--json`): concise lines naming vehicle, engine id, engine
spec, activation path. Exit 2 on unknown engine or invalid shadow config (no
partial write).

For `shadow-proposals`, `manifest.decision.engine_config` must be a JSON object
whose keys are only the four `ShadowProposalsConfig` fields (lists for sequence
fields). Activation write uses indent-2 / sort_keys for the file; CLI `--json`
stdout also indent-2 / sort_keys.

#### Info (`info decision`) → `vehicle_decision_info_v0`

Required keys:

| Key | Type | Rule |
| --- | --- | --- |
| `schema` | string | exact `vehicle_decision_info_v0` |
| `vehicle_id` | string | |
| `activation` | object | `path`, `engine_id`, `engine_spec`, `engine_config` |
| `shadow` | object \| null | **required object** when `engine_id=="shadow-proposals"`; else `null` |
| `engine_schema` | object \| null | from activation or live describe |
| `controller_bundle` | object \| null | from activation |
| `combined_view` | object | see below |

When `shadow` is present it **must** contain:

| Key | Type | Rule |
| --- | --- | --- |
| `decision_inputs` | string[] | exact names: `observation`, `memory`, `patterns`, `projections`, `capabilities`, `prior_host_applied_command` |
| `enabled_plugins` | string[] | from activation config (default `["avoid_recent_obstruction"]`) |
| `selector_id` | string | `deterministic_first_active` |
| `output_schemas` | object | keys `action_proposal`, `action_plan`, `shadow_authority`, `cycle_result` → exact PR #74 schema id strings |
| `authority` | object | `proposed_applied=false`, `authorized_idle_reason="shadow-only-idle"`, `authority_mode="shadow_only"` |

`combined_view`:

| Key | Type | Rule |
| --- | --- | --- |
| `view_id` | string | `decision-combined-v0` |
| `url` | string \| null | live URL when view server known; else null |
| `path_template` | string | stable template or relative path for the HTML asset |

Human default **must** print engine id/spec, the decision input names, enabled
plugins, selector, output schema ids, authority shadow-only line, and combined
view URL or path template. Missing activation → exit 2, error
`activation_missing`.

#### Stream (`stream decision`) → `vehicle_decision_stream_frame_v0`

Primary UX: **latest-frame replacement** (clear+redraw or single-frame `--once`),
not unbounded history.

Each published/read frame object:

| Key | Type | Rule |
| --- | --- | --- |
| `schema` | string | exact `vehicle_decision_stream_frame_v0` |
| `vehicle_id` | string | |
| `engine_id` | string | must be `shadow-proposals` for this stream path |
| `run_id` | string | automation run that published this frame |
| `worker_pid` | int | publisher process id |
| `activation_engine_id` | string | exact `shadow-proposals` |
| `activation_activated_at_ms` | int | from staged activation at publish |
| `published_at_ms` | int | wall clock at publish (freshness only; not proposal logic) |
| `frame_id` | string | ASCII id grammar from PR #74; must equal `cycle.frame_id` |
| `frame_index` | int | from cycle |
| `timestamp_ms` | int | cycle timestamp |
| `cycle` | object | exact `ShadowDecisionCycleResult.to_dict()` (`shadow_decision_cycle_result_v0`) |
| `observation_summary` | object | see below |
| `memory_summary` | object | see below |
| `plan_summary` | object | see below |
| `authority_summary` | object | see below |
| `view` | object | `view_id`, `applied_false_emphasized=true` |

**Do not include** an `applied_control` key. Actual host application is only
`cycle.authority.host_application` / `authority_summary.host_application`.

**Stream acceptance (all required or exit 2):**

| Check | Failure code |
| --- | --- |
| File exists and parses as object with `schema=vehicle_decision_stream_frame_v0` | `latest_frame_missing` / `latest_frame_invalid` |
| Vehicle activation present and `engine_id == shadow-proposals` | `activation_missing` / `wrong_engine` |
| Frame `activation_activated_at_ms` equals current activation `activated_at_ms` and `activation_engine_id` / `engine_id` are `shadow-proposals` | `latest_frame_stale` |
| Automation `state.json` exists; `state.run_id == frame.run_id`; if `state.status == "running"`, `state.pid` is a live process **or** (for fixture tests only) `state.pid == frame.worker_pid` with `status` in `{"running","completed"}` and age ok | `latest_frame_stale` |
| `now_ms - published_at_ms ≤ AUTOMA_DECISION_STREAM_MAX_AGE_MS` (default 30000) | `latest_frame_stale` |
| `frame_id == cycle.frame_id` and summaries consistent with cycle | `latest_frame_invalid` |

`--once` succeeds only when all acceptance checks pass. Continuous stream redraws
on success and surfaces the last error on failure without inventing a frame.

`observation_summary` (no privileged handles):

| Key | Type | Rule |
| --- | --- | --- |
| `status` | string | `ready` \| `unavailable` \| `error` \| `absent` |
| `frame_id` | string \| null | ready observation identity when available |
| `reason` | string | empty when ready; else short reason |

Derived from `cycle.source.observation` envelope when source present; else
`absent`.

`memory_summary`:

| Key | Type | Rule |
| --- | --- | --- |
| `status` | string | `ready` \| `unavailable` \| `error` \| `absent` |
| `health` | string \| null | from MemorySnapshot when ready |
| `record_count` | int \| null | |
| `records` | array | up to 12 summaries: `{record_id, kind, confidence, frame_id, observation_id}` from accepted-kind records; empty array when none |

`plan_summary`:

| Key | Type | Rule |
| --- | --- | --- |
| `status` | string \| null | `selected` \| `idle` \| null when `cycle.plan` is null |
| `selected_proposal_id` | string \| null | |
| `candidates` | array | each: `{proposal_id, plugin_id, lifecycle, freshness, confidence, reason, command, source_refs}` where `command` is `{steering, throttle}` or `null` and `source_refs` is the candidate's PR #74 `source_refs` array (may be empty) |
| `contributions` | array | from plan or `[]` |

`authority_summary` (mirrors PR #74 authority; no invented applied field):

| Key | Type | Rule |
| --- | --- | --- |
| `proposed` | object \| null | proposed command dict or null |
| `authorized_output` | object | exact idle dict: `steering=0.0`, `throttle=0.0`, `confidence=1.0`, `reason="shadow-only-idle"` |
| `proposed_applied` | bool | always `false` |
| `host_application` | object | ComponentEnvelope dict as reported or unavailable |
| `proposed_equals_authorized` | bool | from authority |
| `cycle_status` | string | `ok` \| `engine_error` |
| `cycle_reason` | string | |

CLI modes:

| Mode | Behavior |
| --- | --- |
| default | Human latest-frame screen; refresh until interrupt |
| `--once` | Single frame then exit 0 if frame **accepted** (all freshness gates) |
| `--json` | One JSON object per refresh (or once); no ANSI clear |
| missing activation | exit 2 `activation_missing` |
| engine ≠ `shadow-proposals` | exit 2 `wrong_engine` (fail closed; **no** silent idle swap and **no** success with non-shadow labeling) |
| missing/invalid latest frame | exit 2 `latest_frame_missing` / `latest_frame_invalid` |
| stale / wrong generation / dead worker | exit 2 `latest_frame_stale` |

#### Apply / replay (`decision apply --id <vehicle> --from-run <dir>`)

##### Vehicle identity and activation (frozen)

| Item | Rule |
| --- | --- |
| CLI | **`--id <vehicle>` is required.** Omit → exit 2 `missing_vehicle_id` with remediation naming `--id`. |
| Sequence file | Must **not** carry a competing vehicle identity. If `sequence.json` contains top-level `vehicle_id` and it differs from `--id`, exit 2 `run_invalid`. If present and equal, accept. Prefer omitting `vehicle_id` from the sequence. |
| Activation path | `{RUNTIME_ROOT}/<safe_vehicle_id>/…/decision/active.json` via existing bundle helpers for `--id` only. No directory walk, no default vehicle, no inference from `--from-run` path. |
| Engine requirement | Activation must exist and `decision.engine_id == "shadow-proposals"`. Missing → `activation_missing`. Present but other engine → `wrong_engine`. Invalid JSON/config → `activation_invalid`. |
| Engine build | Catalog `create_shadow_proposals_engine(ShadowProposalsConfig(**activation.engine_config))` (defaults for omitted keys). |

##### Replay input contract (single path — frozen)

Offline apply **trusts recorded per-frame inputs**. It does **not** re-run
perception and does **not** rebuild memory through a staged memory engine.

| Rule | Exact behavior |
| --- | --- |
| State reconstruction | For each frame, call `ShadowProposalsEngine.run_cycle` with that frame's recorded observation and memory only |
| Memory | If frame includes a `memory` object, **strict-validate** then construct `MemorySnapshot`; if key absent or JSON `null`, pass `memory=None` (unavailable / missing_input path) |
| Perception | Never invoked |
| Fresh engine per pass | Each full apply pass constructs a new engine instance; no process-global residual state |
| Frame order | Strict array order in `sequence.json`; do not reorder by timestamp |
| Duplicate `frame_id` | Reject run (`run_invalid`) |
| Malformed frame | Reject run (`run_invalid`); do not skip; do not coerce/drop entries |
| Timestamp | Use each frame's `timestamp_ms` as recorded; no wall-clock injection into cycle logic |
| Host application | Always pass `host_application=None` on offline apply (unavailable envelope inside authority) |

##### Strict pre-validation (before coercive constructors)

`Observation.from_dict` / `MemorySnapshot.from_dict` **silently drop** non-object
things/signals/records. Apply must **not** call those constructors until the
payload passes a strict check that rejects droppable malformation.

**Observation** (when not null): must be a `dict` with:

| Check | Rule |
| --- | --- |
| `schema` | if present, exact observation schema id from PR #74 / repo constant; if absent, allow only when remaining fields still pass all checks below |
| `observation_id` | non-empty string |
| `things` | if present: `list`/`tuple`; **every** element is a `dict` (zero non-dicts) |
| `signals` | if present: `list`/`tuple`; **every** element is a `dict` |
| `summary` | if present: `str` or list/tuple of values coercible only if already strings or the list contains only strings — reject non-list/non-str types |
| `sensor_snapshot` / `artifacts` / `metadata` | if present: `dict` |

Any violation → `run_invalid` (do not call `Observation.from_dict`).

**Memory** (when not null): must be a `dict` with:

| Check | Rule |
| --- | --- |
| `schema` | if present, exact `memory_snapshot` schema id used by PR #74 / M005 |
| `bounds` | required `dict` |
| `records` | if present: `list`/`tuple`; **every** element is a `dict` (zero non-dicts). Length after validation must equal input length (no drops). |
| `summary` | if present: `list`/`tuple` of strings only (or empty) |
| `health` / `memory_id` / `epoch_id` | strings when present |

Any violation → `run_invalid` (do not call `MemorySnapshot.from_dict`).

After strict checks pass, construct via the normal APIs. Round-trip optional:
if `to_dict` record/thing counts differ from input counts, treat as `run_invalid`.

##### Run directory layout

`--from-run <dir>` accepts a directory containing:

```text
<dir>/
  sequence.json                 # required
  frames/                       # optional images for --record HTML only
    <frame_id>.png              # optional; exact name = frame_id + ".png"
```

Image addressing (when present):

| Rule | Exact |
| --- | --- |
| Path | Only `{from_run}/frames/<frame_id>.png` (no absolute paths, no `..`, no frame field URL) |
| Association | Image for a frame is present iff that file exists; HTML references the relative path `frames/<frame_id>.png` when the file exists |
| Digest | Image presence/absence does **not** affect digest bytes |
| Escape | Any symlink or path outside `<dir>` → `run_invalid` if resolved during `--record` |

`sequence.json` top-level object:

| Key | Type | Rule |
| --- | --- | --- |
| `schema` | string | exact `automa_decision_apply_sequence_v0` |
| `frames` | array | 1..MAX_FRAMES entries |
| `vehicle_id` | string | optional; if present must equal `--id` |

Each frame object:

| Key | Type | Required | Rule |
| --- | --- | --- | --- |
| `frame_id` | string | yes | ASCII id grammar |
| `frame_index` | int | yes | non-negative safe int |
| `timestamp_ms` | int | yes | safe int |
| `observation` | object \| null | yes | null or strict-validated Observation dict |
| `observation_error` | string \| null | no | when set, non-empty string for source builder |
| `memory` | object \| null | no | omit/null → unavailable; else strict-validated MemorySnapshot dict |

##### Resource bounds (fail closed)

| Bound | Default | Env override | Overflow |
| --- | --- | --- | --- |
| Max frames | `256` | `AUTOMA_DECISION_APPLY_MAX_FRAMES` | exit 2 `run_bounds_exceeded`; no partial digest success |
| Max `sequence.json` bytes | `32 * 1024 * 1024` | `AUTOMA_DECISION_APPLY_MAX_SEQUENCE_FILE_BYTES` | exit 2 before full parse when size known |
| Max `--record` artifact tree bytes | `8 * 1024 * 1024` | `AUTOMA_DECISION_APPLY_MAX_RECORD_BYTES` | fail closed; **delete** partial record dir |

##### Apply result → `vehicle_decision_apply_result_v0`

| Key | Type | Rule |
| --- | --- | --- |
| `schema` | string | `vehicle_decision_apply_result_v0` |
| `vehicle_id` | string | exact `--id` value |
| `from_run` | string | display path |
| `frame_count` | int | |
| `engine_id` | string | `shadow-proposals` |
| `activation` | string | display path of the activation loaded for `--id` |
| `digest` | object | `vehicle_decision_apply_digest_v0` (below) |
| `digest_sha256` | string | hex SHA-256 of `canonical_json_utf8(digest)` (first pass) |
| `deterministic` | bool | true when second-pass `canonical_json_utf8(digest)` equals first pass byte-for-byte |
| `second_pass_digest_sha256` | string \| null | hex SHA-256 of second-pass digest bytes when verify-twice runs |
| `recorded` | bool | |
| `record_dir` | string \| null | |

##### Digest → `vehicle_decision_apply_digest_v0`

| Key | Type | Rule |
| --- | --- | --- |
| `schema` | string | `vehicle_decision_apply_digest_v0` |
| `frame_count` | int | |
| `frames` | array | one entry per input frame, same order |

Each digest frame entry:

| Key | Type | Rule |
| --- | --- | --- |
| `frame_id` | string | |
| `cycle_status` | string | `ok` \| `engine_error` |
| `cycle_reason` | string | |
| `plan_status` | string \| null | `selected` \| `idle` \| null |
| `selected_proposal_id` | string \| null | |
| `candidates` | array | `{plugin_id, lifecycle, reason, confidence}` only (compact; no source_refs in digest) |
| `proposed` | object \| null | `{steering, throttle}` or null |
| `proposed_applied` | bool | always `false` |
| `authorized_output` | object | idle dict with `shadow-only-idle` |

**Byte-identical determinism (exact):**

1. Build digest object for pass A and pass B (fresh engine each pass).
2. `bytes_a = canonical_json_utf8(digest_a)` and `bytes_b = canonical_json_utf8(digest_b)`.
3. Success requires `bytes_a == bytes_b` (full byte equality, **not**
   `canonical_json_bytes` length equality).
4. Report `digest_sha256 = sha256(bytes_a).hexdigest()`.
5. On mismatch: exit 2 `apply_non_deterministic` and include both hashes in
   details when `--json`.

Default human output prints a short summary plus `digest_sha256`. `--json`
prints the full result object. **No files written by default.**

##### `--record` artifact layout

When `--record` is set:

| Item | Rule |
| --- | --- |
| Output root | `Path(os.environ.get("AUTOMA_DECISION_APPLY_OUTPUT_ROOT", ROOT / "lab" / "runs" / "decision-apply"))` |
| Directory name | `record_dir = output_root / f"{vehicle_id}-{utc_compact_timestamp}-{short_nonce}"` (ASCII-safe vehicle id). **Fail if exists** (`exist_ok=False`). |
| Temp build | Write under `record_dir` with `.partial` suffix or sibling temp name; rename to final `record_dir` only when complete and under byte budget |

```text
<record_dir>/
  manifest.json
  result.json
  digest.json
  frames/
    <frame_id>.html    # one exact-frame HTML per input frame
  # optional copies of source images when present in from-run:
  source_frames/
    <frame_id>.png     # only when {from_run}/frames/<frame_id>.png existed
```

| Artifact | Rule |
| --- | --- |
| `manifest.json` | schema `decision_exact_frame_review_v0`: bounds, frame list with per-frame `html` path and optional `source_image` relative path, `proposed_applied=false` note |
| `result.json` | copy of `vehicle_decision_apply_result_v0` with `recorded=true` |
| `digest.json` | the digest object |
| `frames/<frame_id>.html` | combined view template; **must** include selected candidate `source_refs` (see Combined view item 3b) |

Atomicity/cleanup: on any failure or oversize, remove the partial tree (strict
delete; exit 2 if cleanup also fails, mentioning both errors).

#### Combined view (`decision-combined-v0`)

One correlated explanation per `frame_id` (stream human screen and `--record`
HTML share the same field set):

1. Observation / camera plate: show linked image when
   `frames/<frame_id>.png` (stream/live path) or record `source_frames/<frame_id>.png`
   exists; else explicit unavailable.
2. Retained evidence with provenance ids (from memory summary / source).
3. Proposal list: plugin, lifecycle, freshness, confidence, reason, command.
3b. **`source_refs` (required for acceptance when plan status is `selected`):**
    render the **selected** candidate's full `source_refs` array from
    `cycle.plan.candidates[selected]` (same objects as PR #74). Also list
    each candidate's `source_refs` in the proposal list. HTML must contain a
    dedicated section `source_refs` whose serialized entries match the selected
    proposal (or empty only when the selected proposal has empty refs — which
    fails the adversarial acceptance row below when status is selected and
    product evidence expects refs). Stream human view shows the same selected
    refs line.
4. Selection: `selected_proposal_id` or idle; contribution `plugin_id`.
5. Authority: `proposed` vs `authorized_output` idle; **`proposed_applied=false`**
   emphasized; `host_application` status shown as reported/unavailable — **never**
   an invented applied-control zero presented as host truth.
6. Non-claims line: no object identity; shadow-only; not navigation certification.

Consumes PR #74 serialized objects (`cycle` / plan / authority); no second
proposal schema. Compact digests may omit `source_refs`; stream
`plan_summary.candidates[].source_refs` and `--record` HTML **must not**.

### Ownership

| Concern | Owner |
| --- | --- |
| AutonomyManager adapter (`reset`/`step`/`describe_schema` → `run_cycle`) | `implementations/decision/shadow_adapter.py` |
| Catalog registration (no policy change) | `implementations/decision/catalog.py` + `cli/automa_cli/decision.py` `DECISION_ENGINES` |
| Decision stage/info | `cli/automa_cli/decision.py` |
| Decision apply/replay + digest + `--record` | `cli/automa_cli/decision.py` (or focused sibling under `cli/automa_cli/`) |
| Decision stream (read latest frame) | `cli/automa_cli/decision.py` + streaming helpers as needed |
| Latest-frame publication + atomic write | `cli/automa_cli/automation.py` (+ shared helper in `decision.py` preferred) |
| Combined decision view template | `cli/automa_cli/` HTML/view asset |
| Shadow engine / plugin / authority (unchanged) | PR #74 modules — **call only** |
| Deterministic tests | `tests/cli/` and `tests/implementations/decision/` as needed |

### Affected Paths

| Path | Expected result |
| --- | --- |
| Stage `shadow-proposals` then `info --json` | Complete contract; authority shadow-only; adapter engine_spec |
| `AutonomyManager` load of staged `shadow-proposals` | Succeeds (adapter `reset`/`describe_schema`); no `EngineLoadError` |
| Automation cycle with fixture/shadow adapter | Publishes atomic `latest_decision.json` with full cycle + summaries |
| Stream after publish | Latest-frame replacement; generation fields present; `proposed_applied=false`; no `applied_control` key |
| Stream while engine is `idle` | exit 2 `wrong_engine` |
| Stream after worker stop / restage / age > max | exit 2 `latest_frame_stale` |
| Adapter step invalid frame_id after a good frame | `last_cycle_result is None`; prior frame not republished as new |
| Stream/apply with empty or unavailable memory | Fail-closed inactive or missing_input; idle plan; `proposed_applied=false` |
| Apply without `--id` | exit 2 `missing_vehicle_id` |
| Apply with idle activation | exit 2 `wrong_engine` |
| Apply twice same `--id` + `--from-run` | `canonical_json_utf8` digests equal; matching sha256 |
| Apply without `--record` | No review artifact files |
| Apply with `--record` | Exact-frame HTML under fixed output root; selected `source_refs` rendered; partial tree cleaned on failure |
| Apply oversize sequence / record | exit 2; no successful partial record left behind |
| Apply memory records containing a non-object entry | `run_invalid` (no silent drop) |
| Invalid engine config | Activation rejected |
| Stream without activation | exit 2; remediation text |
| Privilege keys in constructed sources used by surfaces | Rejected (reuse/extend PR #74 source tests) |

## Adversarial Matrix

| Case | Expected result |
| --- | --- |
| `update decision --engine ghost` | Reject; list known engines (`idle`, `shadow-proposals`) |
| Invalid `steer_magnitude` / empty `enabled_plugins` | Reject activation; no write |
| Stage then `AutonomyManager` load | Adapter loads; bare `ShadowProposalsEngine` is **not** the activation `engine_spec` |
| `info` without activation | Exit 2; point to update |
| `info` omits authority or view template | Fail acceptance |
| Stream while engine is `idle` | Exit 2 `wrong_engine` only |
| Stream with valid schema but dead worker / old `run_id` / old `activated_at_ms` | Exit 2 `latest_frame_stale` |
| Valid frame then invalid frame_id step | No publish of stale prior `last_cycle_result` |
| Nonzero proposed steering | Stream/view/digest show nonzero **proposed** and idle **authorized_output**; `proposed_applied=false`; no `applied_control` key |
| Host application unavailable | `host_application` unavailable envelope; still no invented applied zeros field |
| Memory unavailable on apply frame | missing_input / idle or inactive plan; `proposed_applied=false` |
| Fresh center-only + retained side (fixture) | inactive (PR #74); no retained fallback |
| Apply without `--id` | `missing_vehicle_id` |
| Apply `--id` with non-shadow activation | `wrong_engine` |
| Sequence `vehicle_id` ≠ `--id` | `run_invalid` |
| Duplicate `frame_id` in sequence | `run_invalid` |
| Observation `things` contains a non-dict | `run_invalid` |
| Memory `records` contains a non-dict | `run_invalid` |
| Apply frames > MAX_FRAMES | `run_bounds_exceeded` |
| Double apply same inputs | `canonical_json_utf8` byte-identical digests (not length-only) |
| Same-length different digests | Must **not** count as deterministic |
| Apply default disk behavior | No review artifacts |
| `--record` then force oversize | Partial dir removed; exit 2 |
| `--record` HTML for selected plan missing selected `source_refs` section | Fail acceptance |
| Alternate default plugin without proposal change | Out of scope / reject |
| Live Chase/Pi package as acceptance for this PR | Out of scope (next frontier) |

## External Assumptions

- PR #74 shadow engine, schemas, and `avoid_recent_obstruction` remain
  importable without modification of their acceptance contracts.
- M005 memory stage/info/stream/replay and idle host paths remain available for
  operators, but **decision apply does not call memory stage replay**.
- Offline fixtures can exercise active and fail-closed decision cycles without
  a live vehicle.
- Automation continues to own Chase live capture; this unit only requires that
  when `shadow-proposals` is staged, automation can load the adapter and publish
  decision frames (fixture tests cover publication helpers without live Chase).
- The later cross-environment evidence frontier will reuse these exact surface
  contracts and the combined view without renaming schemas.

## Non-Goals

- Tracked Chase or stationary PiRacer evidence packages (M006-06).
- Live host drive-mode / pilot-zero attestation packages (M006-07), beyond
  deterministic privilege-free source tests already owned by PR #74 / this
  surface wiring.
- Changing DecisionDataSource / ActionProposal / ActionPlan / authority schemas
  or `avoid_recent_obstruction` selection policy.
- Adding an unconditional `applied_control` field that invents host application.
- A second reference policy, learned mixer, or multi-plugin consensus product.
- Applied vehicle movement or non-idle authority.
- Consuming Chase evaluator / reference-decision / map-privileged state.
- Rebuilding memory through perception during decision apply.
- New perception algorithms or perception tuning.
- Semantic object identity, tracking, SLAM, prediction, or trajectory claims.
- Milestone closeout (M006-08).

## File Impact

### Create

- `implementations/decision/shadow_adapter.py` — `ShadowProposalsAutonomyEngine`
- Decision stream / apply / view helpers under `cli/automa_cli/` as needed
- Combined decision view HTML (or equivalent) asset
- Deterministic CLI + adapter tests
- Offline fixture run directory(ies) for apply/stream tests as needed
- `canonical_json_utf8` helper (bytes) next to existing size helper if not already present

### Modify

- `cli/automa_cli/decision.py` — register `shadow-proposals`, richer info, stream,
  apply (`--id` required), record; shared frame builder; strict apply pre-validation
- `cli/automa_cli/automation.py` — load adapter; clear/invalidates latest frame on
  start; publish only when gate matches; include generation identity
- `cli/automa_cli/app.py` / `vehicles.py` — wire subcommands if not already present
- `autonomy/decision/memory.py` (or shared util) — add `canonical_json_utf8` if created there
- `implementations/decision/catalog.py` — only if a non-behavioral export is
  required (no policy change)
- Milestone `plan.md` / `plan.html` only at implementation handoff transitions

### Remove

- None

### Explicitly deferred (next frontier)

- `docs/milestones/006-.../evidence/chase-shadow-decision/`
- `docs/milestones/006-.../evidence/physical-shadow-decision/`
- Live M006-07 environment attestations on real Chase/Pi hosts

## Validation Plan

### Deterministic (required in CI / PR)

```text
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
# focused tests as implemented, e.g.:
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.cli.decision.test_shadow_decision_surfaces \
  tests.implementations.decision.test_shadow_adapter \
  -v
```

Must prove:

1. Stage `shadow-proposals`; reject unknown engines / invalid config; activation
   `engine_spec` is the adapter class.
2. `AutonomyManager` successfully loads the staged adapter (no `EngineLoadError`).
3. Adapter `step` clears `last_cycle_result` first; success returns idle control
   with reason `shadow-only-idle` and a matching cycle result; invalid frame_id
   leaves `last_cycle_result is None` (no stale republish).
4. Info human + `--json` completeness (inputs, plugins, selector, authority,
   view).
5. Apply requires `--id`; wrong engine → `wrong_engine`; missing id →
   `missing_vehicle_id`.
6. Apply digest determinism: double run with equal `canonical_json_utf8` bytes
   and matching sha256; length-only equality is **not** used.
7. Apply default writes nothing; `--record` writes under the frozen output root
   with exact-frame HTML including selected `source_refs`; oversize/failure
   cleans up.
8. Stream/latest-frame payload includes generation fields; **no**
   `applied_control` key; freshness gates reject stale/dead-worker/restage.
9. Stream with non-shadow activation exits 2 `wrong_engine`.
10. Strict apply pre-validation rejects non-dict observation things/signals and
    non-dict memory records (`run_invalid`).
11. Privileged-origin keys cannot appear in constructed decision sources used by
    the surface (reuse/extend PR #74 tests as needed).
12. No test enables applied non-idle control for `shadow-proposals`.
13. Apply bounds: too many frames / oversize sequence refuse closed.

### Live / external

**Not required for this unit.** Live Chase/Pi packages and host attestations
belong to the next frontier (M006-06–M006-07).

### Documentation

```text
python3 docs/render_markdown.py --check
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/006-decision-facing-perception-readiness \
  --head-ref m006/shadow-decision-surfaces-proposal \
  --base-sha <merge-base> --head-sha <head>
git diff --check
```

## Expected Handoff

Post-merge implementation success template (merge-time identity filled by
`complete-implementation`; do not predeclare PR/SHA):

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Automa decision stage/info/apply/stream/view for shadow-proposals; AutonomyManager adapter over run_cycle with no stale last_cycle_result; generation-scoped latest_decision publication; --id apply with canonical_json_utf8 digest equality; strict apply pre-validation; combined decision view with source_refs; opt-in --record exact-frame HTML; proposed_applied=false and authorized idle output in PR #{pr}",
  "criterion_updates": {
    "M006-05": {
      "status": "Met",
      "evidence": "Decision stage/info/apply/stream/view with concise default, --json, adapter-backed automation publication, byte-equal apply digests, freshness-gated latest-frame stream, combined view with source_refs, opt-in --record HTML, no default disk writes, no invented applied_control in PR #{pr}"
    }
  },
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "Cross-environment shadow proposal evidence is promoted from the frozen next-candidate slot after M006-05.",
    "revisit_when": "Live Chase/Pi evidence packages (M006-06–M006-07) are accepted or closeout planning begins after a plan revision queues M006-08."
  }
}
```

### Sequence after this proposal merges

1. `workflow.py accept-proposal` → `ready_for_implementation`.
2. Implementation branch `m006/shadow-decision-surfaces` implements only this
   contract against PR #74 types.
3. Implementation PR validates the deterministic suite (no live vehicle required).
4. On accept, handoff marks M006-05 Met and promotes **Cross-environment shadow
   proposal evidence** (M006-06–M006-07). Queue Milestone closeout via plan
   revision while that evidence frontier is `ready_for_proposal` if the
   next-candidate slot is empty after promotion.
