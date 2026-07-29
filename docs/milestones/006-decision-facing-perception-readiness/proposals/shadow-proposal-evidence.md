# Proposal: Cross-environment shadow proposal evidence

Milestone: 006 Decision-Facing Perception Readiness  
Frontier: Cross-environment shadow proposal evidence  
Proposal branch: `m006/shadow-proposal-evidence-proposal`  
Implementation branch: `m006/shadow-proposal-evidence`  
Exit criteria: M006-05, M006-06, M006-07  

Prerequisite (accepted, do not re-open):

- Implementation PR [#74](https://github.com/GeorgeLuo/auto-driving/pull/74) at
  `7830cb0c509eb6c601bf74f707d8caeca177ed8d`
- Proposal artifact:
  `docs/milestones/006-decision-facing-perception-readiness/proposals/shadow-proposals.md`
- Schemas and engine id frozen there: `decision_data_source_v0`,
  `action_proposal_v0`, `action_plan_v0`, `shadow_authority_result_v0`,
  `shadow_decision_cycle_result_v0`, engine `shadow-proposals`, plugin
  `avoid_recent_obstruction`, selector `deterministic_first_active`

## Review Question

Does the staged `avoid_recent_obstruction` proposal produce deterministic,
provenance-complete shadow action plans and one correlated visual explanation
through the same operator workflow on recorded replay, Chase, and PiRacer
inputs while applied control remains zero?

This proposal is ready for implementation only if an implementer can wire Automa
surfaces and produce tracked evidence packages **without inventing decision
policy**, changing PR #74 proposal/plan/authority semantics, selecting another
plugin, applying vehicle movement, consuming privileged Chase evaluator state,
or claiming physical navigation readiness.

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
| Stream / latest frame schema | `vehicle_decision_stream_frame_v0` (defined below) |
| Replay digest schema | `vehicle_decision_apply_digest_v0` |
| Combined view id | `decision-combined-v0` |
| Exact-frame HTML schema | `decision_exact_frame_review_v0` |
| Evidence package root | `docs/milestones/006-decision-facing-perception-readiness/evidence/` |
| Chase vehicle id (default) | `chase-sim-chaser` |
| Pi vehicle id (default) | `piracer` |
| Pi drive mode for all evidence | `user` (or equivalent host manual/user mode) |
| Pilot / applied autonomy | zero for every frame in every environment |

### Scope split (exact)

| Exit criterion | What this unit must deliver |
| --- | --- |
| **M006-05** | Automa decision **stage / info / apply / stream / view** surfaces with concise default text, complete `--json`, deterministic offline replay, latest-frame replacement stream semantics, one combined frame+evidence+proposal+authority view, opt-in exact-frame HTML via `--record`, **no default disk writes** |
| **M006-06** | Tracked evidence packages on **recorded replay**, **Chase**, and **stationary PiRacer** exercising the same contracts and the same combined view; prove provenance, freshness transitions, selection, proposed intent, and zero applied control |
| **M006-07** | Prove Chase evaluator / reference-decision / map-privileged channels are **absent** from DecisionDataSource and controller inputs used by the shadow engine; prove Pi remains user mode with zero pilot output for the entire evidence set |

Product implementation in this unit is limited to **operator surfaces, packaging,
correlation view, and evidence recording** around the already-accepted shadow
engine. It must **import and call** PR #74 types and runner; it must not edit
plugin selection policy, lifecycle matrix, selector ranking, authority idle
guarantee, or privilege-free DecisionDataSource rules.

### Operator workflow (one shared path)

The same command sequence is the acceptance path for local replay fixtures,
Chase, and Pi. Only `--id` / input source change.

```text
# 1) Stage shadow decision engine (and retain staged memory/perception as needed)
./cli/automa vehicles update decision --id <vehicle> --engine shadow-proposals

# 2) Inspect contract (human default; complete machine contract with --json)
./cli/automa vehicles info decision --id <vehicle>
./cli/automa vehicles info decision --id <vehicle> --json

# 3a) Live path: run automation, then stream latest decision frame
./cli/automa vehicles automation run --id <vehicle>
./cli/automa vehicles stream decision --id <vehicle>

# 3b) Offline path: deterministic apply from a recorded run directory
./cli/automa vehicles decision apply --from-run <dir>
./cli/automa vehicles decision apply --from-run <dir> --record

# 4) Combined visual explanation (URL or local HTML from stream/view/record)
# Must correlate one frame_id across observation, memory overlay, proposal,
# plan selection, and authority (applied=false).
```

Implementation may refine flag names only if the **verbs and outcomes** above
remain operator-visible and the validation plan lists the exact final commands.

### M006-05 surface contracts

#### Stage (`update decision`)

- Accepts `--engine shadow-proposals` and rejects unknown engine ids with a
  non-zero exit and a message listing known catalog ids (including at least
  `idle` and `shadow-proposals` after this unit).
- Writes / updates decision activation under the vehicle bundle using
  `automa_decision_activation_v0`.
- For `shadow-proposals`, activation `engine_config` must be valid under PR #74
  `ShadowProposalsConfig` (enabled plugins 1..4 unique catalog ids defaulting to
  `avoid_recent_obstruction`, accepted kinds, retained age, steer magnitude).
  Invalid config **rejects activation** (exit ≠ 0); does not write a partial
  activation that would load an invalid engine.
- Default text is concise (vehicle, engine id, activation path). `--json` emits
  the full `vehicle_decision_update_v0` payload including manifest.
- Does **not** enable applied movement authority.

#### Info (`info decision`)

Human default **must** name at least:

| Field | Requirement |
| --- | --- |
| Engine id / spec | `shadow-proposals` and import path |
| Decision inputs | observation, memory, patterns, projections, capabilities, prior_host_applied_command (by name) |
| Enabled plugins | exact list from activation (default includes `avoid_recent_obstruction`) |
| Selector / mixer | `deterministic_first_active` |
| Output schemas | action_proposal / action_plan / shadow_authority / cycle result schema ids |
| Authority | shadow-only; `proposed_applied=false`; authorized idle reason `shadow-only-idle` |
| Combined view | URL or path template for the decision combined view |

`--json` emits the complete machine contract (`vehicle_decision_info_v0`) including
activation path, engine_config, schema references, and view location. Missing
activation → exit 2 with remediation text pointing at `update decision`.

#### Stream (`stream decision`)

- Shows **latest-frame replacement** (not an append-only unbounded scroll of full
  history as the primary UX). Each display tick is one correlated frame.
- Each frame payload (`vehicle_decision_stream_frame_v0`) **must** include:

| Key | Rule |
| --- | --- |
| `frame_id` | ASCII id grammar from PR #74 |
| `timestamp_ms` | cycle timestamp |
| `observation` | envelope summary or ready observation identity (no raw privileged handles) |
| `memory` | health + accepted-kind record summaries with provenance ids/frame_ids |
| `plan` | status `selected`/`idle`, `selected_proposal_id`, candidate summaries |
| `selected` / candidates | lifecycle, freshness, confidence, reason, command or null, source_refs |
| `authority` | `proposed`, `authorized_output`, `proposed_applied=false`, `host_application` status, `proposed_equals_authorized` |
| `applied_control` | always idle zeros for this engine; reason `shadow-only-idle` |

- Browser or TUI view must show: source frame (or explicit unavailable), retained
  evidence overlay or structured list, proposal status and command, selected
  contribution, exact source references, and `applied=false`.
- Stream must fail closed if activation is missing or engine is not
  `shadow-proposals` when decision stream is requested for this frontier’s
  evidence path (clear error; no silent idle engine swap).

#### Apply / replay (`decision apply --from-run`)

- Offline, deterministic: given a recorded observation sequence (and memory
  inputs or enough perception evidence to rebuild them under staged activations),
  run the shadow engine in frame order and emit a concise digest.
- Digest (`vehicle_decision_apply_digest_v0`) includes per-frame:
  `frame_id`, proposal lifecycle/reason, selected id or idle, steering/throttle
  proposed (or null), `proposed_applied=false`.
- Two consecutive applies on the same `--from-run` directory with the same staged
  activations must produce **byte-identical** digests when measured with
  `canonical_json_bytes` on the structured digest body (or an equivalent
  documented stable JSON serialization using the repository canonical helper).
- **No files written by default.**
- `--record` writes an opt-in exact-frame HTML review artifact under a run-local
  or explicit output path; artifact schema `decision_exact_frame_review_v0`.
  Default apply without `--record` leaves the tree clean of new review files.

#### Combined view (`decision-combined-v0`)

One correlated visual explanation per frame_id:

1. Observation / camera plate (or explicit unavailable state).
2. Retained evidence (zones/bbox or structured list) with provenance ids.
3. Proposal list: plugin id, lifecycle, freshness, confidence, reason, command.
4. Selection: selected_proposal_id or idle; contribution plugin_id.
5. Authority: proposed vs authorized idle; **applied=false** emphasized.
6. Explicit non-claims line: no object identity; shadow-only; not navigation
   certification.

The same view template is used for stream (live) and exact-frame HTML (record).
It consumes PR #74 serialized objects; it must not invent a second proposal
schema.

### M006-06 evidence packages (tracked)

All packages live under:

```text
docs/milestones/006-decision-facing-perception-readiness/evidence/
  replay-shadow-decision/
  chase-shadow-decision/
  physical-shadow-decision/
```

Each package **must** contain:

| Artifact | Requirement |
| --- | --- |
| `README.md` | Environment, commands run, non-claims, link to PR |
| Structured digest | `apply` digest or stream capture with ≥1 active and ≥1 fail-closed frame when the environment can produce them |
| Provenance sample | At least one selected proposal with `source_refs` pointing at memory_record (and observation when ready) |
| Authority proof | Every frame shows `proposed_applied=false` and authorized idle |
| Combined view sample | Screenshot or exact-frame HTML for at least one selected and one idle/error frame |
| Environment attestation | See M006-07 rows below |

#### Recorded replay package

- Built only from committed or CI-available fixtures (no live vehicle required).
- Demonstrates deterministic double-apply identity.
- Demonstrates lifecycle progression when fixtures support it: at least one of
  fresh → retained → stale → inactive across frames, or explicit fixture limits
  documented if a transition cannot be synthesized offline (must still show
  active + fail-closed).

#### Chase package (`--id chase-sim-chaser` or documented equivalent)

- Uses camera-derived observation / memory path only.
- Shows left or right obstruction evidence producing steer-away when the
  packaged Chase kind (`obstruction_evidence` or accepted kinds) is present.
- Attests evaluator isolation (M006-07).

#### Stationary Pi package (`--id piracer` or documented equivalent)

- Vehicle stationary; drive mode `user` throughout.
- Pilot / applied autonomy output zero throughout.
- Live proposals change with attributable fresh / absent / stale memory evidence
  when the packaged perception chain emits supported evidence; fail-closed
  inactive/missing_input is acceptable and must be shown honestly when
  perception does not emit lateral cues (do **not** retune perception in this
  unit).

### M006-07 isolation and zero-applied proof

| Environment | Required proof |
| --- | --- |
| Chase | DecisionDataSource / cycle inputs used by `shadow-proposals` contain **no** evaluator, reference-decision, map-privileged, ground_truth, or debug_truth channels (same forbidden-origin rules as PR #74). Candidate controller path does not apply proposal commands. |
| PiRacer | Drive mode remains `user` (manual) for every evidence frame; host pilot/applied command is zero; shadow `proposed_applied=false` and authorized idle on every frame. |
| All | No milestone command enables non-idle applied autonomy for this engine. |

Proof may be: structured fields in digests, host status probes already used by
M005 physical checks, and explicit negative tests that privileged keys are
rejected or absent from recorded sources.

### Ownership

| Concern | Owner |
| --- | --- |
| Decision stage/info activation for `shadow-proposals` | `cli/automa_cli/decision.py` + catalog wiring to `implementations.decision.catalog` |
| Decision apply/replay + digest + `--record` HTML | `cli/automa_cli/decision.py` (or focused sibling module under `cli/automa_cli/`) |
| Decision stream + latest-frame replacement | `cli/automa_cli/decision.py` + streaming helpers as needed |
| Combined decision view template | `cli/automa_cli/` HTML/view asset (parallel to memory/perception views) |
| Shadow engine / plugin / authority (unchanged) | `autonomy/decision/*`, `implementations/decision/*` from PR #74 — **call only** |
| Evidence packages | `docs/milestones/006-.../evidence/**` |
| Deterministic CLI tests | `tests/cli/` (and integration tests as needed) |
| Live evidence capture scripts/notes | package README + optional `tests/live/` opt-in only |

### Affected Paths

| Path | Expected result |
| --- | --- |
| Stage `shadow-proposals` then `info decision --json` | Complete contract; authority shadow-only |
| Stream on staged engine with obstruction left/right | Selected steer-away proposal; applied false |
| Stream / apply with empty or unavailable memory | Fail-closed inactive or missing_input; idle plan; applied false |
| Apply twice on same run dir | Identical digests |
| Apply without `--record` | No new review files |
| Apply with `--record` | Exact-frame HTML; correlated fields present |
| Chase evidence | Active + fail-closed samples; no evaluator fields in sources |
| Pi evidence | User mode; zero pilot; applied false; honest inactive if no lateral cue |
| Attempt to stage invalid engine config | Activation rejected |
| Stream without activation | Exit ≠ 0; remediation text |

## Adversarial Matrix

| Case | Expected result |
| --- | --- |
| `update decision --engine ghost` | Reject; list known engines |
| Invalid `steer_magnitude` / empty `enabled_plugins` in config | Reject activation |
| `info decision` without activation | Exit 2; point to update |
| `info` human text omits authority or view URL | Fail acceptance (must name shadow-only + view) |
| Stream while engine is `idle` for decision-evidence path | Clear error or explicit non-shadow labeling; evidence packages require `shadow-proposals` |
| Plugin would propose nonzero steering | Stream/view/digest show nonzero **proposed** and zero **applied** |
| Memory unavailable | missing_input / idle plan; applied false |
| Fresh center-only + retained side (fixture) | inactive (PR #74 policy); no retained fallback |
| Prior-frame proposal id in replay input | Not selected; synthetic or current-frame only (PR #74 admission) |
| Double apply same `--from-run` | Byte-identical digest bodies |
| Apply default disk behavior | No review artifact files created |
| `--record` artifact missing source_refs on selected frame | Fail package acceptance |
| Chase source includes `EvaluatorOutput` / map privileged keys | Rejected or absent; package fails if present in recorded source |
| Pi drive mode not user during capture | Package invalid |
| Pi non-zero pilot/applied autonomy | Package invalid |
| Alternate plugin staged as default without proposal change | Out of scope / reject as contract change |
| Perception retune “to make Pi pass” | Forbidden; document honest inactive instead |
| Claim collision avoidance / navigation readiness in view copy | Forbidden non-claim text required |

## External Assumptions

- PR #74 shadow engine, schemas, and `avoid_recent_obstruction` remain on the
  milestone branch and importable without modification of their acceptance
  contracts.
- M005 memory stage/info/stream/replay, bounded evidence ledger, and idle host
  paths remain available.
- Packaged Chase can emit `obstruction_evidence` (or other accepted kinds) with
  image-frame lateral cues in at least one scripted scenario.
- Packaged physical perception may not always emit lateral cues; inactive
  outcomes are valid evidence.
- PiRacer can be held stationary in user mode with zero pilot output for the
  capture window (same assumption class as M005 physical memory checks).
- Operator has Automa vehicle bundle access for Chase and Pi as in prior
  milestones.

## Non-Goals

- Changing DecisionDataSource / ActionProposal / ActionPlan / authority schemas
  or `avoid_recent_obstruction` selection policy.
- A second reference policy, learned mixer, or multi-plugin consensus product.
- Applied vehicle movement, non-idle authority, or DriveMode automation for
  motion.
- Consuming Chase evaluator / reference-decision / map-privileged state.
- New perception algorithms, VLM products, or perception tuning “to pass” Pi.
- Semantic object identity, tracking, SLAM, prediction, or trajectory claims.
- Milestone closeout (M006-08) or activating a later movement milestone.
- Claiming physical navigation readiness or safety certification.

## File Impact

### Create

- Decision stream / apply / view helpers under `cli/automa_cli/` (as needed
  beside `decision.py`)
- Combined decision view HTML (or equivalent) asset
- Deterministic CLI tests for stage/info/apply/stream contracts
- Evidence package trees:
  - `docs/milestones/006-decision-facing-perception-readiness/evidence/replay-shadow-decision/`
  - `docs/milestones/006-decision-facing-perception-readiness/evidence/chase-shadow-decision/`
  - `docs/milestones/006-decision-facing-perception-readiness/evidence/physical-shadow-decision/`
- Optional opt-in live helpers under `tests/live/` only if required to capture
  packages (not required to pass default CI)

### Modify

- `cli/automa_cli/decision.py` — register `shadow-proposals`, richer info, stream,
  apply, record
- `cli/automa_cli/app.py` / `vehicles.py` — wire subcommands if not already
  present
- `implementations/decision/catalog.py` — only if activation catalog exposure
  needs a non-behavioral registration hook (no policy change)
- Milestone `plan.md` / `plan.html` only at implementation handoff transitions

### Remove

- None

### Explicitly out of bounds

- Edits to PR #74 lifecycle matrix, selector ranking, privilege rules, or plugin
  lateral policy except bugfix **if and only if** a proposal repair is opened
  (not this unit’s default path)

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

1. Stage `shadow-proposals` and reject unknown engines / invalid config.
2. Info human + `--json` completeness (inputs, plugins, selector, authority,
   view).
3. Apply digest determinism (double run identical).
4. Apply default writes nothing; `--record` produces exact-frame HTML with
   correlated fields and `proposed_applied=false`.
5. Stream/latest-frame payload schema fields present on a fixture cycle.
6. Privileged-origin keys cannot appear in constructed decision sources used by
   the surface (reuse or extend PR #74 source tests as needed).
7. No test enables applied non-idle control for `shadow-proposals`.

### Live / external (required for package acceptance; not default CI)

Document exact commands, host versions, and environmental assumptions in each
package README.

| Package | Minimum proof |
| --- | --- |
| replay-shadow-decision | Double-apply identity; active + fail-closed frames; `--record` sample |
| chase-shadow-decision | Stage/stream or apply path; steer-away when evidence present; evaluator isolation attestation |
| physical-shadow-decision | User mode; zero pilot; applied false every frame; active and/or honest inactive |

Live failures due to missing lateral perception are **not** fixed by retuning
perception in this unit; record inactive and still prove authority/isolation.

### Documentation

```text
python3 docs/render_markdown.py --check
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/006-decision-facing-perception-readiness \
  --head-ref m006/shadow-proposal-evidence-proposal \
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
  "durable_evidence": "Automa decision stage/info/apply/stream/view for shadow-proposals; deterministic replay digests; combined decision view; tracked replay/Chase/Pi evidence packages with proposed_applied=false in PR #{pr}",
  "criterion_updates": {
    "M006-05": {
      "status": "Met",
      "evidence": "Decision stage/info/apply/stream/view with concise default, --json, deterministic apply, latest-frame stream, combined view, opt-in --record HTML, no default disk writes in PR #{pr}"
    },
    "M006-06": {
      "status": "Met",
      "evidence": "Tracked replay, Chase, and stationary Pi evidence packages under docs/milestones/006-decision-facing-perception-readiness/evidence/ exercising the same contracts and view in PR #{pr}"
    },
    "M006-07": {
      "status": "Met",
      "evidence": "Chase sources free of evaluator/map-privileged channels; Pi user mode with zero pilot/applied output; proposed_applied=false throughout evidence in PR #{pr}"
    }
  },
  "risk_remove": [],
  "risk_upsert": [
    {
      "risk": "Packaged physical perception may not emit stable lateral cues for every placement",
      "consequence": "Pi package may show honest inactive more often than Chase",
      "resolution": "Keep fail-closed evidence; do not retune perception inside M006; defer perception work to a later reviewed unit if needed"
    }
  ],
  "next_frontier": {
    "state": "none",
    "reason": "Milestone closeout is promoted from the frozen next-candidate slot after M006-05–M006-07.",
    "revisit_when": "Closeout judges residual limits and any later movement or prediction milestone."
  }
}
```

### Sequence after this proposal merges

1. Operator runs `workflow.py accept-proposal` (or equivalent) →
   `ready_for_implementation`.
2. Implementation branch `m006/shadow-proposal-evidence` implements only this
   contract against PR #74 types.
3. Implementation PR validates deterministic suite + attaches the three evidence
   packages (live captures may be operator-assisted but must be reviewed).
4. On accept, handoff marks M006-05–M006-07 Met and promotes **Milestone
   closeout** (M006-08) to the current frontier; do not invent a new policy
   plugin.
