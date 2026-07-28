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
| Shadow authority | Proposed command, authorized output, `proposed_applied=false`, optional host application report (below) |
| Bounds | See **Serializable bounds** (enforceable ceilings; fail closed) |

### Serializable bounds (M006-02 / M006-03)

All lengths are **maximum inclusive**. Exceeding a bound is **fail-closed** at the
owning constructor or runner (**reject** the object / activation — never silent
truncate, never “skip run with partial output”).

| Bound | Limit | Owner | Overflow behavior |
| --- | --- | --- | --- |
| `frame_id` / `source.frame_id` | 128 code points | DecisionDataSource / context validation | Reject source construction |
| `plan_id` | exact `action-plan:{frame_id}` so ≤ `11 + 128` | plan builder | Derived; reject if `frame_id` invalid |
| `proposal_id` | exact `{plugin_id}:{frame_id}` so ≤ `64 + 1 + 128` | proposal constructor | Reject if components invalid |
| `reason` string | 240 Unicode code points | `ActionProposal` constructor | Reject proposal construction |
| Each `assumptions[]` entry | 64 code points | constructor | Reject |
| `assumptions` count | 8 | constructor | Reject |
| `source_refs` count | 16 | constructor | Reject |
| Each `SourceRef.id` / `note` | 128 / 64 code points | constructor | Reject |
| Proposal `metadata` serialized JSON | 1024 bytes (UTF-8 canonical) | constructor | Reject |
| Single `ActionProposal` full serialized JSON | **4096** bytes | constructor after field checks | Reject |
| `enabled_plugins` count | **4** | activation validation | **Reject activation** (config invalid; engine must not start) |
| Each `plugin_id` | 64 code points | catalog | Reject catalog entry |
| `enabled_plugins` uniqueness | all ids distinct; each must exist in catalog | activation validation | Reject activation |
| `candidates` count | **exactly** `len(enabled_plugins)` (1..4) | plan builder | Reject plan if not equal |
| `contributions` count | **0** when idle; **exactly 1** when selected | plan builder | Reject |
| Plan `metadata` serialized JSON | 1024 bytes | plan builder | Reject |
| Full `ActionPlan` serialized JSON | **24576** bytes (24 KiB) | plan builder | **Must not fail** for any legal candidate set (see budget); if it fails, that is an implementation bug |
| Envelope `reason` | 240 code points | DecisionDataSource builder | Reject envelope |
| DecisionDataSource `metadata` | 2048 bytes | builder | Reject |
| Cycle/authority diagnostic `reason` | 240 code points | cycle result | Reject |

#### Compositional plan budget (invariant: every legal set fits)

With identifier bounds above, worst case **must** serialize successfully:

```text
4 * 4096  (candidates — exactly one per enabled plugin, each ≤ 4096 B)
+ 4096    (selected deep-copy of one candidate, when status=selected)
+ 1024    (plan metadata)
+ 2048    (fixed plan envelope keys + bounded plan_id/frame_id/selector_id/status/contributions)
= 23552  ≤ 24576
```

**Invariant (choose-one, frozen):** every set of 1..4 proposals that each pass
ActionProposal construction **always** yields a successful ActionPlan under
24576 bytes. There is **no** allowed “legal set overflows to engine_error” path.
Implementations must unit-test a maximum legal complete set (4× max-size
proposals + selected copy) and assert **plan construction succeeds**.

#### Cycle result envelope (every successful cycle)

Every cycle produces one frozen `ShadowDecisionCycleResult`
(`shadow_decision_cycle_result_v0`):

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | exact `shadow_decision_cycle_result_v0` | |
| `frame_id` | string | ≤ 128 |
| `status` | `"ok" \| "engine_error"` | |
| `reason` | string | `""` when `ok`; required non-empty ≤ 240 when `engine_error` |
| `source` | DecisionDataSource or null | non-null when source built; null only if source construction failed |
| `plan` | ActionPlan or null | non-null iff `status=ok`; **null** iff `status=engine_error` |
| `authority` | ShadowAuthorityResult | **always** present |

#### Runner outcomes (singular paths)

| Situation | `status` | `plan` | `authority` | `reason` |
| --- | --- | --- | --- | --- |
| Normal selection or idle plan | `ok` | ActionPlan | normal fields | `""` |
| Plugin raises / returns non-ActionProposal | `ok` | ActionPlan including a **synthetic** `error` candidate for that plugin_id (see below) | normal | `""` |
| DecisionDataSource construction fails | `engine_error` | `null` | idle authorized, `proposed=null`, `proposed_applied=false`, `host_application` unavailable | `decision_data_source_invalid` or bounded detail |
| Plan builder invariant broken (candidate count ≠ enabled count, etc.) | `engine_error` | `null` | same idle authority shape | `action_plan_invariant_violated` |
| Unexpected internal exception after source built | `engine_error` | `null` | same idle authority shape | `engine_internal_error` (detail truncated to 240) |

Invalid activation still **rejects activation** before any cycle (no cycle result).

**There is no** “idle plan with empty candidates” alternative for engine_error.
**There is no** placement of cycle errors solely in `authorized_output.reason`
(that field stays `shadow-only-idle` whenever authority is emitted).

#### ShadowAuthorityResult diagnostic field

Add exact field:

| Field | Type | Rule |
| --- | --- | --- |
| `cycle_status` | `"ok" \| "engine_error"` | Mirrors cycle result `status` |
| `cycle_reason` | string | Mirrors cycle result `reason` (max 240) |

So authority alone is inspectable without a separate side channel.

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

Each enabled plugin emits **exactly one** `ActionProposal` per cycle. Schema
`action_proposal_v0`. Ordinary “nothing to do” is lifecycle `inactive`, not
omission. The runner **never** drops a plugin from `candidates`.

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | exact `action_proposal_v0` | |
| `proposal_id` | string | Exact `{plugin_id}:{frame_id}` |
| `plugin_id` | string | Catalog id; ≤ 64 code points; reference is `avoid_recent_obstruction` |
| `lifecycle` | enum | See lifecycle table |
| `confidence` | float | **Reject** if non-finite or not in `[0, 1]` (no silent clamp at proposal boundary) |
| `reason` | string | Required; max **240** code points; reject if longer |
| `command` | `ProposedVehicleCommand` or `null` | Must match compatibility matrix below |
| `freshness` | enum | `fresh`, `retained`, `stale`, `none` — must match matrix |
| `assumptions` | list[string] | Max **8** entries; each ≤ **64** code points |
| `source_refs` | list[SourceRef] | Max **16** entries; count requirements per matrix |
| `available` | bool | Must match matrix (derived; constructor rejects mismatch) |
| `metadata` | strict JSON object | Max **1024** serialized bytes |

Full proposal serialization ceiling: **4096** bytes (see Serializable bounds).

#### Lifecycle × freshness × available × command × source_refs (exact)

Constructor **rejects** any combination not listed. Selector may assume every
admitted proposal already satisfies this matrix.

| `lifecycle` | allowed `freshness` | `available` | `command` | `source_refs` min |
| --- | --- | --- | --- | --- |
| `fresh` | `fresh` only | `true` | non-null | ≥ 1 |
| `retained` | `retained` only | `true` | non-null | ≥ 1 |
| `stale` | `stale` only | `false` | `null` | ≥ 1 (last supporting refs) |
| `inactive` | `none` only | `false` | `null` | ≥ 0 |
| `incompatible` | `none` only | `false` | `null` | ≥ 0 |
| `missing_input` | `none` only | `false` | `null` | ≥ 0 |
| `error` | `none` only | `false` | `null` | ≥ 0 |

Forbidden examples (must reject): `lifecycle=fresh` with `freshness=stale`;
`lifecycle=retained` with `available=false`; `lifecycle=stale` with non-null
`command`; `lifecycle=inactive` with `freshness=fresh`.

#### Lifecycle meanings

| Lifecycle | Meaning |
| --- | --- |
| `fresh` | Active proposal from current-cycle evidence |
| `retained` | Active proposal from retained memory still within age policy |
| `stale` | Best supporting evidence is stale; no command |
| `inactive` | Plugin ran; nothing to propose (including no accepted kinds) |
| `incompatible` | Accepted-kind inputs present but structurally unusable |
| `missing_input` | Required envelope not ready |
| `error` | Plugin failed closed |

#### SourceRef

| Field | Type | Rule |
| --- | --- | --- |
| `kind` | `"observation" \| "memory_record" \| "pattern" \| "projection" \| "capability"` | |
| `id` | string | e.g. memory `record_id`, observation_id; max 128 |
| `frame_id` | string or null | Exact frame when known |
| `observation_id` | string or null | When applicable |
| `plugin_id` | string or null | Perception/memory source plugin when known |
| `note` | string | Short role tag e.g. `"primary_obstruction"`; max 64 |

#### Plugin protocol

```text
propose(source: DecisionDataSource) -> ActionProposal
```

Rules:

- **Must return exactly one** `ActionProposal` instance (never `None`).
- Pure with respect to DecisionDataSource: no memory writes, no perception runs,
  no network/vehicle I/O, no evaluator access.
- Deterministic for identical source payloads.
- Fail closed into `error` or `missing_input` / `incompatible` / `inactive`
  rather than inventing evidence or omitting a return.

#### Runner synthetic candidate (exact)

If `propose` raises or returns a non-`ActionProposal` value, the runner inserts
**exactly one** synthetic proposal for that `plugin_id`:

| Field | Value |
| --- | --- |
| `lifecycle` | `error` |
| `freshness` | `none` |
| `available` | `false` |
| `command` | `null` |
| `confidence` | `0.0` |
| `reason` | `plugin_exception` or `plugin_invalid_return` (≤ 240) |
| `assumptions` | `[]` |
| `source_refs` | `[]` |
| `proposal_id` | `{plugin_id}:{frame_id}` |
| `metadata` | `{}` |

If even the synthetic proposal cannot be constructed (should not happen under
these field limits), the cycle is `engine_error` with reason
`synthetic_error_proposal_failed`.

### ActionPlan selector / mixer (M006-03)

One deterministic selector with id `deterministic_first_active`.

#### Inputs

- The same `DecisionDataSource`
- The complete ordered list of `ActionProposal` results from enabled plugins
  (catalog order; stable sort by `plugin_id` if order otherwise undefined)

#### Selection algorithm (exact)

1. Require `len(candidates) == len(enabled_plugins)` and one candidate per
   enabled `plugin_id`. If not, cycle `engine_error` /
   `action_plan_invariant_violated`.
2. If any candidate fails the lifecycle compatibility matrix (constructor bug),
   cycle `engine_error` / `action_proposal_matrix_violated`.
3. Partition into **active** when **all** hold:
   - `lifecycle in {fresh, retained}`
   - `freshness == lifecycle` (fresh↔fresh, retained↔retained)
   - `available is true`
   - `command is not null`
4. If active is empty:
   - Emit plan with `selected=null`, `contributions=[]`, `status="idle"`.
5. If active is non-empty:
   - Sort active by: higher `confidence` descending; tie-break by `plugin_id`
     ascending lexicographic.
   - Select the first proposal after sort.
   - Emit **exactly one** contribution:
     `{proposal_id, plugin_id, weight: 1.0, role: "selected"}`.
6. **Never** blend steering/throttle from multiple proposals in this unit.
7. **Never** implement Chase-style consensus, tournaments, or learned mixing.

#### ActionPlan fields (`action_plan_v0`)

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | exact `action_plan_v0` | |
| `plan_id` | string | Exact `action-plan:{frame_id}` |
| `frame_id` | string | ≤ 128 code points |
| `timestamp_ms` | int | From source |
| `status` | `"selected" \| "idle"` | |
| `selected` | ActionProposal or null | Deep-detached copy when selected; null when idle |
| `contributions` | list | Empty when idle; exactly one entry when selected |
| `candidates` | list[ActionProposal] | **Exactly** one entry per enabled plugin, detached, stable order by `plugin_id` ascending |
| `selector_id` | exact `deterministic_first_active` | |
| `metadata` | object | Max 1024 serialized bytes; may include lifecycle counts |

Full plan serialization ceiling: **24576** bytes. Under identifier bounds, every
legal candidate set **must** fit (no overflow branch for legal sets).

### Shadow authority: proposed vs authorized vs host application (M006-03)

After the plan is produced, runtime authority emits
`ShadowAuthorityResult` (`shadow_authority_result_v0`) **separate** from the plan.

| Channel | Field | What the decision layer may claim |
| --- | --- | --- |
| **Proposed** | `proposed` | Selected intent only |
| **Authorized autonomy output** | `authorized_output` | What this engine hands to host gates — **always idle zeros** for `shadow-proposals` |
| **Proposed-applied guarantee** | `proposed_applied` | **Always `false`**: the **proposed** command was not applied as vehicle intent by this engine |
| **Host application report** | `host_application` | Envelope: ready only if host reports actuator application this cycle; else unavailable/unknown — **never invented** by shadow authority |

#### ShadowAuthorityResult fields

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | exact `shadow_authority_result_v0` | |
| `frame_id` | string | ≤ 128 |
| `proposed` | `ProposedVehicleCommand` or null | Copy of selected command when plan `status=selected`; **`null` when plan idle or `cycle_status=engine_error`** |
| `authorized_output` | object | Always `{steering: 0.0, throttle: 0.0, confidence: 1.0, reason: "shadow-only-idle"}` for engine `shadow-proposals` (including engine_error cycles) |
| `proposed_applied` | bool | **Always `false`** for engine `shadow-proposals`. Means: the **proposed** command was not applied as proposed. Does **not** claim host actuator application of authorized idle |
| `host_application` | component envelope | Ready only when host reports actuator application this cycle; else `unavailable` / `error` — never invented |
| `proposed_equals_authorized` | bool | See table below (`true` when `proposed` is null) |
| `cycle_status` | `"ok" \| "engine_error"` | Mirrors `ShadowDecisionCycleResult.status` |
| `cycle_reason` | string | Mirrors cycle reason; `""` when ok; max 240 |
| `authority_mode` | exact `"shadow_only"` | |
| `drive_mode_gate` | string | Echo host mode when known (`user` / `autonomy` / `unknown`) without overriding host gates |

#### `proposed_equals_authorized` (exact)

Compare `proposed` to `authorized_output` on steering/throttle only. Tolerance
`1e-9`.

| `proposed` | `authorized_output` (always idle) | `proposed_equals_authorized` |
| --- | --- | --- |
| `null` | zeros | **`true`** |
| non-null with `abs(steering)<1e-9` and `abs(throttle)<1e-9` | zeros | **`true`** |
| non-null with any `|steering|≥1e-9` or `|throttle|≥1e-9` | zeros | **`false`** |

#### Operator-facing `applied=false` mapping

Milestone completion usage that shows **`applied=false`** means
**`proposed_applied=false`** (the proposal was not applied). It must **not** be
implemented as a hardcoded claim that host actuators received nothing: after host
gating, authorized idle may be what the pilot path emits (e.g. non-user modes
forwarding autonomy output). Actual actuator application is only
`host_application` when the host reports it.

Rules:

- Decision-cycle `AutonomyControl` returned toward the vehicle path must equal
  `authorized_output` (idle) regardless of selected proposal.
- Host DriveMode / user-mode gates remain authoritative.
- Inspectable outputs must show `proposed`, `authorized_output`,
  `proposed_applied`, and `host_application` separately.

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

Default `accepted_kinds` (frozen defaults — include packaged Chase sim kind):

```text
["floor_boundary", "obstacle", "obstruction_evidence"]
```

- `floor_boundary` / `obstacle`: physical/packaged floor-plane style kinds
- `obstruction_evidence`: exact kind emitted by packaged Chase `sim_color_targets`

A record is an **accepted obstruction candidate** only if **all** hold:

1. `kind` is exactly one of the activation `accepted_kinds` list (default above;
   no label substring matching).
2. `location` is present and `location.frame == "image"`.
3. Lateral cue exists:
   - `location.zone` in `{left, right}`, **or**
   - `bbox_xyxy_norm` is a 4-tuple/list of finite floats so
     `mid_x = (x0 + x1) / 2` is defined.

Unrecognized kinds are **ignored** for selection. If after filtering no accepted
candidates remain → lifecycle **`inactive`**, command null
(reason `no_accepted_obstruction_evidence`). Do **not** emit a command from
unrecognized located records.

Activation may replace `accepted_kinds` only with a non-empty list of at most 8
identifier strings (each ≤ 64 code points, unique). Invalid config → **reject
activation**.

#### Freshness classification of one accepted record (exact)

Let `now = source.timestamp_ms`, `updated = record.provenance.updated_at_ms`.

| Condition | Resulting freshness class |
| --- | --- |
| `updated > now` (future-dated provenance) | **`invalid_future`** — not selectable; see below |
| `provenance.frame_id == source.frame_id` | `fresh` |
| else if `now - updated` is in `0 .. retained_max_age_ms` inclusive | `retained` |
| else (`now - updated > retained_max_age_ms`) | `stale` |

Do **not** clamp future ages to zero. Default `retained_max_age_ms = 1000`;
activation override must be a finite int with `1 ≤ retained_max_age_ms ≤ 60_000`
or **reject activation**.

#### Record selection (exact order)

From `memory.value.records` when memory envelope is ready:

1. Filter to accepted obstruction candidates (structure rules above).
2. Drop `invalid_future` records from active consideration; if **every** accepted
   candidate is `invalid_future` (and at least one exists) → lifecycle **`error`**,
   reason `future_dated_provenance`, freshness `none`, command null.
3. Partition remaining accepted candidates into `fresh`, `retained`, and `stale`
   classes using the table above.
4. **Active pool** = all `fresh` candidates if any exist; else all `retained`
   candidates if any exist; else empty.
5. If active pool is non-empty:
   - Primary = highest `confidence` in the active pool; tie-break `record_id`
     ascending.
   - Emit lifecycle/freshness matching the primary’s class (`fresh` or
     `retained`), command from lateral policy, `available=true`.
6. Else if any `stale` accepted candidates exist:
   - Primary = highest confidence among stale; tie-break `record_id`.
   - Emit lifecycle `stale`, freshness `stale`, command null, `available=false`,
     source_refs from that primary.
7. Else → `inactive`, freshness `none`, command null.

**Stale high-confidence records must not suppress fresh/retained evidence:**
step 4 guarantees any fresh candidate is preferred to all retained, and any
retained to all stale, before confidence is considered.

#### Lateral side (active primary only)

| Cue | Side |
| --- | --- |
| `zone == "left"` | left |
| `zone == "right"` | right |
| zone missing or `center`, bbox `mid_x < 0.45` | left |
| zone missing or `center`, bbox `mid_x > 0.55` | right |
| otherwise (center band without left/right zone) | **inactive** for this record (try next in pool by confidence order; if none left in active pool, fall through as no active) |

Zone `left`/`right` wins over bbox when both are present.

#### Command when active (`fresh` or `retained`)

| Side | steering | throttle | gear |
| --- | --- | --- | --- |
| left obstruction | `+m` (steer right / away) | `0.0` | `hold` |
| right obstruction | `-m` (steer left / away) | `0.0` | `hold` |

**`steer_magnitude` config:** finite float with **`0 < steer_magnitude ≤ 1`**;
default `0.35`. If not finite or outside that range → **reject activation**.

**Magnitude `m` when capabilities ready:**
`m = min(steer_magnitude, capabilities.value.max_abs_steering)` after validating
`max_abs_steering` is finite and in `(0, 1]`; if capability field invalid →
plugin `error` reason `invalid_capabilities`.

**When capabilities envelope is not ready (`unavailable` or `error`):**
use `m = steer_magnitude` unchanged (still in `(0, 1]` from activation). Do **not**
invent a different default. Reason assumptions must still list
`capabilities_not_ready` in `assumptions` when this path is used.

**Confidence on the proposal:** use primary record `confidence` only if finite
and in `[0, 1]`; otherwise lifecycle `error`, reason `invalid_record_confidence`.

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
- Config (defaults; invalid values **reject activation**):
  - `retained_max_age_ms`: default `1000`; require `1..60000`
  - `steer_magnitude`: default `0.35`; require finite and `0 < value ≤ 1`
  - `enabled_plugins`: default `["avoid_recent_obstruction"]`; max **4** unique
    catalog ids
  - `accepted_kinds`: default
    `["floor_boundary", "obstacle", "obstruction_evidence"]`; max 8 unique ids
- Per cycle: build DecisionDataSource → invoke each enabled plugin once in
  `plugin_id` ascending order → **exactly one candidate each** (synthetic
  `error` if needed) → build ActionPlan → build ShadowAuthorityResult → emit
  `ShadowDecisionCycleResult` → return **authorized idle** `AutonomyControl`
  (`proposed_applied=false`; `host_application` only if host reports)
- Host may leave `prior_host_applied_command` and `host_application` unavailable;
  runner must not invent host application history

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
| ShadowAuthorityResult + authorized-idle / `proposed_applied=false` | `autonomy/decision/` + cycle/engine integration |
| `ShadowDecisionCycleResult` ok/engine_error envelope | same runner module |
| Plugin protocol + catalog loading | `autonomy/decision/` + `implementations/decision/` |
| `avoid_recent_obstruction` | `implementations/decision/proposals/avoid_recent_obstruction.py` (path may sharpen) |
| Activation id `shadow-proposals` | decision activation/catalog parallel to memory activation |
| Deterministic proof | focused unit tests listed under File Impact |

### Affected Paths

- Success: memory healthy with accepted obstruction (fresh/retained preferred) →
  selector selects → authority: `proposed` may be nonzero; `authorized_output`
  idle; `proposed_applied=false`; `host_application` unavailable unless host
  reports; `proposed_equals_authorized=false` when proposed nonzero.
- Stale-only evidence: plugin `stale`; plan idle; `proposed_applied=false`.
- Fresh record present with higher-confidence stale sibling: select **fresh**
  path (not stale).
- Future-dated provenance only: plugin `error` / `future_dated_provenance`.
- Memory empty/unavailable/error: as health mapping; never invent host application.
- Legal max 4-candidate set: plan **always builds** under budget (tested).
- engine_error: `plan=null`, authority present with `cycle_status=engine_error`
  and `cycle_reason` set; authorized idle; `proposed_applied=false`.
- Every enabled plugin appears exactly once in `candidates`.

## Adversarial Matrix

| Case | Expected result |
| --- | --- |
| DecisionDataSource frozen; plugin mutates a dict field it received | Other plugins/selector still see original values (detach/freeze) |
| Memory health `unavailable` / `error` | Envelope not ready / `value=null`; plugin `missing_input` |
| Memory health `empty` | Ready empty snapshot; plugin `inactive` |
| Left-zone `floor_boundary` frame match | `fresh` + steer away |
| `obstruction_evidence` kind (Chase packaged) with lateral cue | Accepted; active when fresh/retained |
| Right-zone `obstacle` retained | `retained` + steer away |
| Stale conf 0.9 and fresh conf 0.8 both accepted | Select **fresh** 0.8 path |
| Only stale accepted evidence | `stale`; command null |
| Future `updated_at_ms > now` | `error` / `future_dated_provenance` (no age clamp) |
| Only non-accepted kind located | `inactive` |
| Accepted kind non-image frame only | `incompatible` |
| `lifecycle=fresh` + `freshness=stale` | Reject at ActionProposal construction |
| Selected nonzero proposal | `proposed_applied=false`; `proposed_equals_authorized=false`; host_application not invented |
| Host does not report application | `host_application.status=unavailable` |
| No active proposals | Plan idle; `contributions=[]`; `proposed_equals_authorized=true`; `proposed_applied=false` |
| `steer_magnitude=-0.35` or `0` or `1.1` or NaN | Reject activation |
| Duplicate `enabled_plugins` or unknown id or count 5 | Reject activation |
| Capabilities unavailable | Use config `steer_magnitude`; assume `capabilities_not_ready` |
| Capabilities ready with invalid max_abs_steering | Plugin `error` / `invalid_capabilities` |
| reason length 241 / >16 source_refs / proposal >4096 B | Reject proposal construction |
| Max legal 4×4096 candidates + selected copy | Plan **must build** under 24576; tested |
| Plugin raises / returns None | Synthetic `error` candidate still present; candidates count unchanged |
| engine_error cycle | `plan is null`; authority `cycle_status=engine_error`; `authorized_output.reason` still `shadow-only-idle`; diagnostic in `cycle_reason` only |
| Confidence `1.5` or `NaN` on ActionProposal | Reject construction |
| VehicleAction as proposal command | Reject construction |
| Evaluator/map keys on DecisionDataSource | Must not appear |

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
  ActionProposal + matrix/bounds validation
- `autonomy/decision/action_plan.py` — ActionPlan + `deterministic_first_active`
  selector
- `autonomy/decision/shadow_authority.py` — ShadowAuthorityResult +
  ShadowDecisionCycleResult helpers
- `implementations/decision/proposals/avoid_recent_obstruction.py` — reference
  plugin
- `implementations/decision/catalog.py` / activation wiring for `shadow-proposals`
  as needed
- `tests/autonomy/decision/test_decision_data_source.py`
- `tests/autonomy/decision/test_action_proposal_plan.py`
- `tests/implementations/decision/test_avoid_recent_obstruction.py`
- Focused runner tests: authorized idle, `proposed_applied=false`, one candidate
  per plugin, max legal plan builds, engine_error shape

### Modify

- `autonomy/decision/__init__.py` — exports
- `autonomy/decision/cycle.py` and/or runtime engine integration — only as needed
  to build DecisionDataSource, run shadow engine, emit authorized idle control,
  attach inspectable cycle result/plan/authority **without** applying proposals
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
3. Lifecycle×freshness×available×command×source_refs matrix is enforced at
   construction; selector never admits contradictory tuples.
4. DecisionDataSource is immutable across plugins.
5. Compositional bounds hold; every legal candidate set fits; max legal set
   **builds** (no dual overflow path for legal sets).
6. Exactly one candidate per enabled plugin; synthetic `error` on plugin failure.
7. engine_error representation is singular: `ShadowDecisionCycleResult` with
   `plan=null` and authority `cycle_status`/`cycle_reason`.
8. Selector matches `deterministic_first_active`; contributions empty or exactly
   one; no blending.
9. Authority: idle `authorized_output`, **`proposed_applied=false`**, host
   application only when reported; `proposed_equals_authorized` per table.
10. Active selection prefers fresh over retained over stale before confidence;
    future-dated provenance fails closed.
11. Default accepted kinds include `obstruction_evidence`; unrecognized kinds
    never produce a command.
12. `steer_magnitude` activation range enforced; capabilities unavailable path
    exact.
13. Confidence rejects non-finite/out-of-range values (no silent clamp).
14. No live vehicle dependency in CI; no evaluator fields on DecisionDataSource.

## Expected Handoff

Post-merge implementation success template (merge-time identity filled by
`complete-implementation`; do not predeclare PR/SHA):

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "DecisionDataSource, ActionProposal/Plan, ShadowAuthorityResult (proposed_applied=false), ShadowDecisionCycleResult, and avoid_recent_obstruction matrix in PR #{pr}",
  "criterion_updates": {
    "M006-01": {
      "status": "Met",
      "evidence": "Immutable DecisionDataSource with observation/memory/patterns/projections/capabilities/prior_host_applied_command envelopes in PR #{pr}"
    },
    "M006-02": {
      "status": "Met",
      "evidence": "Bounded ActionProposal schema with lifecycle/freshness matrix, ProposedVehicleCommand, assumptions, source_refs; one candidate per enabled plugin in PR #{pr}"
    },
    "M006-03": {
      "status": "Met",
      "evidence": "deterministic_first_active ActionPlan; ShadowAuthorityResult proposed vs authorized_output vs proposed_applied=false vs host_application; cycle ok/engine_error envelope in PR #{pr}"
    },
    "M006-04": {
      "status": "Met",
      "evidence": "avoid_recent_obstruction fresh-before-stale selection and lifecycle matrix including obstruction_evidence kind in PR #{pr}"
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
