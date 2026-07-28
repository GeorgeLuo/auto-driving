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
| Shadow authority | Applied autonomy control is always idle for this milestone; proposals may be nonzero |

### DecisionDataSource (M006-01)

Every proposal plugin and the selector receive **one** immutable
`DecisionDataSource` built once per controller cycle **after** observation and
memory stages complete and **before** any proposal plugin runs.

#### Construction order (one cycle)

1. Obtain cycle timing from `DecisionFrameContext` (`frame_id`, `frame_index`,
   `timestamp_ms`).
2. Attach the current `Observation | None` produced for this cycle (or an
   explicit unavailable/error component if the observe path failed closed).
3. Attach the current `MemorySnapshot` (including empty, unavailable, and error
   snapshots already defined by M005).
4. Attach pattern and projection outputs for this cycle. Until pattern/projection
   stages exist as first-class products, expose them as **typed component slots**
   that may be `unavailable` or `error` without inventing prediction content.
5. Attach vehicle capabilities (static or activation-declared) and prior applied
   autonomy control context for this vehicle process.
6. Freeze the object (frozen dataclass or equivalent). After freeze, any mutation
   attempt by a plugin must not alter the source seen by other plugins or by the
   selector.

#### Required fields

| Field | Type / shape | Notes |
| --- | --- | --- |
| `schema` | exact `decision_data_source_v0` | |
| `source_id` | string | Stable per cycle; recommended `decision-data:{frame_id}` |
| `frame_id` | string | From cycle context |
| `frame_index` | int ≥ 0 | From cycle context |
| `timestamp_ms` | int ≥ 0 | Cycle timestamp used for freshness comparisons |
| `observation` | component envelope | Current observation or unavailable/error |
| `memory` | component envelope | Memory snapshot or unavailable/error |
| `patterns` | component envelope | Pattern outputs or unavailable/error |
| `projections` | component envelope | Projection outputs or unavailable/error |
| `capabilities` | component envelope | Declared vehicle/engine limits for proposal clamping |
| `prior_applied_control` | component envelope | Last **applied** autonomy control (not proposed) |
| `metadata` | strict JSON object | Non-authoritative diagnostics only |

#### Component envelope

Every input component uses one envelope:

```text
{
  "status": "ready" | "unavailable" | "error",
  "value": <typed payload or null>,
  "reason": <string, required when status != ready>,
  "updated_at_ms": <int >= 0>
}
```

Rules:

- `status=ready` ⇒ `value` is the typed payload; `reason` may be empty.
- `status=unavailable` ⇒ `value` is `null`; no silent empty-object stand-in that
  looks ready.
- `status=error` ⇒ `value` is `null`; `reason` is a bounded diagnostic string.
- Plugins must not treat `unavailable`/`error` as empty ready data.
- Envelopes are immutable after source construction.

#### Typed payloads (when ready)

| Component | Payload |
| --- | --- |
| `observation` | Existing `Observation` (or its detached dict form with schema `observation_v0` / current observation schema) |
| `memory` | Existing `MemorySnapshot` (schema `decision_memory_snapshot_v0`) including records and metadata |
| `patterns` | Opaque but **JSON-serializable** mapping with schema key `pattern_bundle_schema` (string). This unit does **not** define prediction algorithms; empty ready map is allowed only if a stage explicitly produced it. Prefer `unavailable` when no stage ran. |
| `projections` | Same rule as patterns with `projection_bundle_schema`. |
| `capabilities` | Mapping with at least: `max_abs_steering` (float in `[0,1]`), `max_abs_throttle` (float in `[0,1]`), `allows_reverse` (bool), `coordinate_frame` (string, default `"image"` for relative image evidence). |
| `prior_applied_control` | Detached applied control dict: `steering`, `throttle`, `confidence`, `reason`, `applied=true` always for this field, plus `source` (`"runtime"` / `"idle"`). |

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
| `steering` | float | Clamped to `[-1, 1]`; same unit convention as `AutonomyControl.steering` |
| `throttle` | float | Clamped to `[-1, 1]`; positive forward, negative reverse; **not** `VehicleAction.forward/reverse` booleans |
| `gear` | `"forward" \| "reverse" \| "hold"` | Derived consistently: `hold` if `abs(throttle) < 1e-9`; else sign of throttle. Must not contradict throttle sign. |
| `normalized` | bool | Always `true` after construction |

Conversion isolation:

- **Runtime adapters** (Donkey / Chase hosts) convert
  `ProposedVehicleCommand` → host-specific applied inputs **only** when a future
  milestone permits application. This unit’s adapters always emit applied idle.
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
| `proposal_id` | string | Stable within cycle: `{plugin_id}:{frame_id}` |
| `plugin_id` | string | Catalog id; reference is `avoid_recent_obstruction` |
| `lifecycle` | enum | See lifecycle table |
| `confidence` | float | In `[0, 1]` after normalization |
| `reason` | string | Bounded human-readable explanation (≤ 240 chars recommended) |
| `command` | `ProposedVehicleCommand` or `null` | **Required non-null** when lifecycle is `fresh` or `retained`; **must be null** when lifecycle is `inactive`, `stale`, `incompatible`, `missing_input`, or `error` |
| `freshness` | enum | `fresh`, `retained`, `stale`, `none` — independent of lifecycle when inactive for non-age reasons |
| `assumptions` | list[string] | Explicit limits (e.g. `"no_object_identity"`, `"image_relative_only"`) |
| `source_refs` | list[SourceRef] | Exact evidence/pattern/projection references |
| `available` | bool | `true` only for `fresh` or `retained` |
| `metadata` | strict JSON object | Non-authoritative |

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
| `plan_id` | string | `action-plan:{frame_id}` |
| `frame_id` | string | |
| `timestamp_ms` | int | From source |
| `status` | `"selected" \| "idle"` | |
| `selected` | ActionProposal or null | Deep-detached copy |
| `contributions` | list | As above |
| `candidates` | list[ActionProposal] | Complete set, detached, stable order by plugin_id |
| `selector_id` | exact `deterministic_first_active` | |
| `metadata` | object | May include counts by lifecycle |

### Shadow authority: proposed vs applied (M006-03)

After the plan is produced, runtime authority emits
`ShadowAuthorityResult` (`shadow_authority_result_v0`) **separate** from the plan:

| Field | Type | Rule |
| --- | --- | --- |
| `schema` | exact `shadow_authority_result_v0` | |
| `frame_id` | string | |
| `proposed` | `ProposedVehicleCommand` or null | Copy of selected command when plan status is `selected`; else null |
| `applied` | applied control dict | **Always** idle for this milestone: `steering=0.0`, `throttle=0.0`, `applied=true`, `reason` includes `shadow-only` |
| `proposed_applied_equal` | bool | Must be computed; for nonzero proposed must be `false` |
| `authority_mode` | exact `"shadow_only"` | |
| `drive_mode_gate` | string | Echo host mode when known (`user` / `autonomy` / `unknown`) without overriding host gates |

Rules:

- Decision-cycle **applied** `AutonomyControl` returned to the vehicle path must
  remain idle (steering 0, throttle 0) regardless of selected proposal.
- Inspectable outputs must show both proposed and applied so operators cannot
  confuse them.
- Host DriveMode / user-mode gates remain authoritative; this unit does not
  bypass them.

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
| Memory ready | `memory.status == ready` | `missing_input` |
| Observation optional | if not ready, may still use retained memory | — |
| Patterns/projections | not required | ignore if unavailable |

#### Record selection

From `memory.value.records` (retained evidence list):

1. Consider only records with `location` present and `location.frame == "image"`
   (or capabilities coordinate_frame when it is `image`).
2. Prefer kinds commonly used for floor/obstruction boundaries when present:
   `floor_boundary`, `obstacle`, or labels containing `boundary` / `obstacle`
   (case-insensitive). If none match, any located record may be considered
   (documented in reason).
3. Drop records without usable lateral cue:
   - `location.zone` in `{left, right}` **or**
   - `bbox_xyxy_norm` present so mid_x = `(x0+x1)/2` is defined.
4. Primary record = highest `confidence`; tie-break by `record_id` ascending.
5. If none remain → lifecycle `inactive`, freshness `none`, command null,
   reason explains no lateral obstruction evidence.

#### Lateral side

| Cue | Side |
| --- | --- |
| `zone == "left"` | left |
| `zone == "right"` | right |
| `zone == "center"` or missing zone, bbox mid_x < 0.45 | left |
| bbox mid_x > 0.55 | right |
| otherwise (center band) | **inactive** (no steer-away claim) |

#### Freshness policy

Let `now = source.timestamp_ms`, `updated = record.provenance.updated_at_ms`,
`age = max(0, now - updated)`.

| Condition | freshness | lifecycle (if otherwise active) |
| --- | --- | --- |
| `age == 0` or record’s provenance.frame_id == source.frame_id | `fresh` | `fresh` |
| `0 < age ≤ retained_max_age_ms` | `retained` | `retained` |
| `age > retained_max_age_ms` | `stale` | `stale` (command null) |

Default `retained_max_age_ms = 1000` unless activation config overrides with a
positive int. Stale is **not** an error; it is an explicit lifecycle.

If memory bounds expose `max_age_ms` and the record would already be expired by
ledger policy, treat as missing/inactive rather than inventing a command.

#### Command when active (`fresh` or `retained`)

| Side | steering | throttle | gear |
| --- | --- | --- | --- |
| left obstruction | `+steer_magnitude` (steer right / away) | `0.0` | `hold` |
| right obstruction | `-steer_magnitude` (steer left / away) | `0.0` | `hold` |

Default `steer_magnitude = 0.35`, clamped by `capabilities.max_abs_steering`.
Confidence = clamp(primary record confidence, 0, 1).

#### Incompatible

Emit `incompatible` when memory is ready but:

- records exist with locations in a non-image frame only; or
- primary candidate has location without zone and without bbox; or
- properties/types are not as retained-evidence contract (implementation-level
  validation failure on record shape).

#### Missing input

Emit `missing_input` when `memory.status != ready` (unavailable or error), with
reason naming the envelope status.

#### Error

Emit `error` only for unexpected plugin exceptions or violated internal
invariants (e.g. failed to build command after selecting active side). Do not
use `error` for ordinary absence of evidence (`inactive`) or age-out (`stale`).

#### Source refs when active or stale

Include at least:

- memory_record ref with `record_id`, `frame_id`, `observation_id`,
  `plugin_id` from provenance;
- optional observation ref when observation envelope is ready.

### Runner / activation shape (implementation obligation)

This unit introduces the minimal activation/runner so plugins execute:

- Activation engine id: `shadow-proposals`
- Config keys (optional overrides): `retained_max_age_ms`, `steer_magnitude`,
  `enabled_plugins` (default `["avoid_recent_obstruction"]`)
- Per cycle: build DecisionDataSource → run each enabled plugin → select plan →
  build ShadowAuthorityResult → return applied idle AutonomyControl to the
  vehicle path while retaining plan/proposals/authority for inspection

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

- Success: ready components → plugin emits fresh/retained proposal → selector
  selects it → authority shows nonzero proposed, applied idle.
- Missing memory: plugin `missing_input`; plan idle; applied idle.
- Stale evidence: plugin `stale`; plan idle unless another plugin active (none
  in this unit).
- Incompatible geometry: plugin `incompatible`; plan idle.
- Inactive (no lateral evidence): plugin `inactive`; plan idle.
- Plugin exception: plugin `error`; plan idle; cycle still returns applied idle.
- Multi-plugin future: selector ordering defined now; only one plugin ships.
- Serialization: all public objects round-trip through strict JSON dicts.
- Evaluator leakage: construction rejects or ignores evaluator keys; tests assert
  absence.

## Adversarial Matrix

| Case | Expected result |
| --- | --- |
| DecisionDataSource frozen; plugin mutates a dict field it received | Other plugins/selector still see original values (detach/freeze) |
| Memory envelope `unavailable` | `avoid_recent_obstruction` → `missing_input`; plan idle |
| Memory envelope `error` | `missing_input` or `error` with reason; no command |
| Empty ready memory records | `inactive`; freshness `none` |
| Left-zone floor_boundary fresh (frame_id match) | `fresh`; steering `+steer_magnitude`; throttle 0 |
| Right-zone obstacle retained within age | `retained`; steering `-steer_magnitude` |
| Evidence age > retained_max_age_ms | `stale`; command null |
| Center-only bbox mid_x in (0.45, 0.55) | `inactive` |
| Non-image location frame only | `incompatible` |
| Two active proposals different confidence | Higher confidence selected; weight 1.0; no blend |
| Two active equal confidence | Lower `plugin_id` lexicographic wins |
| Selected nonzero proposal | Authority `proposed` nonzero; `applied` idle; `proposed_applied_equal=false` |
| No active proposals | Plan `idle`; authority proposed null; applied idle |
| Proposal command uses VehicleAction booleans only | Reject at construction; plugins must use ProposedVehicleCommand |
| Source includes evaluator/map privileged keys | Not present on DecisionDataSource; tests fail if leaked into propose() inputs |
| source_refs missing on fresh proposal | Validation error / plugin fails closed to `error` |
| command non-null on stale lifecycle | Reject at proposal validation |
| Plugin attempts memory.update | Not available on DecisionDataSource; no write path |
| Patterns/projections unavailable | Reference plugin still works from memory alone |
| prior_applied_control ready with last idle | Visible on source; plugin does not need it for M006-04 but field exists for later prediction work |
| Confidence outside [0,1] | Normalized or rejected at proposal construction (pick one in impl; tests lock it) |
| Catalog enables only avoid_recent_obstruction | Runner order deterministic |

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
2. DecisionDataSource is immutable across plugins; unavailable/error envelopes
   are distinct from empty ready payloads.
3. ProposedVehicleCommand is the only proposal command shape; no plugin emits
   applied AutonomyControl as its proposal API.
4. Selector matches `deterministic_first_active` exactly; no blending.
5. Authority always reports applied idle when engine is `shadow-proposals`.
6. `avoid_recent_obstruction` covers fresh, retained, stale, inactive,
   incompatible, missing_input, and error paths.
7. No live vehicle dependency in CI for this unit.
8. No evaluator fields on DecisionDataSource.

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
