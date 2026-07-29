# Proposal: Automa shadow decision surfaces

Milestone: 006 Decision-Facing Perception Readiness  
Frontier: Automa shadow decision surfaces  
Proposal branch: `m006/shadow-decision-surfaces-proposal`  
Implementation branch: `m006/shadow-decision-surfaces`  
Exit criteria: M006-05  

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
with no default disk writes while applied control remains zero?

This proposal is ready for implementation only if an implementer can wire Automa
surfaces around PR #74 **without inventing decision policy**, changing
proposal/plan/authority semantics, selecting another plugin, applying vehicle
movement, consuming privileged Chase evaluator state, shipping live Chase/Pi
evidence packages, or claiming physical navigation readiness.

Live Chase and stationary PiRacer packages remain the **next frontier**
(M006-06–M006-07) and are out of scope here except that surface contracts must
be consumable unchanged by that later evidence unit.

## Proposed Contract

### Design constants

| Constant | Exact value / rule |
| --- | --- |
| Engine activation id | `shadow-proposals` (PR #74 catalog; no second engine) |
| Reference plugin | `avoid_recent_obstruction` only (no alternate policy) |
| Selector | `deterministic_first_active` (unchanged) |
| Authority | `proposed_applied=false`; authorized idle reason exact `shadow-only-idle` |
| Decision activation schema | `automa_decision_activation_v0` (extend, do not fork) |
| Info payload schema | `vehicle_decision_info_v0` (human + `--json`) |
| Update payload schema | `vehicle_decision_update_v0` |
| Stream / latest frame schema | `vehicle_decision_stream_frame_v0` (below) |
| Replay digest schema | `vehicle_decision_apply_digest_v0` |
| Combined view id | `decision-combined-v0` |
| Exact-frame HTML schema | `decision_exact_frame_review_v0` |

### Scope (M006-05 only)

| In | Out |
| --- | --- |
| Stage / info / apply / stream / view for `shadow-proposals` | Tracked Chase evidence packages (M006-06) |
| Deterministic offline apply + double-run identity | Tracked stationary Pi packages (M006-06) |
| Combined correlated visual explanation template | Live environment attestation procedures (M006-07 host proofs beyond deterministic privilege tests) |
| Opt-in `--record` exact-frame HTML; no default disk writes | Perception retune, second policy, applied movement |
| Deterministic CLI/unit tests in default CI | Live vehicle dependency in CI |

Product implementation is limited to **operator surfaces, packaging, correlation
view, and offline replay recording** around the already-accepted shadow engine.
It must **import and call** PR #74 types and runner; it must not edit plugin
selection policy, lifecycle matrix, selector ranking, authority idle guarantee,
or privilege-free DecisionDataSource rules.

### Operator workflow (deterministic / local fixtures)

```text
# 1) Stage shadow decision engine
./cli/automa vehicles update decision --id <vehicle> --engine shadow-proposals

# 2) Inspect contract
./cli/automa vehicles info decision --id <vehicle>
./cli/automa vehicles info decision --id <vehicle> --json

# 3a) Live stream path (local staged host or fixture-backed as implemented)
./cli/automa vehicles automation run --id <vehicle>   # when applicable
./cli/automa vehicles stream decision --id <vehicle>

# 3b) Offline deterministic apply
./cli/automa vehicles decision apply --from-run <dir>
./cli/automa vehicles decision apply --from-run <dir> --record
```

Implementation may refine flag names only if the **verbs and outcomes** remain
operator-visible and the validation plan lists the exact final commands.

### Surface contracts

#### Stage (`update decision`)

- Accepts `--engine shadow-proposals`; rejects unknown engines (exit ≠ 0; list
  known ids including at least `idle` and `shadow-proposals` after this unit).
- Writes `automa_decision_activation_v0` under the vehicle bundle.
- For `shadow-proposals`, `engine_config` must satisfy PR #74
  `ShadowProposalsConfig`. Invalid config **rejects activation** (no partial
  write that would load an invalid engine).
- Concise default text; `--json` → full `vehicle_decision_update_v0`.
- Does **not** enable applied movement authority.

#### Info (`info decision`)

Human default **must** name at least:

| Field | Requirement |
| --- | --- |
| Engine id / spec | `shadow-proposals` and import path |
| Decision inputs | observation, memory, patterns, projections, capabilities, prior_host_applied_command (by name) |
| Enabled plugins | exact list from activation (default includes `avoid_recent_obstruction`) |
| Selector | `deterministic_first_active` |
| Output schemas | action_proposal / action_plan / shadow_authority / cycle result schema ids |
| Authority | shadow-only; `proposed_applied=false`; authorized idle reason `shadow-only-idle` |
| Combined view | URL or path template for the decision combined view |

`--json` → complete `vehicle_decision_info_v0`. Missing activation → exit 2 with
remediation pointing at `update decision`.

#### Stream (`stream decision`)

- **Latest-frame replacement** primary UX (not unbounded full-history scroll).
- Each frame (`vehicle_decision_stream_frame_v0`) **must** include:

| Key | Rule |
| --- | --- |
| `frame_id` | ASCII id grammar from PR #74 |
| `timestamp_ms` | cycle timestamp |
| `observation` | envelope summary or ready observation identity (no privileged handles) |
| `memory` | health + accepted-kind record summaries with provenance ids/frame_ids |
| `plan` | status `selected`/`idle`, `selected_proposal_id`, candidate summaries |
| candidates / selected | lifecycle, freshness, confidence, reason, command or null, source_refs |
| `authority` | `proposed`, `authorized_output`, `proposed_applied=false`, `host_application` status, `proposed_equals_authorized` |
| `applied_control` | idle zeros; reason `shadow-only-idle` |

- View must show: source frame (or unavailable), retained evidence, proposal
  status/command, selection, source refs, `applied=false`.
- Fail closed if activation missing or engine is not `shadow-proposals` for the
  shadow-decision stream path (clear error; no silent idle swap).

#### Apply / replay (`decision apply --from-run`)

- Offline, deterministic over a recorded observation sequence (and memory inputs
  or enough perception evidence under staged activations).
- Digest (`vehicle_decision_apply_digest_v0`) per frame: `frame_id`, lifecycle/
  reason, selected id or idle, proposed steering/throttle or null,
  `proposed_applied=false`.
- Two consecutive applies on the same `--from-run` with the same staged
  activations → **byte-identical** digests via `canonical_json_bytes` (or an
  equivalent documented stable JSON serialization using the repository helper).
- **No files written by default.**
- `--record` writes opt-in exact-frame HTML (`decision_exact_frame_review_v0`)
  under a run-local or explicit path.

#### Combined view (`decision-combined-v0`)

One correlated explanation per `frame_id`:

1. Observation / camera plate (or explicit unavailable).
2. Retained evidence with provenance ids.
3. Proposal list: plugin, lifecycle, freshness, confidence, reason, command.
4. Selection: `selected_proposal_id` or idle; contribution plugin_id.
5. Authority: proposed vs authorized idle; **applied=false** emphasized.
6. Non-claims line: no object identity; shadow-only; not navigation certification.

Same template for stream and `--record` HTML. Consumes PR #74 serialized
objects; no second proposal schema.

### Ownership

| Concern | Owner |
| --- | --- |
| Decision stage/info for `shadow-proposals` | `cli/automa_cli/decision.py` + catalog wiring |
| Decision apply/replay + digest + `--record` | `cli/automa_cli/decision.py` (or focused sibling) |
| Decision stream + latest-frame replacement | `cli/automa_cli/decision.py` + streaming helpers |
| Combined decision view template | `cli/automa_cli/` HTML/view asset |
| Shadow engine / plugin / authority (unchanged) | PR #74 modules — **call only** |
| Deterministic CLI tests | `tests/cli/` (and integration as needed) |

### Affected Paths

| Path | Expected result |
| --- | --- |
| Stage `shadow-proposals` then `info --json` | Complete contract; authority shadow-only |
| Stream with fixture obstruction left/right | Selected steer-away; applied false |
| Stream/apply with empty or unavailable memory | Fail-closed inactive or missing_input; idle plan; applied false |
| Apply twice on same run dir | Identical digests |
| Apply without `--record` | No review artifact files |
| Apply with `--record` | Exact-frame HTML; correlated fields; applied false |
| Invalid engine config | Activation rejected |
| Stream without activation | Exit ≠ 0; remediation text |
| Privilege keys in constructed sources used by surfaces | Rejected (reuse/extend PR #74 source tests) |

## Adversarial Matrix

| Case | Expected result |
| --- | --- |
| `update decision --engine ghost` | Reject; list known engines |
| Invalid `steer_magnitude` / empty `enabled_plugins` | Reject activation |
| `info` without activation | Exit 2; point to update |
| `info` omits authority or view URL | Fail acceptance |
| Stream while engine is `idle` on shadow path | Clear error or explicit non-shadow labeling |
| Nonzero proposed steering | Stream/view/digest show nonzero **proposed** and zero **applied** |
| Memory unavailable | missing_input / idle plan; applied false |
| Fresh center-only + retained side (fixture) | inactive (PR #74); no retained fallback |
| Double apply same `--from-run` | Byte-identical digests |
| Apply default disk behavior | No review artifacts |
| `--record` selected frame missing source_refs | Fail acceptance |
| Alternate default plugin without proposal change | Out of scope / reject |
| Live Chase/Pi package as acceptance for this PR | Out of scope (next frontier) |

## External Assumptions

- PR #74 shadow engine, schemas, and `avoid_recent_obstruction` remain
  importable without modification of their acceptance contracts.
- M005 memory stage/info/stream/replay and idle host paths remain available.
- Offline fixtures can exercise active and fail-closed decision cycles without
  a live vehicle.
- The later cross-environment evidence frontier will reuse these exact surface
  contracts and the combined view without renaming schemas.

## Non-Goals

- Tracked Chase or stationary PiRacer evidence packages (M006-06).
- Live host drive-mode / pilot-zero attestation packages (M006-07), beyond
  deterministic privilege-free source tests already owned by PR #74 / this
  surface wiring.
- Changing DecisionDataSource / ActionProposal / ActionPlan / authority schemas
  or `avoid_recent_obstruction` selection policy.
- A second reference policy, learned mixer, or multi-plugin consensus product.
- Applied vehicle movement or non-idle authority.
- Consuming Chase evaluator / reference-decision / map-privileged state.
- New perception algorithms or perception tuning.
- Semantic object identity, tracking, SLAM, prediction, or trajectory claims.
- Milestone closeout (M006-08).

## File Impact

### Create

- Decision stream / apply / view helpers under `cli/automa_cli/` as needed
- Combined decision view HTML (or equivalent) asset
- Deterministic CLI tests for stage/info/apply/stream contracts
- Offline fixture run directory(ies) for apply/stream tests as needed

### Modify

- `cli/automa_cli/decision.py` — register `shadow-proposals`, richer info, stream,
  apply, record
- `cli/automa_cli/app.py` / `vehicles.py` — wire subcommands if not already present
- `implementations/decision/catalog.py` — only non-behavioral registration if
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
# focused CLI / decision surface tests as implemented, e.g.:
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.cli.decision.test_shadow_decision_surfaces \
  -v
```

Must prove:

1. Stage `shadow-proposals`; reject unknown engines / invalid config.
2. Info human + `--json` completeness (inputs, plugins, selector, authority,
   view).
3. Apply digest determinism (double run identical).
4. Apply default writes nothing; `--record` produces exact-frame HTML with
   correlated fields and `proposed_applied=false`.
5. Stream/latest-frame payload fields present on a fixture cycle.
6. Privileged-origin keys cannot appear in constructed decision sources used by
   the surface (reuse/extend PR #74 tests as needed).
7. No test enables applied non-idle control for `shadow-proposals`.

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
  "durable_evidence": "Automa decision stage/info/apply/stream/view for shadow-proposals; deterministic offline apply digests; combined decision view; opt-in --record exact-frame HTML; proposed_applied=false in PR #{pr}",
  "criterion_updates": {
    "M006-05": {
      "status": "Met",
      "evidence": "Decision stage/info/apply/stream/view with concise default, --json, deterministic apply, latest-frame stream, combined view, opt-in --record HTML, no default disk writes in PR #{pr}"
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
