# Proposal: Modular shadow action proposal foundation

Milestone: 006 Decision-Facing Perception Readiness
Frontier: Modular shadow action proposal foundation
Proposal branch: `m006/shadow-proposals-proposal`
Implementation branch: `m006/shadow-proposals`
Exit criteria: M006-01, M006-02, M006-03, M006-04

## Review Question

Can independent action-proposal plugins consume one immutable, cycle-aligned
decision data source and produce attributable, replayable action plans while
runtime authority guarantees that no proposed command is applied?

This proposal is ready for implementation only if an implementer can apply the
contracts below without inventing policy during coding. It freezes the
foundation for M006-01–M006-04. The later combined decision view and
cross-environment evidence (M006-05–M006-07) remain the next frontier and must
not be implemented in this unit beyond schema fields required so that view can
consume the same proposal/plan/authority objects without redefinition.

## Proposed Contract

### Design constants

| Constant | Exact value / rule |
| --- | --- |
| Decision-data schema | `decision_data_source_v0` |
| Action-proposal schema | `action_proposal_v0` |
| Action-plan schema | `action_plan_v0` |
| Authority-result schema | `shadow_authority_result_v0` |
| Engine activation id | `shadow-proposals` |
| Reference plugin id | `avoid_recent_obstruction` |
| Selector id | `deterministic_first_active` |
| Canonical proposed command | `ProposedVehicleCommand` (below) — **not** raw `VehicleAction` and **not** applied `AutonomyControl` |
| Shadow authority | Three distinct channels: proposed command, authorized autonomy output, application status (below) |
| Bounds | See **Serializable bounds** (enforceable ceilings; fail closed) |

### Serializable bounds (M006-02 / M006-03)

All lengths are **maximum inclusive**. Exceeding a bound is **fail-closed** at the
owning constructor or runner (raise / reject the object); do not silently
truncate except where a row explicitly says truncate.

| Bound | Limit | Owner | Overflow behavior |
| --- | --- | --- | --- |
| `reason` string | 240 Unicode code points | `ActionProposal` constructor | Reject proposal construction |
| Each `assumptions[]` entry | 64 code points | constructor | Reject |
| `assumptions` count | 8 | constructor | Reject |
| `source_refs` count | 16 | constructor | Reject |
| Each `SourceRef.id` / `note` | 128 / 64 code points | constructor | Reject |
| Proposal `metadata` serialized JSON | 2048 bytes (UTF-8 canonical) | constructor | Reject |
| Single `ActionProposal` full serialized JSON | 8192 bytes | constructor after field checks | Reject |
| `enabled_plugins` count | 8 | activation / runner | Reject activation or skip run with engine error snapshot |
| Each `plugin_id` | 64 code points | catalog | Reject catalog entry |
| `candidates` count | ≤ enabled plugin count (≤ 8) | plan builder | Reject plan if exceeded |
| `contributions` count | ≤ 8 | plan builder | Reject |
| Plan `metadata` serialized JSON | 2048 bytes | plan builder | Reject |
| Full `ActionPlan` serialized JSON | 65536 bytes | plan builder | Reject |
| Envelope `reason` | 240 code points | DecisionDataSource builder | Reject envelope |
| DecisionDataSource `metadata` | 2048 bytes | builder | Reject |

Plugins that cannot emit a valid proposal under these bounds must return
lifecycle `error` with a short reason (itself ≤ 240), not an oversized object.

### DecisionDataSource (M006-01)

Every proposal plugin and the selector receive **one** immutable
`DecisionDataSource` built once per controller cycle **after** observation and
memory stages complete and **before** any proposal plugin runs.

#### Construction order (one cycle)

1. Obtain cycle timing from `DecisionFrameContext` (`frame_id`, `frame_index`,
   `timestamp_ms`).
2. Attach the current observation as a component envelope (mapping below).
3. Attach memory as a component envelope using the **exact**
   `MemorySnapshot.health` mapping below.
4. Attach pattern and projection outputs as component envelopes. Until those
   stages exist, use `status=unavailable`, `value=null`,
   `reason="stage_not_configured"` (do not invent prediction content).
5. Attach vehicle capabilities (static or activation-declared).
6. Attach **prior host-applied command** only if the host supplies it (below);
   otherwise `unavailable`.
7. Freeze the object (frozen dataclass or equivalent). After freeze, any mutation
   attempt by a plugin must not alter the source seen by other plugins or by the
   selector.

#### Required fields

| Field | Type / shape | Notes |
| --- | --- | --- |
| `schema` | exact `decision_data_source_v0` | |
| `source_id` | string | Exact form `decision-data:{frame_id}` |
| `frame_id` | string | From cycle context |
| `frame_index` | int ≥ 0 | From cycle context |
| `timestamp_ms` | int ≥ 0 | Cycle timestamp used for freshness comparisons |
| `observation` | component envelope | See observation mapping |
| `memory` | component envelope | See memory health mapping |
| `patterns` | component envelope | Usually unavailable in this unit |
| `projections` | component envelope | Usually unavailable in this unit |
| `capabilities` | component envelope | Declared vehicle/engine limits for proposal clamping |
| `prior_host_applied_command` | component envelope | **Not** “last engine idle guess” — see below |
| `metadata` | strict JSON object | Non-authoritative diagnostics only |

#### Component envelope

Every input component uses one envelope:

```text
{
  "status": "ready" | "unavailable" | "error",
  "value": <typed payload or null>,
  "reason": <string, required when status != ready; max 240>,
  "updated_at_ms": <int >= 0>
}
```

Rules:

- `status=ready` ⇒ `value` is the typed payload; `reason` is `""`.
- `status=unavailable` ⇒ `value` is **always** `null`; no empty-object stand-in.
- `status=error` ⇒ `value` is **always** `null`; `reason` is a bounded diagnostic.
- Plugins must not treat `unavailable`/`error` as empty ready data.
- Envelopes are immutable after source construction.

#### MemorySnapshot.health → memory envelope (exact)

| `MemorySnapshot.health` | Envelope `status` | Envelope `value` | `avoid_recent_obstruction` lifecycle |
| --- | --- | --- | --- |
| `healthy` | `ready` | detached `MemorySnapshot` | Normal record selection |
| `empty` | `ready` | detached empty `MemorySnapshot` | `inactive` (no records; not an error) |
| `unavailable` | `unavailable` | `null` | `missing_input` |
| `error` | `error` | `null` | `missing_input` (reason must include `memory_error:`; **not** plugin `error`) |

Notes:

- For `unavailable`/`error` health, do **not** place the snapshot object in
  `value` even if one exists in memory; put a short diagnostic in `reason`.
- `empty` is ready data with zero records — distinct from `unavailable`.
- No other health strings are permitted (M005 already freezes the four values).

#### Observation mapping (exact)

| Cycle observation | Envelope `status` | `value` |
| --- | --- | --- |
| Non-null `Observation` | `ready` | detached observation |
| `None` because observe stage not configured | `unavailable` | `null`, reason `observation_not_configured` |
| Observe path failed closed with diagnostic | `error` | `null`, reason bounded diagnostic |

#### Typed payloads (when ready)

| Component | Payload |
| --- | --- |
| `observation` | Existing `Observation` (detached dict allowed) |
| `memory` | Existing `MemorySnapshot` only when health is `healthy` or `empty` |
| `patterns` | JSON-serializable mapping with `pattern_bundle_schema` string key when a stage produced it; else unavailable |
| `projections` | Same rule with `projection_bundle_schema` |
| `capabilities` | Mapping with at least: `max_abs_steering` (float in `[0,1]`), `max_abs_throttle` (float in `[0,1]`), `allows_reverse` (bool), `coordinate_frame` (string, default `"image"`) |
| `prior_host_applied_command` | Only when host-reported (below) |

#### Prior host-applied command (exact)

Field name: **`prior_host_applied_command`** (not `prior_applied_control`).

| Host report | Envelope |
| --- | --- |
| Host supplies the **final command that was applied to the vehicle actuators** for the previous cycle (including user/manual path when the host can observe it) | `status=ready`, value = `{steering, throttle, confidence, reason, applied: true, source: "host"}` |
| Host cannot observe physical application (typical Chase no-handoff; Pi user mode without an applied-command reporter) | `status=unavailable`, `value=null`, reason `host_did_not_report_applied_command` |
| Host application reporter failed | `status=error`, `value=null`, reason bounded diagnostic |

Forbidden:

- Inferring “applied idle” solely because the shadow engine authorized zero.
- Treating last `AutonomyControl` engine output as applied when DriveMode/user
  path may have moved the vehicle.
- Marking `applied: true` on authorized autonomy output that was not applied.

This unit’s reference plugin **does not require** a ready prior host-applied
command. The field exists so later prediction work can condition on true prior
motion when the host reports it.

#### Forbidden inputs

DecisionDataSource **must not** include:

- Chase evaluator / reference-decision / map-privileged state;
- raw mutable stage handles, plugin instances, or live vehicle clients;
- writable memory or perception handles;
- parallel “debug truth” channels.

Mutation tests (implementation) must show that deep-copy or freeze prevents a
plugin from changing another plugin’s view of the source.

### ProposedVehicleCommand (canonical proposed command)

One canonical proposed-command shape for all plugins and the plan:

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | exact `proposed_vehicle_command_v0` | |
| `steering` | float | Finite; clamped to `[-1, 1]`; same unit convention as `AutonomyControl.steering` |
| `throttle` | float | Finite; clamped to `[-1, 1]`; positive forward, negative reverse; **not** `VehicleAction.forward/reverse` booleans |
| `gear` | `"forward" \| "reverse" \| "hold"` | Derived consistently: `hold` if `abs(throttle) < 1e-9`; else sign of throttle. Must not contradict throttle sign. |
| `normalized` | bool | Always `true` after construction |

Non-finite `steering`/`throttle` ⇒ constructor **rejects** (no NaN/Inf clamp tricks).

Conversion isolation:

- **Runtime adapters** convert `ProposedVehicleCommand` → host inputs **only** when
  a future milestone permits application. This unit never applies proposals.
- Proposal plugins **must not** import DonkeyCar or Chase-specific action types
  to express intent.
- Existing `VehicleAction` remains the vehicle-boundary pulse type; it is not the
  proposal command schema.

### ActionProposal (M006-02)

Each plugin emits zero or one `ActionProposal` per cycle (this unit’s runner
invokes each enabled plugin once). Schema `action_proposal_v0`.

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | exact `action_proposal_v0` | |
| `proposal_id` | string | Exact `{plugin_id}:{frame_id}` |
| `plugin_id` | string | Catalog id; ≤ 64 code points; reference is `avoid_recent_obstruction` |
| `lifecycle` | enum | See lifecycle table |
| `confidence` | float | **Reject** if non-finite or not in `[0, 1]` (no silent clamp at proposal boundary) |
| `reason` | string | Required; max **240** code points; reject if longer |
| `command` | `ProposedVehicleCommand` or `null` | **Required non-null** when lifecycle is `fresh` or `retained`; **must be null** when lifecycle is `inactive`, `stale`, `incompatible`, `missing_input`, or `error` |
| `freshness` | enum | `fresh`, `retained`, `stale`, `none` |
| `assumptions` | list[string] | Max **8** entries; each ≤ **64** code points |
| `source_refs` | list[SourceRef] | Max **16** entries |
| `available` | bool | `true` only for `fresh` or `retained` |
| `metadata` | strict JSON object | Max **2048** serialized bytes |

Full proposal serialization ceiling: **8192** bytes (see Serializable bounds).

#### Lifecycle values

| Lifecycle | Meaning | `available` | `command` |
| --- | --- | --- | --- |
| `fresh` | Active proposal from current-cycle evidence | true | non-null |
| `retained` | Active proposal continuing from retained memory still within age | true | non-null |
| `stale` | Supporting evidence exceeded freshness policy; no command | false | null |
| `inactive` | Plugin ran but has nothing to propose (no claim of error) | false | null |
| `incompatible` | Inputs present but structurally unusable for this plugin | false | null |
| `missing_input` | Required component envelope not `ready` | false | null |
| `error` | Plugin failed closed; reason required | false | null |

#### SourceRef

| Field | Type | Rule |
| --- | --- | --- |
| `kind` | `"observation" \| "memory_record" \| "pattern" \| "projection" \| "capability"` | |
| `id` | string | e.g. memory `record_id`, observation_id |
| `frame_id` | string or null | Exact frame when known |
| `observation_id` | string or null | When applicable |
| `plugin_id` | string or null | Perception/memory source plugin when known |
| `note` | string | Short role tag e.g. `"primary_obstruction"` |

At least one `source_refs` entry is required for `fresh` and `retained`.
`stale` should retain the last supporting refs when known. `missing_input` /
`error` may cite the missing component name via `kind` + `id`.

#### Plugin protocol

```text
propose(source: DecisionDataSource) -> ActionProposal
```

Rules:

- Pure with respect to DecisionDataSource: no memory writes, no perception runs,
  no network/vehicle I/O, no evaluator access.
- Deterministic for identical source payloads.
- Fail closed into `error` or `missing_input` / `incompatible` rather than
  inventing evidence.

### ActionPlan selector / mixer (M006-03)

One deterministic selector with id `deterministic_first_active`.

#### Inputs

- The same `DecisionDataSource`
- The complete ordered list of `ActionProposal` results from enabled plugins
  (catalog order; stable sort by `plugin_id` if order otherwise undefined)

#### Selection algorithm (exact)

1. Partition proposals into **active** (`lifecycle in {fresh, retained}` and
   `available is true` and `command is not null`) vs **inactive_set** (all others).
2. If active is empty:
   - Emit plan with `selected=null`, `contributions=[]` (or only diagnostic
     contributions with `weight=0`), `status="idle"`.
3. If active is non-empty:
   - Sort active by: higher `confidence` descending; tie-break by `plugin_id`
     ascending lexicographic.
   - Select the first proposal after sort.
   - Emit one contribution: `{proposal_id, plugin_id, weight: 1.0, role: "selected"}`.
4. **Never** blend steering/throttle from multiple proposals in this unit.
5. **Never** implement Chase-style consensus, tournaments, or learned mixing.

#### ActionPlan fields (`action_plan_v0`)

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | exact `action_plan_v0` | |
| `plan_id` | string | Exact `action-plan:{frame_id}` |
| `frame_id` | string | |
| `timestamp_ms` | int | From source |
| `status` | `"selected" \| "idle"` | |
| `selected` | ActionProposal or null | Deep-detached copy |
| `contributions` | list | Max 8; as above |
| `candidates` | list[ActionProposal] | Complete set for enabled plugins (≤ 8), detached, stable order by `plugin_id` |
| `selector_id` | exact `deterministic_first_active` | |
| `metadata` | object | Max 2048 serialized bytes; may include lifecycle counts |

Full plan serialization ceiling: **65536** bytes.

### Shadow authority: three channels (M006-03)

After the plan is produced, runtime authority emits
`ShadowAuthorityResult` (`shadow_authority_result_v0`) **separate** from the plan.
It freezes **three** non-interchangeable channels:

| Channel | Field | Meaning for this milestone |
| --- | --- | --- |
| **Proposed** | `proposed` | Selected `ProposedVehicleCommand` or `null` — intent only |
| **Authorized autonomy output** | `authorized_output` | What the autonomy path is allowed to hand to host gates this cycle — **always idle zeros** under `shadow-proposals` |
| **Application status** | `application` | Whether that output (or any autonomy command) was **applied to actuators** — **always not applied** for this engine |

#### ShadowAuthorityResult fields

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | exact `shadow_authority_result_v0` | |
| `frame_id` | string | |
| `proposed` | `ProposedVehicleCommand` or null | Copy of selected command when plan `status=selected`; else `null` |
| `authorized_output` | object | Always `{steering: 0.0, throttle: 0.0, confidence: 1.0, reason: "shadow-only-idle"}` for engine `shadow-proposals` |
| `application` | object | Always `{applied: false, reason: "shadow_only_not_applied"}` for this engine. **Never** `applied: true` for a proposed command in this milestone |
| `proposed_equals_authorized` | bool | See exact table below |
| `authority_mode` | exact `"shadow_only"` | |
| `drive_mode_gate` | string | Echo host mode when known (`user` / `autonomy` / `unknown`) without overriding host gates |

#### `proposed_equals_authorized` (exact)

Compare `proposed` to `authorized_output` on steering/throttle only (gear ignored
if proposed is null). Use absolute tolerance `1e-9`.

| `proposed` | `authorized_output` (always idle) | `proposed_equals_authorized` |
| --- | --- | --- |
| `null` | zeros | **`true`** (no proposal; authorized idle matches “no nonzero intent”) |
| non-null with `abs(steering)<1e-9` and `abs(throttle)<1e-9` | zeros | **`true`** |
| non-null with any `|steering|≥1e-9` or `|throttle|≥1e-9` | zeros | **`false`** |

Note: operator-facing “applied” is **`application.applied`**, always `false` for
this engine. Do **not** set `application.applied=true` for authorized idle.
Completion usage that shows `applied=false` refers to `application.applied`.

Rules:

- Decision-cycle `AutonomyControl` returned toward the vehicle path must equal
  `authorized_output` (idle) regardless of selected proposal.
- Host DriveMode / user-mode gates remain authoritative; this unit does not
  bypass them or claim physical application.
- Inspectable outputs must show `proposed`, `authorized_output`, and
  `application` so operators cannot confuse intent with authorization or
  application.

Compatibility with the later combined decision view: plan, candidates,
authority, and source refs must be serializable together under one JSON object
without renaming these schemas. The view itself is **out of scope** for this
implementation PR.

### Reference plugin: `avoid_recent_obstruction` (M006-04)

Exactly one packaged reference proposal implementation.

#### Intent (diagnostic, not safety)

When retained or current image-relative obstruction evidence appears **left or
right of center**, propose steering **away** from that evidence with zero or
near-zero throttle. This demonstrates the data path; it is **not** collision
avoidance, navigation, or identity tracking.

#### Assumptions (must appear on every proposal from this plugin)

- `no_object_identity` — recurring evidence ids are slots, not physical objects
- `image_relative_only` — uses image-frame zone/bbox, not metric world pose
- `shadow_only` — never claims applied movement
- `single_primary_record` — uses at most one primary memory record per cycle

#### Input requirements

| Requirement | Envelope path | Fail lifecycle |
| --- | --- | --- |
| Decision source present | (runner) | `error` |
| Memory envelope `ready` (`healthy` or `empty` snapshot in value) | `memory.status == ready` | else `missing_input` (covers envelope `unavailable` and `error`) |
| Observation optional | if not ready, may still use retained memory | — |
| Patterns/projections | not required | ignore if not ready |

#### Accepted obstruction evidence (exact)

A record is an **accepted obstruction candidate** only if **all** hold:

1. `kind` is exactly one of: **`floor_boundary`**, **`obstacle`**
   (no label substring matching; no other kinds).
2. `location` is present and `location.frame == "image"`.
3. Lateral cue exists:
   - `location.zone` in `{left, right}`, **or**
   - `bbox_xyxy_norm` is a 4-tuple/list of finite floats so
     `mid_x = (x0 + x1) / 2` is defined.

Unrecognized kinds (including located sensor-frame, surface, generic-region,
or any other kind) are **ignored** for selection. If after filtering no accepted
candidates remain → lifecycle **`inactive`**, command null
(reason `no_accepted_obstruction_evidence`). Do **not** emit a command from
unrecognized located records.

Optional activation override: `accepted_kinds` may replace the default pair only
with a non-empty list of identifier strings (max 8 kinds, each ≤ 64 code points)
reviewed in the implementation PR config defaults. Default remains
`["floor_boundary", "obstacle"]`.

#### Record selection

From `memory.value.records` when memory envelope is ready:

1. Filter to accepted obstruction candidates (exact rules above).
2. Primary record = highest `confidence` among accepted; tie-break by
   `record_id` ascending lexicographic.
3. If none → `inactive`, freshness `none`, command null.

#### Lateral side (accepted candidates only)

| Cue | Side |
| --- | --- |
| `zone == "left"` | left |
| `zone == "right"` | right |
| zone missing or `center`, bbox `mid_x < 0.45` | left |
| zone missing or `center`, bbox `mid_x > 0.55` | right |
| otherwise (center band without left/right zone) | **inactive** (no steer-away claim) |

Zone `left`/`right` wins over bbox when both are present.

#### Freshness policy

Let `now = source.timestamp_ms`, `updated = record.provenance.updated_at_ms`,
`age = max(0, now - updated)`.

| Condition | freshness | lifecycle (if otherwise active) |
| --- | --- | --- |
| `provenance.frame_id == source.frame_id` | `fresh` | `fresh` |
| else if `age ≤ retained_max_age_ms` | `retained` | `retained` |
| else | `stale` | `stale` (command null) |

Default `retained_max_age_ms = 1000` unless activation config overrides with a
positive int ≤ 60_000. Stale is **not** an error; it is an explicit lifecycle.

#### Command when active (`fresh` or `retained`)

| Side | steering | throttle | gear |
| --- | --- | --- | --- |
| left obstruction | `+steer_magnitude` (steer right / away) | `0.0` | `hold` |
| right obstruction | `-steer_magnitude` (steer left / away) | `0.0` | `hold` |

Default `steer_magnitude = 0.35`, then
`min(steer_magnitude, capabilities.max_abs_steering)` (capabilities ready
required; if capabilities not ready, use `0.35` then clamp to `[-1,1]`).

**Confidence on the proposal:** use primary record `confidence` only if it is
finite and in `[0, 1]`; otherwise emit lifecycle `error` with reason
`invalid_record_confidence` (do not clamp at the plugin boundary).

#### Incompatible

Emit `incompatible` when memory is ready and at least one record has
`kind` in the accepted set but:

- every accepted-kind record lacks image-frame location; or
- accepted-kind records exist only with non-image `location.frame`.

(Unrecognized kinds alone → `inactive`, not `incompatible`.)

#### Missing input

Emit `missing_input` when `memory.status` is `unavailable` or `error`
(see health mapping). Reason must name the envelope status
(`memory_unavailable` or `memory_error:...`).

#### Error

Emit `error` only for unexpected plugin exceptions, bound violations when
building the proposal, or invalid primary confidence. Do **not** use `error` for
ordinary absence of accepted evidence (`inactive`) or age-out (`stale`) or
missing memory envelopes (`missing_input`).

#### Source refs when active or stale

Include at least:

- memory_record ref with `record_id`, `frame_id`, `observation_id`,
  `plugin_id` from provenance;
- optional observation ref when observation envelope is ready.

### Runner / activation shape (implementation obligation)

This unit introduces the minimal activation/runner so plugins execute:

- Activation engine id: `shadow-proposals`
- Config keys (optional overrides): `retained_max_age_ms` (default 1000),
  `steer_magnitude` (default 0.35), `enabled_plugins` (default
  `["avoid_recent_obstruction"]`, max 8), `accepted_kinds` (default
  `["floor_boundary", "obstacle"]`)
- Per cycle: build DecisionDataSource → run each enabled plugin → select plan →
  build ShadowAuthorityResult → return **authorized idle** `AutonomyControl` to
  the vehicle path (`application.applied=false`) while retaining
  plan/proposals/authority for inspection
- Host may leave `prior_host_applied_command` unavailable; runner must not invent
  applied history

Operator CLI surfaces (stage/info/stream/apply/view) that complete M006-05 are
**not** required to be finished in this unit; however, types must be importable
and unit-tested so the evidence frontier can wire them without schema churn.
If a minimal `info` dump aids local debug, it must not expand into the full
combined view.

### Ownership

| Concern | Owner |
| --- | --- |
| DecisionDataSource type + freeze | `autonomy/decision/` (new module e.g. `decision_data.py`) |
| ActionProposal, ProposedVehicleCommand, SourceRef | `autonomy/decision/` (e.g. `action_proposal.py`) |
| ActionPlan + deterministic selector | `autonomy/decision/` (e.g. `action_plan.py`) |
| ShadowAuthorityResult + applied idle enforcement | `autonomy/decision/` + cycle/engine integration points |
| Plugin protocol + catalog loading | `autonomy/decision/` + `implementations/decision/` |
| `avoid_recent_obstruction` | `implementations/decision/proposals/avoid_recent_obstruction.py` (path may sharpen) |
| Activation id `shadow-proposals` | decision activation/catalog parallel to memory activation |
| Deterministic proof | focused unit tests listed under File Impact |

### Affected Paths

- Success: memory healthy with accepted obstruction → plugin fresh/retained →
  selector selects → authority: nonzero `proposed`, idle `authorized_output`,
  `application.applied=false`, `proposed_equals_authorized=false`.
- Memory health `empty`: envelope ready; plugin `inactive`; plan idle;
  `proposed=null`; `proposed_equals_authorized=true`.
- Memory health `unavailable` or `error`: envelope unavailable/error with
  `value=null`; plugin `missing_input`; plan idle.
- Stale accepted evidence: plugin `stale`; plan idle (single plugin).
- Only non-accepted kinds located: plugin `inactive` (not a command).
- Accepted kind but non-image frame only: plugin `incompatible`.
- Plugin exception / invalid confidence / bound overflow: plugin `error`; plan
  idle; authorized still idle; `application.applied=false`.
- Serialization: all public objects round-trip under bound ceilings.
- Evaluator leakage: not present on DecisionDataSource; tests assert absence.
- Prior host-applied: default unavailable; never invent applied idle as history.

## Adversarial Matrix

| Case | Expected result |
| --- | --- |
| DecisionDataSource frozen; plugin mutates a dict field it received | Other plugins/selector still see original values (detach/freeze) |
| Memory health `unavailable` | Envelope `unavailable`/`value=null`; plugin `missing_input`; plan idle |
| Memory health `error` | Envelope `error`/`value=null`; plugin `missing_input` (not plugin `error`); plan idle |
| Memory health `empty` | Envelope `ready` with empty snapshot; plugin `inactive` |
| Memory health `healthy`, left-zone `floor_boundary`, frame_id match | `fresh`; steering `+steer_magnitude`; throttle 0 |
| Right-zone `obstacle` retained within age | `retained`; steering `-steer_magnitude` |
| Accepted evidence age > retained_max_age_ms | `stale`; command null |
| Only `kind=surface` (or other non-accepted) with location | `inactive`; no command |
| Accepted kind with non-image location frame only | `incompatible` |
| Center-only bbox mid_x in `[0.45, 0.55]` | `inactive` |
| Two active proposals different confidence | Higher confidence selected; weight 1.0; no blend |
| Two active equal confidence | Lower `plugin_id` lexicographic wins |
| Selected nonzero proposal | `proposed` nonzero; `authorized_output` zeros; `application.applied=false`; `proposed_equals_authorized=false` |
| No active proposals (`proposed=null`) | Plan idle; `proposed_equals_authorized=true`; `application.applied=false` |
| Active proposal with zero command | `proposed_equals_authorized=true`; still `application.applied=false` |
| Proposal command uses VehicleAction booleans only | Reject at construction |
| source_refs missing on fresh proposal | Reject proposal construction |
| command non-null on stale lifecycle | Reject proposal construction |
| reason length 241 | Reject proposal construction |
| >16 source_refs | Reject proposal construction |
| enabled_plugins count 9 | Reject activation / fail closed before run |
| ActionPlan serialized >65536 bytes | Reject plan construction |
| Confidence `1.5` or `NaN` on ActionProposal | Reject construction (no silent clamp) |
| Record confidence `NaN` in plugin | Plugin lifecycle `error` / `invalid_record_confidence` |
| Plugin attempts memory.update | Not available on DecisionDataSource |
| Patterns/projections unavailable | Reference plugin still works from memory alone |
| `prior_host_applied_command` unavailable | Default; plugin ignores; no invented applied history |
| Host reports true prior applied command | Envelope ready; unused by M006-04 plugin |
| Evaluator/map keys in source construction | Must not appear on DecisionDataSource |

## External Assumptions

- M005 bounded evidence memory, provenance, replay, and idle host paths remain
  available at tag `milestone-005` / current mainline.
- Image-relative `ViewLocation` zone/bbox semantics from perception/memory are
  stable enough for a diagnostic steer-away demo.
- Pattern and projection stages may remain unavailable in this unit.
- Host DriveMode / user mode continue to force zero applied pilot output on
  PiRacer; Chase candidate path does not apply proposal commands.
- Later combined decision view will consume these schemas without renaming.

## Non-Goals

- Applied vehicle movement or non-idle authority.
- New perception algorithms, VLM products, or perception tuning.
- Prediction, trajectory, SLAM, or metric motion models.
- Semantic object identity or multi-object tracking claims.
- Complex consensus, learned mixing, or multiple reference policies.
- Consuming Chase evaluator / reference-decision / map state.
- Completing Automa combined decision view, full stream UX, or live Chase/Pi
  evidence packages (M006-05–M006-07 next frontier).
- Preserving parallel proposal command shapes (`VehicleAction` as proposal API).
- Collision-avoidance or safety certification claims for `avoid_recent_obstruction`.

## File Impact

### Create

- `autonomy/decision/decision_data.py` (or equivalent) — DecisionDataSource +
  component envelopes
- `autonomy/decision/action_proposal.py` — ProposedVehicleCommand, SourceRef,
  ActionProposal + validation
- `autonomy/decision/action_plan.py` — ActionPlan + `deterministic_first_active`
  selector
- `autonomy/decision/shadow_authority.py` — ShadowAuthorityResult helpers
- `implementations/decision/proposals/avoid_recent_obstruction.py` — reference
  plugin
- `implementations/decision/catalog.py` / activation wiring for `shadow-proposals`
  as needed
- `tests/autonomy/decision/test_decision_data_source.py`
- `tests/autonomy/decision/test_action_proposal_plan.py`
- `tests/implementations/decision/test_avoid_recent_obstruction.py`
- Focused runner test module proving applied idle + proposed/applied separation

### Modify

- `autonomy/decision/__init__.py` — exports
- `autonomy/decision/cycle.py` and/or runtime engine integration — only as needed
  to build DecisionDataSource, run shadow engine, keep applied idle, and attach
  inspectable plan/authority on cycle results or engine metadata **without**
  applying proposals
- Decision activation / packaging paths parallel to memory activation (minimal)
- Milestone plan/HTML only at implementation handoff transitions

### Remove

- None

### Explicitly deferred (next frontier)

- Automa `vehicles update/info/stream/apply decision` full UX and combined HTML
  view (M006-05)
- Tracked Chase/Pi evidence packages (M006-06, M006-07)

## Validation Plan

Deterministic only for this unit:

```text
PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
# or focused:
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.autonomy.decision.test_decision_data_source \
  tests.autonomy.decision.test_action_proposal_plan \
  tests.implementations.decision.test_avoid_recent_obstruction \
  -v
```

Acceptance requires:

1. Every adversarial matrix row has a direct test or explicit subsumption note.
2. Memory health → envelope mapping is exact; empty ≠ unavailable; error envelope
   has `value=null` and yields plugin `missing_input`.
3. DecisionDataSource is immutable across plugins.
4. ProposedVehicleCommand is the only proposal command shape.
5. Bounds tables are enforced with fail-closed constructors (no silent truncate
   except where not used).
6. Selector matches `deterministic_first_active` exactly; no blending.
7. Authority always: idle `authorized_output`, `application.applied=false`, and
   `proposed_equals_authorized` per the frozen table.
8. `avoid_recent_obstruction` accepts only configured kinds (default
   `floor_boundary`/`obstacle`); unrecognized kinds never produce a command.
9. Confidence rejects non-finite and out-of-range values (no silent clamp at
   ActionProposal construction).
10. No live vehicle dependency in CI; no evaluator fields on DecisionDataSource.

## Expected Handoff

Post-merge implementation success template (merge-time identity filled by
`complete-implementation`; do not predeclare PR/SHA):

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "DecisionDataSource, ActionProposal/Plan, shadow authority, and avoid_recent_obstruction deterministic matrix in PR #{pr}",
  "criterion_updates": {
    "M006-01": {
      "status": "Met",
      "evidence": "Immutable DecisionDataSource with observation/memory/patterns/projections/capabilities/prior_applied envelopes in PR #{pr}"
    },
    "M006-02": {
      "status": "Met",
      "evidence": "ActionProposal schema with lifecycle, confidence, reason, ProposedVehicleCommand, freshness, assumptions, source_refs in PR #{pr}"
    },
    "M006-03": {
      "status": "Met",
      "evidence": "deterministic_first_active ActionPlan selector and ShadowAuthorityResult proposed-vs-applied separation in PR #{pr}"
    },
    "M006-04": {
      "status": "Met",
      "evidence": "avoid_recent_obstruction fresh/retained/stale/inactive/incompatible/missing_input/error matrix in PR #{pr}"
    }
  },
  "risk_remove": [
    "A shared decision-data source could degrade into an untyped mutable bag",
    "`VehicleAction` and `AutonomyControl` currently express direction/throttle differently",
    "A mixer interface could invite premature consensus machinery"
  ],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "Cross-environment shadow proposal evidence is promoted from the frozen next-candidate slot.",
    "revisit_when": "Evidence frontier completes M006-05–M006-07 or closeout decides residual work."
  }
}
```

### Sequence after this proposal merges

1. Accept proposal on the milestone branch:
   ```text
   python3 docs/milestones/workflow.py accept-proposal \
     --plan docs/milestones/006-decision-facing-perception-readiness/plan.md \
     --pr <this-proposal-pr-number>
   ```
2. Start implementation only:
   ```text
   python3 docs/milestones/workflow.py start-implementation \
     --plan docs/milestones/006-decision-facing-perception-readiness/plan.md \
     --branch m006/shadow-proposals
   ```
3. Implement **only** this contract (M006-01–M006-04). Do not implement the
   combined decision-view evidence frontier.
4. After the implementation PR merges, run `complete-implementation` so the
   frozen next candidate (cross-environment evidence) becomes current.
