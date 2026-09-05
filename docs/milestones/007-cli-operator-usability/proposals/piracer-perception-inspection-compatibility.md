# Proposal: PiRacer perception-inspection compatibility

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | PiRacer perception-inspection compatibility |
| Proposal branch | `m007/piracer-perception-inspection-compatibility-proposal` |
| Implementation branch | `m007/piracer-perception-inspection-compatibility` |
| Exit criterion | M007-06 |
| Review finding | [P1] staged PiRacer perception inspection suppresses reachable live state ([PR #81 inline finding](https://github.com/GeorgeLuo/auto-driving/pull/81#discussion_r3849733263)) |
| Review kind | Review repair |

## Review Question

Can `vehicles info perception --id piracer` retain successful offline
inspection of a locally staged `active.json` while also enriching that result
with reachable live PiRacer observation and local-view state, without making a
PiRacer network outage invalidate the offline inspection path?

This is a new product review unit because PR #81 is a closed-plan cumulative
review surface. The PiRacer inspection finding must be repaired and reviewed at
its owning CLI perception boundary; PR #81 remains historical and is not edited
by this unit.

The proposal is grounded in the exact Phase C review finding. At the reviewed
PR #81 head, a valid local PiRacer activation made `vehicles info perception`
report only staged state and omit reachable live observation and physical-view
status. A PiRacer network outage could also collapse a valid offline inspection
into failure. This unit restores dual-source inspection without widening
inspection into control, deployment, or hardware work.

## Proposed Contract

### Dual-source inspection

`get_vehicle_perception_info` remains a read-only inspection operation with two
independent sources:

1. The local staged source reads and validates the activation manifest,
   controller bundle, mapper schema, local automation status, and any local
   perception view exactly as it does today. A valid local activation remains
   inspectable when no PiRacer is reachable.
2. The live source performs the existing bounded vehicle discovery even when a
   local activation exists. When the selected vehicle is a reachable `picar`,
   the result includes the existing physical observation publication and its
   physical local-view status. A healthy physical view may be reflected in the
   top-level `published_view` while preserving the staged activation fields.

The live source is enrichment, not a replacement for the staged source. A
discovery or physical-publication failure is represented as unavailable live
observation with an actionable error while a valid local inspection still
returns exit 0. If there is no local activation, the existing requirement for a
reachable PiRacer remains unchanged; a missing or non-Pi live vehicle still
returns the existing activation error.

The JSON envelope remains `vehicle_perception_info_v0`. For every result that
has a local activation, `live_observation` is present even when discovery finds
no selected vehicle, finds a non-Pi vehicle, or the Pi publication is
unavailable; those cases use `available: false` and a provider/reason or error
that makes the missing live capability explicit. A Pi result carries its
physical `published_view` status inside that object. Human output must always
print the corresponding live-observation state and reason alongside staged
algorithm/schema and bundle details, with the same availability meaning as
JSON. Offline operation must not claim live health, and a reachable live view
must not be silently reported as unavailable merely because `active.json`
exists.

### Regression acceptance

The implementation must add a deterministic CLI regression that:

- writes a valid local PiRacer activation and staged bundle fixture;
- supplies a reachable-Pi discovery result (`provider: "picar"`) with its
  connection/base URL and a valid observation publication;
- supplies a physical view status, including the available-view case;
- invokes the supported perception-info surface in JSON and, where practical,
  human form; and
- asserts that local activation/schema data and live observation/view data are
  both present, that the live discovery/publication path was called, and that
  the command succeeds.

Focused companion cases must prove that an unreachable PiRacer leaves valid
staged inspection successful with an explicit unavailable-live result, while a
reachable PiRacer without local staging preserves the existing live-only
inspection behavior. The test must not contact hardware or require a real Pi.

## Ownership

| Concern | Owner |
| --- | --- |
| Local-plus-live perception inspection composition and output | `cli/automa_cli/perception.py` |
| Reachable-Pi/local-activation regression and offline fallback coverage | `tests/cli/perception/test_commands.py` |
| Physical observation/view helpers consumed by this unit | Existing `cli/automa_cli/physical_observation.py` and stream contract; no ownership change |

The implementation owner is `cli/automa_cli/perception.py`; helper changes
outside that file require a proposal amendment because this unit is intended to
close one named owner boundary.

## Affected Paths

- `cli/automa_cli/perception.py` — compose local staged inspection with
  bounded live discovery and PiRacer observation/view enrichment.
- `tests/cli/perception/test_commands.py` — local-activation plus reachable-Pi
  regression and offline/unreachable fallback cases.

No PiRacer service, deployment, transport, or hardware implementation path is
changed by this contract. This proposal PR does not contain those later
implementation files.

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| Valid local PiRacer activation, reachable `provider: picar`, valid observation and available physical view | Exit 0; staged activation/schema and live observation are both present; live view is surfaced without being suppressed; no control or mutation is issued. |
| Valid local activation, reachable PiRacer observation, physical view unavailable | Exit 0; staged inspection and live observation remain visible; live view is explicitly unavailable with its reason. |
| Valid local activation, PiRacer discovery timeout/connection error | Exit 0; staged inspection remains complete; live observation is explicitly unavailable/error; no traceback and no false live health. |
| Valid local activation, reachable non-Pi vehicle | Exit 0 for staged inspection; `live_observation.available` is false with the observed provider/reason; no PiRacer observation is fabricated or substituted. |
| No local activation, reachable PiRacer | Preserve existing live-only inspection: exit 0 with live observation/view and no staged activation claim. |
| No local activation, PiRacer unavailable or non-Pi vehicle | Preserve existing nonzero activation guidance and do not report a successful inspection. |
| Local activation malformed or mapper/bundle missing while PiRacer is reachable | Fail the local inspection with the existing actionable activation error; do not let live enrichment hide a broken staged contract. |
| Discovery returns a `picar` without a usable base URL | Keep staged inspection successful; live observation is unavailable with a connection explanation; no fabricated endpoint/view. |
| Repeated inspection with an existing local activation | Perform no staging, worker start/stop, control, or input action; each result reflects fresh bounded live reachability. |
| Human output versus `--json` | Both distinguish staged and live state and agree on availability/error meaning; JSON remains valid `vehicle_perception_info_v0`. |

### Residuals and nonclaims

The frozen contract does not add a strict schema or adversarial validation
surface for malformed publication/frame/control values, arbitrary numeric
magnitudes, malformed local-view records, malformed activation JSON types, or
unexpected helper exception classes beyond the stated discovery and publication
failure paths. Those out-of-contract shapes remain residual/nonclaims for this
review unit; they are not required acceptance rows and are not implementation
authorization.

## External Assumptions

- `discover_active_vehicles` continues to return a stable `provider: "picar"`
  record with the connection information consumed by `picar_base_url`.
- The existing `fetch_observation_publication` and `physical_view_status`
  contracts remain the authoritative PiRacer observation and view sources.
- Reachability is bounded by the inspection function’s existing `timeout_s`
  argument (the current CLI uses its 3.0-second default); this unit does not
  add a timeout flag or redesign timeout parsing or operation-deadline policy.
- Deterministic tests may mock discovery/publication/view boundaries. No
  physical PiRacer, network, browser, or movement is required for acceptance.

## Non-Goals

- Repairing PR #81 or changing its closed cumulative review/closeout history.
- PiRacer deployment, service, discovery-protocol, camera, mapper, or runtime
  redesign.
- Requiring a live PiRacer or browser in the default test suite.
- Starting/stopping workers, streaming, sending controls, changing vehicle
  state, or adding movement authority to an inspection command.
- Reworking physical observation publication or view helpers owned by another
  boundary.
- Repairing the separate PR #81 Chase image-envelope finding.
- Combining this proposal with implementation in the same PR, or adopting a
  non-frontier combined-repair workflow.
- New output-schema versioning, broad CLI hierarchy changes, or milestone
  closeout acceptance.
- Beginning implementation in this proposal PR.

## File Impact

| Path | Proposal change | Later implementation role |
| --- | --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/piracer-perception-inspection-compatibility.md` | Add this reviewed contract | Immutable accepted proposal |
| `docs/milestones/007-cli-operator-usability/plan.md` | Select the current frontier and record M007-06 ownership; proposal workflow forbids pre-claiming criterion or risk changes | Record proposal/implementation handoffs only |
| `docs/milestones/007-cli-operator-usability/plan.html` | Generated rendering of the plan transition | Regenerated with canonical plan changes |
| `cli/automa_cli/perception.py` | None | Compose staged inspection with bounded live discovery and PiRacer observation/view enrichment |
| `tests/cli/perception/test_commands.py` | None | Public-boundary regressions for reachable PiRacer, unavailable/non-Pi discovery, live-only behavior, and human/JSON parity |

## Validation Plan

### Proposal PR

The proposal PR must contain only this artifact, the canonical plan transition,
and generated plan HTML:

```sh
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 -m unittest \
  tests.docs.test_milestone_proposal_workflow \
  tests.docs.test_milestone_planning
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/piracer-perception-inspection-compatibility-proposal \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

The proposal review verifies the exact PR #81 finding link, one review
question, the `perception.py` owner, dual-source inspection, the offline
fallback, and the absence of implementation files.

### Implementation PR after proposal acceptance

Deterministic tests must:

- cover every applied matrix row, especially local activation plus reachable
  PiRacer and explicit unavailable-live objects/reasons for discovery, non-Pi
  outcomes, unavailable publication/view results, and unusable PiCar base URLs;
- prove human/JSON parity and that inspection performs no staging, worker,
  control, or input action;
- keep the JSON envelope `vehicle_perception_info_v0`; and
- avoid live hardware or browser evidence.

Run the focused CLI perception tests, the repository suite, workflow
validation, Markdown rendering check, and `git diff --check`. No live PiRacer
or browser run is required for this deterministic review repair.

## Expected Handoff

Post-merge successful implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "PiRacer perception-inspection compatibility in PR #{pr}: local staged inspection remains available offline while reachable PiRacer observation and physical view state are surfaced through the owned perception CLI boundary, with deterministic local-activation plus reachable-Pi regression coverage.",
  "criterion_updates": {
    "M007-06": {
      "status": "Partial",
      "evidence": "PR #{pr} closes the staged PiRacer inspection regression with local/offline, reachable-Pi, and unavailable-live coverage while preserving the existing read-only contract; M007-06 remains Partial until the separate whole-milestone closeout records the primary journey, all accepted-unit evidence, and residual limits."
    }
  },
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "The PiRacer inspection repair is promoted and the milestone remains idle; the separate Phase C Chase image-envelope finding remains outside this unit.",
    "revisit_when": "A later proposal is justified by the remaining Phase C finding or by a new milestone acceptance decision."
  }
}
```

This handoff applies only after the implementation review has verified the
entire matrix and the exact-head acceptance receipt. It does not mark M007-06
Met or authorize cumulative PR #81 to merge.

## Sequence After This Proposal Merges

1. Obtain the exact-head proposal review receipt and merge this proposal into
   `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal` for the proposal PR and confirm
   `ready_for_implementation` with the recorded reviewed head and merge commit.
3. Start `m007/piracer-perception-inspection-compatibility` and implement only
   this contract. Parked product work from the former combined PR may be
   rebased onto that branch after acceptance.
4. Review the implementation against the matrix, repair within this unit if
   required, then complete the implementation handoff.
5. Return M007 to idle. A later proposal may route the remaining Phase C Chase
   finding; this unit does not select or implement it.

## Review Kind

**Review repair** — a separate owned product review unit is required because
the exact P1 was found during the rejected cumulative PR #81 review and the
closed-plan PR must remain unchanged. The unit is bounded to the PiRacer
perception-inspection owner and its regressions.
