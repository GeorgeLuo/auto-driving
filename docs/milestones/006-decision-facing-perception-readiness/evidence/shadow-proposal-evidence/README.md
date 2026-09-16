# M006 cross-environment shadow-proposal evidence

Status: **incomplete; canonical capture not ready**.

This directory is the stable per-frontier evidence root required by the
accepted M006 proposal. The committed material currently contains the frozen
procedure, fail-closed readiness result, and one explicitly preparatory offline
public-door check. It does not contain a successful Chase or PiRacer package.
No live worker, simulator, vehicle, or physical control was started by this
run.

## Authority and scope

- Accepted proposal:
  `docs/milestones/006-decision-facing-perception-readiness/proposals/shadow-proposal-evidence.md`
- Governing plan:
  `docs/milestones/006-decision-facing-perception-readiness/plan.md`
- Review kind: `Live or external evidence`
- Criteria: `M006-06`, `M006-07`
- Engine: `shadow-proposals`
- Plugin: `avoid_recent_obstruction`
- Selector: `deterministic_first_active`
- Evidence root: `evidence/shadow-proposal-evidence/`

The accepted question remains fixed: determine whether the staged proposal path
produces provenance-complete shadow plans and the same correlated visual
explanation on Chase and stationary PiRacer inputs while applied control stays
zero and privileged simulator state stays outside controller inputs.

## Readiness gate

Canonical capture is allowed only when all of the following are recorded:

1. The accepted proposal and `ready_for_implementation` workflow state are
   still recorded on the governing base.
2. This procedure, the lossless sequence projection, bounded selection, and
   verification checks are frozen.
3. D1 has an explicit availability/verification receipt for a genuine physical
   shadow-cycle publication with run, activation, frame, and liveness identity,
   plus host mode and pilot observations.
4. D2 has an explicit availability/verification receipt for a correlated live
   decision URL with the retained-evidence explanation required by the
   proposal.
5. The operator explicitly authorizes the bounded Chase and stationary PiRacer
   capture after reviewing those receipts.

The current result is blocked before step 5. D1 and D2 are provisional
candidate conditions, not implementation permission. This evidence unit does
not implement either capability or edit the accepted proposal.

### Current receipts

| Receipt | Status | Observation | Impact |
| --- | --- | --- | --- |
| Proposal/workflow identity | verified | Plan reports `ready_for_implementation`; PR #201 acceptance is recorded at merge `9e2a353c` from reviewed head `f9705785`. | Preparation may continue. |
| D1 physical shadow-cycle publication/liveness | blocked | `cli/automa_cli/automation.py` discovers with `include_picar=False` and rejects every provider other than `chase-sim`; the documented automation worker therefore has no PiRacer shadow-cycle publication route. | M006-07 physical live capture cannot be claimed. |
| D2 live decision URL/retained-evidence overlay | blocked | Staged decision `info` returns `combined_view.url: null` and only `cli/automa_cli/decision_view.html#decision-combined-v0`; the CLI exposes offline `apply` and terminal `stream`, not a live decision-view command. | The live visual-correlation claim cannot be claimed. |
| Operator capture authorization | pending | No authorization is valid while D1/D2 receipts are blocked. | Do not start canonical capture. |

The exact machine-readable disposition is in [result.json](result.json), with
the derived review page in [result.html](result.html). The JSON record is
authoritative; regenerate the page with `python3 render_result.py`.

## Frozen capture procedure (when the gate is satisfied)

The operator must retain the command output and raw records beside each
environment package. Every accepted interval is a join of the same vehicle,
run, activation generation, frame/cycle identity, source image, source
references, and host authority observation.

### Shared setup and identity

1. Record repository/deployed bundle identity, policy revision, engine/plugin
   configuration, host time, operator, and environment. Use the accepted
   `shadow-proposals` configuration without retuning:
   `avoid_recent_obstruction`, accepted kinds, `retained_max_age_ms <= 1000`,
   and the staged steering magnitude.
2. Run the passive status check for the selected environment and retain its
   complete JSON. Do not run `simulators ensure`, change the scenario, alter
   playback, take control, or reset a session as part of this evidence unit.
3. Stage and inspect the decision path through the existing public surfaces:

   ```sh
   ./cli/automa vehicles update decision --id <vehicle> \
     --engine shadow-proposals --json
   ./cli/automa vehicles info decision --id <vehicle> --json
   ```

   Accept only an exact engine configuration, `deterministic_first_active`,
   `proposed_applied=false`, and a verified live combined-view URL. The local
   path template is not a D2 receipt.

### Chase package

1. Preserve the existing Metrics UI/Chase session. Retain the session
   fingerprint, scenario, simulation epoch, playback/control source before,
   during, and after capture. A refusal is recorded as a gap; it is not worked
   around by starting or resetting the simulator.
2. Start only the supported observe-only automation path after the readiness
   gate, retaining its run id, worker pid, activation timestamp, and terminal
   cleanup state. The intended public command is:

   ```sh
   ./cli/automa vehicles automation run --id chase-sim-chaser \
     --observe-only --record --frames <bounded-count>
   ```

3. Collect generation-matched decision frames through the existing stream:

   ```sh
   ./cli/automa vehicles stream decision --id chase-sim-chaser \
     --json --once
   ```

   Retain the full frame, not only the concise terminal rendering. Reject a
   frame after worker stop, generation mismatch, stale publication, source
   image mismatch, or missing host authority.
4. Capture one supported left and one supported right scene. For each, record
   fresh `avoid_recent_obstruction` selection, steering sign, throttle zero,
   gear hold, source references, and the separate authorized idle/host output.
5. Without resetting the run, remove one side's evidence and retain a
   contiguous fresh -> retained active -> stale command-null/idle -> inactive
   sequence. Use real timestamps and memory snapshots. Keep the prior source
   image for retained/stale references; never edit timestamps to manufacture a
   transition.
6. Record an absent/expired supported-evidence witness with no selected
   proposal, source, or movement intent. Unsupported placement is a limit and
   cannot satisfy the active witness.

### Stationary PiRacer package

1. Verify D1’s genuine physical shadow-cycle publication and liveness receipt
   for the same accepted workflow before starting a worker.
2. Confirm stationary placement, `user` mode, idle user inputs, zero pilot
   steering/throttle, and matched onboard frame/image identity for the declared
   interval. Retain mode and pilot telemetry for every accepted interval.
3. Repeat the same left/right, continuous removal, absent, stop, cleanup, and
   replay-supported checks. A physical observation publication or still
   photograph alone is insufficient.

### Replay and visual review

1. Project captured observation/memory records losslessly into the accepted
   `automa_decision_apply_sequence_v0` shape, retaining source cycle identities,
   frozen activation, and code revision.
2. Apply each bounded sequence twice through the public door:

   ```sh
   ./cli/automa vehicles decision apply --id <vehicle> \
     --from-run <package> --json --record
   ```

   Record canonical digest bytes and SHA-256. Digest equality is necessary but
   not sufficient: captured source references, lifecycle, selection, and
   proposed command must agree with replay-supported fields.
3. Inspect the actual rendered exact-frame HTML and every current/retained
   source image it references. A template-only screenshot, JSON-only assertion,
   or offline replay page without original images does not satisfy the visual
   claim. Record the visual inspection receipt and screenshot paths.
4. Preserve raw publications/configuration/host records, derived sequences and
   replay results, exact-frame HTML, screenshots, failure receipts, uncovered
   intervals, and cleanup errors. Do not assemble a successful package from a
   partial run.

## Case map and acceptance boundaries

| Case | Required evidence |
| --- | --- |
| C1 | Matching environment, vehicle/run/activation/frame identity; same engine/plugin/selector; host output zero. |
| C2 | Left and right fresh active intent with expected sign, throttle zero, gear hold, and complete source references. |
| C3 | One no-reset fresh, retained, stale command-null/idle, inactive sequence with real timestamps and retained images. |
| C4 | Empty/expired evidence yields inactive, no selection, and no invented source or movement intent. |
| C5 | Mismatch, stale generation, missing host observation, or stopped worker is rejected and retained as a failure/gap. |
| C6 | Two replay passes have byte-equal canonical digests; visual review confirms actual image/reference correlation. |
| C7 | Chase evaluator state is outside controller inputs; Pi is stationary in `user` mode with zero pilot output throughout the accepted interval. |

No case is marked passed until the authoritative observation and all required
interval coverage are present. M006-06 and M006-07 remain `Unmet` in this
implementation unit.

## Bounds and non-claims

- Maximum apply sequence: 256 frames and 32 MiB of input.
- Maximum individual record: 8 MiB.
- Hashes establish byte consistency, not hardware authenticity.
- Recurring evidence IDs do not prove object identity; memory is not a
  trajectory; image motion does not prove self-motion.
- This unit does not claim prediction, localization, navigation, collision
  avoidance, movement safety, physical readiness, or applied control.
- Missing evidence is incomplete, not zero and not success.

## Preparatory-only material

`preparatory/public-door-result.json` records one offline fixture check through
the existing public CLI. Its input is the tracked test fixture
`tests/cli/decision/fixtures/apply_active_left/sequence.json` with SHA-256
`bc5bcc2164f2c8b198163d3ed6e61648b787a62d82904ed56855270e07a39c76`. The
decision digest was deterministic with SHA-256
`63369a158af3198a44e145ed11aa71dbb62097e46a39e3e458909fe08a53e54b`; it showed
proposed steering `+0.35`, authorized steering/throttle `0.0`, and
`proposed_applied=false`. The fixture has no source image, so this check is
explicitly not evidence for C2/C6 visual correlation or either live criterion.
